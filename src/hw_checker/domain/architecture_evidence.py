"""Factual, repository-local project and dependency-injection evidence."""

from enum import StrEnum
from typing import Annotated

from pydantic import Field

from hw_checker.domain.base import DomainModel, NonBlankStr, RepoRelativePath

EvidencePath = Annotated[RepoRelativePath, Field(max_length=1_024)]
"""A bounded repository-relative path carried by local evidence."""
EvidenceIdentifier = Annotated[NonBlankStr, Field(max_length=256)]
"""A bounded source identifier, never a source excerpt."""


class EvidenceCoverageStatus(StrEnum):
    """How much of one evidence dimension could be scanned."""

    COMPLETE = "complete"
    PARTIAL = "partial"
    UNAVAILABLE = "unavailable"


class EvidenceIssueKind(StrEnum):
    """A bounded reason why evidence may be incomplete or unsupported."""

    REPOSITORY_UNAVAILABLE = "repository_unavailable"
    UNSAFE_PATH_SKIPPED = "unsafe_path_skipped"
    SCAN_LIMIT_REACHED = "scan_limit_reached"
    FILE_TOO_LARGE = "file_too_large"
    FILE_UNREADABLE = "file_unreadable"
    INVALID_UTF8 = "invalid_utf8"
    MALFORMED_PROJECT = "malformed_project"
    UNSUPPORTED_PROJECT = "unsupported_project"
    UNSAFE_PROJECT_REFERENCE = "unsafe_project_reference"
    MISSING_PROJECT_REFERENCE = "missing_project_reference"
    CASE_MISMATCHED_PROJECT_REFERENCE = "case_mismatched_project_reference"
    NO_CSHARP_SOURCE = "no_csharp_source"
    PREPROCESSOR_CONDITION = "preprocessor_condition"
    USING_ALIAS = "using_alias"
    AMBIGUOUS_SYNTAX = "ambiguous_syntax"
    DUPLICATE_TYPE_NAME = "duplicate_type_name"
    """A matched simple type name is declared more than once in the repository."""
    HEURISTIC_CSHARP_SCAN = "heuristic_csharp_scan"


class EvidenceCoverageIssue(DomainModel):
    """A structured limitation without source text or an absolute path."""

    kind: EvidenceIssueKind
    file_path: EvidencePath | None = None
    line: int | None = Field(default=None, ge=1)


class EvidenceCoverage(DomainModel):
    """Coverage for project inventory or source-signal scanning."""

    status: EvidenceCoverageStatus
    files_examined: int = Field(ge=0)
    issues: tuple[EvidenceCoverageIssue, ...] = ()


class ProjectReferenceStatus(StrEnum):
    """Lexical status of a relative ProjectReference against the scanned inventory."""

    MATCHED = "matched"
    MISSING = "missing"
    CASE_MISMATCHED = "case_mismatched"
    UNSUPPORTED = "unsupported"
    UNSAFE = "unsafe"


class ProjectReferenceEvidence(DomainModel):
    """A safe, repository-relative reference; MSBuild expressions are not evaluated."""

    source_project: EvidencePath
    target_project: EvidencePath | None
    status: ProjectReferenceStatus


class ProjectEvidence(DomainModel):
    """Factual project-file inventory and literal SDK references."""

    file_path: EvidencePath
    sdk_references: tuple[EvidenceIdentifier, ...] = ()


class ArchitectureSignalKind(StrEnum):
    """A syntactic signal only; none of these implies correctness or a policy result."""

    INTERFACE_DECLARATION = "interface_declaration"
    CONSTRUCTOR_CANDIDATE = "constructor_candidate"
    CONSTRUCTOR_INTERFACE_PARAMETER = "constructor_interface_parameter"
    REGISTRATION_CANDIDATE = "registration_candidate"


class ArchitectureSignal(DomainModel):
    """A source location and identifiers extracted from supported C# syntax."""

    kind: ArchitectureSignalKind
    file_path: EvidencePath
    line: int = Field(ge=1)
    subject: EvidenceIdentifier
    related_subject: EvidenceIdentifier | None = None


class ArchitectureEvidence(DomainModel):
    """Read-only facts for one repository; source text and student identity are omitted."""

    project_files: tuple[EvidencePath, ...] = ()
    projects: tuple[ProjectEvidence, ...] = ()
    project_references: tuple[ProjectReferenceEvidence, ...] = ()
    signals: tuple[ArchitectureSignal, ...] = ()
    inventory_coverage: EvidenceCoverage
    signal_coverage: EvidenceCoverage
