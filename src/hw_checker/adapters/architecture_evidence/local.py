"""Bounded local inventory and lexical evidence extraction for an untrusted repository."""

import os
import posixpath
import re
import xml.etree.ElementTree as ElementTree
from pathlib import Path
from typing import Final

from hw_checker.adapters.docker_runner.code_facts import CollectedFacts, facts_in_source
from hw_checker.adapters.docker_runner.csharp_source import mask_source
from hw_checker.domain import (
    ArchitectureEvidence,
    ArchitectureSignal,
    ArchitectureSignalKind,
    EvidenceCoverage,
    EvidenceCoverageIssue,
    EvidenceCoverageStatus,
    EvidenceIssueKind,
    ProjectEvidence,
    ProjectReferenceEvidence,
    ProjectReferenceStatus,
    ServiceLifetime,
    TypeKind,
)
from hw_checker.ports.architecture_evidence import ArchitectureEvidenceReader

_PRIMARY_CONSTRUCTOR = re.compile(
    r"\b(?:class|record(?:\s+class)?)\s+[A-Za-z_]\w*(?:\s*<[^;{}]*>)?\s*\("
)

EXCLUDED_DIRS: Final = frozenset({"bin", "obj", ".git", ".vs"})
GENERATED_DIRS: Final = frozenset({"properties", "migrations"})
GENERATED_SUFFIXES: Final = (".designer.cs", ".g.cs", ".g.i.cs")
MAX_INVENTORY_FILES: Final = 2_000
MAX_DIRECTORY_COUNT: Final = 5_000
MAX_VISITED_FILES: Final = 20_000
MAX_PROJECT_FILE_BYTES: Final = 1_000_000
MAX_SOURCE_FILE_BYTES: Final = 1_000_000
MAX_TOTAL_SOURCE_BYTES: Final = 16_000_000
MAX_RELATIVE_PATH_CHARS: Final = 1_024
MAX_IDENTIFIER_CHARS: Final = 256
_DRIVE_PATH = re.compile(r"^[A-Za-z]:")
_SAFE_SDK_REFERENCE = re.compile(r"^[A-Za-z0-9._/-]{1,256}$")
_UNSUPPORTED_PATH = re.compile(r"[$*?\[\]{}]")
_ALIAS = re.compile(r"(?m)^\s*(?:global\s+)?using\s+[A-Za-z_]\w*\s*=")
_PREPROCESSOR = re.compile(r"(?m)^\s*#\s*(?:if|elif|else|endif)\b")
_TYPE_DECLARATION = re.compile(
    r"\b(?P<kind>class|record(?:\s+class)?)\s+(?P<name>[A-Za-z_]\w*)"
    r"(?:\s*<[^;{}]*>)?[^;{}]*\{"
)
_CONSTRUCTOR = re.compile(
    r"^\s*(?:(?:public|private|protected|internal|unsafe|extern)\s+)*"
    r"(?P<name>[A-Za-z_]\w*)\s*\((?P<parameters>[^(){}]*)\)"
    r"\s*(?::\s*(?:base|this)\s*\([^{}]*\)\s*)?$"
)
_PARAMETER = re.compile(
    r"(?:(?:this|params|ref|in|out|scoped)\s+)*"
    r"(?P<type>[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*(?:<[^<>]*>)?(?:\[\])?\??)"
    r"\s+[A-Za-z_]\w*(?:\s*=\s*[^,]+)?"
)
_REGISTRATION = re.compile(
    r"\b(?P<name>Add(?:Singleton|Scoped|Transient)(?:Keyed)?|"
    r"AddKeyed(?:Singleton|Scoped|Transient))\s*(?=[<(])"
)
_LIFETIMES: Final = {
    "Singleton": ServiceLifetime.SINGLETON,
    "Scoped": ServiceLifetime.SCOPED,
    "Transient": ServiceLifetime.TRANSIENT,
}
_TYPE_TEXT = r"[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*(?:\s*<[^()<>]*(?:<[^()<>]*>[^()<>]*)*>)?"
_TYPE_ARGUMENT = re.compile(rf"{_TYPE_TEXT}\??")
_EMPTY_CALL = re.compile(r"\s*\(\s*\)")
_TYPEOF_CALL = re.compile(
    rf"\(\s*typeof\s*\(\s*(?P<service>{_TYPE_TEXT})\s*\)\s*"
    rf"(?:,\s*typeof\s*\(\s*(?P<implementation>{_TYPE_TEXT})\s*\)\s*)?\)"
)
_DOCTYPE = re.compile(r"<!\s*DOCTYPE\b", re.IGNORECASE)


class LocalArchitectureEvidenceReader(ArchitectureEvidenceReader):
    """Read a bounded repository-local inventory and report lexical signals only.

    The adapter never runs MSBuild or student code, follows links, or opens a referenced
    project path. Source is decoded strictly and kept only in local variables while scanning.
    """

    def scan(self, root: Path) -> ArchitectureEvidence:
        """Return evidence with separate inventory and C# signal coverage."""
        if root.is_symlink() or root.is_junction():
            return _unavailable(EvidenceIssueKind.UNSAFE_PATH_SKIPPED)
        try:
            resolved_root = root.resolve(strict=True)
            if not resolved_root.is_dir():
                return _unavailable()
        except (OSError, RuntimeError):
            return _unavailable()

        inventory_issues: list[EvidenceCoverageIssue] = []
        source_issues: list[EvidenceCoverageIssue] = []
        supported_files, walk_issue = _supported_files(resolved_root)
        if walk_issue is not None:
            inventory_issues.append(walk_issue)
            source_issues.append(walk_issue)

        project_files = tuple(path for path in supported_files if path.lower().endswith(".csproj"))
        project_set = set(project_files)
        projects: list[ProjectEvidence] = []
        references: list[ProjectReferenceEvidence] = []
        for relative in project_files:
            parsed, file_references, issues = _read_project(resolved_root, relative, project_set)
            if parsed is not None:
                projects.append(parsed)
            references.extend(file_references)
            inventory_issues.extend(issues)

        source_files = tuple(
            path
            for path in supported_files
            if path.lower().endswith(".cs") and not _generated_source_path(path)
        )
        sources: list[tuple[str, str]] = []
        total_source_bytes = 0
        if not source_files:
            source_issues.append(EvidenceCoverageIssue(kind=EvidenceIssueKind.NO_CSHARP_SOURCE))
        for relative in source_files:
            if total_source_bytes >= MAX_TOTAL_SOURCE_BYTES:
                source_issues.append(
                    EvidenceCoverageIssue(
                        kind=EvidenceIssueKind.SCAN_LIMIT_REACHED, file_path=relative
                    )
                )
                break
            try:
                path = _safe_file(resolved_root, relative)
                size = path.stat().st_size
                if size > MAX_SOURCE_FILE_BYTES:
                    source_issues.append(
                        EvidenceCoverageIssue(
                            kind=EvidenceIssueKind.FILE_TOO_LARGE, file_path=relative
                        )
                    )
                    continue
                data = path.read_bytes()
            except OSError:
                source_issues.append(
                    EvidenceCoverageIssue(
                        kind=EvidenceIssueKind.FILE_UNREADABLE, file_path=relative
                    )
                )
                continue
            except ValueError:
                source_issues.append(
                    EvidenceCoverageIssue(
                        kind=EvidenceIssueKind.UNSAFE_PATH_SKIPPED, file_path=relative
                    )
                )
                continue
            total_source_bytes += len(data)
            if total_source_bytes > MAX_TOTAL_SOURCE_BYTES:
                source_issues.append(
                    EvidenceCoverageIssue(
                        kind=EvidenceIssueKind.SCAN_LIMIT_REACHED, file_path=relative
                    )
                )
                break
            try:
                text = data.decode("utf-8")
            except UnicodeDecodeError:
                source_issues.append(
                    EvidenceCoverageIssue(kind=EvidenceIssueKind.INVALID_UTF8, file_path=relative)
                )
                continue
            sources.append((relative, text))

        signals, issues = _signals_in_sources(sources)
        source_issues.extend(issues)
        if source_files:
            source_issues.append(
                EvidenceCoverageIssue(kind=EvidenceIssueKind.HEURISTIC_CSHARP_SCAN)
            )
        return ArchitectureEvidence(
            project_files=project_files,
            projects=tuple(projects),
            project_references=tuple(references),
            signals=tuple(sorted(signals, key=_signal_sort_key)),
            inventory_coverage=_coverage(
                inventory_issues,
                len(project_files),
            ),
            signal_coverage=_coverage(
                source_issues,
                len(source_files),
                unavailable=not source_files,
            ),
        )


def _supported_files(root: Path) -> tuple[tuple[str, ...], EvidenceCoverageIssue | None]:
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


def _read_project(
    root: Path, relative: str, project_files: set[str]
) -> tuple[
    ProjectEvidence | None,
    tuple[ProjectReferenceEvidence, ...],
    tuple[EvidenceCoverageIssue, ...],
]:
    try:
        path = _safe_file(root, relative)
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
    evidence = ProjectEvidence(file_path=relative, sdk_references=sdk_references)
    references: list[ProjectReferenceEvidence] = []
    issues: list[EvidenceCoverageIssue] = [
        EvidenceCoverageIssue(kind=EvidenceIssueKind.UNSUPPORTED_PROJECT, file_path=relative)
        for value in raw_sdk_references
        if not _SAFE_SDK_REFERENCE.fullmatch(value.strip())
    ]
    for element in xml_root.iter():
        if _local_name(element.tag) != "ProjectReference":
            continue
        include = element.get("Include")
        target, status = _project_reference_target(relative, include, project_files)
        references.append(
            ProjectReferenceEvidence(
                source_project=relative,
                target_project=target,
                status=status,
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


def _signals_in_sources(
    sources: list[tuple[str, str]],
) -> tuple[tuple[ArchitectureSignal, ...], tuple[EvidenceCoverageIssue, ...]]:
    """Two passes: interfaces from every file first, then constructors matched against them."""
    issues: list[EvidenceCoverageIssue] = []
    readable: list[tuple[str, str]] = []
    collected = CollectedFacts()
    for file_path, text in sources:
        masked = mask_source(text).text
        issues.extend(_lexical_issues(masked, file_path))
        if not _balanced_csharp(masked):
            issues.append(
                EvidenceCoverageIssue(kind=EvidenceIssueKind.AMBIGUOUS_SYNTAX, file_path=file_path)
            )
            continue
        facts_in_source(text, file_path, collected)
        readable.append((file_path, masked))

    signals: list[ArchitectureSignal] = []
    declarations: dict[str, int] = {}
    for fact in collected.types:
        if fact.kind is not TypeKind.INTERFACE:
            continue
        if len(fact.name) > MAX_IDENTIFIER_CHARS:
            issues.append(
                EvidenceCoverageIssue(
                    kind=EvidenceIssueKind.AMBIGUOUS_SYNTAX,
                    file_path=fact.file_path,
                    line=fact.line,
                )
            )
            continue
        declarations[fact.name] = declarations.get(fact.name, 0) + 1
        signals.append(
            ArchitectureSignal(
                kind=ArchitectureSignalKind.INTERFACE_DECLARATION,
                file_path=fact.file_path,
                line=fact.line,
                subject=fact.name,
            )
        )

    for file_path, masked in readable:
        file_signals, file_issues = _signals_in_file(masked, file_path, declarations)
        signals.extend(file_signals)
        issues.extend(file_issues)
    return tuple(signals), tuple(issues)


def _lexical_issues(masked: str, file_path: str) -> list[EvidenceCoverageIssue]:
    issues: list[EvidenceCoverageIssue] = []
    if _PREPROCESSOR.search(masked):
        issues.append(
            EvidenceCoverageIssue(
                kind=EvidenceIssueKind.PREPROCESSOR_CONDITION, file_path=file_path
            )
        )
    if _ALIAS.search(masked):
        issues.append(
            EvidenceCoverageIssue(kind=EvidenceIssueKind.USING_ALIAS, file_path=file_path)
        )
    for primary in _PRIMARY_CONSTRUCTOR.finditer(masked):
        issues.append(
            EvidenceCoverageIssue(
                kind=EvidenceIssueKind.AMBIGUOUS_SYNTAX,
                file_path=file_path,
                line=masked.count("\n", 0, primary.start()) + 1,
            )
        )
    return issues


def _signals_in_file(
    masked: str, file_path: str, declarations: dict[str, int]
) -> tuple[list[ArchitectureSignal], list[EvidenceCoverageIssue]]:
    signals: list[ArchitectureSignal] = []
    issues: list[EvidenceCoverageIssue] = []
    for match in _REGISTRATION.finditer(masked):
        service, implementation = _registered_types(masked, match.end())
        signals.append(
            ArchitectureSignal(
                kind=ArchitectureSignalKind.REGISTRATION_CANDIDATE,
                file_path=file_path,
                line=masked.count("\n", 0, match.start("name")) + 1,
                subject=service,
                related_subject=implementation,
                registration_method=match["name"],
                lifetime=next(
                    lifetime for word, lifetime in _LIFETIMES.items() if word in match["name"]
                ),
            )
        )

    for declaration in _TYPE_DECLARATION.finditer(masked):
        type_name = declaration["name"]
        if len(type_name) > MAX_IDENTIFIER_CHARS:
            issues.append(
                EvidenceCoverageIssue(
                    kind=EvidenceIssueKind.AMBIGUOUS_SYNTAX,
                    file_path=file_path,
                    line=masked.count("\n", 0, declaration.start()) + 1,
                )
            )
            continue
        opening = declaration.end() - 1
        closing = _matching_brace(masked, opening)
        if closing is None:
            issues.append(
                EvidenceCoverageIssue(
                    kind=EvidenceIssueKind.AMBIGUOUS_SYNTAX,
                    file_path=file_path,
                    line=masked.count("\n", 0, opening) + 1,
                )
            )
            continue
        body = masked[opening + 1 : closing]
        for header, offset in _top_level_member_headers(body):
            constructor = _CONSTRUCTOR.fullmatch(header)
            if constructor is None or constructor["name"] != type_name:
                continue
            line = masked.count("\n", 0, opening + 1 + offset + constructor.start("name")) + 1
            signals.append(
                ArchitectureSignal(
                    kind=ArchitectureSignalKind.CONSTRUCTOR_CANDIDATE,
                    file_path=file_path,
                    line=line,
                    subject=type_name,
                )
            )
            parameter_types = _parameter_types(constructor["parameters"])
            if parameter_types is None:
                issues.append(
                    EvidenceCoverageIssue(
                        kind=EvidenceIssueKind.AMBIGUOUS_SYNTAX,
                        file_path=file_path,
                        line=line,
                    )
                )
                continue
            for parameter_type in parameter_types:
                count = declarations.get(
                    parameter_type.rstrip("?").split("<", 1)[0].split(".")[-1], 0
                )
                if not count:
                    continue
                signals.append(
                    ArchitectureSignal(
                        kind=ArchitectureSignalKind.CONSTRUCTOR_INTERFACE_PARAMETER,
                        file_path=file_path,
                        line=line,
                        subject=parameter_type,
                        related_subject=type_name,
                    )
                )
                if count > 1:
                    issues.append(
                        EvidenceCoverageIssue(
                            kind=EvidenceIssueKind.DUPLICATE_TYPE_NAME,
                            file_path=file_path,
                            line=line,
                        )
                    )
    return signals, issues


def _registered_types(masked: str, position: int) -> tuple[str | None, str | None]:
    """The service and implementation types of `<IX, X>()` or `(typeof(IX), typeof(X))`.

    Any other call shape (a factory, an instance, a key) names no type here: (None, None).
    """
    if masked.startswith("<", position):
        closing = _closing_angle(masked, position)
        if closing is None or not _EMPTY_CALL.match(masked, closing + 1):
            return None, None
        arguments = _split_top_level(masked[position + 1 : closing])
        if arguments is None or len(arguments) > 2:
            return None, None
        if any(not _TYPE_ARGUMENT.fullmatch(argument) for argument in arguments):
            return None, None
        types = arguments
    else:
        typed = _TYPEOF_CALL.match(masked, position)
        if typed is None:
            return None, None
        types = [name for name in (typed["service"], typed["implementation"]) if name]
    if any(len(name) > MAX_IDENTIFIER_CHARS for name in types):
        return None, None
    return types[0], types[1] if len(types) == 2 else None


def _closing_angle(source: str, opening: int) -> int | None:
    depth = 0
    for index in range(opening, len(source)):
        character = source[index]
        if character == "<":
            depth += 1
        elif character == ">":
            depth -= 1
            if depth == 0:
                return index
        elif character in "(){};":
            return None
    return None


def _split_top_level(text: str) -> list[str] | None:
    """Split on commas outside `<>`; None when the angle brackets are unbalanced."""
    parts: list[str] = []
    start = depth = 0
    for index, character in enumerate(text):
        if character == "<":
            depth += 1
        elif character == ">":
            depth -= 1
            if depth < 0:
                return None
        elif character == "," and depth == 0:
            parts.append(text[start:index].strip())
            start = index + 1
    if depth:
        return None
    parts.append(text[start:].strip())
    return parts


def _parameter_types(parameters: str) -> tuple[str, ...] | None:
    if not parameters.strip():
        return ()
    parts = _split_top_level(parameters)
    if parts is None:
        return None
    result: list[str] = []
    for part in parts:
        match = _PARAMETER.fullmatch(part)
        if match is None:
            return None
        if len(match["type"]) > MAX_IDENTIFIER_CHARS:
            return None
        result.append(match["type"])
    return tuple(result)


def _top_level_member_headers(body: str) -> tuple[tuple[str, int], ...]:
    headers: list[tuple[str, int]] = []
    depth = 0
    start = 0
    for index, character in enumerate(body):
        if character == "{" and depth == 0:
            headers.append((body[start:index], start))
            depth = 1
        elif character == "{":
            depth += 1
        elif character == "}":
            depth -= 1
            if depth == 0:
                start = index + 1
        elif character == ";" and depth == 0:
            headers.append((body[start:index], start))
            start = index + 1
    return tuple(headers)


def _matching_brace(source: str, opening: int) -> int | None:
    depth = 0
    for index in range(opening, len(source)):
        if source[index] == "{":
            depth += 1
        elif source[index] == "}":
            depth -= 1
            if depth == 0:
                return index
    return None


def _balanced_csharp(source: str) -> bool:
    pairs = {")": "(", "]": "[", "}": "{"}
    stack: list[str] = []
    for character in source:
        if character in "([{":
            stack.append(character)
        elif character in pairs and (not stack or stack.pop() != pairs[character]):
            return False
    return not stack


def _generated_source_path(path: str) -> bool:
    parts = path.casefold().split("/")
    name = parts[-1]
    return bool(GENERATED_DIRS & set(parts[:-1])) or name.endswith(GENERATED_SUFFIXES)


def _safe_file(root: Path, relative: str) -> Path:
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


def _coverage(
    issues: list[EvidenceCoverageIssue],
    files_examined: int,
    *,
    unavailable: bool = False,
) -> EvidenceCoverage:
    status = (
        EvidenceCoverageStatus.UNAVAILABLE
        if unavailable
        else EvidenceCoverageStatus.PARTIAL
        if issues
        else EvidenceCoverageStatus.COMPLETE
    )
    return EvidenceCoverage(status=status, files_examined=files_examined, issues=tuple(issues))


def _signal_sort_key(
    signal: ArchitectureSignal,
) -> tuple[str, int, str, str]:
    return (
        signal.file_path,
        signal.line,
        signal.kind.value,
        signal.subject or "",
    )


def _unavailable(
    issue_kind: EvidenceIssueKind = EvidenceIssueKind.REPOSITORY_UNAVAILABLE,
) -> ArchitectureEvidence:
    issue = EvidenceCoverageIssue(kind=issue_kind)
    return ArchitectureEvidence(
        inventory_coverage=EvidenceCoverage(
            status=EvidenceCoverageStatus.UNAVAILABLE,
            files_examined=0,
            issues=(issue,),
        ),
        signal_coverage=EvidenceCoverage(
            status=EvidenceCoverageStatus.UNAVAILABLE,
            files_examined=0,
            issues=(issue,),
        ),
    )
