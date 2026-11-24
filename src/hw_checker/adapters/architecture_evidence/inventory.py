"""Bounded, link-free inventory of a repository's `.csproj` and `.cs` files and its projects.

Project files are parsed as XML without a DTD; a `ProjectReference` is resolved lexically
against the scanned inventory and never opened outside the repository.
"""

import os
import posixpath
import re
import xml.etree.ElementTree as ElementTree
from pathlib import Path
from typing import Final

from hw_checker.domain import (
    EvidenceCoverageIssue,
    EvidenceIssueKind,
    ProjectEvidence,
    ProjectReferenceEvidence,
    ProjectReferenceStatus,
)

EXCLUDED_DIRS: Final = frozenset({"bin", "obj", ".git", ".vs"})
GENERATED_DIRS: Final = frozenset({"properties", "migrations"})
GENERATED_SUFFIXES: Final = (".designer.cs", ".g.cs", ".g.i.cs")
MAX_INVENTORY_FILES: Final = 2_000
MAX_DIRECTORY_COUNT: Final = 5_000
MAX_VISITED_FILES: Final = 20_000
MAX_PROJECT_FILE_BYTES: Final = 1_000_000
MAX_RELATIVE_PATH_CHARS: Final = 1_024
_DRIVE_PATH = re.compile(r"^[A-Za-z]:")
_SAFE_SDK_REFERENCE = re.compile(r"^[A-Za-z0-9._/-]{1,256}$")
_NAMESPACE_NAME = re.compile(r"^[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*$")
MAX_NAMESPACE_CHARS: Final = 256
_UNSUPPORTED_PATH = re.compile(r"[$*?\[\]{}]")
_DOCTYPE = re.compile(r"<!\s*DOCTYPE\b", re.IGNORECASE)
_XML_COMMENT = re.compile(r"<!--.*?-->", re.DOTALL)
_REFERENCE_TAG = re.compile(r"<(?:[\w.-]+:)?ProjectReference\b")


def inventory_files(root: Path) -> tuple[tuple[str, ...], EvidenceCoverageIssue | None]:
    """Sorted relative `.csproj` and `.cs` paths, and the last limit or link skipped."""
    found: list[str] = []
    directory_count = 0
    visited_files = 0
    issue: EvidenceCoverageIssue | None = None

    def failed_walk(_: OSError) -> None:
        raise OSError("repository traversal failed")

    try:
        for directory, subdirectories, names in os.walk(
            root, topdown=True, followlinks=False, onerror=failed_walk
        ):
            directory_count += 1
            if directory_count > MAX_DIRECTORY_COUNT:
                issue = EvidenceCoverageIssue(kind=EvidenceIssueKind.SCAN_LIMIT_REACHED)
                break
            current = Path(directory)
            kept: list[str] = []
            for name in sorted(subdirectories):
                candidate = current / name
                if name.lower() in EXCLUDED_DIRS:
                    continue
                if _is_link_or_junction(candidate):
                    relative = candidate.relative_to(root).as_posix()
                    issue = EvidenceCoverageIssue(
                        kind=EvidenceIssueKind.UNSAFE_PATH_SKIPPED,
                        file_path=(relative if len(relative) <= MAX_RELATIVE_PATH_CHARS else None),
                    )
                    continue
                kept.append(name)
            subdirectories[:] = kept
            for name in sorted(names):
                visited_files += 1
                if visited_files > MAX_VISITED_FILES:
                    issue = EvidenceCoverageIssue(kind=EvidenceIssueKind.SCAN_LIMIT_REACHED)
                    return tuple(sorted(found)), issue
                candidate = current / name
                if _is_link_or_junction(candidate):
                    relative = candidate.relative_to(root).as_posix()
                    issue = EvidenceCoverageIssue(
                        kind=EvidenceIssueKind.UNSAFE_PATH_SKIPPED,
                        file_path=(relative if len(relative) <= MAX_RELATIVE_PATH_CHARS else None),
                    )
                    continue
                if candidate.suffix.lower() not in {".csproj", ".cs"}:
                    continue
                relative = candidate.relative_to(root).as_posix()
                if len(relative) > MAX_RELATIVE_PATH_CHARS:
                    issue = EvidenceCoverageIssue(kind=EvidenceIssueKind.SCAN_LIMIT_REACHED)
                    continue
                if len(found) >= MAX_INVENTORY_FILES:
                    issue = EvidenceCoverageIssue(kind=EvidenceIssueKind.SCAN_LIMIT_REACHED)
                    return tuple(sorted(found)), issue
                found.append(relative)
    except OSError:
        issue = EvidenceCoverageIssue(kind=EvidenceIssueKind.FILE_UNREADABLE)
    return tuple(sorted(found)), issue


def read_project(
    root: Path, relative: str, project_files: set[str]
) -> tuple[
    ProjectEvidence | None,
    tuple[ProjectReferenceEvidence, ...],
    tuple[EvidenceCoverageIssue, ...],
]:
    """One project's SDKs and references, with the issues that lower inventory coverage."""
    try:
        path = safe_file(root, relative)
        if path.stat().st_size > MAX_PROJECT_FILE_BYTES:
            return (
                None,
                (),
                (EvidenceCoverageIssue(kind=EvidenceIssueKind.FILE_TOO_LARGE, file_path=relative),),
            )
        data = path.read_bytes()
    except OSError:
        return (
            None,
            (),
            (EvidenceCoverageIssue(kind=EvidenceIssueKind.FILE_UNREADABLE, file_path=relative),),
        )
    except ValueError:
        return (
            None,
            (),
            (
                EvidenceCoverageIssue(
                    kind=EvidenceIssueKind.UNSAFE_PATH_SKIPPED, file_path=relative
                ),
            ),
        )

    try:
        project_text = data.decode("utf-8-sig")
    except UnicodeDecodeError:
        return (
            None,
            (),
            (EvidenceCoverageIssue(kind=EvidenceIssueKind.INVALID_UTF8, file_path=relative),),
        )
    if _DOCTYPE.search(project_text):
        return (
            None,
            (),
            (
                EvidenceCoverageIssue(
                    kind=EvidenceIssueKind.UNSUPPORTED_PROJECT, file_path=relative
                ),
            ),
        )
    try:
        xml_root = ElementTree.fromstring(project_text)
    except ElementTree.ParseError:
        return (
            None,
            (),
            (EvidenceCoverageIssue(kind=EvidenceIssueKind.MALFORMED_PROJECT, file_path=relative),),
        )

    raw_sdk_references = tuple(
        value
        for value in (
            xml_root.get("Sdk"),
            *(
                element.get("Name")
                for element in xml_root.iter()
                if _local_name(element.tag) == "Sdk"
            ),
        )
        if value is not None and value.strip()
    )
    sdk_references = tuple(
        value.strip()
        for value in raw_sdk_references
        if _SAFE_SDK_REFERENCE.fullmatch(value.strip())
    )
    root_namespace, root_namespace_issues = _root_namespace(xml_root, relative)
    evidence = ProjectEvidence(
        file_path=relative, sdk_references=sdk_references, root_namespace=root_namespace
    )
    references: list[ProjectReferenceEvidence] = []
    issues: list[EvidenceCoverageIssue] = [
        EvidenceCoverageIssue(kind=EvidenceIssueKind.UNSUPPORTED_PROJECT, file_path=relative)
        for value in raw_sdk_references
        if not _SAFE_SDK_REFERENCE.fullmatch(value.strip())
    ]
    issues.extend(root_namespace_issues)
    elements = [e for e in xml_root.iter() if _local_name(e.tag) == "ProjectReference"]
    lines = _reference_lines(project_text, len(elements))
    for element, line in zip(elements, lines, strict=True):
        include = element.get("Include")
        target, status = _project_reference_target(relative, include, project_files)
        references.append(
            ProjectReferenceEvidence(
                source_project=relative,
                target_project=target,
                status=status,
                line=line,
            )
        )
        if status is ProjectReferenceStatus.UNSAFE:
            issues.append(
                EvidenceCoverageIssue(
                    kind=EvidenceIssueKind.UNSAFE_PROJECT_REFERENCE, file_path=relative
                )
            )
        elif status is ProjectReferenceStatus.MISSING:
            issues.append(
                EvidenceCoverageIssue(
                    kind=EvidenceIssueKind.MISSING_PROJECT_REFERENCE, file_path=relative
                )
            )
        elif status is ProjectReferenceStatus.CASE_MISMATCHED:
            issues.append(
                EvidenceCoverageIssue(
                    kind=EvidenceIssueKind.CASE_MISMATCHED_PROJECT_REFERENCE,
                    file_path=relative,
                )
            )
        elif status is ProjectReferenceStatus.UNSUPPORTED:
            issues.append(
                EvidenceCoverageIssue(
                    kind=EvidenceIssueKind.UNSUPPORTED_PROJECT, file_path=relative
                )
            )
    return evidence, tuple(references), tuple(issues)


def _root_namespace(
    xml_root: ElementTree.Element, relative: str
) -> tuple[str | None, list[EvidenceCoverageIssue]]:
    """The project's literal `<RootNamespace>`; None when it has none. A value that is not a
    plain namespace (an MSBuild expression) or a second, different one is not guessed: None
    and an `unsupported_project` issue."""
    values = {
        element.text.strip()
        for element in xml_root.iter()
        if _local_name(element.tag) == "RootNamespace" and element.text and element.text.strip()
    }
    if not values:
        return None, []
    if len(values) == 1:
        (value,) = values
        if _NAMESPACE_NAME.fullmatch(value) and len(value) <= MAX_NAMESPACE_CHARS:
            return value, []
    issue = EvidenceCoverageIssue(kind=EvidenceIssueKind.UNSUPPORTED_PROJECT, file_path=relative)
    return None, [issue]


def _reference_lines(project_text: str, count: int) -> list[int | None]:
    """The line of each `<ProjectReference` start tag, in document order (the order
    ElementTree iterates them); all None when the tags found outside comments are not
    `count`. ElementTree keeps no line numbers, so the tags are found in the text."""
    text = _XML_COMMENT.sub(lambda match: re.sub(r"[^\n]", " ", match[0]), project_text)
    lines: list[int | None] = [
        text.count("\n", 0, match.start()) + 1 for match in _REFERENCE_TAG.finditer(text)
    ]
    return lines if len(lines) == count else [None] * count


def _project_reference_target(
    project_path: str, include: str | None, project_files: set[str]
) -> tuple[str | None, ProjectReferenceStatus]:
    if (
        include is None
        or not include.strip()
        or len(include) > MAX_RELATIVE_PATH_CHARS
        or ";" in include
        or _UNSUPPORTED_PATH.search(include)
    ):
        return None, ProjectReferenceStatus.UNSUPPORTED
    normalized_include = include.strip().replace("\\", "/")
    if (
        normalized_include.startswith("/")
        or normalized_include.startswith("//")
        or _DRIVE_PATH.match(normalized_include)
    ):
        return None, ProjectReferenceStatus.UNSAFE
    if ":" in normalized_include:
        return None, ProjectReferenceStatus.UNSUPPORTED
    target = posixpath.normpath(posixpath.join(posixpath.dirname(project_path), normalized_include))
    if target in {"", ".", ".."} or target.startswith("../"):
        return None, ProjectReferenceStatus.UNSAFE
    if len(target) > MAX_RELATIVE_PATH_CHARS:
        return None, ProjectReferenceStatus.UNSUPPORTED
    if target in project_files:
        return target, ProjectReferenceStatus.MATCHED
    case_matches = [path for path in project_files if path.casefold() == target.casefold()]
    if len(case_matches) == 1:
        return target, ProjectReferenceStatus.CASE_MISMATCHED
    if len(case_matches) > 1:
        return None, ProjectReferenceStatus.UNSUPPORTED
    return target, ProjectReferenceStatus.MISSING


def generated_source_path(path: str) -> bool:
    """True for generated, migration and properties sources."""
    parts = path.casefold().split("/")
    name = parts[-1]
    return bool(GENERATED_DIRS & set(parts[:-1])) or name.endswith(GENERATED_SUFFIXES)


def safe_file(root: Path, relative: str) -> Path:
    """The resolved file; ValueError when a link is on the way or it leaves the root."""
    candidate = root.joinpath(*relative.split("/"))
    current = root
    for part in relative.split("/"):
        current = current / part
        if _is_link_or_junction(current):
            raise ValueError("unsafe linked path")
    resolved = candidate.resolve(strict=True)
    if not resolved.is_relative_to(root):
        raise ValueError("path leaves repository")
    return resolved


def _is_link_or_junction(path: Path) -> bool:
    return path.is_symlink() or path.is_junction()


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]
