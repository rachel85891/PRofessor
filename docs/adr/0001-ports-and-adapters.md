# 0001. Layered Ports & Adapters architecture

- **Status:** Accepted
- **Date:** 2026-10-20

## Context

Every external component (Excel, Git, GitHub, Docker, LLM, SQLite, UI) may need to be replaced, the system must be testable without those components, and it will be handed over to another developer (design §3).

## Decision

Use a layered Ports & Adapters (hexagonal) architecture. Dependencies point inward toward the domain, adapters implement ports, and only `bootstrap.py` creates adapters and wires them in.

## Consequences

Components are swappable and every layer can be tested with fakes, which makes handover easier. Every external capability needs a port and an in-memory fake, and the layer rules must be enforced in review.

## Rejected alternatives

- Flat structure — does not give swappable components or tests with fakes.
