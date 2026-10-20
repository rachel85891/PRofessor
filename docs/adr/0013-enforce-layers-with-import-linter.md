# 0013. Enforce layer rules with import-linter

- **Status:** Accepted
- **Date:** 2026-10-20

## Context

The layer rules (ADR 0001, `CLAUDE.md`, design §3) were enforced only in review. A broken dependency direction could reach a commit unnoticed and be caught late, or not at all. The layer packages are still empty, so adding automatic enforcement now costs almost nothing.

## Decision

Encode the layer table as import-linter contracts in `pyproject.toml` (`[tool.importlinter]`, root package `hw_checker`). Run them in a local pre-commit hook and, as `uv run lint-imports`, before every stage report.

The contracts:

1. A `layers` contract: `ui` → `graph` → `services | checks | judge | reporting` (independent of each other) → `ports` → `domain`, checked including indirect imports.
2. `graph` does not import `ports`, `checks`, `judge`, `reporting`, `adapters` or `bootstrap` (direct imports only).
3. `services`, `checks`, `judge` and `reporting` do not import `graph`, `ui`, `adapters` or `bootstrap` (including indirect imports).
4. No layer package except `bootstrap` imports `adapters` (direct imports only).
5. `domain` imports no other `hw_checker` package (including `config`) and none of a listed set of third-party libraries (including indirect imports).
6. `ui` does not import `graph`, `adapters` or `ports` (direct imports only).

Contracts 2, 4 and 6 check direct imports only, because the legal paths `ui → bootstrap → adapters`, `ui → services → ports` and `graph → services → ports` would otherwise fail them.

## Consequences

- A broken layer rule fails pre-commit and the standard checks with a message that names the rule, the importing module and the line.
- `bootstrap.py` needs no contract change when it is added: it is not a source in any contract, so it may import adapters, and `ui` may import it.
- Known limitations:
  - `domain` is checked against a forbidden list, not an allowlist of "stdlib and pydantic": import-linter has no allowlist contract for external packages. A third-party library missing from the list would not be caught. New libraries must be added to the list in the stage that introduces them.
  - The adapters rule lists its source packages explicitly. A new top-level module or package under `hw_checker` must be added to that list. A `protected` contract would cover new modules automatically, but it fails while `hw_checker.bootstrap` does not exist.
  - Direct-only contracts do not catch a forbidden dependency reached through an allowed module (for example `ui → services → graph`). Contracts 1 and 3 cover the paths that matter, since `services` may not import `graph`.
  - `config` is cross-cutting and is not restricted for layers other than `domain`.
- import-linter has no `python -m` entry point. On machines where console-script launchers hang, the hook calls its CLI function through `uv run python -c`.

## Rejected alternatives

- Review only — violations are caught late or missed, and every review must check the rules by hand.
- A custom pytest test that parses imports — duplicates what import-linter already does, and must be written and maintained in the project.
