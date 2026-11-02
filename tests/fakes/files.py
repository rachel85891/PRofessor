"""In-memory fakes for the SourceFiles and TextTemplates ports."""

import json
from collections.abc import Mapping
from pathlib import Path, PurePosixPath

from hw_checker.domain.errors import FixError


class FakeSourceFiles:
    """Keeps each root's files in memory, by repo-relative path."""

    def __init__(self, trees: Mapping[Path, Mapping[str, bytes]] | None = None) -> None:
        self.trees = {root: dict(files) for root, files in (trees or {}).items()}

    def list_files(self, root: Path, skip_dirs: frozenset[str]) -> tuple[str, ...]:
        if root not in self.trees:
            raise FixError(f"{root} is not a folder")
        return tuple(
            sorted(
                path
                for path in self.trees[root]
                if not skip_dirs & set(PurePosixPath(path).parts[:-1])
            )
        )

    def read_bytes(self, root: Path, path: str) -> bytes:
        try:
            return self.trees[root][path]
        except KeyError as error:
            raise FixError(f"cannot read {path}") from error

    def write_bytes(self, root: Path, path: str, data: bytes) -> None:
        if path not in self.trees.get(root, {}):
            raise FixError(f"{path} does not exist; the fixer never adds files")
        self.trees[root][path] = data


class FakeTextTemplates:
    """Renders a template as its name and its values as JSON; `version` is fixed."""

    def render(self, name: str, values: Mapping[str, object]) -> str:
        return f"{name}\n{json.dumps(values, default=str, sort_keys=True)}"

    def version(self) -> str:
        return "fake-templates-v1"
