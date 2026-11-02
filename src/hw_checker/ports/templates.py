"""Port for the versioned text templates: the LLM prompts and the PR body."""

from collections.abc import Mapping
from typing import Protocol


class TextTemplates(Protocol):
    """Renders the repo's template files (`hw_checker/prompts/`) by name."""

    def render(self, name: str, values: Mapping[str, object]) -> str:
        """Render the template `name` with `values`.

        Raises:
            FixError: the template is missing or cannot be rendered with `values`.
        """
        ...

    def version(self) -> str:
        """A hash of every template file, so cached replies change when a prompt changes."""
        ...
