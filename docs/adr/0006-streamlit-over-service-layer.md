# 0006. Streamlit UI on top of a service layer

- **Status:** Superseded by 0017 (for v1)
- **Date:** 2026-10-20

## Context

A UI is needed now to start rounds, track progress and approve proposals, and a later move to FastAPI + React must remain possible (design §12).

## Decision

Build a thin Streamlit app that calls only the service layer, through `bootstrap`.

## Consequences

A usable UI ships fast. Because the UI knows neither LangGraph nor the adapters, it can later be replaced by FastAPI + React without a rewrite.

## Rejected alternatives

- React now — slower than Streamlit for a UI that is needed now.
- Gradio — not chosen.
