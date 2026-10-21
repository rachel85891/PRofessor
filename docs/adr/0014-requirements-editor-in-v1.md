# 0014. Requirements editor in v1

- **Status:** Accepted
- **Date:** 2026-10-21

## Context

Requirements are written each week by the developer, and the developer prefers a form over hand-editing YAML.

## Decision

Add a requirements editor screen to the v1 UI (stage 12). YAML files stay the source of truth, so requirements stay versioned in Git and hashed per round, and the form reads and writes them through a service. Until the screen exists, files are written by hand following `assignments/README.md`.

## Consequences

The YAML format must stay flat and easy to write back, and stage 12 grows by one screen.

## Rejected alternatives

- Storing requirements in the database — they would lose Git versioning and the per-round hash.
- Keeping the editor in the roadmap — weekly hand-editing of YAML is error-prone.
