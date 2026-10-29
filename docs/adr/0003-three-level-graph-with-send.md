# 0003. Three graph levels: round → repo → assignment, using Send

- **Status:** Accepted; refined by 0019
- **Date:** 2026-10-20

## Context

A round checks about 40 repos, each against assignments 1..N, with bounded parallelism on a local machine (design §7).

## Decision

A round graph fans out repos with Send to a subgraph per repo; inside it, an inner fan-out with Send runs one check per assignment 1..N. Results merge into state through list reducers.

## Consequences

Parallelism and regression follow from the structure. The LangGraph Send API must be checked against the pinned version before use.

## Rejected alternatives

- One flat graph — gives neither parallelism nor natural regression.
