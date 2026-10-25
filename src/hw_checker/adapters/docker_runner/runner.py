"""DockerCodeRunner: prepares, builds, runs and probes one student repo in Docker (ADR 0015).

Student code never runs on the host, never has network access during build or run, and
everything created for a repo is removed in `finally`.

The app runs like `dotnet run` on the student's machine: its working directory is a
writable copy of the Web project's folder (with its data files), on a per-repo volume of its
own, while the source and the published output stay read-only.
"""

import dataclasses
import logging
import re
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from hw_checker.adapters.docker_runner.archive import (
    PROJECT_DIR,
    ArchiveTooLargeError,
    build_project_archive,
    build_work_archive,
    directory_archive,
)
from hw_checker.adapters.docker_runner.body_adapter import adapt_body
from hw_checker.adapters.docker_runner.body_models import ProjectSources
from hw_checker.adapters.docker_runner.classification import RunnerStep, StepOutcome, classify
from hw_checker.adapters.docker_runner.discovery import (
    DiscoveryFailure,
    WebProject,
    discover_web_project,
    is_older_framework,
)
from hw_checker.adapters.docker_runner.engine import (
    ContainerSpec,
    DockerEngine,
    EngineError,
    Mount,
    Resource,
)
from hw_checker.adapters.docker_runner.excerpt import excerpt_log
from hw_checker.adapters.docker_runner.host_paths import PathPlan, plan_path_fixes
from hw_checker.adapters.docker_runner.http_response import HttpResponse, excerpt, parse_response
from hw_checker.adapters.docker_runner.routes import (
    ControllerChoice,
    ProbeRoute,
    choose_primary_controller,
    extract_route_table,
    resolve_probe_path,
)
from hw_checker.adapters.docker_runner.runner_config import (
    ContainerLimits,
    DockerRunnerConfig,
    FrameworkImages,
)
from hw_checker.config.logging_setup import log_context
from hw_checker.domain import (
    BodyAdaptation,
    ExecutionRequest,
    ExecutionResult,
    FailureKind,
    HttpProbe,
    PathRewrite,
    ProbeResult,
    RouteTable,
    StepStatus,
)
from hw_checker.domain.errors import ExecutionEnvironmentError
from hw_checker.domain.execution import MAX_PROBE_ERROR_CHARS

logger = logging.getLogger(__name__)

WORK_MOUNT = "/work"
CACHE_MOUNT = "/nuget"
OUTPUT_DIR = "/work/out"
DATA_MOUNT = "/data"
APP_WORKING_DIR = f"{DATA_MOUNT}/{PROJECT_DIR}"
"""The app's working directory: the writable copy of the Web project's folder."""
DOTNET_ENVIRONMENT = {
    "NUGET_PACKAGES": f"{CACHE_MOUNT}/packages",
    "DOTNET_CLI_TELEMETRY_OPTOUT": "1",
    "DOTNET_NOLOGO": "1",
}
TMPFS = {"/tmp": "size=64m"}
APP_ALIAS = "app"
"""The app's DNS name on its per-repo network. Container names can pass the 63-character
DNS label limit (they include the run and repo ids), and then cannot be resolved; each repo
has its own network, so a fixed short alias never collides."""
CACHE_KIND = "cache"
CURL_RESOLVE_ERROR = 6
CURL_ERRORS = {
    CURL_RESOLVE_ERROR: "could not resolve the app",
    7: "connection refused",
    28: "timed out",
}
_INVALID_NAME_CHARACTERS = re.compile(r"[^a-zA-Z0-9_.-]")

Clock = Callable[[], float]
Sleep = Callable[[float], None]


class DockerCodeRunner:
    """`CodeRunner` that builds and runs student projects in hardened Docker containers."""

    def __init__(
        self,
        config: DockerRunnerConfig,
        engine: DockerEngine,
        clock: Clock = time.monotonic,
        sleep: Sleep = time.sleep,
    ) -> None:
        self._config = config
        self._engine = engine
        self._clock = clock
        self._sleep = sleep

    def execute(self, request: ExecutionRequest) -> ExecutionResult:
        """Prepare, restore, build, run and probe one repo, and always clean up after it.

        Raises:
            ExecutionEnvironmentError: the engine cannot be reached, a pinned image is
                missing, or the package cache volume does not exist, so no step could start.
        """
        with log_context(run_id=request.run_id, repo_id=request.repo_id):
            execution = _Execution(self._config, self._engine, request, self._clock, self._sleep)
            return execution.run()

    def check_environment(self) -> None:
        """Check that Docker answers, pull any missing pinned image, and prepare the cache.

        Raises:
            ExecutionEnvironmentError: Docker is not running, an image cannot be pulled, or
                the package cache volume cannot be created.
        """
        try:
            self._engine.ping()
        except EngineError as error:
            raise ExecutionEnvironmentError(
                f"Docker Desktop is not running or cannot be reached: {error}"
            ) from error
        for image in self._config.images:
            self._ensure_image(image)
        try:
            self._ensure_cache_volume()
        except EngineError as error:
            raise ExecutionEnvironmentError(
                f"cannot prepare the package cache volume {self._config.nuget_cache_volume}: "
                f"{error}"
            ) from error

    def sweep_leftovers(self) -> int:
        """Remove every labelled container, network and volume except the package cache.

        Raises:
            ExecutionEnvironmentError: Docker cannot be reached.
        """
        prefix = self._config.label_prefix
        try:
            resources = self._engine.list_labelled(prefix)
        except EngineError as error:
            raise ExecutionEnvironmentError(f"cannot list leftovers in Docker: {error}") from error
        order = {"container": 0, "network": 1, "volume": 2}
        leftovers = sorted(
            (item for item in resources if item.labels.get(f"{prefix}.kind") != CACHE_KIND),
            key=lambda item: order[item.kind],
        )
        removed = sum(1 for item in leftovers if self._remove_leftover(item))
        logger.info("Swept %d leftover Docker resources", removed, extra={"removed": removed})
        return removed

    def _ensure_image(self, image: str) -> None:
        try:
            if self._engine.image_exists(image):
                return
            logger.info("Pulling missing image %s", image)
            self._engine.pull(image)
            logger.info("Pulled image %s", image)
        except EngineError as error:
            raise ExecutionEnvironmentError(f"cannot pull {image}: {error}") from error

    def _ensure_cache_volume(self) -> None:
        name = self._config.nuget_cache_volume
        if not self._engine.volume_exists(name):
            prefix = self._config.label_prefix
            self._engine.create_volume(name, {prefix: "1", f"{prefix}.kind": CACHE_KIND})
            logger.info("Created the package cache volume %s", name)
        # The packages folder must belong to the app user, or restore cannot write to it.
        helper = self._engine.create_container(
            ContainerSpec(
                name=f"{self._config.label_prefix}-cache-init-{uuid.uuid4().hex[:8]}",
                image=self._config.helper_image,
                command=("true",),
                labels={
                    self._config.label_prefix: "1",
                    f"{self._config.label_prefix}.kind": "init",
                },
                user=self._config.user,
                memory_mb=self._config.probe_limits.memory_mb,
                cpus=self._config.probe_limits.cpus,
                pids=self._config.probe_limits.pids,
                mounts=(Mount(name, CACHE_MOUNT, read_only=False),),
                network_mode="none",
            )
        )
        try:
            self._engine.put_archive(
                helper,
                CACHE_MOUNT,
                directory_archive("packages", self._config.app_uid, self._config.app_gid),
            )
        finally:
            self._engine.remove_container(helper)

    def _remove_leftover(self, resource: Resource) -> bool:
        removers = {
            "container": self._engine.remove_container,
            "network": self._engine.remove_network,
            "volume": self._engine.remove_volume,
        }
        try:
            removers[resource.kind](resource.name)
        except EngineError as error:
            logger.warning(
                "Could not remove leftover %s %s: %s", resource.kind, resource.name, error
            )
            return False
        return True


@dataclass
class _StepRecord:
    step: RunnerStep
    outcome: StepOutcome
    log: str


@dataclass
class _Execution:
    """One `execute` call: the resources it created and the steps it ran."""

    config: DockerRunnerConfig
    engine: DockerEngine
    request: ExecutionRequest
    clock: Clock
    sleep: Sleep
    containers: list[str] = field(default_factory=list)
    networks: list[str] = field(default_factory=list)
    volumes: list[str] = field(default_factory=list)
    suffix: str = field(default_factory=lambda: uuid.uuid4().hex[:8])
    routes: RouteTable | None = None
    controllers: ControllerChoice = field(default_factory=lambda: ControllerChoice(None, ()))
    framework_images: FrameworkImages | None = None
    project_sources: ProjectSources | None = None

    def run(self) -> ExecutionResult:
        self.request.logs_dir.mkdir(parents=True, exist_ok=True)
        try:
            return self._pipeline()
        finally:
            self._cleanup()

    # ------------------------------------------------------------------ pipeline

    def _pipeline(self) -> ExecutionResult:
        discovery = discover_web_project(self.request.working_copy)
        if isinstance(discovery, DiscoveryFailure):
            return self._prepare_failed(discovery.message, project_found=False)
        framework = {
            "target_framework": discovery.target_framework,
            "framework_outdated": is_older_framework(
                discovery.framework, self.config.course_framework
            ),
        }
        if (problem := discovery.framework_problem(self.config.framework_images)) is not None:
            result = self._prepare_failed(f"not run, flagged for review: {problem}")
            return result.model_copy(update=framework)
        assert discovery.framework is not None  # framework_problem rejects a missing one
        self.framework_images = self.config.framework_images[discovery.framework]
        plan = plan_path_fixes(self.request.working_copy, discovery.folder)
        self.routes = extract_route_table(self.request.working_copy, discovery.folder)
        self.controllers = choose_primary_controller(self.request.probes, self.routes)
        self.project_sources = ProjectSources.read(
            self.request.working_copy, discovery.folder, discovery.nullable
        )
        result = self._prepare_build_run(discovery, plan)
        # Every result after this point records the detected paths, the route table and the
        # controller choice.
        return result.model_copy(
            update={
                "path_rewrites": plan.rewrites,
                "routes": self.routes,
                "primary_controller": self.controllers.primary,
                "serving_controllers": self.controllers.serving,
                **framework,
            }
        )

    def _prepare_build_run(self, discovery: WebProject, plan: PathPlan) -> ExecutionResult:
        limits = (self.config.max_archive_bytes, self.config.max_file_bytes)
        uid, gid = self.config.app_uid, self.config.app_gid
        prefix = f"{discovery.folder}/" if discovery.folder else ""
        project_overrides = {
            path.removeprefix(prefix): data
            for path, data in plan.overrides.items()
            if path.startswith(prefix)
        }
        try:
            archive = build_work_archive(
                self.request.working_copy, uid, gid, *limits, overrides=plan.overrides
            )
            project_archive = build_project_archive(
                self.request.working_copy / discovery.folder,
                uid,
                gid,
                *limits,
                overrides=project_overrides,
            )
        except ArchiveTooLargeError as error:
            return self._prepare_failed(f"not run, flagged for review: {error}")
        if archive.skipped:
            logger.warning(
                "Skipped unsafe paths in the working copy: %s", ", ".join(archive.skipped)
            )
        self._preflight()
        work_volume = self._name("work")
        project_volume = self._name("project")
        try:
            self._create_volume(work_volume, "work", WORK_MOUNT, archive.data)
            self._create_volume(project_volume, "project", DATA_MOUNT, project_archive.data)
        except EngineError as error:
            return self._prepare_failed(f"cannot copy the code into Docker: {error}", engine=True)
        self._write_log(
            RunnerStep.PREPARE,
            f"Web project: {discovery.path} ({discovery.framework})\n"
            + _describe_paths(plan.rewrites)
            + _describe_routes(self.routes)
            + _describe_controllers(self.routes, self.controllers),
        )
        if plan.rewrites:
            rewritten = sum(1 for rewrite in plan.rewrites if rewrite.rewritten is not None)
            logger.info(
                "Adapted %d of %d detected paths in the temporary copy",
                rewritten,
                len(plan.rewrites),
                extra={"paths_detected": len(plan.rewrites), "paths_rewritten": rewritten},
            )

        project = f"{WORK_MOUNT}/src/{discovery.path}"
        restore = self._run_step(
            RunnerStep.RESTORE,
            self._step_spec(
                "restore",
                self._images().sdk,
                ("dotnet", "restore", project),
                work_volume,
                cache_read_only=False,
                network_mode="bridge",
                limits=self.config.restore_limits,
            ),
            self.config.restore_timeout_seconds,
        )
        if (kind := classify(restore.outcome)) is not None:
            return self._build_failed(kind, restore.log)
        build = self._run_step(
            RunnerStep.BUILD,
            self._step_spec(
                "build",
                self._images().sdk,
                ("dotnet", "publish", project, "--no-restore", "-c", "Release", "-o", OUTPUT_DIR),
                work_volume,
                cache_read_only=True,
                network_mode="none",
                limits=self.config.build_limits,
            ),
            self.config.build_timeout_seconds,
        )
        if (kind := classify(build.outcome)) is not None:
            return self._build_failed(kind, build.log)
        return self._run_and_probe(work_volume, project_volume, discovery.assembly_name)

    def _preflight(self) -> None:
        try:
            self.engine.ping()
            missing = [image for image in self.config.images if not self.engine.image_exists(image)]
            cache_exists = self.engine.volume_exists(self.config.nuget_cache_volume)
        except EngineError as error:
            raise ExecutionEnvironmentError(f"Docker is not reachable: {error}") from error
        if missing:
            raise ExecutionEnvironmentError(
                f"Docker images are missing: {', '.join(missing)}; run the environment check"
            )
        if not cache_exists:
            raise ExecutionEnvironmentError(
                f"the package cache volume {self.config.nuget_cache_volume} does not exist; "
                "run the environment check"
            )

    def _create_volume(self, name: str, kind: str, mount: str, data: bytes) -> None:
        """Create a per-repo volume and copy `data` (a tar) into it through a helper."""
        self.engine.create_volume(name, self._labels(kind))
        self.volumes.append(name)
        helper = self.engine.create_container(
            ContainerSpec(
                name=self._name("copy"),
                image=self.config.helper_image,
                command=("true",),
                labels=self._labels("copy"),
                user=self.config.user,
                memory_mb=self.config.probe_limits.memory_mb,
                cpus=self.config.probe_limits.cpus,
                pids=self.config.probe_limits.pids,
                mounts=(Mount(name, mount, read_only=False),),
                network_mode="none",
            )
        )
        self.containers.append(helper)
        self.engine.put_archive(helper, mount, data)
        self._remove_container(helper)

    def _run_and_probe(
        self, work_volume: str, project_volume: str, assembly_name: str
    ) -> ExecutionResult:
        network = self._name("net")
        app_name = self._name("app")
        started = self.clock()
        logger.info("Step run started")
        try:
            self.engine.create_network(network, self._labels("network"), internal=True)
            self.networks.append(network)
            app = self._create(
                self._app_spec(app_name, network, work_volume, project_volume, assembly_name)
            )
            sidecar = self._create(self._probe_spec(network))
            self.engine.start(sidecar)
            self.engine.start(app)
            base_url = f"http://{APP_ALIAS}:{self.config.app_port}"
            outcome = self._wait_until_ready(app, sidecar, base_url)
            probe_results = (
                tuple(self._probe(sidecar, base_url, probe) for probe in self.request.probes)
                if outcome.ready
                else ()
            )
            log = self.engine.logs(app)
        except EngineError as error:
            outcome = StepOutcome(RunnerStep.RUN, engine_error=True, log_text=str(error))
            probe_results, log = (), str(error)
        if outcome.unresolved_host:
            log += (
                f"\n[runner] the probe sidecar could not resolve {APP_ALIAS!r} for more than "
                f"{self.config.resolve_grace_seconds:g} s; this is an environment problem\n"
            )
        self._log_step_end(RunnerStep.RUN, started, outcome)
        self._write_log(RunnerStep.RUN, log)
        kind = classify(dataclasses.replace(outcome, log_text=log))
        if kind is not None:
            return ExecutionResult(
                build_status=StepStatus.SUCCEEDED,
                run_status=StepStatus.FAILED,
                failure_kind=kind,
                logs_excerpt=excerpt_log(log),
                logs_path=self.request.logs_dir,
            )
        return ExecutionResult(
            build_status=StepStatus.SUCCEEDED,
            run_status=StepStatus.SUCCEEDED,
            logs_excerpt=excerpt_log(log),
            logs_path=self.request.logs_dir,
            probe_results=probe_results,
        )

    def _wait_until_ready(self, app: str, sidecar: str, base_url: str) -> StepOutcome:
        deadline = self.clock() + self.config.readiness_timeout_seconds
        unresolved_since: float | None = None
        while True:
            response, error = self._http_request(sidecar, "GET", f"{base_url}/", None)
            if response is not None:
                return StepOutcome(RunnerStep.RUN, ready=True)
            if error == CURL_ERRORS[CURL_RESOLVE_ERROR]:
                unresolved_since = self.clock() if unresolved_since is None else unresolved_since
                if self.clock() - unresolved_since >= self.config.resolve_grace_seconds:
                    return StepOutcome(RunnerStep.RUN, unresolved_host=True)
            else:
                unresolved_since = None
            state = self.engine.state(app)
            if not state.running:
                return StepOutcome(
                    RunnerStep.RUN, exit_code=state.exit_code, oom_killed=state.oom_killed
                )
            if self.clock() >= deadline:
                return StepOutcome(RunnerStep.RUN, timed_out=True)
            self.sleep(self.config.readiness_poll_seconds)

    def _probe(self, sidecar: str, base_url: str, probe: HttpProbe) -> ProbeResult:
        route = resolve_probe_path(probe, self.routes, self.controllers.primary)
        path, resolution = route.path, route.resolution
        if path is None:
            logger.info("Probe %s %s not sent: no declared route matches", probe.method, probe.path)
            return ProbeResult(
                probe=probe,
                passed=False,
                error="no route declared in the code matches the probe's paths",
                route_resolution=resolution,
            )
        body, adaptation = self._probe_body(probe, route)
        response, error = self._http_request(sidecar, probe.method.value, base_url + path, body)
        status = None if response is None else response.status
        return ProbeResult(
            probe=probe,
            actual_status=status,
            passed=status in probe.allowed_statuses,
            error=error[:MAX_PROBE_ERROR_CHARS],
            sent_path=path,
            route_resolution=resolution,
            controller=route.controller,
            sent_body=body,
            body_adaptation=adaptation,
            response_excerpt="" if response is None else excerpt(response.body),
        )

    def _probe_body(
        self, probe: HttpProbe, route: ProbeRoute
    ) -> tuple[str | None, BodyAdaptation | None]:
        """The body to send: the probe's intended body adapted to the action's model."""
        if probe.body is None:
            return None, None
        if route.route is None or self.project_sources is None:
            return probe.body, BodyAdaptation(
                adapted=False, reason="the probe's route was not resolved against the code"
            )
        return adapt_body(probe.body, self.project_sources.body_model(route.route))

    def _http_request(
        self, sidecar: str, method: str, url: str, body: str | None
    ) -> tuple[HttpResponse | None, str]:
        """Send one request from the sidecar; return the response (None if no answer) and an
        error.

        curl prints the headers and then the body, cut at `max_response_bytes` by `head`, so
        a large or binary body never fails the probe. Arguments reach curl through `"$@"`, so
        nothing in them is interpreted by the shell.
        """
        curl = [
            "--silent",
            "--globoff",
            "--dump-header",
            "-",
            "--output",
            "-",
            "--max-time",
            str(self.config.probe_timeout_seconds),
            "--request",
            method,
        ]
        if body is not None:
            # --data-raw never treats a leading "@" as a file name.
            curl += ["--header", "Content-Type: application/json", "--data-raw", body]
        curl += ["--", url]
        script = f'set -o pipefail; curl "$@" | head -c {self.config.max_response_bytes}'
        result = self.engine.exec(sidecar, ("sh", "-c", script, "sh", *curl))
        response = parse_response(result.output)
        if response is not None:
            # A body cut by `head` makes curl exit with a write error; the response is fine.
            return response, ""
        return None, CURL_ERRORS.get(result.exit_code, f"no answer (curl exit {result.exit_code})")

    # ------------------------------------------------------------------ steps and specs

    def _run_step(self, step: RunnerStep, spec: ContainerSpec, timeout: float) -> _StepRecord:
        started = self.clock()
        logger.info("Step %s started", step.value)
        try:
            container = self._create(spec)
            self.engine.start(container)
            exit_code, oom_killed, timed_out = self._wait_for_exit(container, started + timeout)
            log = self.engine.logs(container)
            self._remove_container(container)
            outcome = StepOutcome(step, exit_code, timed_out, oom_killed, log_text=log)
        except EngineError as error:
            log = str(error)
            outcome = StepOutcome(step, engine_error=True, log_text=log)
        self._log_step_end(step, started, outcome)
        self._write_log(step, log)
        return _StepRecord(step, outcome, log)

    def _wait_for_exit(self, container: str, deadline: float) -> tuple[int | None, bool, bool]:
        while True:
            state = self.engine.state(container)
            if not state.running:
                return state.exit_code, state.oom_killed, False
            if self.clock() >= deadline:
                return None, state.oom_killed, True
            self.sleep(self.config.step_poll_seconds)

    def _step_spec(
        self,
        kind: str,
        image: str,
        command: tuple[str, ...],
        work_volume: str,
        *,
        cache_read_only: bool,
        network_mode: str,
        limits: ContainerLimits,
    ) -> ContainerSpec:
        return ContainerSpec(
            name=self._name(kind),
            image=image,
            command=command,
            labels=self._labels(kind),
            user=self.config.user,
            memory_mb=limits.memory_mb,
            cpus=limits.cpus,
            pids=limits.pids,
            mounts=(
                Mount(work_volume, WORK_MOUNT, read_only=False),
                Mount(self.config.nuget_cache_volume, CACHE_MOUNT, read_only=cache_read_only),
            ),
            network_mode=network_mode,
            environment=DOTNET_ENVIRONMENT,
        )

    def _app_spec(
        self, name: str, network: str, work_volume: str, project_volume: str, assembly_name: str
    ) -> ContainerSpec:
        limits = self.config.run_limits
        environment = {
            **dict(self.request.environment),
            # Set last, so the student's environment cannot move the app to another port.
            "ASPNETCORE_HTTP_PORTS": str(self.config.app_port),
        }
        return ContainerSpec(
            name=name,
            image=self._images().runtime,
            command=("dotnet", f"{OUTPUT_DIR}/{assembly_name}.dll"),
            labels=self._labels("app"),
            user=self.config.user,
            memory_mb=limits.memory_mb,
            cpus=limits.cpus,
            pids=limits.pids,
            mounts=(
                Mount(work_volume, WORK_MOUNT, read_only=True),
                # The only writable place: the project copy, discarded in cleanup.
                Mount(project_volume, DATA_MOUNT, read_only=False),
            ),
            network=network,
            network_aliases=(APP_ALIAS,),
            environment=environment,
            read_only_root=True,
            tmpfs=TMPFS,
            working_dir=APP_WORKING_DIR,
        )

    def _probe_spec(self, network: str) -> ContainerSpec:
        limits = self.config.probe_limits
        budget = self.config.readiness_timeout_seconds + self.config.probe_timeout_seconds * (
            len(self.request.probes) + 1
        )
        return ContainerSpec(
            name=self._name("probe"),
            image=self.config.probe_image,
            command=(str(int(budget) + 60),),
            entrypoint=("sleep",),
            labels=self._labels("probe"),
            user=self.config.user,
            memory_mb=limits.memory_mb,
            cpus=limits.cpus,
            pids=limits.pids,
            network=network,
            read_only_root=True,
        )

    # ------------------------------------------------------------------ results

    def _prepare_failed(
        self, message: str, *, project_found: bool = True, engine: bool = False
    ) -> ExecutionResult:
        outcome = StepOutcome(
            RunnerStep.PREPARE,
            exit_code=0 if engine else 1,
            project_found=project_found,
            engine_error=engine,
            log_text=message,
        )
        logger.info("Step prepare failed: %s", message)
        self._write_log(RunnerStep.PREPARE, message + "\n")
        return ExecutionResult(
            prepare_status=StepStatus.FAILED,
            build_status=StepStatus.SKIPPED,
            run_status=StepStatus.SKIPPED,
            failure_kind=classify(outcome) or FailureKind.ENVIRONMENT_ERROR,
            logs_excerpt=excerpt_log(message),
            logs_path=self.request.logs_dir,
        )

    def _build_failed(self, kind: FailureKind, log: str) -> ExecutionResult:
        return ExecutionResult(
            build_status=StepStatus.FAILED,
            run_status=StepStatus.SKIPPED,
            failure_kind=kind,
            logs_excerpt=excerpt_log(log),
            logs_path=self.request.logs_dir,
        )

    # ------------------------------------------------------------------ helpers

    def _images(self) -> FrameworkImages:
        if self.framework_images is None:
            raise RuntimeError("the project's framework images are chosen during prepare")
        return self.framework_images

    def _name(self, kind: str) -> str:
        raw = f"{self.config.label_prefix}-{self.request.run_id}-{self.request.repo_id}-{kind}"
        return f"{_INVALID_NAME_CHARACTERS.sub('-', raw)}-{self.suffix}"

    def _labels(self, kind: str) -> dict[str, str]:
        prefix = self.config.label_prefix
        return {
            prefix: "1",
            f"{prefix}.run": self.request.run_id,
            f"{prefix}.repo": self.request.repo_id,
            f"{prefix}.kind": kind,
        }

    def _create(self, spec: ContainerSpec) -> str:
        container = self.engine.create_container(spec)
        self.containers.append(container)
        return container

    def _remove_container(self, container: str) -> None:
        self.engine.remove_container(container)
        self.containers.remove(container)

    def _write_log(self, step: RunnerStep, text: str) -> None:
        path: Path = self.request.logs_dir / f"{step.value}.log"
        path.write_bytes(text.encode("utf-8"))

    def _log_step_end(self, step: RunnerStep, started: float, outcome: StepOutcome) -> None:
        kind = classify(outcome)
        logger.info(
            "Step %s finished in %.1f s: %s",
            step.value,
            self.clock() - started,
            "ok" if kind is None else kind.value,
            extra={"step": step.value, "outcome": "ok" if kind is None else kind.value},
        )

    def _cleanup(self) -> None:
        """Remove everything this execution created; never raise."""
        for container in reversed(self.containers):
            self._quietly("container", self.engine.remove_container, container)
        for network in reversed(self.networks):
            self._quietly("network", self.engine.remove_network, network)
        for volume in reversed(self.volumes):
            self._quietly("volume", self.engine.remove_volume, volume)
        self.containers.clear()
        self.networks.clear()
        self.volumes.clear()

    @staticmethod
    def _quietly(kind: str, remove: Callable[[str], None], name: str) -> None:
        try:
            remove(name)
        except EngineError as error:
            logger.warning("Cleanup could not remove %s %s: %s", kind, name, error)


def _describe_paths(rewrites: tuple[PathRewrite, ...]) -> str:
    """One prepare-log line per detected path (kept in the per-repo log, not the app log)."""
    lines = []
    for rewrite in rewrites:
        outcome = (
            f"rewritten to {rewrite.rewritten!r}"
            if rewrite.rewritten is not None
            else f"not rewritten ({rewrite.not_rewritten_reason})"
        )
        lines.append(
            f"{rewrite.kind} path {rewrite.original!r} at {rewrite.file_path}:{rewrite.line}: "
            f"{outcome}\n"
        )
    return "".join(lines)


def _describe_routes(routes: RouteTable | None) -> str:
    """The route table, one prepare-log line per route or unsupported form."""
    if routes is None:
        return ""
    lines = [
        f"route {route.method or 'ANY'} /{route.template} "
        f"({route.controller}.{route.action}, {route.file_path}:{route.line})\n"
        for route in routes.routes
    ]
    lines += [
        f"unsupported routing: {item.form} at {item.file_path}:{item.line}\n"
        for item in routes.unsupported
    ]
    return "".join(lines)


def _describe_controllers(routes: RouteTable | None, choice: ControllerChoice) -> str:
    """The controllers that could serve probes, and the one the probes are steered to."""
    if routes is None or not routes.usable:
        return ""
    if choice.primary is None:
        return "controllers serving probes: none\n"
    return (
        f"controllers serving probes: {', '.join(choice.serving)}; "
        f"probes sent to {choice.primary} when it serves them\n"
    )
