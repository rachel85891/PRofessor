"""JinjaTextTemplates: the TextTemplates port over the template files in `hw_checker/prompts/`,
where a course may replace a frame with its own file in `course/prompts/` (ADR 0023)."""

import hashlib
from collections.abc import Mapping
from pathlib import Path

from jinja2 import ChoiceLoader, Environment, FileSystemLoader, StrictUndefined, TemplateError

from hw_checker.domain.errors import FixError

TEMPLATE_SUFFIX = ".j2"


class JinjaTextTemplates:
    """Renders plain-text templates (no HTML escaping); a missing value is an error.

    A template in `overrides_dir` (the course's `prompts/`, when it exists) is used instead
    of the one with the same name in `templates_dir`; it changes only with the developer's
    approval.
    """

    def __init__(self, templates_dir: Path, overrides_dir: Path | None = None) -> None:
        self._dirs = [d for d in (overrides_dir, templates_dir) if d is not None]
        self._environment = Environment(
            loader=ChoiceLoader([FileSystemLoader(directory) for directory in self._dirs]),
            autoescape=False,  # prompts and Markdown, not HTML
            undefined=StrictUndefined,
            trim_blocks=True,
            lstrip_blocks=True,
            keep_trailing_newline=True,
        )

    def render(self, name: str, values: Mapping[str, object]) -> str:
        """Render the template `name` with `values`.

        Raises:
            FixError: the template is missing or a value it uses is missing.
        """
        try:
            return self._environment.get_template(name).render(**values)
        except TemplateError as error:
            raise FixError(f"cannot render the template {name}: {error}") from error

    def version(self) -> str:
        """SHA-256 over every template's name and bytes, overrides included."""
        digest = hashlib.sha256()
        for directory in self._dirs:
            digest.update(directory.as_posix().encode())
            for path in sorted(directory.rglob(f"*{TEMPLATE_SUFFIX}")):
                digest.update(path.relative_to(directory).as_posix().encode())
                digest.update(path.read_bytes())
        return digest.hexdigest()
