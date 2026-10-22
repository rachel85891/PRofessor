"""Pydantic models of the requirements.yaml file format (see assignments/README.md).

They check the file's shape and reject unknown keys. Domain rules are checked afterwards,
when the entries are mapped to domain models.
"""

from typing import Any

from pydantic import BaseModel, ConfigDict


class _FileModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ProbeEntry(_FileModel):
    """One HTTP probe of an `http` requirement."""

    method: str
    path: str
    path_alternatives: list[str] = []
    body: str | dict[str, Any] | list[Any] | None = None
    expected_status: int
    allowed_statuses: list[int] = []
    notes: str = ""


class RequirementEntry(_FileModel):
    """One requirement in a requirements.yaml file."""

    id: str
    kind: str
    title: str
    description: str = ""
    severity: str
    allowed_alternatives: list[str] = []
    check: str | None = None
    params: dict[str, Any] = {}
    probes: list[ProbeEntry] = []


class AssignmentFile(_FileModel):
    """The whole requirements.yaml file of one assignment.

    Requirement entries are kept raw here and validated one by one with `RequirementEntry`,
    so a problem in one requirement does not hide problems in the others.
    """

    id: str
    order: int
    title: str
    taught_techniques: list[str] = []
    lenient_notes: list[str] = []
    requirements: list[Any] = []
