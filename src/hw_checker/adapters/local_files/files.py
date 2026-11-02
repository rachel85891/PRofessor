"""LocalSourceFiles: the SourceFiles port over the local file system."""

import os
from pathlib import Path

from hw_checker.domain.errors import FixError


class LocalSourceFiles:
    """Lists, reads and writes files inside one working copy, never outside it."""

    def list_files(self, root: Path, skip_dirs: frozenset[str]) -> tuple[str, ...]:
        """Every file under `root`, repo-relative with forward slashes, sorted.

        Raises:
            FixError: `root` cannot be read.
        """
        if not root.is_dir():
            raise FixError(f"{root} is not a folder")
        found: list[str] = []
        try:
            for folder, dirs, names in os.walk(root, onerror=_raise):
                dirs[:] = sorted(name for name in dirs if name not in skip_dirs)
                base = Path(folder).relative_to(root)
                found += [(base / name).as_posix() for name in names]
        except OSError as error:
            raise FixError(f"cannot list the files of {root}: {error}") from error
        return tuple(sorted(found))

    def read_bytes(self, root: Path, path: str) -> bytes:
        """The bytes of `path` under `root`.

        Raises:
            FixError: the file cannot be read, or `path` leaves `root`.
        """
        target = _inside(root, path)
        try:
            return target.read_bytes()
        except OSError as error:
            raise FixError(f"cannot read {path}: {error}") from error

    def write_bytes(self, root: Path, path: str, data: bytes) -> None:
        """Replace the existing file `path` under `root` with `data`.

        Raises:
            FixError: the file does not exist or cannot be written, or `path` leaves `root`.
        """
        target = _inside(root, path)
        if not target.is_file():
            raise FixError(f"{path} does not exist; the fixer never adds files")
        try:
            target.write_bytes(data)
        except OSError as error:
            raise FixError(f"cannot write {path}: {error}") from error


def _inside(root: Path, path: str) -> Path:
    resolved_root = root.resolve()
    target = (resolved_root / path).resolve()
    if not target.is_relative_to(resolved_root):
        raise FixError(f"{path} is outside the working copy")
    return target


def _raise(error: OSError) -> None:
    raise error
