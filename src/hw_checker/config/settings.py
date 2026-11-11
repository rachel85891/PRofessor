"""Application settings: secrets from the environment or `.env`, the rest from `config.yaml`."""

from pathlib import Path
from typing import Annotated, Any, Literal, Self, get_args
from urllib.parse import urlsplit

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


class GitHubSettings(BaseModel):
    """The GitHub API and the publishing account (docs/fix-pr-workflow.md §1).

    The account's token comes only from `.env` (`HW_CHECKER_GITHUB_TOKEN`).
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    api_url: NonBlankText = "https://api.github.com"
    web_url: NonBlankText = "https://github.com"
    """Where branches are pushed: `<web_url>/<account>/<fork>.git`."""
    account: NonBlankText | None = None
    """The publishing account; publishing refuses to run without it."""
    fork_prefix: NonBlankText = "check_"
    """A student's fork is named `<fork_prefix><student's GitHub username>`."""
    timeout_seconds: float = Field(default=30, gt=0)
    fork_wait_seconds: float = Field(default=60, gt=0)
    """How long to wait for a new fork to be ready."""
    fork_poll_seconds: float = Field(default=2, gt=0)


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
    web_project_commits: int = Field(default=20, ge=0)
    """How many of the latest commits are read to choose between several Web projects
    (the one they changed last); 0 reads none."""
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


LlmRole = Literal["reviewer", "judge", "fixer", "writer", "author"]
"""The roles the LLM is called for (ADR 0008); code names a role, never a model."""
LLM_ROLES: tuple[LlmRole, ...] = get_args(LlmRole)
LlmProvider = Literal["openrouter", "copilot"]
"""Where the roles' models run: an OpenAI-compatible endpoint such as OpenRouter
(ADR 0020), or the developer's GitHub Copilot seat through the Copilot SDK (ADR 0022)."""
OPENROUTER_HOST = "openrouter.ai"


def _missing_roles(roles: dict[LlmRole, str]) -> list[LlmRole]:
    return [role for role in LLM_ROLES if role not in roles]


class CopilotSettings(BaseModel):
    """The Copilot seat's model for each role (ADR 0022)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    roles: dict[LlmRole, NonBlankText] = {
        "reviewer": "claude-haiku-5.5",
        "judge": "claude-sonnet-5.5",
        "fixer": "claude-sonnet-5.5",
        "writer": "claude-haiku-5.5",
        "author": "claude-sonnet-5.5",
    }
    """The Copilot model id for each role, as the seat's model list names it."""
    start_timeout_seconds: float = Field(default=60, gt=0)
    """The longest wait for the Copilot CLI to start and report its login."""


class LlmSettings(BaseModel):
    """The LLM provider and the model for each role (ADR 0008, ADR 0020, ADR 0022).

    `provider` picks the client: `openrouter` uses `base_url`, `roles`, `temperature`,
    `max_output_tokens`, `extra_body` and the API key; `copilot` uses `copilot`. The call
    limits (`timeout_seconds`, `max_retries`, `retry_backoff_seconds`, `max_prompt_chars`)
    apply to both. Secrets come only from `.env`, never from `config.yaml`: the API key
    `HW_CHECKER_LLM__API_KEY`, and the optional Copilot token
    `HW_CHECKER_LLM__COPILOT_TOKEN`.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    provider: LlmProvider = "openrouter"
    base_url: NonBlankText = "https://openrouter.ai/api/v1"
    roles: dict[LlmRole, NonBlankText] = {
        "reviewer": "anthropic/claude-haiku-5.5",
        "judge": "anthropic/claude-sonnet-5.5",
        "fixer": "anthropic/claude-sonnet-5.5",
        "writer": "anthropic/claude-haiku-5.5",
        "author": "anthropic/claude-sonnet-5.5",
    }
    """The model id for each role, as the endpoint names it."""
    temperature: float | None = Field(default=None, ge=0, le=1)
    """None leaves it out of the request: with OpenRouter's `require_parameters`, a
    parameter the model does not support makes the request unroutable (ADR 0020)."""
    max_output_tokens: int = Field(default=4096, ge=1)
    """The most tokens a reply may have; always sent, so a call never reserves the
    model's whole output limit (ADR 0020)."""
    timeout_seconds: float = Field(default=60, gt=0)
    """The longest wait for one reply, for a role without its own in `role_timeouts`."""
    role_timeouts: dict[LlmRole, Annotated[float, Field(gt=0)]] = {}
    """A role's own longest wait for one reply (the fixer's long plans, stage 14c)."""
    role_max_prompt_chars: dict[LlmRole, Annotated[int, Field(ge=1)]] = {}
    """A role's own prompt limit, instead of `max_prompt_chars` (the author's long input,
    stage 16)."""
    role_max_output_tokens: dict[LlmRole, Annotated[int, Field(ge=1)]] = {}
    """A role's own reply limit, instead of `max_output_tokens` (OpenRouter only: the
    Copilot runtime has no such setting)."""
    max_retries: int = Field(default=2, ge=0, le=10)
    """Retries after a rate limit, a timeout or a server error; never after other errors."""
    retry_backoff_seconds: float = Field(default=2, ge=0, le=60)
    """The wait before the first retry; it doubles for each retry after that."""
    max_prompt_chars: int = Field(default=60_000, ge=1)
    """A longer prompt is refused before it is sent."""
    extra_body: dict[str, Any] = {
        "provider": {"data_collection": "deny", "require_parameters": True}
    }
    """Extra JSON fields sent with every request. On OpenRouter, `provider.data_collection`
    must be `deny`, so providers that train on the data are never used (ADR 0020)."""
    api_key: SecretStr | None = None
    copilot: CopilotSettings = CopilotSettings()
    copilot_token: SecretStr | None = None
    """A GitHub token for the Copilot seat; without it the Copilot CLI's logged-in user
    is used (ADR 0022)."""
    student_code_approved: bool = False
    """Whether the lecturer approved sending students' code to the LLM; the developer sets it."""

    def role_timeout(self, role: LlmRole) -> float:
        """The longest wait for one reply of `role`."""
        return self.role_timeouts.get(role, self.timeout_seconds)

    def role_prompt_limit(self, role: LlmRole) -> int:
        """The longest prompt of `role`, in characters."""
        return self.role_max_prompt_chars.get(role, self.max_prompt_chars)

    def role_output_tokens(self, role: LlmRole) -> int:
        """The most tokens one reply of `role` may have (OpenRouter only)."""
        return self.role_max_output_tokens.get(role, self.max_output_tokens)

    def role_models(self) -> dict[LlmRole, str]:
        """The model of each role for the configured provider."""
        roles = self.copilot.roles if self.provider == "copilot" else self.roles
        return {role: roles[role] for role in LLM_ROLES}

    @model_validator(mode="after")
    def _every_role_has_a_model(self) -> Self:
        roles = self.copilot.roles if self.provider == "copilot" else self.roles
        missing = _missing_roles(roles)
        if missing:
            section = "llm.copilot.roles" if self.provider == "copilot" else "llm.roles"
            raise ValueError(f"no model for the roles: {', '.join(missing)} (in {section})")
        return self

    @model_validator(mode="after")
    def _openrouter_denies_data_collection(self) -> Self:
        if self.provider != "openrouter" or urlsplit(self.base_url).hostname != OPENROUTER_HOST:
            return self
        provider = self.extra_body.get("provider")
        if not isinstance(provider, dict) or provider.get("data_collection") != "deny":
            raise ValueError(
                "on OpenRouter, extra_body.provider.data_collection must be 'deny' (ADR 0020)"
            )
        return self


class FixerSettings(BaseModel):
    """The fixer (stage 14, ADR 0021): proposed review branches, verified before the gate."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    enabled: bool = False
    """Sends each repo's code to the LLM; needs `llm.student_code_approved`."""
    self_review: bool = True
    """Let the `judge` role remove applied edits that break the review rules."""
    max_attempts: int = Field(default=2, ge=1, le=5)
    """Plans per repo before failing fixes fall back to their notes."""
    reply_attempts: int = Field(default=2, ge=1, le=5)
    """Calls per LLM step when a reply cannot be parsed (RetryPolicy)."""
    max_edits: int = Field(default=12, ge=1)
    max_fix_lines: int = Field(default=6, ge=1)
    """A `fix` changes at most this many code lines."""
    max_file_chars: int = Field(default=12_000, ge=100)
    """A longer source file is cut to this many characters in the prompt."""
    source_suffixes: tuple[NonBlankText, ...] = (".cs", ".csproj")
    config_globs: tuple[NonBlankText, ...] = ("appsettings*.json",)
    """Configuration files shown to the model, never edited."""
    data_suffixes: tuple[NonBlankText, ...] = (".txt", ".json")
    """A file with one of these suffixes that is not configuration or ignored is a data file."""
    ignored_globs: tuple[NonBlankText, ...] = (
        "launchSettings.json",
        "*.deps.json",
        "*.runtimeconfig.json",
        "global.json",
    )
    skip_dirs: tuple[NonBlankText, ...] = ("bin", "obj", ".git", ".vs", ".vscode", "node_modules")


class AuthoringSettings(BaseModel):
    """Adding an assignment (stage 16, design §8.3)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    max_attempts: int = Field(default=3, ge=1, le=10)
    """Drafts per run while validation fails; then the run stops with the report."""
    reply_attempts: int = Field(default=2, ge=1, le=5)
    """Calls per draft when the author's reply cannot be read (RetryPolicy)."""
    preview_flag_share: float = Field(default=0.9, gt=0, le=1)
    """A drafted requirement that fails at least this share of the repos it could evaluate
    in the preview is flagged in the report."""


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
    course_dir: Path = Path("course")
    """The course profile and the course's review rules (ADR 0023)."""
    max_parallel_repos: int = Field(default=3, ge=1)
    logging: LoggingSettings = LoggingSettings()
    roster: RosterSettings = RosterSettings()
    git: GitSettings = GitSettings()
    runner: RunnerSettings = RunnerSettings()
    storage: StorageSettings = StorageSettings()
    llm: LlmSettings = LlmSettings()
    github: GitHubSettings = GitHubSettings()
    github_token: SecretStr | None = None
    fixer: FixerSettings = FixerSettings()
    """The publishing account's token: a classic PAT with the `public_repo` scope only."""
    authoring: AuthoringSettings = AuthoringSettings()

    @model_validator(mode="after")
    def _fixer_needs_the_lecturers_approval(self) -> Self:
        if self.fixer.enabled and not self.llm.student_code_approved:
            raise ValueError(
                "fixer.enabled sends students' code to the LLM, which needs the lecturer's "
                "approval: the developer sets llm.student_code_approved: true once the lecturer "
                "has approved it"
            )
        return self

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
