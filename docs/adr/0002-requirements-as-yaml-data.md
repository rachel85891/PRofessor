# 0002. Requirements as YAML data with optional checks.py plugins

- **Status:** Accepted
- **Date:** 2026-10-20

## Context

A new assignment arrives regularly during the semester, and every check must also cover all earlier assignments (regression) (design §8).

## Decision

Each assignment is a folder under `assignments/` with a `requirements.yaml` validated by a Pydantic model, plus an optional `checks.py` whose custom checks the registry discovers. Checking assignment N loads `_general` and every assignment whose order is ≤ N.

## Consequences

A new assignment needs no code change, and regression comes automatically. Changing an old requirement affects later rounds, so requirement files are kept in Git and their hash is stored with each round's results.

## Rejected alternatives

- A code node per assignment — every new assignment would require code changes.
