"""Read-only local architecture-evidence capability."""

from pathlib import Path
from typing import Protocol

from hw_checker.domain.architecture_evidence import ArchitectureEvidence


class ArchitectureEvidenceReader(Protocol):
    """Collect factual project and C# signals without executing repository contents."""

    def scan(self, root: Path) -> ArchitectureEvidence:
        """Scan one checked repository and return explicit coverage with its evidence."""
        ...
