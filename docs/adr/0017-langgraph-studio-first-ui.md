# 0017. LangGraph Studio as the first UI; Streamlit deferred

- **Status:** Accepted
- **Date:** 2026-10-29

## Context

ADR 0006 planned a Streamlit app (stage 12) as the v1 UI. With the services and adapters done, the developer wants to see the round graph itself first: its nodes, the state of each repo as it moves through them, and the results. LangGraph Studio shows exactly that for a graph served by the local LangGraph dev server, with no UI code of our own (developer decision, 2026-10-29).

## Decision

LangGraph Studio, over the local dev server (`langgraph dev`, configured in `langgraph.json`), is the first UI. Rounds also run from a command line (`python -m hw_checker round ...`). Streamlit is deferred, not dropped: stage 12 moves after the approval gate (stage 13a). This supersedes ADR 0006 for v1.

## Consequences

- The developer starts rounds and watches them in Studio without a UI stage in between.
- The service layer stays UI-agnostic, so Streamlit, or FastAPI with React, can still be added on top of it later without a rewrite.
- The requirements editor of ADR 0014 moves with stage 12; until then requirements are written by hand.
- The round's state holds students' names and findings. LangSmith tracing must stay off (no `LANGSMITH_API_KEY`, `LANGSMITH_TRACING=false`): the dev server keeps state on this machine, and the Studio page in the browser reads it from `127.0.0.1` only.
- Studio is a developer tool, not a screen for a lecturer: review and approval screens come with the approval gate and the deferred UI.

## Rejected alternatives

- Build Streamlit now (ADR 0006) — delays seeing the graph, and its round-progress screen would duplicate what Studio already shows.
- A custom web UI — far more work than this stage needs.
