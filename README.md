# HW Checker

HW Checker is a Python LangGraph agent that checks homework for a .NET 10 Web API course. It builds and runs each student's repo, checks it against the requirements of every assignment so far, and writes an English report for the reviewer. After manual approval it can also propose fixes as a PR.

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

## The LLM key

The LLM layer (ADR 0020) calls an OpenAI-compatible endpoint, OpenRouter for development. Nothing in a round needs it yet. To set it up:

1. Create a key at <https://openrouter.ai/settings/keys>.
2. Put it in `.env` as `HW_CHECKER_LLM__API_KEY=<your key>` (never in `config.yaml` or the repo).
3. Check it:

```powershell
uv run python -m hw_checker llm-check
```

It sends one tiny synthetic prompt (no student data) to the model of each role in `config.yaml` (`llm.roles`) and prints the role, the model, ok or the error, and the time taken. The exit code is 0 only when every role answered.

## Running a round

Start Docker Desktop first. The roster `.xlsx` file holds students' names, so keep it outside the repo.

### From the command line

```powershell
uv run python -m hw_checker round --assignment hw01 --roster C:\path\to\roster.xlsx
uv run python -m hw_checker round --assignment hw01 --roster C:\path\to\roster.xlsx --limit 3
uv run python -m hw_checker round --assignment hw01 --roster C:\path\to\roster.xlsx --repo owner__name --repo other__name
```

The round checks `_general` and every assignment up to the one given, saves the run in the database (`storage.database_url`), and prints one line per repo (status, findings by severity, checks not evaluated, errors), the roster warnings, the run id and the path of the round's report. Reports go to `workspace/runs/<run_id>/reports/`: one English Markdown report per repo, `round.md` (one row per repo, in roster order) and `counts.csv` (counts only, no grade yet; UTF-8 with a BOM for Excel). They hold students' names and findings, so keep them out of the repo. Clones go to `workspace/clones/`, build and run logs to `workspace/runs/<run_id>/logs/<repo_id>/`, and the graph's checkpoints to `workspace/checkpoints.sqlite` (thread id = run id). The exit code is 0 when the round completed and 1 when it failed or could not start.

### From LangGraph Studio

```powershell
$env:LANGGRAPH_CLI_NO_ANALYTICS = "1"   # Git Bash: export LANGGRAPH_CLI_NO_ANALYTICS=1
uv run python -m langgraph_cli dev --no-browser --allow-blocking --no-reload
```

Then open <https://smith.langchain.com/studio/?baseUrl=http://127.0.0.1:2024>, choose the graph `round`, fill in `assignment_id` and `roster_path` (optionally `repo_ids` and `limit` for a quick round), and submit. Each repo passes through `check_repo`: `fetch_repo`, `build_and_run`, one `assignment_<id>` node per assignment, `record_repo` and `write_report`; then `finish_round` and `write_round_report`. Results are saved in the database and the reports written as for a command-line round, and the output's `summary` lists each repo's findings.

- `python -m langgraph_cli` is used because the `langgraph` launcher (`.exe`) hangs on some machines.
- `--allow-blocking`: the graph is built (database migration, Docker connection, assignment files) when the server first needs it, which the dev server would otherwise refuse as blocking I/O.
- `--no-reload`: otherwise the server restarts whenever any file changes, including files in `.venv`. The graph is built from the assignment files when the server starts, so restart it after adding or editing an assignment.
- Privacy: the round's state holds students' names and findings. LangSmith tracing stays off: `langgraph.json` sets `LANGSMITH_TRACING=false`, and the program refuses to run if a tracing switch is on or `LANGSMITH_API_KEY` / `LANGCHAIN_API_KEY` is set. The Studio page is served by smith.langchain.com, but it reads the state from your local server in the browser; the dev server keeps it on this machine in `.langgraph_api/` (git-ignored). Do not put `LANGSMITH_*` variables in `.env`.

## The fixer: review branches

The fixer (ADR 0021, [docs/fix-pr-workflow.md](docs/fix-pr-workflow.md)) turns a checked repo into one review branch with a PR text, in the approved format: small safe fixes marked `//fix: ...`, English `//` questions and naming notes, never the core of the assignment. It runs after each repo's report, in a separate working copy (`workspace/runs/<run_id>/fixes/<repo_id>`), and only proposes what passes the guardrails, a self-review and a rebuild and recheck (no requirement that passed may fail, every claimed fix must pass). The proposal then waits at the approval gate below, and the repo report links its diff or says why there is none.

It sends each repo's code to the LLM, so it is off by default and needs two settings in `config.yaml`:

```yaml
fixer:
  enabled: true
llm:
  student_code_approved: true   # only once the lecturer has approved sending students' code
```

With `fixer.enabled` but without the approval, the program refuses to start. The LLM key must be set (see "The LLM key"). Replies are cached in `workspace/fixer_cache.sqlite`, so a round on the same commits does not pay again; `round.md` shows the round's LLM calls and tokens and the number of proposals. Logs carry counts and tokens only, never code, prompts or replies.

## Approving and publishing pull requests

After every repo is checked, the round stops at one approval gate (ADR 0018) if any repo has a fix proposal waiting for review. Proposals come from the fixer (above); with the fixer off, no round has any, and every round ends without stopping. A repo without a proposal is never published. Publishing follows [docs/fix-pr-workflow.md](docs/fix-pr-workflow.md): the branch is pushed to the publishing account's fork of the student repo (`check_<username>`, created once and reused) and opened as a pull request on the student's repo; publishing again updates the same pull request. Before each publish the student's default branch is checked again: if it moved since the check, nothing is pushed (`stale`), and the repo needs a new round.

Publishing needs, in `.env` (see `.env.example`):

- `HW_CHECKER_GITHUB_TOKEN`: the publishing account's classic token with the `public_repo` scope only. It is sent only in the GitHub API's `Authorization` header and to `git push` through that command's environment, never in a URL, the git config or a log.
- `HW_CHECKER_GIT__COMMIT_AUTHOR_NAME` and `HW_CHECKER_GIT__COMMIT_AUTHOR_EMAIL`: the publishing account and its no-reply address. Publishing refuses to run without them.

The account and the fork prefix are in `config.yaml` (`github:`).

### From the command line

A command-line round that stops at the gate ends with `Waiting for approval: N repos`. Its state is kept in `workspace/checkpoints.sqlite`, so the answer can come days later:

```powershell
uv run python -m hw_checker review 20261101-090000-a1b2c3
uv run python -m hw_checker approve 20261101-090000-a1b2c3 --repo dana__api --repo noa__webapi --decline tamar__webapi --note tamar__webapi="Fix by hand first"
```

`review` lists each waiting repo with its counts, branch, pull request title, report and diff (`workspace/runs/<run_id>/reports/<repo_id>.diff`), and the last publish attempt if one failed. `approve` resumes the round: approved repos are published one after another, declined repos are never published, and repos not mentioned stay pending, so the gate asks again for them. It prints one line per publish (`published` or `updated` with the pull request's link, `stale` or `failed` with the reason) and whether repos are still waiting. A failed publish goes back to waiting, so it can be approved again. The pull request links also go into `round.md`. An unknown repo id records nothing and the gate asks again with the reason. `review` and `approve` build the round graph, so Docker Desktop must be running.

### From LangGraph Studio

When the round stops at `approval_gate`, Studio shows the interrupt's value: the pending repos and how to answer. Resume the thread with the same JSON as the command line:

```json
{"approve": ["dana__api", "noa__webapi"], "decline": ["tamar__webapi"], "notes": {"tamar__webapi": "Fix by hand first"}}
```

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
  reporting/         reviewer reports and the round's counts (Jinja2 templates)
  config/            settings and logging
  ui/                Streamlit app (deferred, ADR 0017)
  bootstrap.py       composition root: creates adapters, builds the graph
  cli.py             `python -m hw_checker round ...`, `review`, `approve` and `llm-check`
tests/unit/          unit tests (integration tests are marked `integration`)
```

Dependencies point inward only; see the layer table in [CLAUDE.md](CLAUDE.md).

## Documentation

- [docs/design.md](docs/design.md): system design and stage plan
- [docs/fix-pr-workflow.md](docs/fix-pr-workflow.md): the approved fix-PR workflow (publishing and the fixer)
- [docs/adr/](docs/adr/): architecture decision records
- [CLAUDE.md](CLAUDE.md): working rules for the implementing agent
- [docs/planner-guide.md](docs/planner-guide.md): guide for the planner/reviewer agent
