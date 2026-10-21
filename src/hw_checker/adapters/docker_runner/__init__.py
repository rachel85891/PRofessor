"""Docker runner adapter: builds and runs student projects in Docker (ADR 0015).

Pure helpers (discovery, archive, classification, log excerpts) live here without any
Docker SDK import; the runner itself comes in stage 5b-2.
"""
