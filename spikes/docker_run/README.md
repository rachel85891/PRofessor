# Docker run spike (stage 5a)

A throwaway experiment that builds and runs the sample .NET 9 Web API
(`tests/fixtures/sample_webapi/`) in Docker on Windows, and measures what stage 5b needs to
know. The results and the recommended design are in
[docs/adr/0015-docker-run-spike.md](../../docs/adr/0015-docker-run-spike.md).

This is not production code: no tests, not part of the `src/` layers.

## Prerequisites

- Docker Desktop running (WSL2 backend), reachable over the default named pipe.
- The images are pulled automatically on first use, or ahead of time with
  `docker pull mcr.microsoft.com/dotnet/sdk:9.0` (plus `aspnet:9.0`, `curlimages/curl:8.10.1`,
  `busybox:1.37`).
- Network access for the NuGet restore step.

## Running

```powershell
uv run python spikes/docker_run/run_spike.py                       # all scenarios
uv run python spikes/docker_run/run_spike.py --scenario pipeline   # one scenario (repeatable)
```

It prints each measurement as it goes and writes all of them to a JSON file (path printed at
the end; `--output` to choose it). The exit code is non-zero if a scenario failed.

## What each scenario measures

| Scenario | Measures |
| --- | --- |
| `environment` | Docker version, backend, CPUs and memory, image sizes, digests and users |
| `restore` | Cold vs warm NuGet restore and build times; restore without network |
| `copy-vs-mount` | Copying the code into a volume vs bind-mounting the Windows working copy |
| `images` | Running on `aspnet:9.0` vs `sdk:9.0`: readiness time and memory |
| `network` | Egress and host reachability for default bridge, internal network, bridge without masquerade |
| `pipeline` | One repo end to end: copy, restore, offline publish, run, readiness, probes |
| `parallel` | Three repos one after another vs in parallel: wall time, memory |
| `failures` | Build error, startup crash and a never-listening app: exit codes, logs, cleanup |
| `windows` | CRLF sources and a very long path |
| `interrupt` | Hard-kills a child run mid-build, then shows the label sweep removing the leftovers |

## Safety and cleanup

- Student containers run as the non-root `app` user (UID 1654), with `--memory`, `--cpus`,
  a PID limit, all capabilities dropped and `no-new-privileges`. Never `--privileged`, never
  the Docker socket.
- Build and run have no external network: build uses `--network none`, run uses an
  `internal` network, and the probes come from a sidecar on that network.
- Everything is labelled `hw-checker.spike=1` and removed in `finally`; a sweep by label at
  start removes leftovers from a crashed run.
