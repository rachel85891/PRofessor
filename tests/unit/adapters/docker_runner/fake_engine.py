"""An in-memory DockerEngine for unit tests: records every call and simulates containers."""

from collections.abc import Mapping
from dataclasses import dataclass, field

from hw_checker.adapters.docker_runner.engine import (
    ContainerSpec,
    ContainerState,
    EngineError,
    ExecResult,
    Resource,
)


@dataclass
class FakeContainer:
    id: str
    spec: ContainerSpec
    started: bool = False

    @property
    def kind(self) -> str:
        return next(v for k, v in self.spec.labels.items() if k.endswith(".kind"))


class FakeClock:
    """A monotonic clock that only moves when the runner sleeps."""

    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += seconds


@dataclass
class FakeEngine:
    """Containers are simulated by their `kind` label.

    `exit_codes` maps a step kind (restore, build) to its exit code; None keeps it running.
    `app` is "ready", "crash" (exits with 139) or "hang". `fail_on` maps "operation" or
    "operation:kind" to an exception to raise there. The first `resolve_failures` requests
    from the sidecar fail as curl does when it cannot resolve the host (exit 6).
    `probe_responses` maps (method, path) to a (status, headers, body) response; otherwise
    the status comes from `probe_statuses` (200 by default) with no headers and no body.
    """

    exit_codes: dict[str, int | None] = field(default_factory=dict)
    logs_by_kind: dict[str, str] = field(default_factory=dict)
    app: str = "ready"
    probe_statuses: dict[tuple[str, str], int] = field(default_factory=dict)
    probe_responses: dict[tuple[str, str], tuple[int, dict[str, str], str]] = field(
        default_factory=dict
    )
    images_present: bool = True
    cache_present: bool = True
    cache_volume: str = "hw-checker-test-nuget"
    reachable: bool = True
    oom_kinds: set[str] = field(default_factory=set)
    resolve_failures: int = 0
    fail_on: dict[str, BaseException] = field(default_factory=dict)
    containers: dict[str, FakeContainer] = field(default_factory=dict)
    networks: dict[str, tuple[dict[str, str], bool]] = field(default_factory=dict)
    volumes: dict[str, dict[str, str]] = field(default_factory=dict)
    calls: list[str] = field(default_factory=list)
    exec_commands: list[tuple[str, ...]] = field(default_factory=list)
    pulled: list[str] = field(default_factory=list)
    archives: list[tuple[str, str, int]] = field(default_factory=list)
    removed: list[str] = field(default_factory=list)
    created: list[FakeContainer] = field(default_factory=list)
    _next_id: int = 0

    def _fail(self, operation: str, kind: str | None = None) -> None:
        for key in (f"{operation}:{kind}", operation):
            if key in self.fail_on:
                raise self.fail_on[key]

    def _record(self, operation: str, detail: str = "") -> None:
        self.calls.append(f"{operation} {detail}".strip())

    # engine operations

    def ping(self) -> None:
        self._record("ping")
        if not self.reachable:
            raise EngineError("Docker engine did not answer: pipe not found")

    def image_exists(self, image: str) -> bool:
        return self.images_present or image in self.pulled

    def pull(self, image: str) -> None:
        self._fail("pull")
        self._record("pull", image)
        self.pulled.append(image)

    def volume_exists(self, name: str) -> bool:
        return name in self.volumes or (self.cache_present and name == self.cache_volume)

    def create_volume(self, name: str, labels: Mapping[str, str]) -> None:
        self._fail("create_volume")
        self._record("create_volume", name)
        self.volumes[name] = dict(labels)

    def remove_volume(self, name: str) -> None:
        self._fail("remove_volume")
        self._record("remove_volume", name)
        self.volumes.pop(name, None)
        self.removed.append(name)

    def create_network(self, name: str, labels: Mapping[str, str], internal: bool) -> None:
        self._fail("create_network")
        self._record("create_network", name)
        self.networks[name] = (dict(labels), internal)

    def remove_network(self, name: str) -> None:
        self._fail("remove_network")
        self._record("remove_network", name)
        self.networks.pop(name, None)
        self.removed.append(name)

    def create_container(self, spec: ContainerSpec) -> str:
        kind = next(v for k, v in spec.labels.items() if k.endswith(".kind"))
        self._fail("create_container", kind)
        self._next_id += 1
        container = FakeContainer(f"c{self._next_id}", spec)
        self.containers[container.id] = container
        self.created.append(container)
        self._record("create_container", kind)
        return container.id

    def put_archive(self, container: str, path: str, data: bytes) -> None:
        self._fail("put_archive")
        self.archives.append((container, path, len(data)))
        self._record("put_archive", path)

    def start(self, container: str) -> None:
        item = self.containers[container]
        self._fail("start", item.kind)
        item.started = True
        self._record("start", item.kind)

    def state(self, container: str) -> ContainerState:
        item = self.containers[container]
        self._fail("state", item.kind)
        oom = item.kind in self.oom_kinds
        if item.kind == "app":
            if self.app == "crash":
                return ContainerState(running=False, exit_code=139, oom_killed=oom)
            return ContainerState(running=True, exit_code=None, oom_killed=oom)
        if item.kind == "probe":
            return ContainerState(running=True, exit_code=None, oom_killed=False)
        exit_code = self.exit_codes.get(item.kind, 0)
        if exit_code is None:
            return ContainerState(running=True, exit_code=None, oom_killed=oom)
        return ContainerState(running=False, exit_code=exit_code, oom_killed=oom)

    def logs(self, container: str) -> str:
        item = self.containers[container]
        self._fail("logs", item.kind)
        return self.logs_by_kind.get(item.kind, f"{item.kind} log\n")

    def exec(self, container: str, command: tuple[str, ...]) -> ExecResult:
        self._fail("exec")
        self.exec_commands.append(command)
        if len(self.exec_commands) <= self.resolve_failures:
            return ExecResult(6, "000")
        if self.app != "ready":
            return ExecResult(7, "000")
        method = command[command.index("--request") + 1]
        url = command[command.index("--") + 1]
        path = "/" + url.split("/", 3)[3] if url.count("/") >= 3 else "/"
        status, headers, body = self.probe_responses.get(
            (method, path), (self.probe_statuses.get((method, path), 200), {}, "")
        )
        header_lines = "".join(f"{name}: {value}\r\n" for name, value in headers.items())
        return ExecResult(0, f"HTTP/1.1 {status} Status\r\n{header_lines}\r\n{body}")

    def remove_container(self, container: str) -> None:
        item = self.containers.get(container)
        self._fail("remove_container", item.kind if item else None)
        self._record("remove_container", item.kind if item else container)
        self.containers.pop(container, None)
        self.removed.append(container)

    def list_labelled(self, label: str) -> tuple[Resource, ...]:
        self._fail("list_labelled")
        containers = [
            Resource("container", item.id, dict(item.spec.labels))
            for item in self.containers.values()
            if label in item.spec.labels
        ]
        networks = [
            Resource("network", name, labels)
            for name, (labels, _) in self.networks.items()
            if label in labels
        ]
        volumes = [
            Resource("volume", name, labels)
            for name, labels in self.volumes.items()
            if label in labels
        ]
        return (*containers, *networks, *volumes)

    # helpers for assertions

    def specs(self, kind: str) -> list[ContainerSpec]:
        """Specs of every container of `kind` ever created, removed or not."""
        return [item.spec for item in self.created if item.kind == kind]

    @property
    def leftovers(self) -> int:
        """Containers, networks and volumes still present, ignoring the shared cache."""
        work_volumes = [name for name in self.volumes if name != self.cache_volume]
        return len(self.containers) + len(self.networks) + len(work_volumes)
