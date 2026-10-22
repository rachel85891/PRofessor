"""A narrow, typed wrapper around the Docker SDK for the runner.

Only `SdkDockerEngine` touches the SDK (typed through `types-docker`), and every SDK or
transport exception is turned into `EngineError`, so nothing from the SDK leaks out.
The `DockerEngine` protocol lets the pipeline be tested with an in-memory fake.
"""

from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Protocol

import docker
import requests
from docker.errors import DockerException, ImageNotFound, NotFound

CONTAINER_WORKDIR = "/work"


class EngineError(Exception):
    """The Docker engine refused or failed an operation."""


@dataclass(frozen=True)
class Mount:
    """A named volume mounted into a container."""

    volume: str
    target: str
    read_only: bool


@dataclass(frozen=True)
class ContainerSpec:
    """Everything needed to create one hardened container."""

    name: str
    image: str
    command: tuple[str, ...]
    labels: Mapping[str, str]
    user: str
    memory_mb: int
    cpus: float
    pids: int
    mounts: tuple[Mount, ...] = ()
    network: str | None = None
    network_mode: str | None = None
    environment: Mapping[str, str] = field(default_factory=dict)
    entrypoint: tuple[str, ...] | None = None
    read_only_root: bool = False
    tmpfs: Mapping[str, str] = field(default_factory=dict)
    working_dir: str = CONTAINER_WORKDIR


@dataclass(frozen=True)
class ContainerState:
    """A container's run state."""

    running: bool
    exit_code: int | None
    oom_killed: bool


@dataclass(frozen=True)
class ExecResult:
    """The result of running a command inside a container."""

    exit_code: int
    output: str


@dataclass(frozen=True)
class Resource:
    """A labelled container, network or volume."""

    kind: str  # "container", "network" or "volume"
    name: str
    labels: Mapping[str, str]


class DockerEngine(Protocol):
    """The Docker operations the runner needs. Every method raises `EngineError` on failure."""

    def ping(self) -> None: ...
    def image_exists(self, image: str) -> bool: ...
    def pull(self, image: str) -> None: ...
    def volume_exists(self, name: str) -> bool: ...
    def create_volume(self, name: str, labels: Mapping[str, str]) -> None: ...
    def remove_volume(self, name: str) -> None: ...
    def create_network(self, name: str, labels: Mapping[str, str], internal: bool) -> None: ...
    def remove_network(self, name: str) -> None: ...
    def create_container(self, spec: ContainerSpec) -> str: ...
    def put_archive(self, container: str, path: str, data: bytes) -> None: ...
    def start(self, container: str) -> None: ...
    def state(self, container: str) -> ContainerState: ...
    def logs(self, container: str) -> str: ...
    def exec(self, container: str, command: tuple[str, ...]) -> ExecResult: ...
    def remove_container(self, container: str) -> None: ...
    def list_labelled(self, label: str) -> tuple[Resource, ...]: ...


@contextmanager
def _engine_errors(action: str) -> Iterator[None]:
    try:
        yield
    except (DockerException, requests.exceptions.RequestException, OSError) as error:
        raise EngineError(f"{action}: {error}") from error


def digest_reference(image: str) -> str:
    """`name:tag@sha256:x` becomes `name@sha256:x`; the digest alone identifies the image."""
    if "@" not in image:
        return image
    name, digest = image.split("@", 1)
    last_segment = name.rsplit("/", 1)[-1]
    if ":" in last_segment:
        name = name[: len(name) - len(last_segment)] + last_segment.split(":", 1)[0]
    return f"{name}@{digest}"


class SdkDockerEngine:
    """`DockerEngine` backed by the Docker SDK for Python (named pipe on Windows)."""

    def __init__(self, client: docker.DockerClient) -> None:
        self._client = client

    @classmethod
    def from_environment(cls, timeout_seconds: int) -> "SdkDockerEngine":
        """Connect using the standard Docker environment (DOCKER_HOST or the default pipe)."""
        with _engine_errors("cannot connect to Docker"):
            return cls(docker.from_env(timeout=timeout_seconds))

    def ping(self) -> None:
        with _engine_errors("Docker engine did not answer"):
            self._client.ping()

    def image_exists(self, image: str) -> bool:
        with _engine_errors(f"cannot inspect image {image}"):
            try:
                self._client.images.get(digest_reference(image))
            except ImageNotFound:
                return False
            return True

    def pull(self, image: str) -> None:
        with _engine_errors(f"cannot pull {image}"):
            self._client.api.pull(digest_reference(image))

    def volume_exists(self, name: str) -> bool:
        with _engine_errors(f"cannot inspect volume {name}"):
            try:
                self._client.volumes.get(name)
            except NotFound:
                return False
            return True

    def create_volume(self, name: str, labels: Mapping[str, str]) -> None:
        with _engine_errors(f"cannot create volume {name}"):
            self._client.volumes.create(name=name, labels=dict(labels))

    def remove_volume(self, name: str) -> None:
        with _engine_errors(f"cannot remove volume {name}"):
            try:
                self._client.volumes.get(name).remove(force=True)
            except NotFound:
                return

    def create_network(self, name: str, labels: Mapping[str, str], internal: bool) -> None:
        with _engine_errors(f"cannot create network {name}"):
            self._client.networks.create(
                name, driver="bridge", internal=internal, labels=dict(labels)
            )

    def remove_network(self, name: str) -> None:
        with _engine_errors(f"cannot remove network {name}"):
            for network in self._client.networks.list(names=[name]):
                network.remove()

    def create_container(self, spec: ContainerSpec) -> str:
        with _engine_errors(f"cannot create container {spec.name}"):
            container = self._client.containers.create(
                spec.image,
                command=list(spec.command),
                entrypoint=list(spec.entrypoint) if spec.entrypoint is not None else None,
                name=spec.name,
                labels=dict(spec.labels),
                user=spec.user,
                environment=dict(spec.environment),
                volumes={
                    mount.volume: {"bind": mount.target, "mode": "ro" if mount.read_only else "rw"}
                    for mount in spec.mounts
                },
                working_dir=spec.working_dir,
                network=spec.network,
                network_mode=spec.network_mode,
                mem_limit=f"{spec.memory_mb}m",
                memswap_limit=f"{spec.memory_mb}m",
                nano_cpus=int(spec.cpus * 1_000_000_000),
                pids_limit=spec.pids,
                cap_drop=["ALL"],
                security_opt=["no-new-privileges"],
                privileged=False,
                read_only=spec.read_only_root,
                tmpfs=dict(spec.tmpfs),
            )
            return str(container.id)

    def put_archive(self, container: str, path: str, data: bytes) -> None:
        with _engine_errors(f"cannot copy files into {container}"):
            if not self._client.containers.get(container).put_archive(path, data):
                raise EngineError(f"cannot copy files into {container}")

    def start(self, container: str) -> None:
        with _engine_errors(f"cannot start container {container}"):
            self._client.containers.get(container).start()

    def state(self, container: str) -> ContainerState:
        with _engine_errors(f"cannot inspect container {container}"):
            attributes = self._client.containers.get(container).attrs
            state = attributes.get("State", {})
            running = bool(state.get("Running", False))
            exit_code = None if running else int(state.get("ExitCode", 0))
            return ContainerState(running, exit_code, bool(state.get("OOMKilled", False)))

    def logs(self, container: str) -> str:
        with _engine_errors(f"cannot read logs of {container}"):
            raw = self._client.containers.get(container).logs(stdout=True, stderr=True)
            return bytes(raw).decode("utf-8", errors="replace")

    def exec(self, container: str, command: tuple[str, ...]) -> ExecResult:
        with _engine_errors(f"cannot run a command in {container}"):
            result = self._client.containers.get(container).exec_run(list(command))
            raw = result.output or b""
            # Without stream=True the output is bytes; the stubs also allow an iterator.
            data = raw if isinstance(raw, bytes) else b"".join(raw)
            exit_code = -1 if result.exit_code is None else int(result.exit_code)
            return ExecResult(exit_code, data.decode("utf-8", errors="replace"))

    def remove_container(self, container: str) -> None:
        with _engine_errors(f"cannot remove container {container}"):
            try:
                self._client.containers.get(container).remove(force=True)
            except NotFound:
                return

    def list_labelled(self, label: str) -> tuple[Resource, ...]:
        with _engine_errors(f"cannot list resources labelled {label}"):
            selector: dict[str, str | list[str] | bool] = {"label": label}
            containers = [
                Resource("container", str(item.name), dict(item.labels or {}))
                for item in self._client.containers.list(all=True, filters=selector)
            ]
            networks = [
                Resource("network", str(item.name), dict(item.attrs.get("Labels") or {}))
                for item in self._client.networks.list(filters=selector)
            ]
            volumes = [
                Resource("volume", str(item.name), dict(item.attrs.get("Labels") or {}))
                for item in self._client.volumes.list(filters=selector)
            ]
            return (*containers, *networks, *volumes)
