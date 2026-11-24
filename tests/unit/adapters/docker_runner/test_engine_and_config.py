import dataclasses
from typing import Any

import pytest

from hw_checker.adapters.docker_runner.engine import EngineError, SdkDockerEngine, digest_reference
from hw_checker.adapters.docker_runner.runner_config import (
    ContainerLimits,
    DockerRunnerConfig,
    FrameworkImages,
    SqlServerConfig,
)

DIGEST = "sha256:" + "a" * 64


def config(**overrides: Any) -> DockerRunnerConfig:
    limits = ContainerLimits(memory_mb=512, cpus=1, pids=64)
    values: dict[str, Any] = {
        "framework_images": {
            version: FrameworkImages(
                sdk=f"mcr.microsoft.com/dotnet/sdk:{version[3:]}@{DIGEST}",
                runtime=f"mcr.microsoft.com/dotnet/aspnet:{version[3:]}@{DIGEST}",
            )
            for version in ("net8.0", "net9.0", "net10.0")
        },
        "course_framework": "net10.0",
        "probe_image": f"curlimages/curl:8.10.1@{DIGEST}",
        "restore_limits": limits,
        "build_limits": limits,
        "run_limits": limits,
        "probe_limits": limits,
        "restore_timeout_seconds": 10,
        "build_timeout_seconds": 10,
        "readiness_timeout_seconds": 5,
        "probe_timeout_seconds": 2,
        "resolve_grace_seconds": 2,
        "readiness_poll_seconds": 0.1,
        "app_port": 8080,
        "app_uid": 1654,
        "app_gid": 1654,
        "nuget_cache_volume": "hw-checker-test-nuget",
        "label_prefix": "hw-checker-test",
        "max_archive_bytes": 1000,
        "max_file_bytes": 100,
    }
    return DockerRunnerConfig(**(values | overrides))


@pytest.mark.parametrize(
    ("image", "expected"),
    [
        (f"mcr.microsoft.com/dotnet/sdk:9.0@{DIGEST}", f"mcr.microsoft.com/dotnet/sdk@{DIGEST}"),
        (f"curlimages/curl@{DIGEST}", f"curlimages/curl@{DIGEST}"),
        (f"localhost:5000/team/app:1.2@{DIGEST}", f"localhost:5000/team/app@{DIGEST}"),
        ("busybox:1.37", "busybox:1.37"),
    ],
)
def test_digest_reference_drops_the_tag(image: str, expected: str) -> None:
    assert digest_reference(image) == expected


def test_unreachable_engine_raises_engine_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DOCKER_HOST", "npipe:////./pipe/hw_checker_no_such_engine")

    with pytest.raises(EngineError):
        SdkDockerEngine.from_environment(timeout_seconds=2).ping()


def test_config_exposes_user_and_images() -> None:
    runner_config = config()

    assert runner_config.user == "1654:1654"
    assert runner_config.images == (
        f"mcr.microsoft.com/dotnet/sdk:8.0@{DIGEST}",
        f"mcr.microsoft.com/dotnet/aspnet:8.0@{DIGEST}",
        f"mcr.microsoft.com/dotnet/sdk:9.0@{DIGEST}",
        f"mcr.microsoft.com/dotnet/aspnet:9.0@{DIGEST}",
        f"mcr.microsoft.com/dotnet/sdk:10.0@{DIGEST}",
        f"mcr.microsoft.com/dotnet/aspnet:10.0@{DIGEST}",
        f"curlimages/curl:8.10.1@{DIGEST}",
    )
    assert runner_config.helper_image == f"mcr.microsoft.com/dotnet/aspnet:10.0@{DIGEST}"


def test_config_needs_images_for_the_course_framework() -> None:
    with pytest.raises(ValueError, match=r"course framework net11\.0"):
        config(course_framework="net11.0")


@pytest.mark.parametrize(
    "field",
    ["restore_timeout_seconds", "readiness_timeout_seconds", "readiness_poll_seconds"],
)
def test_config_rejects_non_positive_durations(field: str) -> None:
    with pytest.raises(ValueError, match="must be positive"):
        config(**{field: 0})


def test_config_rejects_non_positive_archive_limits() -> None:
    with pytest.raises(ValueError, match="size limits must be positive"):
        config(max_file_bytes=0)


@pytest.mark.parametrize("field", ["memory_mb", "cpus", "pids"])
def test_limits_must_be_positive(field: str) -> None:
    values = dataclasses.asdict(ContainerLimits(memory_mb=1, cpus=1, pids=1)) | {field: 0}

    with pytest.raises(ValueError, match="must be positive"):
        ContainerLimits(**values)


def sql_server_config(**overrides: Any) -> SqlServerConfig:
    values: dict[str, Any] = {
        "image": f"mcr.microsoft.com/mssql/server:2022-latest@{DIGEST}",
        "edition": "Developer",
        "accept_eula": "Y",
        "memory_mb": 2048,
        "mssql_memory_limit_mb": 1536,
        "cpus": 2,
        "pids": 512,
        "startup_timeout_seconds": 10,
        "readiness_poll_seconds": 1,
        "alias": "sqlserver",
        "user": "sa",
        "container_user": "mssql",
        "sqlcmd_path": "/opt/mssql-tools18/bin/sqlcmd",
        "max_database_name_length": 128,
        "added_capabilities": ("NET_BIND_SERVICE",),
    }
    return SqlServerConfig(**(values | overrides))


def test_config_lists_the_sql_server_image_only_when_databases_are_on() -> None:
    assert config().images[-1] == f"curlimages/curl:8.10.1@{DIGEST}"
    assert config(database=sql_server_config()).images[-1].startswith("mcr.microsoft.com/mssql")


def test_sql_server_memory_cap_must_be_below_the_container_limit() -> None:
    with pytest.raises(ValueError, match="below the container limit"):
        sql_server_config(mssql_memory_limit_mb=2048)


def test_sql_server_durations_must_be_positive() -> None:
    with pytest.raises(ValueError, match="must be positive"):
        sql_server_config(startup_timeout_seconds=0)
