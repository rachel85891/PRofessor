"""Docker run spike (stage 5a): build and run a sample .NET 9 Web API in Docker on Windows.

Throwaway experiment, not production code. It measures the questions listed in
docs/adr/0015-docker-run-spike.md and prints a summary. Run from the repo root:

    uv run python spikes/docker_run/run_spike.py --scenario all

Everything it creates carries the label `hw-checker.spike=1` and is removed in `finally`
blocks, plus a sweep by label at start.
"""

from __future__ import annotations

import argparse
import http.client
import io
import json
import shutil
import subprocess
import sys
import tarfile
import tempfile
import threading
import time
import uuid
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import docker
from docker.errors import APIError, NotFound
from docker.models.containers import Container

REPO_ROOT = Path(__file__).resolve().parents[2]
FIXTURE = REPO_ROOT / "tests" / "fixtures" / "sample_webapi"
PROJECT_FILE = "SampleWebApi.csproj"
APP_DLL = "SampleWebApi.dll"

SDK_IMAGE = "mcr.microsoft.com/dotnet/sdk:9.0"
ASPNET_IMAGE = "mcr.microsoft.com/dotnet/aspnet:9.0"
PROBE_IMAGE = "curlimages/curl:8.10.1"
HELPER_IMAGE = "busybox:1.37"

LABEL = "hw-checker.spike"
APP_USER = "1654:1654"  # the non-root `app` user in the official .NET images
APP_PORT = 8080
MEMORY_LIMIT = "1g"
NANO_CPUS = 2_000_000_000  # 2 CPUs
READINESS_TIMEOUT_S = 60.0
DOTNET_ENV = {
    "DOTNET_CLI_TELEMETRY_OPTOUT": "1",
    "DOTNET_NOLOGO": "1",
    "NUGET_PACKAGES": "/nuget/packages",
}
HARDENING: dict[str, Any] = {
    "cap_drop": ["ALL"],
    "security_opt": ["no-new-privileges"],
    "pids_limit": 512,
}


# ---------------------------------------------------------------- results


@dataclass
class Step:
    """One container step: how long it took, how it ended, and its peak resource use."""

    name: str
    seconds: float
    exit_code: int | None
    peak_memory_mb: float = 0.0
    peak_cpu_percent: float = 0.0
    log_tail: str = ""


@dataclass
class RepoReport:
    """Everything measured while building and running one repo copy."""

    repo: str
    mode: str
    steps: list[Step] = field(default_factory=list)
    ready_seconds: float | None = None
    probes: dict[str, int] = field(default_factory=dict)
    run_memory_mb: float = 0.0
    failure: str | None = None
    total_seconds: float = 0.0

    def step(self, name: str) -> Step | None:
        return next((step for step in self.steps if step.name == name), None)


RESULTS: dict[str, Any] = {}


def record(key: str, value: object) -> None:
    RESULTS[key] = value
    print(f"  {key}: {value}")


# ---------------------------------------------------------------- docker helpers


def labels(run_id: str, repo: str = "-") -> dict[str, str]:
    return {LABEL: "1", f"{LABEL}.run": run_id, f"{LABEL}.repo": repo}


def sweep(client: docker.DockerClient) -> dict[str, int]:
    """Remove every container, network and volume labelled by the spike."""
    selector = {"label": LABEL}
    removed = {"containers": 0, "networks": 0, "volumes": 0}
    for container in client.containers.list(all=True, filters=selector):
        container.remove(force=True)
        removed["containers"] += 1
    for network in client.networks.list(filters=selector):
        network.remove()
        removed["networks"] += 1
    for volume in client.volumes.list(filters=selector):
        volume.remove(force=True)
        removed["volumes"] += 1
    return removed


def leftovers(client: docker.DockerClient) -> dict[str, int]:
    selector = {"label": LABEL}
    return {
        "containers": len(client.containers.list(all=True, filters=selector)),
        "networks": len(client.networks.list(filters=selector)),
        "volumes": len(client.volumes.list(filters=selector)),
    }


class StatsSampler:
    """Samples a container's memory (minus page cache) and CPU until stopped."""

    def __init__(self, container: Container) -> None:
        self._container = container
        self._stop = threading.Event()
        self.peak_memory_mb = 0.0
        self.peak_cpu_percent = 0.0
        self.last_memory_mb = 0.0
        self._thread = threading.Thread(target=self._run, daemon=True)

    def __enter__(self) -> StatsSampler:
        self._thread.start()
        return self

    def __exit__(self, *_: object) -> None:
        self._stop.set()
        self._thread.join(timeout=5)

    def _run(self) -> None:
        try:
            for sample in self._container.stats(stream=True, decode=True):
                if self._stop.is_set():
                    return
                self._take(sample)
        except (APIError, NotFound, ValueError):
            return

    def _take(self, sample: dict[str, Any]) -> None:
        memory = sample.get("memory_stats", {})
        usage = memory.get("usage", 0) - memory.get("stats", {}).get("inactive_file", 0)
        if usage > 0:
            self.last_memory_mb = usage / 2**20
            self.peak_memory_mb = max(self.peak_memory_mb, self.last_memory_mb)
        cpu, pre = sample.get("cpu_stats", {}), sample.get("precpu_stats", {})
        cpu_delta = cpu.get("cpu_usage", {}).get("total_usage", 0) - pre.get("cpu_usage", {}).get(
            "total_usage", 0
        )
        system_delta = cpu.get("system_cpu_usage", 0) - pre.get("system_cpu_usage", 0)
        if cpu_delta > 0 and system_delta > 0:
            percent = cpu_delta / system_delta * cpu.get("online_cpus", 1) * 100
            self.peak_cpu_percent = max(self.peak_cpu_percent, percent)


def run_step(
    client: docker.DockerClient,
    name: str,
    image: str,
    command: list[str],
    run_id: str,
    repo: str,
    *,
    volumes: dict[str, dict[str, str]],
    network_mode: str | None = None,
    network: str | None = None,
    timeout_s: float = 600,
) -> Step:
    """Run one container to completion with limits and hardening, then remove it."""
    container = client.containers.create(
        image,
        command=command,
        user=APP_USER,
        environment=DOTNET_ENV,
        volumes=volumes,
        working_dir="/work",
        labels=labels(run_id, repo),
        network_mode=network_mode,
        network=network,
        mem_limit=MEMORY_LIMIT,
        nano_cpus=NANO_CPUS,
        **HARDENING,
    )
    try:
        start = time.perf_counter()
        container.start()
        with StatsSampler(container) as sampler:
            try:
                exit_code: int | None = container.wait(timeout=timeout_s)["StatusCode"]
            except Exception:
                exit_code = None
        seconds = time.perf_counter() - start
        log_tail = container.logs(tail=15).decode(errors="replace")
        return Step(
            name,
            round(seconds, 1),
            exit_code,
            round(sampler.peak_memory_mb),
            round(sampler.peak_cpu_percent),
            log_tail,
        )
    finally:
        container.remove(force=True)


def tar_directory(source: Path, prefix: str) -> bytes:
    """Tar `source` under `prefix`, owned by the app user, without bin/obj/.git."""
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w") as archive:
        for directory in (prefix, "out"):
            info = tarfile.TarInfo(directory)
            info.type, info.mode, info.uid, info.gid = tarfile.DIRTYPE, 0o755, 1654, 1654
            archive.addfile(info)

        def owned(info: tarfile.TarInfo) -> tarfile.TarInfo | None:
            parts = Path(info.name).parts
            if {"bin", "obj", ".git"} & set(parts):
                return None
            info.uid = info.gid = 1654
            info.uname = info.gname = "app"
            return info

        for path in sorted(source.rglob("*")):
            archive.add(
                path,
                arcname=f"{prefix}/{path.relative_to(source).as_posix()}",
                recursive=False,
                filter=owned,
            )
    return buffer.getvalue()


def prepare_volume(
    client: docker.DockerClient, volume: str, run_id: str, repo: str, payload: bytes
) -> float:
    """Copy `payload` (a tar) into `volume` through a stopped helper container."""
    start = time.perf_counter()
    helper = client.containers.create(
        HELPER_IMAGE,
        command=["true"],
        volumes={volume: {"bind": "/work", "mode": "rw"}},
        labels=labels(run_id, repo),
    )
    try:
        helper.put_archive("/work", payload)
    finally:
        helper.remove(force=True)
    return time.perf_counter() - start


def ensure_nuget_volume(client: docker.DockerClient, name: str, run_id: str) -> None:
    """Create the shared NuGet cache volume with a `packages` folder owned by the app user."""
    client.volumes.create(name=name, labels=labels(run_id, "nuget-cache"))
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w") as archive:
        info = tarfile.TarInfo("packages")
        info.type, info.mode, info.uid, info.gid = tarfile.DIRTYPE, 0o755, 1654, 1654
        archive.addfile(info)
    prepare_volume(client, name, run_id, "nuget-cache", buffer.getvalue())


@contextmanager
def probe_sidecar(
    client: docker.DockerClient, network: str, run_id: str, repo: str
) -> Iterator[Container]:
    sidecar = client.containers.run(
        PROBE_IMAGE,
        entrypoint=["sleep"],
        command=["3600"],
        detach=True,
        network=network,
        labels=labels(run_id, repo),
        user="100:101",
        mem_limit="64m",
        **HARDENING,
    )
    try:
        yield sidecar
    finally:
        sidecar.remove(force=True)


def curl_status(sidecar: Container, url: str, method: str = "GET", body: str | None = None) -> int:
    command = [
        "curl",
        "-s",
        "-o",
        "/dev/null",
        "-w",
        "%{http_code}",
        "--max-time",
        "2",
        "-X",
        method,
        url,
    ]
    if body is not None:
        command += ["-H", "Content-Type: application/json", "-d", body]
    result = sidecar.exec_run(command)
    text = result.output.decode(errors="replace").strip()
    return int(text) if text.isdigit() else 0


# ---------------------------------------------------------------- one repo end to end


def build_and_run_repo(
    client: docker.DockerClient,
    source: Path,
    run_id: str,
    repo: str,
    nuget_volume: str,
    *,
    mode: str = "copy",
    run_image: str = ASPNET_IMAGE,
    hold_s: float = 0.0,
) -> RepoReport:
    """Copy (or bind-mount), restore with network, publish offline, run on an internal network,
    probe from a sidecar, and always clean up."""
    report = RepoReport(repo=repo, mode=mode)
    total_start = time.perf_counter()
    work_volume = f"hwc-spike-work-{repo}-{run_id}"
    network_name = f"hwc-spike-net-{repo}-{run_id}"
    app: Container | None = None
    try:
        client.volumes.create(name=work_volume, labels=labels(run_id, repo))
        if mode == "copy":
            copy_s = prepare_volume(client, work_volume, run_id, repo, tar_directory(source, "src"))
            report.steps.append(Step("copy-in", round(copy_s, 2), 0))
            src_mount: dict[str, dict[str, str]] = {}
        else:
            prepare_volume(client, work_volume, run_id, repo, _out_only_tar())
            src_mount = {source.as_posix(): {"bind": "/work/src", "mode": "rw"}}
        mounts = {work_volume: {"bind": "/work", "mode": "rw"}, **src_mount}
        nuget_rw = {nuget_volume: {"bind": "/nuget", "mode": "rw"}}
        nuget_ro = {nuget_volume: {"bind": "/nuget", "mode": "ro"}}

        restore = run_step(
            client,
            "restore",
            SDK_IMAGE,
            ["dotnet", "restore", f"/work/src/{PROJECT_FILE}"],
            run_id,
            repo,
            volumes={**mounts, **nuget_rw},
        )
        report.steps.append(restore)
        if restore.exit_code != 0:
            report.failure = "restore_failed"
            return report
        if hold_s:
            time.sleep(hold_s)  # gives the interrupt scenario a window to kill the process
        build = run_step(
            client,
            "build",
            SDK_IMAGE,
            [
                "dotnet",
                "publish",
                f"/work/src/{PROJECT_FILE}",
                "--no-restore",
                "-c",
                "Release",
                "-o",
                "/work/out",
            ],
            run_id,
            repo,
            volumes={**mounts, **nuget_ro},
            network_mode="none",
        )
        report.steps.append(build)
        if build.exit_code != 0:
            report.failure = "build_failed"
            return report

        client.networks.create(
            network_name, driver="bridge", internal=True, labels=labels(run_id, repo)
        )
        app = client.containers.create(
            run_image,
            command=["dotnet", f"/work/out/{APP_DLL}"],
            user=APP_USER,
            environment={"ASPNETCORE_HTTP_PORTS": str(APP_PORT)},
            volumes={work_volume: {"bind": "/work", "mode": "ro"}},
            network=network_name,
            labels=labels(run_id, repo),
            mem_limit=MEMORY_LIMIT,
            nano_cpus=NANO_CPUS,
            read_only=True,
            tmpfs={"/tmp": "size=64m"},
            **HARDENING,
        )
        app.reload()
        app_alias = app.name
        with (
            probe_sidecar(client, network_name, run_id, repo) as sidecar,
            StatsSampler(app) as sampler,
        ):
            started = time.perf_counter()
            app.start()
            base = f"http://{app_alias}:{APP_PORT}"
            report.ready_seconds = wait_until_ready(app, sidecar, f"{base}/api/products", started)
            if report.ready_seconds is None:
                app.reload()
                report.failure = "startup_failed" if app.status == "exited" else "timeout"
                report.steps.append(
                    Step(
                        "run",
                        round(time.perf_counter() - started, 1),
                        app.attrs["State"].get("ExitCode"),
                        log_tail=app.logs(tail=15).decode(errors="replace"),
                    )
                )
                return report
            report.probes = {
                "GET /api/products": curl_status(sidecar, f"{base}/api/products"),
                "GET /api/products/999": curl_status(sidecar, f"{base}/api/products/999"),
                "POST /api/products": curl_status(
                    sidecar, f"{base}/api/products", "POST", '{"name":"Desk","price":250}'
                ),
            }
            time.sleep(3)
            report.run_memory_mb = round(sampler.last_memory_mb)
            report.steps.append(
                Step(
                    "run",
                    round(time.perf_counter() - started, 1),
                    None,
                    round(sampler.peak_memory_mb),
                    round(sampler.peak_cpu_percent),
                )
            )
        return report
    finally:
        if app is not None:
            app.remove(force=True)
        for network in client.networks.list(names=[network_name]):
            network.remove()
        for volume in client.volumes.list(filters={"name": work_volume}):
            volume.remove(force=True)
        report.total_seconds = round(time.perf_counter() - total_start, 1)


def _out_only_tar() -> bytes:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w") as archive:
        info = tarfile.TarInfo("out")
        info.type, info.mode, info.uid, info.gid = tarfile.DIRTYPE, 0o755, 1654, 1654
        archive.addfile(info)
    return buffer.getvalue()


def wait_until_ready(app: Container, sidecar: Container, url: str, started: float) -> float | None:
    """Poll until the app answers any HTTP status; None if it exits or times out."""
    while time.perf_counter() - started < READINESS_TIMEOUT_S:
        if curl_status(sidecar, url) > 0:
            return round(time.perf_counter() - started, 2)
        app.reload()
        if app.status == "exited":
            return None
        time.sleep(0.25)
    return None


# ---------------------------------------------------------------- workspace copies


def make_copy(workdir: Path, name: str, mutate: Callable[[Path], None] | None = None) -> Path:
    target = workdir / name
    if target.exists():
        shutil.rmtree(target)
    shutil.copytree(FIXTURE, target, ignore=shutil.ignore_patterns("bin", "obj"))
    if mutate is not None:
        mutate(target)
    return target


def break_build(path: Path) -> None:
    controller = path / "Controllers" / "ProductsController.cs"
    controller.write_text(
        controller.read_text().replace("return Ok(products);", "return Ok(produts);")
    )


def crash_on_start(path: Path) -> None:
    program = path / "Program.cs"
    program.write_text(
        program.read_text().replace(
            "app.Run();", 'throw new InvalidOperationException("startup failure for the spike");'
        )
    )


def never_listen(path: Path) -> None:
    program = path / "Program.cs"
    program.write_text(
        program.read_text().replace("app.Run();", "Thread.Sleep(Timeout.Infinite);\napp.Run();")
    )


def to_crlf(path: Path) -> None:
    for source in [*path.rglob("*.cs"), *path.rglob("*.csproj"), *path.rglob("*.json")]:
        data = source.read_bytes().replace(b"\r\n", b"\n").replace(b"\n", b"\r\n")
        source.write_bytes(data)


def add_long_path(path: Path) -> None:
    deep = path / "Features" / ("VeryLongFolderNameForTesting" * 4) / ("Nested" * 20)
    deep.mkdir(parents=True)
    (deep / "Helper.cs").write_text(
        "namespace SampleWebApi.Features;\npublic static class "
        "Helper { public static int One() => 1; }\n"
    )


# ---------------------------------------------------------------- scenarios


def scenario_environment(client: docker.DockerClient, **_: object) -> None:
    version, info = client.version(), client.info()
    record("docker_server_version", version.get("Version"))
    record("docker_os", info.get("OperatingSystem"))
    record("kernel", info.get("KernelVersion"))
    record("docker_cpus", info.get("NCPU"))
    record("docker_memory_gib", round(info.get("MemTotal", 0) / 2**30, 2))
    for image in (SDK_IMAGE, ASPNET_IMAGE, PROBE_IMAGE, HELPER_IMAGE):
        attrs = client.images.get(image).attrs
        record(
            f"image {image}",
            {
                "size_mb": round(attrs["Size"] / 1e6),
                "digest": (attrs.get("RepoDigests") or ["-"])[0],
                "user": attrs["Config"].get("User") or "root",
            },
        )


def scenario_restore(client: docker.DockerClient, run_id: str, workdir: Path, **_: object) -> None:
    copy = make_copy(workdir, "restore")
    cache = f"hwc-spike-nuget-restore-{run_id}"
    ensure_nuget_volume(client, cache, run_id)
    try:
        cold = build_and_run_repo(client, copy, run_id, "cold", cache)
        warm = build_and_run_repo(client, copy, run_id, "warm", cache)
        for label, report in (("cold", cold), ("warm", warm)):
            restore, build = report.step("restore"), report.step("build")
            record(
                f"{label}: restore_s / build_s / total_s",
                (restore and restore.seconds, build and build.seconds, report.total_seconds),
            )
            record(
                f"{label}: build peak memory MB / CPU %",
                (build and build.peak_memory_mb, build and build.peak_cpu_percent),
            )
        offline = run_step(
            client,
            "restore-offline",
            SDK_IMAGE,
            ["dotnet", "restore", "/tmp/none.csproj"],
            run_id,
            "offline",
            volumes={},
            network_mode="none",
            timeout_s=120,
        )
        record(
            "restore with --network none (no project, shows the no-network error path)",
            offline.exit_code,
        )
    finally:
        for volume in client.volumes.list(filters={"name": cache}):
            volume.remove(force=True)


def scenario_copy_vs_mount(
    client: docker.DockerClient, run_id: str, workdir: Path, nuget: str, **_: object
) -> None:
    for mode in ("copy", "mount"):
        copy = make_copy(workdir, f"mode-{mode}")
        report = build_and_run_repo(client, copy, run_id, f"mode-{mode}", nuget, mode=mode)
        restore, build = report.step("restore"), report.step("build")
        record(
            f"{mode}: restore_s / build_s / ready_s / total_s / failure",
            (
                restore and restore.seconds,
                build and build.seconds,
                report.ready_seconds,
                report.total_seconds,
                report.failure,
            ),
        )
        if mode == "mount":
            written = sorted(p.name for p in copy.iterdir())
            record("mount: entries in the Windows working copy after build", written)


def scenario_images(
    client: docker.DockerClient, run_id: str, workdir: Path, nuget: str, **_: object
) -> None:
    copy = make_copy(workdir, "images")
    for short_name, image in (("aspnet", ASPNET_IMAGE), ("sdk", SDK_IMAGE)):
        report = build_and_run_repo(
            client, copy, run_id, f"img-{short_name}", nuget, run_image=image
        )
        record(
            f"run on {image}: ready_s / steady MB / probes",
            (report.ready_seconds, report.run_memory_mb, report.probes),
        )


def scenario_pipeline(
    client: docker.DockerClient,
    run_id: str,
    workdir: Path,
    nuget: str,
    hold_s: float = 0.0,
    **_: object,
) -> None:
    copy = make_copy(workdir, "pipeline")
    report = build_and_run_repo(client, copy, run_id, "pipeline", nuget, hold_s=hold_s)
    record("pipeline report", asdict(report))
    if report.failure or report.probes.get("GET /api/products") != 200:
        raise RuntimeError(f"pipeline failed: {report.failure} {report.probes}")


def scenario_parallel(
    client: docker.DockerClient, run_id: str, workdir: Path, nuget: str, **_: object
) -> None:
    copies = [make_copy(workdir, f"par-{index}") for index in range(3)]
    start = time.perf_counter()
    sequential = [
        build_and_run_repo(client, c, run_id, f"seq-{i}", nuget) for i, c in enumerate(copies)
    ]
    sequential_s = time.perf_counter() - start
    reports: list[RepoReport] = []
    threads = [
        threading.Thread(
            target=lambda c=c, i=i: reports.append(
                build_and_run_repo(client, c, run_id, f"par-{i}", nuget)
            )
        )
        for i, c in enumerate(copies)
    ]
    start = time.perf_counter()
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    parallel_s = time.perf_counter() - start
    record("sequential wall time s (3 repos)", round(sequential_s, 1))
    record("parallel wall time s (3 repos)", round(parallel_s, 1))
    record(
        "parallel: per-repo build_s", [r.step("build").seconds for r in reports if r.step("build")]
    )
    record(
        "parallel: sum of build peak memory MB",
        sum(r.step("build").peak_memory_mb for r in reports if r.step("build")),
    )
    record("parallel: sum of run steady memory MB", sum(r.run_memory_mb for r in reports))
    record("parallel: failures", [r.failure for r in [*sequential, *reports] if r.failure])


def scenario_failures(
    client: docker.DockerClient, run_id: str, workdir: Path, nuget: str, **_: object
) -> None:
    cases = {
        "broken-build": break_build,
        "startup-crash": crash_on_start,
        "never-listens": never_listen,
    }
    global READINESS_TIMEOUT_S
    saved, READINESS_TIMEOUT_S = READINESS_TIMEOUT_S, 20.0
    try:
        for name, mutate in cases.items():
            report = build_and_run_repo(
                client, make_copy(workdir, name, mutate), run_id, name, nuget
            )
            last = report.steps[-1]
            record(
                f"{name}: failure / last step / exit code",
                (report.failure, last.name, last.exit_code),
            )
            record(f"{name}: log tail", last.log_tail.strip().splitlines()[-4:])
            record(f"{name}: leftovers after cleanup", leftovers_for(client, run_id, name))
    finally:
        READINESS_TIMEOUT_S = saved


def leftovers_for(client: docker.DockerClient, run_id: str, repo: str) -> int:
    selector = {"label": [f"{LABEL}.run={run_id}", f"{LABEL}.repo={repo}"]}
    return (
        len(client.containers.list(all=True, filters=selector))
        + len(client.networks.list(filters=selector))
        + len(client.volumes.list(filters=selector))
    )


def scenario_interrupt(client: docker.DockerClient, **_: object) -> None:
    """Hard-kill a child spike mid-run (finally never runs), then show the label sweep."""
    child = subprocess.Popen(
        [sys.executable, __file__, "--scenario", "pipeline", "--hold", "60", "--no-sweep"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    deadline = time.time() + 240
    while time.time() < deadline and leftovers(client)["volumes"] < 2:
        time.sleep(1)
    time.sleep(3)
    child.kill()
    child.wait()
    record("after hard kill: leftovers by label", leftovers(client))
    record("startup sweep removed", sweep(client))
    record("after sweep: leftovers by label", leftovers(client))


def scenario_windows(
    client: docker.DockerClient, run_id: str, workdir: Path, nuget: str, **_: object
) -> None:
    for name, mutate in (("crlf", to_crlf), ("long-path", add_long_path)):
        try:
            copy = make_copy(workdir, name, mutate)
        except OSError as error:
            record(f"{name}: could not create the copy on Windows", str(error))
            continue
        report = build_and_run_repo(client, copy, run_id, name, nuget)
        record(
            f"{name}: failure / build_s / probes",
            (report.failure, report.step("build") and report.step("build").seconds, report.probes),
        )


def scenario_network(client: docker.DockerClient, run_id: str, **_: object) -> None:
    """Compare egress and host reachability for three network setups."""
    # By IP: DNS fails in the Alpine/musl images on this Docker Desktop even with egress.
    egress_cmd = ["-s", "-o", "/dev/null", "--max-time", "5", "http://1.1.1.1"]
    setups = {
        "default bridge": {},
        "internal network": {"internal": True},
        "bridge without masquerade": {
            "options": {"com.docker.network.bridge.enable_ip_masquerade": "false"}
        },
    }
    for name, options in setups.items():
        network = client.networks.create(
            f"hwc-spike-nettest-{len(name)}-{run_id}",
            driver="bridge",
            labels=labels(run_id, "net"),
            **options,
        )
        try:
            probe = client.containers.run(
                PROBE_IMAGE,
                command=egress_cmd,
                network=network.name,
                labels=labels(run_id, "net"),
                detach=True,
            )
            exit_code = probe.wait(timeout=30)["StatusCode"]
            probe.remove(force=True)
            server = client.containers.run(
                HELPER_IMAGE,
                command=["httpd", "-f", "-p", "8080"],
                network=network.name,
                labels=labels(run_id, "net"),
                detach=True,
                ports={"8080/tcp": ("127.0.0.1", None)},
            )
            try:
                time.sleep(1.5)
                server.reload()
                binding = (server.attrs["NetworkSettings"]["Ports"] or {}).get("8080/tcp")
                host_status = host_get(binding[0]["HostPort"]) if binding else "no port published"
            finally:
                server.remove(force=True)
            record(
                f"{name}: egress to internet / host reaches published port",
                ("allowed" if exit_code == 0 else "blocked", host_status),
            )
        finally:
            network.remove()


def host_get(port: str) -> str:
    # http.client, not urllib: urlopen builds an SSL context even for http URLs, and loading
    # the Windows certificate store hangs on this machine.
    connection = http.client.HTTPConnection("127.0.0.1", int(port), timeout=3)
    try:
        connection.request("GET", "/")
        return f"HTTP {connection.getresponse().status}"
    except OSError as error:
        return f"unreachable ({type(error).__name__})"
    finally:
        connection.close()


SCENARIOS: dict[str, Callable[..., None]] = {
    "environment": scenario_environment,
    "restore": scenario_restore,
    "copy-vs-mount": scenario_copy_vs_mount,
    "images": scenario_images,
    "network": scenario_network,
    "pipeline": scenario_pipeline,
    "parallel": scenario_parallel,
    "failures": scenario_failures,
    "windows": scenario_windows,
    "interrupt": scenario_interrupt,
}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else None)
    parser.add_argument(
        "--scenario",
        action="append",
        choices=[*SCENARIOS, "all"],
        default=None,
        help="scenario to run (repeatable; default: all)",
    )
    parser.add_argument("--hold", type=float, default=0.0, help=argparse.SUPPRESS)
    parser.add_argument("--no-sweep", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument(
        "--output", type=Path, default=Path(tempfile.gettempdir()) / "hwc-spike-results.json"
    )
    args = parser.parse_args()
    chosen = args.scenario or ["all"]
    names = list(SCENARIOS) if "all" in chosen else chosen

    client = docker.from_env(timeout=600)
    run_id = uuid.uuid4().hex[:8]
    workdir = Path(tempfile.gettempdir()) / f"hwc-spike-{run_id}"
    workdir.mkdir(parents=True)
    if not args.no_sweep:
        print(f"startup sweep removed: {sweep(client)}")
    nuget = f"hwc-spike-nuget-{run_id}"
    ensure_nuget_volume(client, nuget, run_id)
    failed: list[str] = []
    try:
        for name in names:
            print(f"\n== {name}")
            start = time.perf_counter()
            try:
                SCENARIOS[name](
                    client=client, run_id=run_id, workdir=workdir, nuget=nuget, hold_s=args.hold
                )
            except Exception as error:
                failed.append(name)
                record(f"{name} FAILED", f"{type(error).__name__}: {error}")
            print(f"   ({time.perf_counter() - start:.0f} s)")
    finally:
        if not args.no_sweep:
            print(f"\nfinal sweep removed: {sweep(client)}")
        shutil.rmtree(workdir, ignore_errors=True)
        args.output.write_text(json.dumps(RESULTS, indent=2, default=str), encoding="utf-8")
        print(f"results written to {args.output}")
    if failed:
        print(f"scenarios that failed: {', '.join(failed)}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
