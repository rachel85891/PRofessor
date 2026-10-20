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
