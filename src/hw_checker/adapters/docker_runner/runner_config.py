"""Configuration of the Docker runner, filled from `RunnerSettings` by bootstrap."""

from collections.abc import Mapping
from dataclasses import dataclass


@dataclass(frozen=True)
class ContainerLimits:
    """Resource limits for one kind of container."""

    memory_mb: int
    cpus: float
    pids: int

    def __post_init__(self) -> None:
        if self.memory_mb < 1 or self.cpus <= 0 or self.pids < 1:
            raise ValueError(f"container limits must be positive: {self}")


@dataclass(frozen=True)
class FrameworkImages:
    """The pinned SDK image (restore, build) and runtime image (run) of one framework."""

    sdk: str
    runtime: str


@dataclass(frozen=True)
class DockerRunnerConfig:
    """Images, limits, timeouts and names used by `DockerCodeRunner` (ADR 0015)."""

    framework_images: Mapping[str, FrameworkImages]
    """Images by target framework (`net8.0`, ...); projects on other frameworks are not run."""
    course_framework: str
    """The course's framework; its runtime image also runs the helper containers."""
    probe_image: str
    restore_limits: ContainerLimits
    build_limits: ContainerLimits
    run_limits: ContainerLimits
    probe_limits: ContainerLimits
    restore_timeout_seconds: float
    build_timeout_seconds: float
    readiness_timeout_seconds: float
    probe_timeout_seconds: float
    readiness_poll_seconds: float
    app_port: int
    app_uid: int
    app_gid: int
    nuget_cache_volume: str
    label_prefix: str
    max_archive_bytes: int
    max_file_bytes: int
    step_poll_seconds: float = 0.5
    resolve_grace_seconds: float = 5.0
    """How long the sidecar may fail to resolve the app's name before it is an environment
    failure instead of a slow start."""

    def __post_init__(self) -> None:
        durations = (
            self.restore_timeout_seconds,
            self.build_timeout_seconds,
            self.readiness_timeout_seconds,
            self.probe_timeout_seconds,
            self.readiness_poll_seconds,
            self.step_poll_seconds,
            self.resolve_grace_seconds,
        )
        if any(duration <= 0 for duration in durations):
            raise ValueError("runner timeouts and poll intervals must be positive")
        if self.max_archive_bytes < 1 or self.max_file_bytes < 1:
            raise ValueError("archive size limits must be positive")
        if self.course_framework not in self.framework_images:
            raise ValueError(f"no images for the course framework {self.course_framework}")

    @property
    def user(self) -> str:
        """The non-root `uid:gid` every container runs as."""
        return f"{self.app_uid}:{self.app_gid}"

    @property
    def helper_image(self) -> str:
        """The image of short-lived helper containers (copying files in, preparing the cache)."""
        return self.framework_images[self.course_framework].runtime

    @property
    def images(self) -> tuple[str, ...]:
        """Every pinned image the runner needs, each once, in framework order."""
        found: list[str] = []
        for pair in self.framework_images.values():
            found += [image for image in (pair.sdk, pair.runtime) if image not in found]
        return (*found, self.probe_image)
