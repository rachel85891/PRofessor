"""Port for reading and writing a working copy's files (the fixer's edits)."""

from pathlib import Path
from typing import Protocol


class SourceFiles(Protocol):
    """Lists, reads and writes files inside one working copy, by repo-relative path."""

    def list_files(self, root: Path, skip_dirs: frozenset[str]) -> tuple[str, ...]:
        """Return every file under `root` as a repo-relative path with forward slashes,
        sorted, skipping folders named in `skip_dirs` at any depth.

        Raises:
            FixError: `root` cannot be read.
        """
        ...

    def read_bytes(self, root: Path, path: str) -> bytes:
        """Return the bytes of the file `path` under `root`.

        Raises:
            FixError: the file cannot be read, or `path` leaves `root`.
        """
        ...

    def write_bytes(self, root: Path, path: str, data: bytes) -> None:
        """Replace the existing file `path` under `root` with `data`.

        Raises:
            FixError: the file cannot be written, does not exist, or `path` leaves `root`.
        """
        ...
