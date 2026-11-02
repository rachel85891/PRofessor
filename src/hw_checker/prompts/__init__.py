"""Versioned text templates: the LLM prompts and the PR body (ADR 0021).

The files here are data, rendered through the TextTemplates port; this package holds no logic.
"""

from pathlib import Path

TEMPLATES_DIR = Path(__file__).parent
"""The folder of the template files."""
