"""Builds the tar stream copied into a repo's work volume, from an untrusted working copy."""

import io
import os
import tarfile
from dataclasses import dataclass
from pathlib import Path

from hw_checker.adapters.docker_runner.discovery import EXCLUDED_DIRS

SOURCE_DIR = "src"
OUTPUT_DIR = "out"
DIRECTORY_MODE = 0o755
FILE_MODE = 0o644


@dataclass(frozen=True)
class WorkArchive:
    """The tar bytes, and the repo-relative paths that were skipped for safety."""

    data: bytes
    skipped: tuple[str, ...]


def build_work_archive(working_copy: Path, uid: int, gid: int) -> WorkArchive:
    """Tar `working_copy` under `src/`, plus an empty `out/`, owned by `uid:gid`.

    `bin/`, `obj/` and `.git/` are left out. Symlinks, junctions and anything that resolves
    outside the working copy are skipped and listed. Modes are normalized and timestamps
    zeroed, so the same input always gives the same bytes.
    """
    root = working_copy.resolve()
    buffer = io.BytesIO()
    skipped: list[str] = []
    with tarfile.open(fileobj=buffer, mode="w", format=tarfile.PAX_FORMAT) as archive:
        _add_directory(archive, SOURCE_DIR, uid, gid)
        _add_directory(archive, OUTPUT_DIR, uid, gid)
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
                _add_directory(archive, _member_name(path, root), uid, gid)
            subdirectories[:] = kept
            for name in sorted(files):
                path = current / name
                if not _is_safe(path, root):
                    skipped.append(path.relative_to(root).as_posix())
                    continue
                _add_file(archive, path, _member_name(path, root), uid, gid)
    return WorkArchive(buffer.getvalue(), tuple(sorted(skipped)))


def _is_safe(path: Path, root: Path) -> bool:
    if path.is_symlink() or path.is_junction():
        return False
    return path.resolve().is_relative_to(root)


def _member_name(path: Path, root: Path) -> str:
    return f"{SOURCE_DIR}/{path.relative_to(root).as_posix()}"


def _owned(name: str, uid: int, gid: int) -> tarfile.TarInfo:
    info = tarfile.TarInfo(name)
    info.uid, info.gid, info.uname, info.gname, info.mtime = uid, gid, "", "", 0
    return info


def _add_directory(archive: tarfile.TarFile, name: str, uid: int, gid: int) -> None:
    info = _owned(name, uid, gid)
    info.type, info.mode = tarfile.DIRTYPE, DIRECTORY_MODE
    archive.addfile(info)


def _add_file(archive: tarfile.TarFile, path: Path, name: str, uid: int, gid: int) -> None:
    data = path.read_bytes()
    info = _owned(name, uid, gid)
    info.mode, info.size = FILE_MODE, len(data)
    archive.addfile(info, io.BytesIO(data))
