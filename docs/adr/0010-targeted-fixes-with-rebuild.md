# 0010. Targeted fixes with a rebuild check

- **Status:** Accepted
- **Date:** 2026-10-20

## Context

Proposed fixes go to second-year students as a PR; they must be readable and must never break the student's code (design §11).

## Decision

The fixer returns targeted search-and-replace patches at student level. Patches are applied on a local branch, then the project is rebuilt and the checks rerun; a patch that breaks something is dropped and its finding stays as a comment only.

## Consequences

Students get a readable diff, and their code is never broken. Each fix round costs a rebuild.

## Rejected alternatives

- Rewriting whole files — unreadable diffs for students.
