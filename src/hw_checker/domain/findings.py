"""Finding models: evidence, findings and the judge's verdict on them (design §5, §10)."""

from enum import StrEnum
from typing import Any, Self

from pydantic import Field, model_validator

from hw_checker.domain.assignment import Severity
from hw_checker.domain.base import DomainModel, Identifier, NonBlankStr, RepoRelativePath


class Evidence(DomainModel):
    """Where in the repo a finding is grounded: file, line range and the quoted code."""

    file_path: RepoRelativePath
    line_start: int = Field(ge=1)
    line_end: int = Field(ge=1)
    quote: NonBlankStr

    @model_validator(mode="after")
    def _line_range_is_ordered(self) -> Self:
        if self.line_end < self.line_start:
            raise ValueError("line_end must be greater than or equal to line_start")
        return self


class FindingSource(StrEnum):
    """What produced a finding."""

    DETERMINISTIC = "deterministic"
    LLM = "llm"


class Finding(DomainModel):
    """A problem found in a repo, tied to one requirement of one assignment."""

    id: Identifier
    requirement_id: Identifier
    assignment_id: Identifier
    severity: Severity
    message_he: NonBlankStr
    message_en: NonBlankStr
    evidence: Evidence | None = None
    source: FindingSource
    confidence: float = Field(ge=0.0, le=1.0)

    @model_validator(mode="after")
    def _llm_findings_have_evidence(self) -> Self:
        if self.source is FindingSource.LLM and self.evidence is None:
            raise ValueError("a finding from the llm must have evidence")
        return self


class Verdict(StrEnum):
    """The judge's decision on a finding."""

    ACCEPTED = "accepted"
    REJECTED = "rejected"


def _finding_severity(validated: dict[str, Any]) -> Severity:
    """Default for `adjusted_severity`: the severity of the already validated finding."""
    finding = validated.get("finding")
    # When the finding itself failed validation, the model fails anyway; any value will do.
    return finding.severity if isinstance(finding, Finding) else Severity.MINOR


class JudgedFinding(DomainModel):
    """A finding with the judge's verdict. Rejected findings are kept for calibration."""

    finding: Finding
    verdict: Verdict
    reason: NonBlankStr
    adjusted_severity: Severity = Field(default_factory=_finding_severity)

    @model_validator(mode="after")
    def _severity_is_never_raised(self) -> Self:
        if self.adjusted_severity > self.finding.severity:
            raise ValueError(
                f"adjusted_severity {self.adjusted_severity} must not be higher than "
                f"the finding's severity {self.finding.severity}"
            )
        return self
