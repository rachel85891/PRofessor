# 0016. Services reach the checks layer through ports

- **Status:** Accepted
- **Date:** 2026-10-21

## Context

Services need to run checks: the assignment catalog validates check params, and the deterministic check service evaluates structure, build and HTTP requirements. The import-linter contracts from stage 0b forbid `services` from importing `checks`, and whether to relax that was deferred to stage 8.

## Decision

Services reach the checks layer only through ports: `CheckCatalog` (stage 3b) and `RequirementChecker` (stage 8a). Both are implemented in `hw_checker.checks` and wired in `bootstrap.py`. The import-linter contracts stay unchanged.

## Consequences

- One more port per capability the services need from the checks layer.
- Services stay testable with in-memory fakes (`FakeCheckCatalog`, `FakeRequirementChecker`), without real checks or a working copy.
- The checks layer can change how a check runs without touching services.

## Rejected alternatives

- Relaxing the contract so services import `checks` directly: less code, but weaker layering, and services would be tied to concrete check implementations.
