# HW Checker

HW Checker is a Python LangGraph agent that checks homework for a .NET 10 Web API course. It builds and runs each student's repo, checks it against the requirements of every assignment so far, and writes a Hebrew report for the reviewer. After manual approval it can also propose fixes as a PR.

"HW Checker" is a working name; the final project name is still open.

## Prerequisites

- Windows with PowerShell or Git Bash
- [Docker Desktop](https://www.docker.com/products/docker-desktop/)
- [.NET 10 SDK](https://dotnet.microsoft.com/download/dotnet/10.0)
- Python 3.12 (uv installs it if it is missing)
- [uv](https://docs.astral.sh/uv/)

## Setup

```powershell
uv sync                              # creates .venv with Python 3.12 and all dependencies
uv run pre-commit install            # installs the git pre-commit hook
Copy-Item .env.example .env          # Git Bash: cp .env.example .env
```

Fill in the secrets in `.env`. Non-secret settings live in `config.yaml`. Any setting can be overridden by an environment variable with the `HW_CHECKER_` prefix and `__` for nesting, for example `HW_CHECKER_LOGGING__LEVEL=DEBUG`.

## Running the checks

```powershell
uv run ruff check
uv run ruff format --check
uv run mypy
uv run lint-imports                  # layer contracts (see [tool.importlinter] in pyproject.toml)
uv run pytest -m "not integration"   # unit tests
uv run pytest -m integration         # Docker / Git / LLM tests, run locally on demand
uv run pre-commit run --all-files
```

If console-script launchers hang on your machine, run the Python tools as modules instead, for example `uv run python -m mypy` or `uv run python -m pytest`. import-linter has no module entry point; use `uv run python -c "from importlinter.cli import lint_imports_command; lint_imports_command()"`.

## Running a round

Start Docker Desktop first. The roster `.xlsx` file holds students' names, so keep it outside the repo.

### From the command line

```powershell
uv run python -m hw_checker round --assignment hw01 --roster C:\path\to\roster.xlsx
uv run python -m hw_checker round --assignment hw01 --roster C:\path\to\roster.xlsx --limit 3
uv run python -m hw_checker round --assignment hw01 --roster C:\path\to\roster.xlsx --repo owner__name --repo other__name
```

The round checks `_general` and every assignment up to the one given, saves the run in the database (`storage.database_url`), and prints one line per repo (status, findings by severity, checks not evaluated, errors), the roster warnings and the run id. Clones go to `workspace/clones/`, build and run logs to `workspace/runs/<run_id>/logs/<repo_id>/`, and the graph's checkpoints to `workspace/checkpoints.sqlite` (thread id = run id). The exit code is 0 when the round completed and 1 when it failed or could not start.

### From LangGraph Studio

```powershell
$env:LANGGRAPH_CLI_NO_ANALYTICS = "1"   # Git Bash: export LANGGRAPH_CLI_NO_ANALYTICS=1
uv run python -m langgraph_cli dev --no-browser --allow-blocking --no-reload
```

Then open <https://smith.langchain.com/studio/?baseUrl=http://127.0.0.1:2024>, choose the graph `round`, fill in `assignment_id` and `roster_path` (optionally `repo_ids` and `limit` for a quick round), and submit. Each repo passes through `check_repo`: `fetch_repo`, `build_and_run`, one `assignment_<id>` node per assignment, and `record_repo`. Results are saved in the database as for a command-line round.

- `python -m langgraph_cli` is used because the `langgraph` launcher (`.exe`) hangs on some machines.
- `--allow-blocking`: the graph is built (database migration, Docker connection, assignment files) when the server first needs it, which the dev server would otherwise refuse as blocking I/O.
- `--no-reload`: otherwise the server restarts whenever any file changes, including files in `.venv`. The graph is built from the assignment files when the server starts, so restart it after adding or editing an assignment.
- Privacy: the round's state holds students' names and findings. LangSmith tracing stays off: `langgraph.json` sets `LANGSMITH_TRACING=false`, and the program refuses to run if a tracing switch is on or `LANGSMITH_API_KEY` / `LANGCHAIN_API_KEY` is set. The Studio page is served by smith.langchain.com, but it reads the state from your local server in the browser; the dev server keeps it on this machine in `.langgraph_api/` (git-ignored). Do not put `LANGSMITH_*` variables in `.env`.

## Folder layout

```
config.yaml          non-secret settings (secrets go in .env)
langgraph.json       the round graph for LangGraph Studio
docs/                design document, planner guide, ADRs
src/hw_checker/
  domain/            pure models and business rules
  ports/             interfaces for external capabilities
  adapters/          implementations of the ports
  services/          application logic, one responsibility each
  checks/, judge/    registered checks and the judge rails
  graph/             LangGraph state, thin nodes, builders
  reporting/         reports, grades file, feedback
  config/            settings and logging
  ui/                Streamlit app (deferred, ADR 0017)
  bootstrap.py       composition root: creates adapters, builds the graph
  cli.py             `python -m hw_checker round ...`
tests/unit/          unit tests (integration tests are marked `integration`)
```

Dependencies point inward only; see the layer table in [CLAUDE.md](CLAUDE.md).

## Documentation

- [docs/design.md](docs/design.md): system design and stage plan
- [docs/adr/](docs/adr/): architecture decision records
- [CLAUDE.md](CLAUDE.md): working rules for the implementing agent
- [docs/planner-guide.md](docs/planner-guide.md): guide for the planner/reviewer agent
