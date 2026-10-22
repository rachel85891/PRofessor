from pathlib import Path

import pytest
from pydantic import SecretStr

from hw_checker.config.errors import ConfigurationError
from hw_checker.config.settings import Settings, load_settings


@pytest.fixture(autouse=True)
def _isolate_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in (
        "HW_CHECKER_WORKSPACE_DIR",
        "HW_CHECKER_MAX_PARALLEL_REPOS",
        "HW_CHECKER_LOGGING__LEVEL",
        "HW_CHECKER_LOGGING__LOG_DIR",
        "HW_CHECKER_GITHUB_TOKEN",
        "HW_CHECKER_GIT__COMMIT_AUTHOR_NAME",
        "HW_CHECKER_GIT__COMMIT_AUTHOR_EMAIL",
    ):
        monkeypatch.delenv(name, raising=False)


def _write(path: Path, content: str) -> Path:
    path.write_text(content, encoding="utf-8")
    return path


def _load(tmp_path: Path, yaml_text: str, env_text: str = "") -> Settings:
    config_path = _write(tmp_path / "config.yaml", yaml_text)
    env_path = _write(tmp_path / ".env", env_text)
    return load_settings(config_path=config_path, env_path=env_path)


def test_loads_values_from_yaml_and_env_file(tmp_path: Path) -> None:
    settings = _load(
        tmp_path,
        "workspace_dir: runs\nmax_parallel_repos: 4\nlogging:\n  level: DEBUG\n  log_dir: out\n",
        "HW_CHECKER_GITHUB_TOKEN=ghp_example\n",
    )

    assert settings.workspace_dir == Path("runs")
    assert settings.max_parallel_repos == 4
    assert settings.logging.level == "DEBUG"
    assert settings.logging.log_dir == Path("out")
    assert settings.github_token is not None
    assert settings.github_token.get_secret_value() == "ghp_example"


def test_defaults_apply_when_values_are_absent(tmp_path: Path) -> None:
    settings = _load(tmp_path, "")

    assert settings.workspace_dir == Path("workspace")
    assert settings.max_parallel_repos == 3
    assert settings.logging.level == "INFO"
    assert settings.logging.log_dir == Path("logs")
    assert settings.github_token is None


def test_missing_env_file_is_allowed(tmp_path: Path) -> None:
    config_path = _write(tmp_path / "config.yaml", "max_parallel_repos: 2\n")

    settings = load_settings(config_path=config_path, env_path=tmp_path / "missing.env")

    assert settings.max_parallel_repos == 2


def test_environment_variable_overrides_yaml(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HW_CHECKER_MAX_PARALLEL_REPOS", "5")
    monkeypatch.setenv("HW_CHECKER_LOGGING__LEVEL", "WARNING")

    settings = _load(tmp_path, "max_parallel_repos: 2\nlogging:\n  level: DEBUG\n  log_dir: out\n")

    assert settings.max_parallel_repos == 5
    assert settings.logging.level == "WARNING"
    assert settings.logging.log_dir == Path("out")


def test_environment_variable_overrides_env_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HW_CHECKER_GITHUB_TOKEN", "from_environment")

    settings = _load(tmp_path, "", "HW_CHECKER_GITHUB_TOKEN=from_file\n")

    assert settings.github_token is not None
    assert settings.github_token.get_secret_value() == "from_environment"


def test_secret_is_secret_str_and_hidden_from_repr(tmp_path: Path) -> None:
    settings = _load(tmp_path, "", "HW_CHECKER_GITHUB_TOKEN=ghp_do_not_leak\n")

    assert isinstance(settings.github_token, SecretStr)
    assert "ghp_do_not_leak" not in repr(settings)
    assert "ghp_do_not_leak" not in str(settings)


def test_empty_secret_in_env_file_is_treated_as_absent(tmp_path: Path) -> None:
    settings = _load(tmp_path, "", "HW_CHECKER_GITHUB_TOKEN=\n")

    assert settings.github_token is None


def test_zero_parallel_repos_is_rejected_naming_the_field(tmp_path: Path) -> None:
    with pytest.raises(ConfigurationError, match="max_parallel_repos"):
        _load(tmp_path, "max_parallel_repos: 0\n")


def test_wrong_type_is_rejected_naming_the_field(tmp_path: Path) -> None:
    with pytest.raises(ConfigurationError, match="max_parallel_repos"):
        _load(tmp_path, "max_parallel_repos: many\n")


def test_invalid_nested_value_is_rejected_naming_the_field(tmp_path: Path) -> None:
    with pytest.raises(ConfigurationError, match=r"logging\.level"):
        _load(tmp_path, "logging:\n  level: LOUD\n")


def test_unknown_key_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(ConfigurationError, match="max_paralel_repos"):
        _load(tmp_path, "max_paralel_repos: 2\n")


def test_missing_config_file_raises_naming_the_file(tmp_path: Path) -> None:
    missing = tmp_path / "absent.yaml"

    with pytest.raises(ConfigurationError, match=r"absent\.yaml"):
        load_settings(config_path=missing, env_path=tmp_path / ".env")


def test_malformed_yaml_raises_naming_the_file(tmp_path: Path) -> None:
    config_path = _write(tmp_path / "broken.yaml", "max_parallel_repos: [1, 2\n")

    with pytest.raises(ConfigurationError, match=r"broken\.yaml"):
        load_settings(config_path=config_path, env_path=tmp_path / ".env")


def test_non_mapping_yaml_raises_naming_the_file(tmp_path: Path) -> None:
    config_path = _write(tmp_path / "list.yaml", "- a\n- b\n")

    with pytest.raises(ConfigurationError, match=r"list\.yaml"):
        load_settings(config_path=config_path, env_path=tmp_path / ".env")


def test_roster_defaults_apply(tmp_path: Path) -> None:
    roster = _load(tmp_path, "").roster

    assert roster.sheet_name is None
    assert roster.header_row == 1
    assert (roster.columns.name, roster.columns.partner_name, roster.columns.github_url) == (
        "Name",
        "Partner",
        "GitHub",
    )


def test_repo_config_has_the_real_roster_headers(tmp_path: Path) -> None:
    from hw_checker.config.settings import DEFAULT_CONFIG_PATH

    roster = load_settings(config_path=DEFAULT_CONFIG_PATH, env_path=tmp_path / ".env").roster

    assert (roster.sheet_name, roster.header_row) == ("Sheet1", 1)
    assert (roster.columns.name, roster.columns.partner_name, roster.columns.github_url) == (
        "\u05e9\u05dd + \u05de\u05e9\u05e4\u05d7\u05d4",
        "\u05d1\u05ea \u05d4\u05d6\u05d5\u05d2 \u05e9\u05dc",
        "Github Url",
    )


def test_roster_values_load_from_yaml(tmp_path: Path) -> None:
    roster = _load(
        tmp_path,
        "roster:\n  sheet_name: Students\n  header_row: 3\n"
        "  columns:\n    name: Full name\n    partner_name: Partner name\n    github_url: Repo\n",
    ).roster

    assert roster.sheet_name == "Students"
    assert roster.header_row == 3
    assert roster.columns.github_url == "Repo"
    assert roster.columns.partner_name == "Partner name"


def test_roster_header_row_zero_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(ConfigurationError, match=r"roster\.header_row"):
        _load(tmp_path, "roster:\n  header_row: 0\n")


def test_roster_blank_header_text_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(ConfigurationError, match=r"roster\.columns\.name"):
        _load(tmp_path, "roster:\n  columns:\n    name: '  '\n")


def test_git_defaults_apply(tmp_path: Path) -> None:
    git = _load(tmp_path, "").git

    assert git.executable == "git"
    assert git.timeout_seconds == 120
    assert git.commit_author_name is None
    assert git.commit_author_email is None


def test_git_values_load_from_yaml(tmp_path: Path) -> None:
    git = _load(tmp_path, "git:\n  executable: C:/Git/bin/git.exe\n  timeout_seconds: 30\n").git

    assert (git.executable, git.timeout_seconds) == ("C:/Git/bin/git.exe", 30)


def test_git_timeout_below_one_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(ConfigurationError, match=r"git\.timeout_seconds"):
        _load(tmp_path, "git:\n  timeout_seconds: 0\n")


def test_git_blank_author_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(ConfigurationError, match=r"git\.commit_author_name"):
        _load(tmp_path, "git:\n  commit_author_name: ' '\n")


def test_git_ssl_backend_defaults_to_none(tmp_path: Path) -> None:
    assert _load(tmp_path, "").git.ssl_backend is None


def test_git_ssl_backend_loads_from_yaml(tmp_path: Path) -> None:
    assert _load(tmp_path, "git:\n  ssl_backend: schannel\n").git.ssl_backend == "schannel"


def test_git_unknown_ssl_backend_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(ConfigurationError, match=r"git\.ssl_backend"):
        _load(tmp_path, "git:\n  ssl_backend: gnutls\n")


def test_git_commit_author_loads_from_env_file(tmp_path: Path) -> None:
    git = _load(
        tmp_path,
        "",
        "HW_CHECKER_GIT__COMMIT_AUTHOR_NAME=Test Reviewer\n"
        "HW_CHECKER_GIT__COMMIT_AUTHOR_EMAIL=1+test@users.noreply.github.com\n",
    ).git

    assert git.commit_author_name == "Test Reviewer"
    assert git.commit_author_email == "1+test@users.noreply.github.com"


def test_git_empty_commit_author_in_env_file_counts_as_unset(tmp_path: Path) -> None:
    git = _load(
        tmp_path,
        "",
        "HW_CHECKER_GIT__COMMIT_AUTHOR_NAME=\nHW_CHECKER_GIT__COMMIT_AUTHOR_EMAIL=\n",
    ).git

    assert (git.commit_author_name, git.commit_author_email) == (None, None)


def test_assignments_dir_default_and_override(tmp_path: Path) -> None:
    assert _load(tmp_path, "").assignments_dir == Path("assignments")
    assert _load(tmp_path, "assignments_dir: data/hw\n").assignments_dir == Path("data/hw")


def test_runner_defaults_load_from_the_repo_config(tmp_path: Path) -> None:
    from hw_checker.config.settings import DEFAULT_CONFIG_PATH

    runner = load_settings(config_path=DEFAULT_CONFIG_PATH, env_path=tmp_path / ".env").runner

    assert runner.sdk_image.startswith("mcr.microsoft.com/dotnet/sdk:10.0@sha256:")
    assert runner.runtime_image.startswith("mcr.microsoft.com/dotnet/aspnet:10.0@sha256:")
    assert "@sha256:" in runner.probe_image
    assert runner.run_limits.memory_mb == 512
    assert runner.readiness_timeout_seconds == 30
    assert runner.probe_timeout_seconds == 10
    assert runner.app_port == 8080
    assert runner.logs_root == Path("logs")


def test_runner_defaults_apply_without_config(tmp_path: Path) -> None:
    runner = _load(tmp_path, "").runner

    assert runner.build_limits.cpus == 2
    assert runner.probe_timeout_seconds == 10
    assert runner.label_prefix == "hw-checker"


@pytest.mark.parametrize(
    "image",
    [
        "mcr.microsoft.com/dotnet/sdk:9.0",
        "mcr.microsoft.com/dotnet/sdk:latest",
        "mcr.microsoft.com/dotnet/sdk@sha256:abc",
        "mcr.microsoft.com/dotnet/sdk:9.0@sha256:" + "Z" * 64,
    ],
)
def test_unpinned_image_is_rejected_naming_the_field(tmp_path: Path, image: str) -> None:
    with pytest.raises(ConfigurationError, match=r"runner\.sdk_image"):
        _load(tmp_path, f"runner:\n  sdk_image: '{image}'\n")


def test_image_pinned_without_tag_is_accepted(tmp_path: Path) -> None:
    image = "mcr.microsoft.com/dotnet/aspnet@sha256:" + "a" * 64

    assert _load(tmp_path, f"runner:\n  runtime_image: '{image}'\n").runner.runtime_image == image


LIMIT_CASES = [
    ("run_limits: {memory_mb: 0, cpus: 1, pids: 10}", r"runner\.run_limits\.memory_mb"),
    ("build_limits: {memory_mb: 512, cpus: 0, pids: 10}", r"runner\.build_limits\.cpus"),
    ("probe_limits: {memory_mb: 64, cpus: 1, pids: 0}", r"runner\.probe_limits\.pids"),
    ("readiness_timeout_seconds: 0", r"runner\.readiness_timeout_seconds"),
    ("restore_timeout_seconds: -5", r"runner\.restore_timeout_seconds"),
    ("readiness_poll_seconds: 0", r"runner\.readiness_poll_seconds"),
    ("app_port: 70000", r"runner\.app_port"),
]


@pytest.mark.parametrize(("runner_line", "field"), LIMIT_CASES)
def test_limits_and_timeouts_must_be_positive(tmp_path: Path, runner_line: str, field: str) -> None:
    with pytest.raises(ConfigurationError, match=field):
        _load(tmp_path, f"runner:\n  {runner_line}\n")


@pytest.mark.parametrize("logs_root", ["../logs", "/var/logs", "C:/logs", "logs/../../x"])
def test_runner_logs_root_must_stay_inside_workspace(tmp_path: Path, logs_root: str) -> None:
    with pytest.raises(ConfigurationError, match=r"runner\.logs_root"):
        _load(tmp_path, f"runner:\n  logs_root: '{logs_root}'\n")


def test_runner_archive_limits_default_and_must_be_positive(tmp_path: Path) -> None:
    runner = _load(tmp_path, "").runner
    assert (runner.max_archive_mb, runner.max_file_mb) == (200, 50)

    with pytest.raises(ConfigurationError, match=r"runner\.max_file_mb"):
        _load(tmp_path, "runner:\n  max_file_mb: 0\n")


def test_storage_database_url_default_and_override(tmp_path: Path) -> None:
    assert _load(tmp_path, "").storage.database_url == "sqlite:///workspace/hw_checker.db"
    url = "postgresql://hw@localhost/hw_checker"
    assert _load(tmp_path, f"storage:\n  database_url: {url}\n").storage.database_url == url


def test_storage_blank_database_url_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(ConfigurationError, match=r"storage\.database_url"):
        _load(tmp_path, "storage:\n  database_url: ' '\n")
