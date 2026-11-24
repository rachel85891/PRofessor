"""Configuration of the Docker runner, filled from `RunnerSettings` by bootstrap."""

from collections.abc import Mapping
from dataclasses import dataclass

MIN_DATABASE_NAME_LENGTH = 16
"""Room for the `hw_` prefix and a hash when a database name is shortened."""


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
class SqlServerConfig:
    """The shared SQL Server container of a round (ADR 0004): image, licence, limits, names.

    Everything here is policy data from `config.yaml`; the adapter holds no literal of it.
    """

    image: str
    edition: str
    """The SQL Server edition (`MSSQL_PID`), for example `Developer`."""
    accept_eula: str
    """The value of `ACCEPT_EULA`; `Y` accepts the licence terms of the image."""
    memory_mb: int
    """The container's memory limit."""
    mssql_memory_limit_mb: int
    """SQL Server's own memory cap (`MSSQL_MEMORY_LIMIT_MB`), kept below the container's."""
    cpus: float
    pids: int
    startup_timeout_seconds: float
    readiness_poll_seconds: float
    alias: str
    """The server's DNS name on every repo's network."""
    user: str
    """The SQL login the apps connect with."""
    container_user: str
    """The operating-system account the container runs as."""
    sqlcmd_path: str
    max_database_name_length: int
    added_capabilities: tuple[str, ...]
    """Capabilities given back after all are dropped (the server binary needs one)."""

    def __post_init__(self) -> None:
        if self.memory_mb < 1 or self.cpus <= 0 or self.pids < 1:
            raise ValueError(f"SQL Server limits must be positive: {self.memory_mb} MB")
        if not 0 < self.mssql_memory_limit_mb < self.memory_mb:
            raise ValueError("MSSQL_MEMORY_LIMIT_MB must be positive and below the container limit")
        if self.startup_timeout_seconds <= 0 or self.readiness_poll_seconds <= 0:
            raise ValueError("SQL Server timeouts and poll intervals must be positive")
        if self.max_database_name_length < MIN_DATABASE_NAME_LENGTH:
            raise ValueError("the database name length limit is too small")


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
    database: SqlServerConfig | None = None
    """The shared SQL Server of a round; None when databases are off."""
    max_response_bytes: int = 65_536
    """The most of a probe response (headers and body) read from the sidecar."""
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
        if self.max_archive_bytes < 1 or self.max_file_bytes < 1 or self.max_response_bytes < 1:
            raise ValueError("archive and response size limits must be positive")
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
        server = () if self.database is None else (self.database.image,)
        return (*found, self.probe_image, *server)
