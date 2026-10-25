import io
import logging
import tarfile
from pathlib import Path
from urllib.parse import urlsplit

import pytest

from hw_checker.adapters.docker_runner.engine import EngineError, Mount
from hw_checker.adapters.docker_runner.runner import DockerCodeRunner
from hw_checker.adapters.docker_runner.runner_config import DockerRunnerConfig
from hw_checker.domain import (
    ExecutionRequest,
    FailureKind,
    HttpMethod,
    HttpProbe,
    RouteResolution,
    StepStatus,
)
from hw_checker.domain.errors import ExecutionEnvironmentError
from tests.unit.adapters.docker_runner.fake_engine import FakeClock, FakeEngine
from tests.unit.adapters.docker_runner.test_engine_and_config import DIGEST
from tests.unit.adapters.docker_runner.test_engine_and_config import config as base_config

WEB_PROJECT = (
    '<Project Sdk="Microsoft.NET.Sdk.Web"><PropertyGroup>'
    "<TargetFramework>net10.0</TargetFramework></PropertyGroup></Project>"
)
BUILD_ERROR = "Program.cs(3,1): error CS0103: The name 'x' does not exist [/work/src/Api.csproj]"


def probe(method: HttpMethod, path: str, status: int, body: str | None = None) -> HttpProbe:
    return HttpProbe(
        requirement_id="hw01-r1",
        assignment_id="hw01",
        method=method,
        path=path,
        body=body,
        expected_status=status,
    )


PROBES = (
    probe(HttpMethod.GET, "/api/products", 200),
    probe(HttpMethod.GET, "/api/products/999", 404),
    probe(HttpMethod.POST, "/api/products", 201, '{"name":"Desk","price":250}'),
)
STATUSES = {
    ("GET", "/api/products"): 200,
    ("GET", "/api/products/999"): 404,
    ("POST", "/api/products"): 201,
}


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    (root / "Controllers").mkdir(parents=True)
    (root / "Shop.Api.csproj").write_text(WEB_PROJECT, encoding="utf-8")
    (root / "Controllers" / "ProductsController.cs").write_text("class C {}", encoding="utf-8")
    return root


def config() -> DockerRunnerConfig:
    return base_config(max_archive_bytes=10_000_000, max_file_bytes=1_000_000)


def request(repo: Path, tmp_path: Path, **overrides: object) -> ExecutionRequest:
    values: dict[str, object] = {
        "run_id": "run-1",
        "repo_id": "dana__api",
        "working_copy": repo,
        "probes": PROBES,
        "environment": (("FEATURE_FLAG", "on"),),
        "logs_dir": tmp_path / "logs",
    }
    return ExecutionRequest.model_validate(values | overrides)


def runner(engine: FakeEngine) -> DockerCodeRunner:
    clock = FakeClock()
    return DockerCodeRunner(config(), engine, clock=clock, sleep=clock.sleep)


def test_success_runs_every_step_and_probe(repo: Path, tmp_path: Path) -> None:
    engine = FakeEngine(probe_statuses=STATUSES)

    result = runner(engine).execute(request(repo, tmp_path))

    assert result.steps == (StepStatus.SUCCEEDED,) * 3
    assert result.failure_kind is None
    assert [(r.actual_status, r.passed) for r in result.probe_results] == [
        (200, True),
        (404, True),
        (201, True),
    ]
    assert result.logs_path == tmp_path / "logs"


def test_step_order_and_cleanup_after_success(repo: Path, tmp_path: Path) -> None:
    engine = FakeEngine()

    runner(engine).execute(request(repo, tmp_path))

    operations = [
        call if call.startswith(("create_container", "start", "put")) else call.split()[0]
        for call in engine.calls
        if call.startswith(("create", "start", "put"))
    ]
    assert operations == [
        "create_volume",
        "create_container copy",
        "put_archive /work",
        "create_volume",
        "create_container copy",
        "put_archive /data",
        "create_container restore",
        "start restore",
        "create_container build",
        "start build",
        "create_network",
        "create_container app",
        "create_container probe",
        "start probe",
        "start app",
    ]
    assert engine.leftovers == 0


def test_container_settings(repo: Path, tmp_path: Path) -> None:
    engine = FakeEngine()
    runner_config = config()

    runner(engine).execute(request(repo, tmp_path))

    (restore,) = engine.specs("restore")
    (build,) = engine.specs("build")
    (app,) = engine.specs("app")
    (sidecar,) = engine.specs("probe")
    work = restore.mounts[0].volume
    assert restore.network_mode == "bridge"
    assert restore.mounts == (
        Mount(work, "/work", read_only=False),
        Mount(runner_config.nuget_cache_volume, "/nuget", read_only=False),
    )
    assert restore.command == ("dotnet", "restore", "/work/src/Shop.Api.csproj")
    assert build.network_mode == "none"
    assert build.mounts[1] == Mount(runner_config.nuget_cache_volume, "/nuget", read_only=True)
    assert build.command[:4] == ("dotnet", "publish", "/work/src/Shop.Api.csproj", "--no-restore")
    assert app.network is not None and app.network == sidecar.network
    assert engine.networks == {} and app.network in engine.removed
    project = app.mounts[1].volume
    assert app.mounts == (
        Mount(work, "/work", read_only=True),
        Mount(project, "/data", read_only=False),
    )
    assert project != work and project in engine.removed
    assert app.working_dir == "/data/project"
    assert restore.working_dir == build.working_dir == "/work"
    assert app.read_only_root and app.tmpfs == {"/tmp": "size=64m"}
    assert app.command == ("dotnet", "/work/out/Shop.Api.dll")
    assert app.environment == {"FEATURE_FLAG": "on", "ASPNETCORE_HTTP_PORTS": "8080"}
    for spec, limits in (
        (restore, runner_config.restore_limits),
        (build, runner_config.build_limits),
        (app, runner_config.run_limits),
        (sidecar, runner_config.probe_limits),
    ):
        assert spec.user == "1654:1654"
        assert (spec.memory_mb, spec.cpus, spec.pids) == (
            limits.memory_mb,
            limits.cpus,
            limits.pids,
        )
        assert spec.labels["hw-checker-test"] == "1"
        assert spec.labels["hw-checker-test.run"] == "run-1"
        assert spec.labels["hw-checker-test.repo"] == "dana__api"


def test_project_copy_holds_only_the_web_project_folder(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    (repo / "WebApi" / "WebApi" / "Data").mkdir(parents=True)
    (repo / "WebApi" / "WebApi.slnx").write_text("<Solution />", encoding="utf-8")
    (repo / "WebApi" / "WebApi" / "WebApi.csproj").write_text(WEB_PROJECT, encoding="utf-8")
    (repo / "WebApi" / "WebApi" / "users.txt").write_text("", encoding="utf-8")
    (repo / "WebApi" / "WebApi" / "Data" / "seed.txt").write_text("1", encoding="utf-8")
    copied: dict[str, set[str]] = {}
    engine = FakeEngine()
    original = engine.put_archive

    def record(container: str, path: str, data: bytes) -> None:
        with tarfile.open(fileobj=io.BytesIO(data)) as archive:
            copied[path] = set(archive.getnames())
        original(container, path, data)

    engine.put_archive = record  # type: ignore[method-assign]

    result = runner(engine).execute(request(repo, tmp_path))

    assert result.failure_kind is None
    assert copied["/data"] == {
        "project",
        "project/WebApi.csproj",
        "project/users.txt",
        "project/Data",
        "project/Data/seed.txt",
    }
    assert "src/WebApi/WebApi.slnx" in copied["/work"]
    (build,) = engine.specs("build")
    assert build.command[2] == "/work/src/WebApi/WebApi/WebApi.csproj"


def test_absolute_path_is_rewritten_in_the_copied_code_only(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    (repo / "Api" / "Data").mkdir(parents=True)
    (repo / "Api" / "Api.csproj").write_text(WEB_PROJECT, encoding="utf-8")
    store = repo / "Api" / "Store.cs"
    store.write_bytes(b'class Store { const string P = @"C:\\Shop\\Data\\users.txt"; }')
    (repo / "Api" / "Data" / "users.txt").write_bytes(b"")
    before = store.read_bytes()
    contents: dict[str, dict[str, bytes]] = {}
    engine = FakeEngine()
    original = engine.put_archive

    def record(container: str, path: str, data: bytes) -> None:
        with tarfile.open(fileobj=io.BytesIO(data)) as archive:
            contents[path] = {
                member.name: archive.extractfile(member).read()  # type: ignore[union-attr]
                for member in archive.getmembers()
                if member.isfile()
            }
        original(container, path, data)

    engine.put_archive = record  # type: ignore[method-assign]

    result = runner(engine).execute(request(repo, tmp_path))

    expected = b'class Store { const string P = @"Data/users.txt"; }'
    assert contents["/work"]["src/Api/Store.cs"] == expected
    assert contents["/data"]["project/Store.cs"] == expected
    assert store.read_bytes() == before
    (rewrite,) = result.path_rewrites
    assert (rewrite.file_path, rewrite.line, rewrite.rewritten) == (
        "Api/Store.cs",
        1,
        "Data/users.txt",
    )
    prepare_log = (tmp_path / "logs" / "prepare.log").read_text(encoding="utf-8")
    assert "rewritten to 'Data/users.txt'" in prepare_log


def test_route_table_is_recorded_on_the_result_and_in_the_prepare_log(
    repo: Path, tmp_path: Path
) -> None:
    (repo / "Controllers" / "ProductsController.cs").write_text(
        '[Route("api/[controller]")]\npublic class ProductsController\n{\n'
        '    [HttpGet("{id}")]\n    public IActionResult Get(int id) => Ok();\n}\n',
        encoding="utf-8",
    )
    engine = FakeEngine(exit_codes={"build": 1}, logs_by_kind={"build": BUILD_ERROR})

    result = runner(engine).execute(request(repo, tmp_path))

    assert result.routes is not None
    assert [(r.method, r.template) for r in result.routes.routes] == [
        (HttpMethod.GET, "api/Products/{id}")
    ]
    prepare_log = (tmp_path / "logs" / "prepare.log").read_text(encoding="utf-8")
    assert "route GET /api/Products/{id} (ProductsController.Get" in prepare_log


USER_CONTROLLER = (
    '[Route("api/[controller]")]\npublic class UserController : ControllerBase\n{\n'
    "    [HttpGet]\n    public IActionResult Get() => Ok();\n"
    '    [HttpPost ("signin")]\n    public IActionResult SignIn() => Ok();\n}\n'
)


def test_probes_are_sent_to_the_resolved_path_or_not_at_all(repo: Path, tmp_path: Path) -> None:
    (repo / "Controllers" / "ProductsController.cs").write_text(USER_CONTROLLER, encoding="utf-8")
    probes = (
        HttpProbe(
            requirement_id="hw01-r1",
            assignment_id="hw01",
            method=HttpMethod.GET,
            path="/api/users",
            path_alternatives=("/api/user",),
            expected_status=200,
        ),
        HttpProbe(
            requirement_id="hw01-r2",
            assignment_id="hw01",
            method=HttpMethod.POST,
            path="/api/users/login",
            path_alternatives=("/api/user/login", "/api/users/signin", "/api/user/signin"),
            body="{}",
            expected_status=200,
        ),
        HttpProbe(
            requirement_id="hw01-r3",
            assignment_id="hw01",
            method=HttpMethod.DELETE,
            path="/api/users/1",
            path_alternatives=("/api/user/1",),
            expected_status=204,
        ),
    )
    engine = FakeEngine()

    result = runner(engine).execute(request(repo, tmp_path, probes=probes))

    urls = [command[command.index("--") + 1] for command in engine.exec_commands]
    sent = [urlsplit(url).path for url in urls if urlsplit(url).path != "/"]
    assert sent == ["/api/user", "/api/user/signin"], "the DELETE was never sent"
    assert [(r.sent_path, r.route_resolution, r.actual_status) for r in result.probe_results] == [
        ("/api/user", RouteResolution.RESOLVED, 200),
        ("/api/user/signin", RouteResolution.RESOLVED, 200),
        (None, RouteResolution.ROUTE_NOT_FOUND, None),
    ]
    assert [r.controller for r in result.probe_results] == [
        "UserController",
        "UserController",
        None,
    ]
    assert (result.primary_controller, result.serving_controllers) == (
        "UserController",
        ("UserController",),
    )
    assert not result.probe_results[2].passed


TWO_CONTROLLERS = (
    '[Route("api/[controller]")]\npublic class UsersController : ControllerBase\n{\n'
    "    [HttpPost]\n    public IActionResult Post() => Ok();\n"
    '    [HttpPut("{id}")]\n    public IActionResult Put(int id) => Ok();\n}\n'
    '[Route("api/[controller]")]\npublic class UserController : ControllerBase\n{\n'
    '    [HttpPut("{id}")]\n    public IActionResult Put(int id) => Ok();\n'
    '    [HttpPost("login")]\n    public IActionResult Login() => Ok();\n}\n'
)


def test_probes_are_steered_to_the_controller_serving_most(repo: Path, tmp_path: Path) -> None:
    (repo / "Controllers" / "ProductsController.cs").write_text(TWO_CONTROLLERS, encoding="utf-8")

    def two_way(method: HttpMethod, path: str, alternative: str) -> HttpProbe:
        return HttpProbe(
            requirement_id="hw01-r1",
            assignment_id="hw01",
            method=method,
            path=path,
            path_alternatives=(alternative,),
            expected_status=200,
        )

    probes = (
        two_way(HttpMethod.POST, "/api/user", "/api/users"),
        two_way(HttpMethod.PUT, "/api/user/3", "/api/users/3"),
        two_way(HttpMethod.POST, "/api/users/login", "/api/user/login"),
    )
    engine = FakeEngine()

    result = runner(engine).execute(request(repo, tmp_path, probes=probes))

    urls = [command[command.index("--") + 1] for command in engine.exec_commands]
    sent = [urlsplit(url).path for url in urls if urlsplit(url).path != "/"]
    assert sent == ["/api/users", "/api/users/3", "/api/user/login"]
    assert [r.controller for r in result.probe_results] == [
        "UsersController",
        "UsersController",
        "UserController",
    ]
    assert result.primary_controller == "UsersController"
    assert result.serving_controllers == ("UserController", "UsersController")
    prepare_log = (tmp_path / "logs" / "prepare.log").read_text(encoding="utf-8")
    assert (
        "controllers serving probes: UserController, UsersController; "
        "probes sent to UsersController when it serves them"
    ) in prepare_log


def test_without_routes_probes_go_to_their_first_path(repo: Path, tmp_path: Path) -> None:
    engine = FakeEngine(probe_statuses=STATUSES)

    result = runner(engine).execute(request(repo, tmp_path))

    assert [(r.sent_path, r.route_resolution, r.controller) for r in result.probe_results] == [
        (probe.path, RouteResolution.UNAVAILABLE, None) for probe in PROBES
    ]
    assert (result.primary_controller, result.serving_controllers) == (None, ())
    assert "controllers serving probes" not in (tmp_path / "logs" / "prepare.log").read_text(
        encoding="utf-8"
    )


def test_project_without_routes_records_an_empty_table(repo: Path, tmp_path: Path) -> None:
    result = runner(FakeEngine()).execute(request(repo, tmp_path))

    assert result.routes is not None
    assert result.routes.routes == () and not result.routes.usable


def test_unresolved_path_is_recorded_on_a_failed_build_too(repo: Path, tmp_path: Path) -> None:
    (repo / "Store.cs").write_text('var p = @"C:\\x\\orders.txt";', encoding="utf-8")
    engine = FakeEngine(exit_codes={"build": 1}, logs_by_kind={"build": BUILD_ERROR})

    result = runner(engine).execute(request(repo, tmp_path))

    assert result.failure_kind is FailureKind.BUILD_FAILED
    assert result.has_unresolved_paths


def test_internal_network_is_used_for_the_run(repo: Path, tmp_path: Path) -> None:
    engine = FakeEngine()
    seen: list[bool] = []
    original = engine.create_network

    def record(name: str, labels: object, internal: bool) -> None:
        seen.append(internal)
        original(name, labels, internal)  # type: ignore[arg-type]

    engine.create_network = record  # type: ignore[method-assign]

    runner(engine).execute(request(repo, tmp_path))

    assert seen == [True]


def test_probes_run_in_order_with_body_and_content_type(repo: Path, tmp_path: Path) -> None:
    engine = FakeEngine(probe_statuses=STATUSES)

    runner(engine).execute(request(repo, tmp_path))

    probe_commands = engine.exec_commands[1:]
    urls = [command[command.index("--") + 1] for command in probe_commands]
    assert [url.split(":8080", 1)[1] for url in urls] == [
        "/api/products",
        "/api/products/999",
        "/api/products",
    ]
    post = probe_commands[2]
    assert post[post.index("--request") + 1] == "POST"
    assert post[post.index("--header") + 1] == "Content-Type: application/json"
    assert post[post.index("--data-raw") + 1] == '{"name":"Desk","price":250}'
    assert "--header" not in probe_commands[0]


def test_probe_that_gets_no_answer_records_an_error(repo: Path, tmp_path: Path) -> None:
    engine = FakeEngine(probe_statuses={("GET", "/api/products"): 500})

    result = runner(engine).execute(request(repo, tmp_path, probes=PROBES[:1]))

    (outcome,) = result.probe_results
    assert (outcome.actual_status, outcome.passed) == (500, False)


def test_restore_failure_skips_build_and_run(repo: Path, tmp_path: Path) -> None:
    engine = FakeEngine(
        exit_codes={"restore": 1},
        logs_by_kind={"restore": "error NU1101: Unable to find package X"},
    )

    result = runner(engine).execute(request(repo, tmp_path))

    assert result.steps == (StepStatus.SUCCEEDED, StepStatus.FAILED, StepStatus.SKIPPED)
    assert result.failure_kind is FailureKind.BUILD_FAILED
    assert engine.specs("build") == []
    assert engine.leftovers == 0


def test_build_failure_has_the_error_in_the_excerpt(repo: Path, tmp_path: Path) -> None:
    engine = FakeEngine(exit_codes={"build": 1}, logs_by_kind={"build": BUILD_ERROR})

    result = runner(engine).execute(request(repo, tmp_path))

    assert result.failure_kind is FailureKind.BUILD_FAILED
    assert "error CS0103" in result.logs_excerpt
    assert engine.specs("app") == []
    assert (tmp_path / "logs" / "build.log").read_text(encoding="utf-8") == BUILD_ERROR
    assert engine.leftovers == 0


def test_startup_crash_stops_readiness_early(repo: Path, tmp_path: Path) -> None:
    engine = FakeEngine(app="crash", logs_by_kind={"app": "Unhandled exception. boom"})

    result = runner(engine).execute(request(repo, tmp_path))

    assert result.failure_kind is FailureKind.STARTUP_FAILED
    assert result.steps == (StepStatus.SUCCEEDED, StepStatus.SUCCEEDED, StepStatus.FAILED)
    assert len(engine.exec_commands) == 1
    assert result.probe_results == ()
    assert "Unhandled exception" in result.logs_excerpt
    assert engine.leftovers == 0


def test_app_is_reached_through_its_short_network_alias(repo: Path, tmp_path: Path) -> None:
    engine = FakeEngine()
    long_ids = {"run_id": "round-2026-10-25-hw01-" + "x" * 30, "repo_id": "student__" + "y" * 40}

    runner(engine).execute(request(repo, tmp_path, **long_ids))

    (app,) = engine.specs("app")
    (sidecar,) = engine.specs("probe")
    assert len(app.name) > 63, "the container name alone would not resolve"
    assert app.network_aliases == ("app",)
    assert sidecar.network_aliases == ()
    urls = {command[command.index("--") + 1] for command in engine.exec_commands}
    assert all(url.startswith("http://app:8080/") for url in urls)


def test_name_that_never_resolves_is_an_environment_error(repo: Path, tmp_path: Path) -> None:
    engine = FakeEngine(resolve_failures=1_000)

    result = runner(engine).execute(request(repo, tmp_path))

    assert result.failure_kind is FailureKind.ENVIRONMENT_ERROR
    assert result.steps == (StepStatus.SUCCEEDED, StepStatus.SUCCEEDED, StepStatus.FAILED)
    # About 2 s of grace / 0.1 s poll, far fewer than the 5 s readiness timeout would take.
    assert 20 <= len(engine.exec_commands) <= 22
    assert "could not resolve 'app'" in (tmp_path / "logs" / "run.log").read_text(encoding="utf-8")
    assert engine.leftovers == 0


def test_name_that_resolves_within_the_grace_period_is_fine(repo: Path, tmp_path: Path) -> None:
    engine = FakeEngine(resolve_failures=5, probe_statuses=STATUSES)

    result = runner(engine).execute(request(repo, tmp_path))

    assert result.failure_kind is None
    assert all(r.passed for r in result.probe_results)


def test_app_that_never_answers_times_out(repo: Path, tmp_path: Path) -> None:
    engine = FakeEngine(app="hang")

    result = runner(engine).execute(request(repo, tmp_path))

    assert result.failure_kind is FailureKind.TIMEOUT
    # About 5 s readiness timeout / 0.1 s poll; float steps may add one more poll.
    assert 50 <= len(engine.exec_commands) <= 52
    assert engine.leftovers == 0


def test_build_that_runs_past_its_timeout_is_stopped(repo: Path, tmp_path: Path) -> None:
    engine = FakeEngine(exit_codes={"build": None})

    result = runner(engine).execute(request(repo, tmp_path))

    assert result.failure_kind is FailureKind.TIMEOUT
    assert engine.leftovers == 0


def test_oom_kill_is_an_environment_error(repo: Path, tmp_path: Path) -> None:
    engine = FakeEngine(exit_codes={"build": 137}, oom_kinds={"build"})

    result = runner(engine).execute(request(repo, tmp_path))

    assert result.failure_kind is FailureKind.ENVIRONMENT_ERROR


@pytest.mark.parametrize("operation", ["start:restore", "logs:build", "create_network", "exec"])
def test_engine_error_mid_step_is_an_environment_error(
    repo: Path, tmp_path: Path, operation: str
) -> None:
    engine = FakeEngine(fail_on={operation: EngineError("engine hiccup")})

    result = runner(engine).execute(request(repo, tmp_path))

    assert result.failure_kind is FailureKind.ENVIRONMENT_ERROR
    assert engine.leftovers == 0


def test_engine_error_while_copying_the_code_fails_prepare(repo: Path, tmp_path: Path) -> None:
    engine = FakeEngine(fail_on={"put_archive": EngineError("disk full")})

    result = runner(engine).execute(request(repo, tmp_path))

    assert result.steps == (StepStatus.FAILED, StepStatus.SKIPPED, StepStatus.SKIPPED)
    assert result.failure_kind is FailureKind.ENVIRONMENT_ERROR
    assert engine.leftovers == 0


def test_unexpected_exception_mid_step_still_cleans_up(repo: Path, tmp_path: Path) -> None:
    engine = FakeEngine(fail_on={"logs:build": RuntimeError("bug")})

    with pytest.raises(RuntimeError, match="bug"):
        runner(engine).execute(request(repo, tmp_path))

    assert engine.leftovers == 0


def test_cleanup_error_does_not_hide_the_original_error(
    repo: Path, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    engine = FakeEngine(
        fail_on={"logs:build": RuntimeError("bug"), "remove_volume": EngineError("busy")}
    )

    with caplog.at_level(logging.WARNING), pytest.raises(RuntimeError, match="bug"):
        runner(engine).execute(request(repo, tmp_path))

    assert any("Cleanup could not remove volume" in r.getMessage() for r in caplog.records)


def test_cleanup_error_does_not_hide_a_successful_result(repo: Path, tmp_path: Path) -> None:
    engine = FakeEngine(fail_on={"remove_network": EngineError("busy")})

    result = runner(engine).execute(request(repo, tmp_path))

    assert result.failure_kind is None


def test_project_not_found_creates_nothing(tmp_path: Path) -> None:
    empty = tmp_path / "empty"
    empty.mkdir()
    engine = FakeEngine()

    result = runner(engine).execute(request(empty, tmp_path))

    assert result.failure_kind is FailureKind.PROJECT_NOT_FOUND
    assert result.steps == (StepStatus.FAILED, StepStatus.SKIPPED, StepStatus.SKIPPED)
    assert engine.calls == []
    assert "no .csproj" in (tmp_path / "logs" / "prepare.log").read_text(encoding="utf-8")


@pytest.mark.parametrize(
    ("framework", "message"),
    [
        ("<TargetFramework>net7.0</TargetFramework>", "Shop.Api.csproj targets net7.0"),
        ("<TargetFrameworks>net8.0;net10.0</TargetFrameworks>", "targets net8.0;net10.0"),
        ("", "no <TargetFramework> found in Shop.Api.csproj"),
    ],
)
def test_other_target_framework_fails_prepare_without_docker(
    repo: Path, tmp_path: Path, framework: str, message: str
) -> None:
    (repo / "Shop.Api.csproj").write_text(
        f'<Project Sdk="Microsoft.NET.Sdk.Web"><PropertyGroup>{framework}</PropertyGroup>'
        "</Project>",
        encoding="utf-8",
    )
    engine = FakeEngine()

    result = runner(engine).execute(request(repo, tmp_path))

    assert result.failure_kind is FailureKind.ENVIRONMENT_ERROR
    assert result.steps == (StepStatus.FAILED, StepStatus.SKIPPED, StepStatus.SKIPPED)
    assert message in result.logs_excerpt
    assert "supported: net8.0, net9.0, net10.0" in result.logs_excerpt
    assert engine.calls == []


@pytest.mark.parametrize(
    ("framework", "tag", "outdated"),
    [("net8.0", "8.0", True), ("net9.0", "9.0", True), ("net10.0", "10.0", False)],
)
def test_project_is_built_and_run_with_the_images_of_its_framework(
    repo: Path, tmp_path: Path, framework: str, tag: str, outdated: bool
) -> None:
    (repo / "Shop.Api.csproj").write_text(
        '<Project Sdk="Microsoft.NET.Sdk.Web"><PropertyGroup>'
        f"<TargetFramework>{framework}</TargetFramework></PropertyGroup></Project>",
        encoding="utf-8",
    )
    engine = FakeEngine()

    result = runner(engine).execute(request(repo, tmp_path))

    assert result.failure_kind is None
    (restore,) = engine.specs("restore")
    (build,) = engine.specs("build")
    (app,) = engine.specs("app")
    assert restore.image == build.image == f"mcr.microsoft.com/dotnet/sdk:{tag}@{DIGEST}"
    assert app.image == f"mcr.microsoft.com/dotnet/aspnet:{tag}@{DIGEST}"
    assert all(spec.image.endswith(f"aspnet:10.0@{DIGEST}") for spec in engine.specs("copy"))
    assert (result.target_framework, result.framework_outdated) == (framework, outdated)
    prepare_log = (tmp_path / "logs" / "prepare.log").read_text(encoding="utf-8")
    assert f"Web project: Shop.Api.csproj ({framework})" in prepare_log


def test_failed_build_still_records_the_framework(repo: Path, tmp_path: Path) -> None:
    (repo / "Shop.Api.csproj").write_text(
        '<Project Sdk="Microsoft.NET.Sdk.Web"><PropertyGroup>'
        "<TargetFramework>net8.0</TargetFramework></PropertyGroup></Project>",
        encoding="utf-8",
    )
    engine = FakeEngine(exit_codes={"build": 1}, logs_by_kind={"build": BUILD_ERROR})

    result = runner(engine).execute(request(repo, tmp_path))

    assert result.failure_kind is FailureKind.BUILD_FAILED
    assert (result.target_framework, result.framework_outdated) == ("net8.0", True)


def test_oversized_repo_fails_prepare_without_docker(repo: Path, tmp_path: Path) -> None:
    (repo / "big.bin").write_bytes(b"\0" * 2_000_000)
    engine = FakeEngine()

    result = runner(engine).execute(request(repo, tmp_path))

    assert result.failure_kind is FailureKind.ENVIRONMENT_ERROR
    assert result.prepare_status is StepStatus.FAILED
    assert "big.bin is 2,000,000 bytes" in result.logs_excerpt
    assert engine.calls == []


@pytest.mark.parametrize(
    ("engine", "message"),
    [
        (FakeEngine(reachable=False), "Docker is not reachable"),
        (FakeEngine(images_present=False), "Docker images are missing"),
        (FakeEngine(cache_present=False), "package cache volume"),
        (FakeEngine(fail_on={"create_volume": EngineError("down")}), None),
    ],
)
def test_raises_when_no_step_could_start(
    repo: Path, tmp_path: Path, engine: FakeEngine, message: str | None
) -> None:
    if message is None:
        result = runner(engine).execute(request(repo, tmp_path))
        assert result.prepare_status is StepStatus.FAILED
        return
    with pytest.raises(ExecutionEnvironmentError, match=message):
        runner(engine).execute(request(repo, tmp_path))
    assert engine.leftovers == 0


def test_logs_are_written_per_step(repo: Path, tmp_path: Path) -> None:
    runner(FakeEngine()).execute(request(repo, tmp_path))

    written = sorted(path.name for path in (tmp_path / "logs").iterdir())
    assert written == ["build.log", "prepare.log", "restore.log", "run.log"]


def test_environment_values_are_never_logged(
    repo: Path, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    secret = request(repo, tmp_path, environment=(("ConnectionStrings__Db", "pw=hunter2"),))

    with caplog.at_level(logging.DEBUG):
        runner(FakeEngine()).execute(secret)

    assert all("hunter2" not in record.getMessage() for record in caplog.records)
