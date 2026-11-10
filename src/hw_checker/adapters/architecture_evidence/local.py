"""Bounded local inventory and lexical evidence extraction for an untrusted repository."""

from pathlib import Path
from typing import Final

from hw_checker.adapters.architecture_evidence.csharp_signals import signals_in_sources
from hw_checker.adapters.architecture_evidence.inventory import (
    generated_source_path,
    inventory_files,
    read_project,
    safe_file,
)
from hw_checker.domain import (
    ArchitectureEvidence,
    ArchitectureSignal,
    EvidenceCoverage,
    EvidenceCoverageIssue,
    EvidenceCoverageStatus,
    EvidenceIssueKind,
    ProjectEvidence,
    ProjectReferenceEvidence,
)
from hw_checker.ports.architecture_evidence import ArchitectureEvidenceReader

MAX_SOURCE_FILE_BYTES: Final = 1_000_000
MAX_TOTAL_SOURCE_BYTES: Final = 16_000_000


class LocalArchitectureEvidenceReader(ArchitectureEvidenceReader):
    """Read a bounded repository-local inventory and report lexical signals only.

    The adapter never runs MSBuild or student code, follows links, or opens a referenced
    project path. Source is decoded strictly and kept only in local variables while scanning.
    C# is read lexically, not compiled: interfaces are collected from every scanned file
    first, then constructor parameters are matched against them by simple name.
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
        supported_files, walk_issue = inventory_files(resolved_root)
        if walk_issue is not None:
            inventory_issues.append(walk_issue)
            source_issues.append(walk_issue)

        project_files = tuple(path for path in supported_files if path.lower().endswith(".csproj"))
        project_set = set(project_files)
        projects: list[ProjectEvidence] = []
        references: list[ProjectReferenceEvidence] = []
        for relative in project_files:
            parsed, file_references, issues = read_project(resolved_root, relative, project_set)
            if parsed is not None:
                projects.append(parsed)
            references.extend(file_references)
            inventory_issues.extend(issues)

        source_files = tuple(
            path
            for path in supported_files
            if path.lower().endswith(".cs") and not generated_source_path(path)
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
                path = safe_file(resolved_root, relative)
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

        signals, issues = signals_in_sources(sources)
        source_issues.extend(issues)
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
