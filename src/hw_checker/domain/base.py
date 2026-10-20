"""Shared base model and constrained field types for the domain models."""

import re
from typing import Annotated

from pydantic import AfterValidator, BaseModel, ConfigDict, Field

_DRIVE_LETTER = re.compile(r"^[A-Za-z]:")


class DomainModel(BaseModel):
    """Base for every domain model: immutable and rejecting unknown fields."""

    model_config = ConfigDict(frozen=True, extra="forbid")


def _reject_empty_or_padded(value: str) -> str:
    if not value:
        raise ValueError("must not be empty")
    if value != value.strip():
        raise ValueError("must not have leading or trailing whitespace")
    return value


def _reject_blank(value: str) -> str:
    if not value.strip():
        raise ValueError("must not be blank")
    return value


def _validate_repo_relative_path(value: str) -> str:
    if not value.strip():
        raise ValueError("must not be blank")
    if "\\" in value:
        raise ValueError("must use forward slashes")
    if value.startswith("/"):
        raise ValueError("must be relative to the repo root, not absolute")
    if _DRIVE_LETTER.match(value):
        raise ValueError("must not start with a drive letter")
    if ".." in value.split("/"):
        raise ValueError("must not contain '..' segments")
    return value


Identifier = Annotated[str, AfterValidator(_reject_empty_or_padded)]
"""A non-empty id without leading or trailing whitespace. Formats are defined elsewhere."""

NonBlankStr = Annotated[str, AfterValidator(_reject_blank)]
"""Text that must contain at least one non-whitespace character."""

RepoRelativePath = Annotated[str, AfterValidator(_validate_repo_relative_path)]
"""A path relative to the repo root, with forward slashes and no '..' segments.

Kept as text rather than `pathlib.Path`, so it means the same on Windows and in containers.
"""

HttpStatusCode = Annotated[int, Field(ge=100, le=599)]
"""An HTTP status code."""
