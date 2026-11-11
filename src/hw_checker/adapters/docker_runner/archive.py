"""Builds the tar stream copied into a repo's work volume, from an untrusted working copy."""

import io
import os
import tarfile
from collections.abc import Collection, Mapping
from dataclasses import dataclass
from pathlib import Path

from hw_checker.adapters.docker_runner.discovery import EXCLUDED_DIRS

SOURCE_DIR = "src"
OUTPUT_DIR = "out"
REPO_DIR = "repo"
DIRECTORY_MODE = 0o755
FILE_MODE = 0o644


@dataclass(frozen=True)
class WorkArchive:
    """The tar bytes, and the repo-relative paths that were skipped for safety."""

    data: bytes
    skipped: tuple[str, ...]


class ArchiveTooLargeError(Exception):
    """The working copy, or one file in it, is larger than the configured limit."""


@dataclass
class _SizeGuard:
    max_total_bytes: int | None
    max_file_bytes: int | None
    total_bytes: int = 0

    def admit(self, path: Path, relative: str) -> None:
        # Measured from the file system, so an oversized file is never read into memory.
        size = path.stat().st_size
        if self.max_file_bytes is not None and size > self.max_file_bytes:
            raise ArchiveTooLargeError(
                f"{relative} is {size:,} bytes; the limit for one file is "
                f"{self.max_file_bytes:,} bytes"
            )
        self.total_bytes += size
        if self.max_total_bytes is not None and self.total_bytes > self.max_total_bytes:
            raise ArchiveTooLargeError(
                f"the working copy is larger than the {self.max_total_bytes:,}-byte limit "
                f"(over {self.total_bytes:,} bytes so far, at {relative})"
            )


def build_work_archive(
    working_copy: Path,
    uid: int,
    gid: int,
    max_total_bytes: int | None = None,
    max_file_bytes: int | None = None,
    overrides: Mapping[str, bytes] | None = None,
) -> WorkArchive:
    """Tar `working_copy` under `src/`, plus an empty `out/`, owned by `uid:gid`.

    `bin/`, `obj/` and `.git/` are left out. Symlinks, junctions and anything that resolves
    outside the working copy are skipped and listed. Modes are normalized and timestamps
    zeroed, so the same input always gives the same bytes. `overrides` replaces the content
    of files (keyed by their path relative to `working_copy`) in the archive only.

    Raises:
        ArchiveTooLargeError: a file, or all files together, exceed the given limits.
    """
    return _tree_archive(
        working_copy,
        SOURCE_DIR,
        (OUTPUT_DIR,),
        uid,
        gid,
        max_total_bytes,
        max_file_bytes,
        overrides or {},
    )


def build_repo_archive(
    working_copy: Path,
    uid: int,
    gid: int,
    max_total_bytes: int | None = None,
    max_file_bytes: int | None = None,
    overrides: Mapping[str, bytes] | None = None,
    created: Collection[str] = (),
) -> WorkArchive:
    """Tar the whole working copy under `repo/`, owned by `uid:gid`, for the app to write.

    Every project's folder keeps its place, so a path from the Web project's folder into a
    sibling project (`../Repository/users.txt`) resolves as on the student's machine. The
    same rules as `build_work_archive`: `bin/`, `obj/` and `.git/` are left out, unsafe paths
    are skipped and listed, the size limits apply, and `overrides` (keyed relative to
    `working_copy`) replaces file contents in the archive only. `created` lists repo-relative
    paths of data files the repo lacks: each is added empty, with any missing folder, so the
    app can start; the working copy itself is never written to.

    Raises:
        ArchiveTooLargeError: a file, or all files together, exceed the given limits.
    """
    return _tree_archive(
        working_copy,
        REPO_DIR,
        (),
        uid,
        gid,
        max_total_bytes,
        max_file_bytes,
        overrides or {},
        created,
    )


def _tree_archive(
    tree: Path,
    prefix: str,
    empty_dirs: tuple[str, ...],
    uid: int,
    gid: int,
    max_total_bytes: int | None,
    max_file_bytes: int | None,
    overrides: Mapping[str, bytes],
    created: Collection[str] = (),
) -> WorkArchive:
    root = tree.resolve()
    guard = _SizeGuard(max_total_bytes, max_file_bytes)
    buffer = io.BytesIO()
    skipped: list[str] = []
    names: set[str] = set()
    with tarfile.open(fileobj=buffer, mode="w", format=tarfile.PAX_FORMAT) as archive:
        for name in (prefix, *empty_dirs):
            _add_directory(archive, name, uid, gid)
        for directory, subdirectories, files in os.walk(root, followlinks=False):
            current = Path(directory)
            kept: list[str] = []
            for name in sorted(subdirectories):
                path = current / name
                if name.lower() in EXCLUDED_DIRS:
                    continue
                if not _is_safe(path, root):
                    skipped.append(path.relative_to(root).as_posix())
                    continue
                kept.append(name)
                names.add(_member_name(path, root, prefix))
                _add_directory(archive, _member_name(path, root, prefix), uid, gid)
            subdirectories[:] = kept
            for name in sorted(files):
                path = current / name
                if not _is_safe(path, root):
                    skipped.append(path.relative_to(root).as_posix())
                    continue
                relative = path.relative_to(root).as_posix()
                guard.admit(path, relative)
                data = overrides.get(relative)
                if data is None:
                    data = path.read_bytes()
                names.add(_member_name(path, root, prefix))
                _add_file(archive, data, _member_name(path, root, prefix), uid, gid)
        for relative in created:
            _add_empty_file(archive, f"{prefix}/{relative}", names, uid, gid)
    return WorkArchive(buffer.getvalue(), tuple(sorted(skipped)))


def _add_empty_file(
    archive: tarfile.TarFile, name: str, names: set[str], uid: int, gid: int
) -> None:
    """Add an empty file `name`, and each of its folders the archive does not have yet."""
    parts = name.split("/")
    if any(part in ("", ".", "..") for part in parts) or name in names:
        raise ValueError(f"not a new path inside the archive: {name!r}")
    for depth in range(2, len(parts)):
        folder = "/".join(parts[:depth])
        if folder not in names:
            names.add(folder)
            _add_directory(archive, folder, uid, gid)
    names.add(name)
    _add_file(archive, b"", name, uid, gid)


def _is_safe(path: Path, root: Path) -> bool:
    if path.is_symlink() or path.is_junction():
        return False
    return path.resolve().is_relative_to(root)


def _member_name(path: Path, root: Path, prefix: str) -> str:
    return f"{prefix}/{path.relative_to(root).as_posix()}"


def _owned(name: str, uid: int, gid: int) -> tarfile.TarInfo:
    info = tarfile.TarInfo(name)
    info.uid, info.gid, info.uname, info.gname, info.mtime = uid, gid, "", "", 0
    return info


def _add_directory(archive: tarfile.TarFile, name: str, uid: int, gid: int) -> None:
    info = _owned(name, uid, gid)
    info.type, info.mode = tarfile.DIRTYPE, DIRECTORY_MODE
    archive.addfile(info)


def _add_file(archive: tarfile.TarFile, data: bytes, name: str, uid: int, gid: int) -> None:
    info = _owned(name, uid, gid)
    info.mode, info.size = FILE_MODE, len(data)
    archive.addfile(info, io.BytesIO(data))


def directory_archive(name: str, uid: int, gid: int) -> bytes:
    """A tar holding one empty directory `name`, owned by `uid:gid`."""
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w", format=tarfile.PAX_FORMAT) as archive:
        _add_directory(archive, name, uid, gid)
    return buffer.getvalue()
