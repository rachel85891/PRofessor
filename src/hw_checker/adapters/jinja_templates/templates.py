"""JinjaTextTemplates: the TextTemplates port over the template files in `hw_checker/prompts/`."""

import hashlib
from collections.abc import Mapping
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, StrictUndefined, TemplateError

from hw_checker.domain.errors import FixError

TEMPLATE_SUFFIX = ".j2"


class JinjaTextTemplates:
    """Renders plain-text templates (no HTML escaping); a missing value is an error."""

    def __init__(self, templates_dir: Path) -> None:
        self._dir = templates_dir
        self._environment = Environment(
            loader=FileSystemLoader(templates_dir),
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
        """SHA-256 over every template's name and bytes."""
        digest = hashlib.sha256()
        for path in sorted(self._dir.rglob(f"*{TEMPLATE_SUFFIX}")):
            digest.update(path.relative_to(self._dir).as_posix().encode())
            digest.update(path.read_bytes())
        return digest.hexdigest()
