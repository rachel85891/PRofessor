"""Configuration of the Docker runner, filled from `RunnerSettings` by bootstrap."""

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
class DockerRunnerConfig:
    """Images, limits, timeouts and names used by `DockerCodeRunner` (ADR 0015)."""

    sdk_image: str
    runtime_image: str
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

    def __post_init__(self) -> None:
        durations = (
            self.restore_timeout_seconds,
            self.build_timeout_seconds,
            self.readiness_timeout_seconds,
            self.probe_timeout_seconds,
            self.readiness_poll_seconds,
            self.step_poll_seconds,
        )
        if any(duration <= 0 for duration in durations):
            raise ValueError("runner timeouts and poll intervals must be positive")
        if self.max_archive_bytes < 1 or self.max_file_bytes < 1:
            raise ValueError("archive size limits must be positive")

    @property
    def user(self) -> str:
        """The non-root `uid:gid` every container runs as."""
        return f"{self.app_uid}:{self.app_gid}"

    @property
    def images(self) -> tuple[str, str, str]:
        """The pinned images the runner needs."""
        return (self.sdk_image, self.runtime_image, self.probe_image)
