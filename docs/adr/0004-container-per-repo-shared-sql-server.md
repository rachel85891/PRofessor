# 0004. One container per repo and a shared SQL Server with a database per repo

- **Status:** Accepted
- **Date:** 2026-10-20

## Context

Student code must run isolated, the projects use SQL Server, and everything runs on a local Windows machine with limited memory (design §9).

## Decision

Build and run each repo in its own .NET 9 container, against one shared SQL Server container started once per round, with a separate database per repo (`hw_<repo_id>_<run_id>`).

## Consequences

Student code never runs on the host, and memory use stays manageable. Database and container teardown must always run, even after an error.

## Rejected alternatives

- Running directly on the host — unsafe.
- A SQL Server container per repo — too heavy for a local machine.
