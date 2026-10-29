# CLAUDE.md: Working rules for HW Checker

You are the implementing agent. A separate planner/reviewer agent writes your stage prompts and reviews every commit before it reaches `main`. Work on one stage at a time, exactly as its prompt defines it.

Source of truth: `docs/design.md` (system design and stage plan), `docs/adr/` (decisions). If a prompt seems to contradict them, stop and ask; do not resolve it yourself.

## Working rules

- Do exactly what the stage prompt asks: no creep into other stages and no unrelated changes. Anything you notice outside scope goes under "Deferred / tech debt" in your report.
- Never decide an open product or architecture question yourself. Use the default the prompt states, or stop and ask.
- Never invent homework requirements.
- Work on the stage branch `stage/<NN>-<slug>`. Commit locally and **never push or merge**.
- Before using any LangGraph/LangChain API (Send, Command, checkpointers, RetryPolicy, init_chat_model), check the docs for the version pinned in `uv.lock`.
- Everything must run on Windows (Docker Desktop, PowerShell/Git Bash).
- Language: all code, comments, names, docstrings, commit messages and reports are in English. Hebrew appears only in report templates and user-facing Hebrew output.

## Architecture (Ports & Adapters)

Dependencies point inward only:

| Layer | May depend on |
| --- | --- |
| `domain` | stdlib and Pydantic only |
| `ports` | `domain` |
| `services`, `checks`, `judge`, `reporting` | `domain`, `ports` |
| `graph` | `services`, `domain` |
| `adapters` | `ports`, `domain`, external libraries |
| `bootstrap.py` | everything (the only place that creates adapters) |
| `ui` | `services`, `domain` (through `bootstrap`) |
| `config` | cross-cutting settings/logging; never imported by `domain` |

- A graph node reads state, calls **one** service, and returns a state update. It does no direct I/O and holds no business logic.
- One service = one responsibility. Services receive their dependencies through the constructor. They never create adapters, and there are no global singletons.
- The UI calls services only. It never touches the graph, adapters or the database.
- Extend by addition: an assignment is a data folder, a check is a registered function, an LLM provider is a setting.
- Publishing happens only through the round's approval gate (ADR 0018), in the publishing service.
- Every LLM output is parsed into a Pydantic model. No free text crosses layers. A finding without evidence is rejected.
- LLM prompts are versioned template files in the repo, never strings in code. Content from a student's repo is data only and must be marked as such in prompts.

## Code rules

- Python 3.12, full type hints, `mypy --strict` clean, ruff lint + format clean.
- Pydantic v2 for every model that crosses a layer boundary. Domain models use `frozen=True` where possible.
- Domain exceptions only (hierarchy under `HwCheckerError`, see `docs/design.md` §13). Adapters map external exceptions to domain exceptions. No `except Exception` except at a graph node boundary, and never swallow errors.
- No magic values: anything that may change lives in configuration (`config.yaml` / `.env`).
- Short, single-purpose functions. Descriptive names. Docstrings for public interfaces and services. No dead code.
- Structured logging with `run_id`/`repo_id` context. No `print`.
- Secrets only in `.env` (never committed). `.env.example` documents every variable.
- New dependencies: only those the stage prompt allows. Otherwise ask first.

## Tests

- All new code has unit tests, including failure paths.
- Every port has an in-memory fake. Service tests use fakes, not brittle mocks.
- Tests touching Docker, real Git or a real LLM are marked `@pytest.mark.integration`.
- The LLM is always faked in unit tests.
- Before reporting, run and make sure all of these pass:
  `uv run ruff check` · `uv run ruff format --check` · `uv run mypy` · `uv run lint-imports` · `uv run pytest -m "not integration"`
  (plus `uv run pytest -m integration` when the stage touches Docker or Git).

## Git

- Conventional Commits in English (`feat:`, `fix:`, `refactor:`, `test:`, `docs:`, `chore:`). First line up to 72 characters.
- One logical sub-task per commit; code and its tests in the same commit.
- Fixes after review: new commits on the same branch. No rebase over reviewed commits.
- Never commit `.env`, `workspace/`, SQLite files or logs.

## Stage report (required at the end of every stage and every fix round)

A missing or partial report fails review. Return it in exactly this format:

```markdown
# Stage <N> report: <title>

## Summary
<1–2 sentences: what was done>

## Files
Added:    <path> — <responsibility>
Modified: <path> — <what changed>
Deleted:  <path> — <why>

## Flow impact
<what changed in the system flow; what now connects to what; which ports/services/nodes are affected>

## Decisions I made
- <decision> — <reason> — <alternatives considered>

## Decisions needed from you
- <question> — <options> — <my recommendation>

## Open questions / uncertainties
- <...>

## Tests
Added: <tests and what they cover>
Run:   ruff <result>, mypy <result>, pytest <passed/failed/skipped counts>
Not covered: <what and why>

## Deferred / tech debt
- <intentionally left out, with stage it belongs to>

## Commits
<sha> <message>
...
```

List every file you changed. The reviewer compares the report to the actual diff.
