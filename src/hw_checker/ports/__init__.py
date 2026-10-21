"""Ports layer: interfaces (Protocol/ABC) for every external capability.

May depend on: domain.
"""

from hw_checker.ports.assignments import AssignmentSource
from hw_checker.ports.checks import CheckCatalog
from hw_checker.ports.clock import Clock
from hw_checker.ports.code_host import CodeHost
from hw_checker.ports.execution import CodeRunner, DatabaseProvisioner
from hw_checker.ports.git import GitClient
from hw_checker.ports.llm import LLMClient
from hw_checker.ports.reporting import ReportWriter
from hw_checker.ports.roster import RosterReader
from hw_checker.ports.storage import FindingRepository, ProposalRepository, RunRepository

__all__ = [
    "AssignmentSource",
    "CheckCatalog",
    "Clock",
    "CodeHost",
    "CodeRunner",
    "DatabaseProvisioner",
    "FindingRepository",
    "GitClient",
    "LLMClient",
    "ProposalRepository",
    "ReportWriter",
    "RosterReader",
    "RunRepository",
]
