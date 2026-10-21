# 0015. Docker run spike: how stage 5b builds and runs student projects

- **Status:** Accepted
- **Date:** 2026-10-21

## Context

Building and running student .NET 9 Web API projects in Docker on Windows is the highest-risk part of the system (design §9, §15). Stage 5a was a time-boxed spike to answer the open questions with measurements before stage 5b builds the real `DockerCodeRunner`. The course has no database yet, so SQL Server was out of scope, and there is no real student repo yet, so the spike used `tests/fixtures/sample_webapi/`: a second-year-level controller API with GET all, GET by id (404 when missing) and POST (201), and one NuGet package (`Microsoft.AspNetCore.OpenApi`).

The spike is `spikes/docker_run/run_spike.py` (see its README). Every number below comes from running it on the machine described in the table.

## Measurements

**Machine:** Intel i7-1185G7 (4 cores / 8 threads), 15.7 GiB RAM, Windows 11 Pro build 26200, `LongPathsEnabled=1`. **Docker:** Docker Desktop 4.43.2, engine 28.3.2, WSL2 backend (kernel 6.6.87.2-microsoft-standard-WSL2), 8 CPUs and 7.62 GiB allocated to Docker. **Limits used for student containers:** 1 GiB memory, 2 CPUs, 512 PIDs, all capabilities dropped, `no-new-privileges`, non-root user 1654.

| Measurement | Value |
| --- | --- |
| Image `mcr.microsoft.com/dotnet/sdk:9.0` (SDK 9.0.319, runtime 9.0.21) | 1.24 GB unpacked; pulled in 95 s; digest `sha256:d9ad72f6b3eb5d61adb158db510ae1c3098c12c92d23ab8da833418275f1c652` |
| Image `mcr.microsoft.com/dotnet/aspnet:9.0` | 329 MB unpacked; pulled in 3 s; digest `sha256:88fcfc1b81c43afa15ba3c138c57d77f77c1cb5118ae9d7024c94e82e2895aa5` |
| Probe sidecar image `curlimages/curl:8.10.1` | 32 MB |
| Copy the working copy into a volume (tar via `put_archive`) | 0.34–0.56 s |
| NuGet restore, cold cache (with network) | 14.6–19.5 s |
| NuGet restore, warm cache | 5.5–7.7 s |
| Publish, offline (`--network none`), after a cold restore | 16.3–17.6 s |
| Publish, offline, after a warm restore | 8.8–12.8 s (5.8–9.0 s on later runs) |
| Publish with a bind-mounted Windows working copy (warm) | 11.8 s (copy mode in the same run: 12.8 s) |
| Container start to first HTTP response | 2.1–5.1 s (5.1 s on the very first run) |
| End to end per repo, cold (copy, restore, publish, run, probes, cleanup) | 43.7–50 s |
| End to end per repo, warm | 24.6–30.5 s |
| Restore step peak memory / CPU | 78–86 MB / 108–119 % |
| Publish step peak memory / CPU | 165–181 MB / 169–204 % (at the 2-CPU limit) |
| Running app steady memory / startup peak CPU | 25–38 MB / 124 % |
| Running on `aspnet:9.0` vs `sdk:9.0` | ready 2.5 s vs 2.8 s; steady memory 38 MB vs 25 MB (within noise) |
| 3 repos one after another (warm) | 62.8 s wall |
| 3 repos in parallel (warm) | 28.3 s wall; publish 8.8–9.0 s each; sum of publish peaks 506 MB; sum of running apps 114 MB |
| Probes from the sidecar | `GET /api/products` 200, `GET /api/products/999` 404, `POST /api/products` 201, every run |
| Egress: default bridge / `internal` network / bridge without masquerade | allowed / blocked / **allowed** |
| Host reaches a published port: default bridge / `internal` / no masquerade | yes / no port is published / yes |
| Build error | publish exit 1, `error CS0103: The name 'produts' does not exist…` |
| Startup crash (unhandled exception before `app.Run()`) | app exits with 139, log `Unhandled exception. System.InvalidOperationException…` |
| App that never listens | still running at the readiness timeout, no log output |
| Leftovers after each failure case (try/finally) | 0 containers, networks, volumes |
| Leftovers after a hard kill mid-run | 1 container, 3 volumes; removed by the label sweep, then 0 |
| CRLF sources / a path longer than 260 characters | both build and answer all probes |

## Answers to questions 1–10

1. **Image.** Build on `sdk:9.0`, run on `aspnet:9.0`. The runtime image is a quarter of the size (329 MB vs 1.24 GB) and has no compiler or SDK tools, so a student app has less to work with. Readiness and memory were the same within noise. Pin both images **by digest** in configuration, so a patch release never changes results in the middle of a semester; update the digests deliberately.
2. **Getting the code in.** Copy, don't bind-mount. Copying the working copy into a per-repo volume took about half a second, and publish times were the same as with a bind mount for this project size. The bind mount wrote `bin/` and `obj/` into the Windows working copy, which would leak into diffs and the fixes branch (design §9), and it gives the student's build write access to a host folder. No permission, path-translation or file-watching problems were seen with the bind mount, but copying avoids the Windows file system (slower for large repos) altogether. The tar is written with the app user's ownership, so no `chown` step is needed.
3. **NuGet restore vs. no network.** Split into two containers. Restore runs with network on the default bridge, writing to a shared NuGet cache volume (`NUGET_PACKAGES=/nuget/packages`). Publish then runs with `--network none` and the cache mounted read-only, using `--no-restore`. Cold restore took 14.6–19.5 s, warm 5.5–7.7 s. **Risk:** restore evaluates the student's project file, and MSBuild can run targets during restore, so student-controlled code runs with network access during restore. Mitigations: non-root, resource and time limits, and no secrets in its environment. The shared cache is writable during restore, so it is protected only by NuGet's package hash checks; a per-round cache volume would limit how long anything written there lives, at the cost of a cold restore per round. Students write a few dozen lines and AI use is forbidden, so this residual risk is acceptable for v1 (see "Decisions needed" in the stage report).
4. **Reaching the app without internet access.** Use an `internal` network per repo, with a small probe sidecar on the same network. It blocks egress, and the probes worked every time. Rejected alternatives: a bridge with masquerade disabled (it does **not** block egress on Docker Desktop, so it offers no isolation); publishing a port on an internal network (Docker publishes nothing); probes inside the app container (the runtime image has no HTTP client, and adding one to the student's container widens its attack surface). Host networking was not considered, because it removes isolation. Trade-off: the student app can reach the sidecar, which only sleeps and runs `curl` on request, as a non-root user with a 64 MB limit.
5. **Readiness.** The official images already set `ASPNETCORE_HTTP_PORTS=8080`. Running the published DLL (`dotnet App.dll`) ignores the student's `launchSettings.json`, whose `applicationUrl` would otherwise override the port, so **never use `dotnet run`**. The app answered 2.1–5.1 s after the container started. Treat any HTTP status (even 404) as "listening". Poll every 0.25–0.5 s, stop early if the container exits, and use a **30 s** readiness timeout (about six times the slowest measured start).
6. **Resources.** Publish is the peak: 165–181 MB and the full 2-CPU limit. Restore stays under 90 MB, and the running app needs 25–38 MB. Three repos in parallel took 28.3 s instead of 62.8 s sequentially, with 506 MB summed at the publish peak. With 7.62 GiB and 8 CPUs given to Docker, 3 parallel repos (the configured default) is comfortable, and 4 would be too. Recommended limits: build and restore `--memory 1g --cpus 2`, run `--memory 512m --cpus 1`, `--pids-limit 512` for all. EF Core projects later will need a re-measurement.
7. **Timing.** Cold: restore 14.6–19.5 s, publish 16.3–17.6 s, 44–50 s end to end. Warm: restore 5.5–7.7 s, publish 8.8–12.8 s, 25–31 s end to end. For 40 repos, 3 at a time, that is roughly 8–10 minutes per round once the cache is warm, plus pulling the images once (about 100 s).
8. **Cleanup.** Every per-repo container, network and volume is removed in `finally`; the build-error, startup-crash and timeout cases each left 0 resources. A hard kill (where `finally` never runs) left 1 container and 3 volumes, and a sweep by label removed them all. Proposed labels: `hw-checker=1`, `hw-checker.run=<run_id>`, `hw-checker.repo=<repo_id>` and `hw-checker.kind=work|network|app|probe|cache`. At round start, sweep everything labelled `hw-checker=1` except `kind=cache`. The spike's sweep also removed the shared NuGet cache, which costs a cold restore, so the cache needs its own label and is cleaned only on request.
9. **Failure classification.** Build error: publish exits non-zero with `error CS…` or `error MSB…` in the log, which maps to `build_failed`. Restore error: restore exits non-zero; `NU1101`/`NU1102` (a package or version that doesn't exist) is the student's `build_failed`, while `NU1301` (source unreachable) is `environment_error`. Startup crash: the app container exits before it answers (139 was observed for an unhandled exception), which maps to `startup_failed`. Timeout: the container is still running at the readiness timeout, which maps to `timeout`. An engine that doesn't answer, a missing image, or `State.OOMKilled=true` maps to `environment_error`. **Pre-build failures** (no `.csproj`, or several `Microsoft.NET.Sdk.Web` projects) cannot be recorded today, because `ExecutionResult` requires a failed build or run step whenever a `failure_kind` is set. The proposal below adds a `prepare` step.
10. **Windows specifics.** CRLF sources and a path over 260 characters both built and ran (the copy goes through Python's tar, and the files land on the Linux side). Problems found and worked around:
    - `urllib.request.urlopen` hangs on this machine even for `http://`, because it builds an SSL context and loading the Windows certificate store hangs. Use `http.client`, or keep probing through the sidecar.
    - DNS fails in Alpine/musl images (busybox, curl) on Docker Desktop's default bridge, while the Debian-based .NET images resolve fine. The egress check therefore uses an IP address.
    - The Docker engine hung after the C: drive filled up and needed a Docker Desktop restart. 5b should ping the engine with a timeout before a round and report `environment_error` if it doesn't answer.
    - `dotnet` prints a harmless "An issue was encountered verifying workloads" warning; it must not be mistaken for an error when the logs are read.

## Decision

Stage 5b implements `DockerCodeRunner` with the Docker SDK for Python as this pipeline per repo:

1. **Prepare:** find the single `Microsoft.NET.Sdk.Web` project in the working copy (pre-build failure if there are none or several). Create a labelled work volume and copy the working copy into it as a tar owned by UID 1654, without `bin/`, `obj/` and `.git/`.
2. **Restore:** `sdk:9.0` (pinned by digest), user 1654, default bridge network, the shared NuGet cache volume read-write, `dotnet restore <project>`, with limits and a timeout.
3. **Build:** `sdk:9.0`, `--network none`, cache read-only, `dotnet publish <project> --no-restore -c Release -o /work/out`.
4. **Run:** `aspnet:9.0` (pinned by digest), user 1654, a per-repo `internal` network, the work volume read-only, read-only root file system with a small `/tmp` tmpfs, `dotnet /work/out/<App>.dll`, `ASPNETCORE_HTTP_PORTS=8080`, `--memory 512m --cpus 1`.
5. **Probe:** a `curlimages/curl` sidecar on the same internal network, polled every 0.25–0.5 s for readiness (30 s timeout), then the assignment's probes in order.
6. **Cleanup:** remove the app, sidecar, network and work volume in `finally`; sweep by label at the start of every round.

Image digests, limits, timeouts and the parallelism are configuration. Full build and run logs go to `workspace/<run_id>/<repo_id>/`, and only an excerpt goes into `ExecutionResult`.

## Proposed changes to the ports and to ExecutionResult (signatures only)

```python
# domain/execution.py
class StepStatus(StrEnum): ...              # unchanged: succeeded, failed, skipped

class FailureKind(StrEnum):                 # add one value
    ...
    PROJECT_NOT_FOUND = "project_not_found"  # no .csproj, or no single Web project (prepare step)

class ExecutionResult(DomainModel):
    prepare_status: StepStatus = StepStatus.SUCCEEDED   # new: project discovery and copy-in
    build_status: StepStatus                             # restore + publish
    run_status: StepStatus
    failure_kind: FailureKind | None = None              # invariant now covers all three steps
    logs_excerpt: str = ""
    logs_path: Path | None = None
    probe_results: tuple[ProbeResult, ...] = ()

class ExecutionRequest(DomainModel):                     # new
    run_id: Identifier
    repo_id: Identifier
    working_copy: Path
    probes: tuple[HttpProbe, ...]
    environment: tuple[tuple[str, str], ...] = ()        # e.g. connection strings later
    logs_dir: Path

# ports/execution.py
class CodeRunner(Protocol):
    def check_environment(self) -> None: ...             # ExecutionEnvironmentError if the engine
                                                         # doesn't answer or an image is missing
    def execute(self, request: ExecutionRequest) -> ExecutionResult: ...
                                                         # prepare, restore, build, run, probe,
                                                         # always clean up
    def sweep_leftovers(self) -> int: ...                # remove labelled resources from crashed runs

class DatabaseProvisioner(Protocol): ...                 # unchanged and still provisional (no database yet)
```

Whether `PROJECT_NOT_FOUND` counts as a student issue (`is_student_issue`) is a decision for the developer.

## Consequences

- Student code never runs on the host, never has network access during build or run, and never touches the Windows working copy.
- A round needs about 1.6 GB of images and a NuGet cache volume. Warm repos take 25–31 s each, so a round of 40 repos at 3 in parallel takes about 8–10 minutes.
- The restore step is the one place where student-controlled MSBuild runs with network access. It is accepted for v1, with the mitigations above.
- `ExecutionResult` and the `CodeRunner` port change in stage 5b, as proposed above.
- EF Core and SQL Server projects will need a new measurement when the course reaches them.

## Rejected alternatives

- **Running on `sdk:9.0` only.** One image fewer, but four times larger, with the full toolchain available to student code, for no measured gain.
- **Bind-mounting the Windows working copy.** It wrote `bin/` and `obj/` into the working copy and gives student code write access to a host folder. Copying cost about half a second.
- **Restoring inside the build container with network.** The build and the student's code would then run with internet access.
- **A bridge network with masquerade disabled.** It did not block egress on Docker Desktop.
- **Host networking or publishing ports.** Host networking removes isolation; ports cannot be published on an internal network.
- **Probes inside the app container.** The runtime image has no HTTP client, and adding tools to the student's container widens its attack surface.
- **`dotnet run`.** It applies the student's `launchSettings.json`, which can move the app to another port.
