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

## Folder layout

```
config.yaml          non-secret settings (secrets go in .env)
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
  ui/                Streamlit app
tests/unit/          unit tests (integration tests are marked `integration`)
```

Dependencies point inward only; see the layer table in [CLAUDE.md](CLAUDE.md).

## Documentation

- [docs/design.md](docs/design.md): system design and stage plan
- [docs/adr/](docs/adr/): architecture decision records
- [CLAUDE.md](CLAUDE.md): working rules for the implementing agent
- [docs/planner-guide.md](docs/planner-guide.md): guide for the planner/reviewer agent
