# 0008. LLM by role, with the model set in configuration

- **Status:** Accepted
- **Date:** 2026-10-20

## Context

Different tasks need different quality at a reasonable cost, and the LLM provider is not decided yet (design §10).

## Decision

`LLMClient` receives a role (`reviewer`, `judge`, `fixer`, `writer`), not a model name. The role → provider + model mapping lives in configuration: cheap models for checks, strong models for the judge and the fixer.

## Consequences

Quality at a reasonable cost, and switching providers or models needs no code change.

## Rejected alternatives

- One model for everything — either too expensive or too weak for the judge and fixer.
