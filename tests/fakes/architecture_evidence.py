"""In-memory fake for the ArchitectureEvidenceReader port."""

from collections.abc import Mapping
from pathlib import Path

from hw_checker.domain import (
    ArchitectureEvidence,
    EvidenceCoverage,
    EvidenceCoverageIssue,
    EvidenceCoverageStatus,
    EvidenceIssueKind,
)


class FakeArchitectureEvidenceReader:
    """Returns per-root evidence, with explicit unavailable coverage for unknown roots."""

    def __init__(self, evidence: Mapping[Path, ArchitectureEvidence] | None = None) -> None:
        self._evidence = dict(evidence or {})
        self.scanned: list[Path] = []

    def scan(self, root: Path) -> ArchitectureEvidence:
        """Return the configured result for `root`, never a success-shaped empty result."""
        self.scanned.append(root)
        if root in self._evidence:
            return self._evidence[root]
        issue = EvidenceCoverageIssue(kind=EvidenceIssueKind.REPOSITORY_UNAVAILABLE)
        coverage = EvidenceCoverage(
            status=EvidenceCoverageStatus.UNAVAILABLE,
            files_examined=0,
            issues=(issue,),
        )
        return ArchitectureEvidence(
            inventory_coverage=coverage,
            signal_coverage=coverage,
        )
