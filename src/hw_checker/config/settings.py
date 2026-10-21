"""Application settings: secrets from the environment or `.env`, the rest from `config.yaml`."""

from pathlib import Path
from typing import Annotated, Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, SecretStr, ValidationError
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
    """How the git CLI is run. The commit author values are placeholders until decided."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    executable: NonBlankText = "git"
    timeout_seconds: int = Field(default=120, ge=1)
    commit_author_name: NonBlankText = "HW Checker"
    commit_author_email: NonBlankText = "hw-checker@example.invalid"


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
    max_parallel_repos: int = Field(default=3, ge=1)
    logging: LoggingSettings = LoggingSettings()
    roster: RosterSettings = RosterSettings()
    git: GitSettings = GitSettings()
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
