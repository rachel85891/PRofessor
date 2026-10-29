"""Application settings: secrets from the environment or `.env`, the rest from `config.yaml`."""

from pathlib import Path
from typing import Annotated, Any, Literal, Self

import yaml
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    SecretStr,
    ValidationError,
    field_validator,
    model_validator,
)
from pydantic_settings import BaseSettings, PydanticBaseSettingsSource, SettingsConfigDict

from hw_checker.config.errors import ConfigurationError

REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_CONFIG_PATH = REPO_ROOT / "config.yaml"
DEFAULT_ENV_PATH = REPO_ROOT / ".env"

LogLevel = Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]


class LoggingSettings(BaseModel):
    """Logging level and the directory for the JSON log file."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    level: LogLevel = "INFO"
    log_dir: Path = Path("logs")


NonBlankText = Annotated[str, Field(pattern=r"\S")]


class RosterColumnsSettings(BaseModel):
    """Header texts of the roster columns. The defaults are placeholders for the real file."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: NonBlankText = "Name"
    partner_name: NonBlankText = "Partner"
    github_url: NonBlankText = "GitHub"


class RosterSettings(BaseModel):
    """Where the roster data sits in the Excel file."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    sheet_name: str | None = None
    header_row: int = Field(default=1, ge=1)
    columns: RosterColumnsSettings = RosterColumnsSettings()


class GitSettings(BaseModel):
    """How the git CLI is run. The commit author is set in `.env`, never in this repo."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    executable: NonBlankText = "git"
    timeout_seconds: int = Field(default=120, ge=1)
    ssl_backend: Literal["openssl", "schannel"] | None = None
    network_retries: int = Field(default=2, ge=0, le=10)
    retry_backoff_seconds: float = Field(default=3, ge=0, le=60)
    commit_author_name: NonBlankText | None = None
    commit_author_email: NonBlankText | None = None


PinnedImage = Annotated[
    str, Field(pattern=r"^[a-z0-9][a-z0-9._/-]*(:[A-Za-z0-9._-]+)?@sha256:[0-9a-f]{64}$")
]
"""An image reference pinned by digest: `<name>[:<tag>]@sha256:<64 hex>`."""

FrameworkName = Annotated[str, Field(pattern=r"^net\d+\.\d+$")]
"""A .NET target framework moniker such as `net10.0`."""


class FrameworkImages(BaseModel):
    """The pinned SDK image (restore, build) and runtime image (run) of one framework."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    sdk: PinnedImage
    runtime: PinnedImage


class StepLimits(BaseModel):
    """Resource limits for one container step."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    memory_mb: int = Field(ge=1)
    cpus: float = Field(gt=0)
    pids: int = Field(ge=1)


class RunnerSettings(BaseModel):
    """How student projects are built and run in Docker (ADR 0015)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    framework_images: dict[FrameworkName, FrameworkImages] = {
        "net8.0": FrameworkImages(
            sdk="mcr.microsoft.com/dotnet/sdk:8.0@sha256:738f72aff955d5c5690e06b6fc8ecd1b90ead564099a257ac528ff720b8da85f",
            runtime="mcr.microsoft.com/dotnet/aspnet:8.0@sha256:74a1d7c07211a7e8a2e35ff3c70ed8e4e1d6451a1ff7cf7bdf526f9eafa23023",
        ),
        "net9.0": FrameworkImages(
            sdk="mcr.microsoft.com/dotnet/sdk:9.0@sha256:d9ad72f6b3eb5d61adb158db510ae1c3098c12c92d23ab8da833418275f1c652",
            runtime="mcr.microsoft.com/dotnet/aspnet:9.0@sha256:88fcfc1b81c43afa15ba3c138c57d77f77c1cb5118ae9d7024c94e82e2895aa5",
        ),
        "net10.0": FrameworkImages(
            sdk="mcr.microsoft.com/dotnet/sdk:10.0@sha256:747cbade5bfd918a175ac32f8f1d931edec2c7d0d5397a3de72eaef1ac1f66d2",
            runtime="mcr.microsoft.com/dotnet/aspnet:10.0@sha256:8af87eba5e7cdafb1514d7388be287365da97c132509e84dfe252ffd552398ca",
        ),
    }
    """The images for each supported target framework; other frameworks are not run."""
    course_framework: FrameworkName = "net10.0"
    """The course's framework; projects on an older one are marked for the student."""
    probe_image: PinnedImage = (
        "curlimages/curl:8.10.1"
        "@sha256:d9b4541e214bcd85196d6e92e2753ac6d0ea699f0af5741f8c6cccbfcf00ef4b"
    )
    restore_limits: StepLimits = StepLimits(memory_mb=1024, cpus=2, pids=512)
    build_limits: StepLimits = StepLimits(memory_mb=1024, cpus=2, pids=512)
    run_limits: StepLimits = StepLimits(memory_mb=512, cpus=1, pids=512)
    probe_limits: StepLimits = StepLimits(memory_mb=64, cpus=0.5, pids=64)
    restore_timeout_seconds: int = Field(default=300, ge=1)
    build_timeout_seconds: int = Field(default=300, ge=1)
    readiness_timeout_seconds: int = Field(default=30, ge=1)
    probe_timeout_seconds: int = Field(default=10, ge=1)
    readiness_poll_seconds: float = Field(default=0.5, gt=0)
    resolve_grace_seconds: float = Field(default=5, gt=0)
    app_port: int = Field(default=8080, ge=1, le=65535)
    app_uid: int = Field(default=1654, ge=1)
    app_gid: int = Field(default=1654, ge=1)
    nuget_cache_volume: str = Field(
        default="hw-checker-nuget-cache", pattern=r"^[a-zA-Z0-9][a-zA-Z0-9_.-]+$"
    )
    label_prefix: str = Field(default="hw-checker", pattern=r"^[a-z0-9][a-z0-9.-]*$")
    max_archive_mb: int = Field(default=200, ge=1)
    max_file_mb: int = Field(default=50, ge=1)
    max_response_kb: int = Field(default=64, ge=1)
    engine_timeout_seconds: int = Field(default=120, ge=1)
    """Seconds the Docker client waits for one engine API call."""
    logs_root: Path = Path("logs")
    """Folder for full build and run logs inside each run's folder:
    `<workspace_dir>/runs/<run_id>/<logs_root>/<repo_id>`."""

    @model_validator(mode="after")
    def _course_framework_has_images(self) -> Self:
        if self.course_framework not in self.framework_images:
            raise ValueError(
                f"course_framework {self.course_framework} has no entry in framework_images"
            )
        return self

    @field_validator("logs_root")
    @classmethod
    def _logs_root_stays_inside_workspace(cls, value: Path) -> Path:
        # `anchor` (drive or root) also catches "/logs", which Windows doesn't call absolute.
        if value.anchor or ".." in value.parts:
            raise ValueError("must be a relative path inside workspace_dir, without '..'")
        return value


class StorageSettings(BaseModel):
    """Where rounds, findings and proposals are stored (ADR 0007)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    database_url: NonBlankText = "sqlite:///workspace/hw_checker.db"


class Settings(BaseSettings):
    """All runtime settings.

    Precedence, highest first: environment variables, `.env`, `config.yaml`, field defaults.
    Environment variables use the `HW_CHECKER_` prefix and `__` as the nested delimiter,
    for example `HW_CHECKER_LOGGING__LEVEL=DEBUG`.
    """

    model_config = SettingsConfigDict(
        env_prefix="HW_CHECKER_",
        env_nested_delimiter="__",
        env_file_encoding="utf-8",
        env_ignore_empty=True,
        extra="forbid",
        frozen=True,
    )

    workspace_dir: Path = Path("workspace")
    assignments_dir: Path = Path("assignments")
    max_parallel_repos: int = Field(default=3, ge=1)
    logging: LoggingSettings = LoggingSettings()
    roster: RosterSettings = RosterSettings()
    git: GitSettings = GitSettings()
    runner: RunnerSettings = RunnerSettings()
    storage: StorageSettings = StorageSettings()
    github_token: SecretStr | None = None

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        """Rank YAML values (passed as init arguments) below the environment and `.env`."""
        return env_settings, dotenv_settings, init_settings, file_secret_settings


def load_settings(
    config_path: Path = DEFAULT_CONFIG_PATH,
    env_path: Path = DEFAULT_ENV_PATH,
) -> Settings:
    """Load settings from `config_path` (required) and `env_path` (optional).

    Raises:
        ConfigurationError: the YAML file is missing or malformed, or a value is invalid.
    """
    yaml_values = _read_yaml_mapping(config_path)
    try:
        return Settings(_env_file=env_path, **yaml_values)
    except ValidationError as error:
        raise ConfigurationError(
            _describe_validation_error(error, config_path, env_path)
        ) from error


def _read_yaml_mapping(path: Path) -> dict[str, Any]:
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError as error:
        raise ConfigurationError(f"Configuration file not found: {path}") from error
    except OSError as error:
        raise ConfigurationError(f"Cannot read configuration file {path}: {error}") from error
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as error:
        raise ConfigurationError(f"Malformed YAML in configuration file {path}: {error}") from error
    if data is None:
        return {}
    if not isinstance(data, dict):
        raise ConfigurationError(f"Configuration file {path} must contain a mapping at top level")
    return data


def _describe_validation_error(error: ValidationError, config_path: Path, env_path: Path) -> str:
    problems = "; ".join(
        f"{'.'.join(str(part) for part in item['loc']) or '<root>'}: {item['msg']}"
        for item in error.errors()
    )
    return f"Invalid configuration (from {config_path}, {env_path} or environment): {problems}"
