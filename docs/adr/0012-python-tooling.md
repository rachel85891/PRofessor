# 0012. Python 3.12, uv, ruff, mypy strict, pytest

- **Status:** Accepted
- **Date:** 2026-10-20

## Context

The system is built while in use and will be handed over to another developer, so maintainability comes first (design §4, §13).

## Decision

Use Python 3.12, uv for dependencies and environments, ruff for lint and format, mypy in strict mode, and pytest, enforced through pre-commit.

## Consequences

A modern, maintainable standard. Every stage must pass ruff, mypy and pytest cleanly.

## Rejected alternatives

- None recorded.
