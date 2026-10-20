# 0007. SQLite with SQLAlchemy and Alembic behind repositories

- **Status:** Accepted
- **Date:** 2026-10-20

## Context

Runs, findings, proposals and review decisions must be stored with zero setup on a local machine, while a later move to a central database must remain possible (design §12).

## Decision

Use SQLite through SQLAlchemy, with Alembic migrations from the start. Each table is accessed only through a repository behind the `Storage` port.

## Consequences

Zero setup now. Moving to PostgreSQL is a connection-string change.

## Rejected alternatives

- JSON files — no migrations and no move to PostgreSQL by changing the connection string.
- PostgreSQL now — setup the first version does not need.
