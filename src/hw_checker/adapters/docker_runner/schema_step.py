"""The runner's schema step: what is added to the run's copy, and how its answer is read.

For a repo that uses SQL Server and has a `DbContext`, the runner adds one C# file to the Web
project in its temporary copy (`schema_startup_source`) and sets
`ASPNETCORE_HOSTINGSTARTUPASSEMBLIES`, so the app creates the tables of its EF model (or
applies its migrations) while it starts, and prints one marker line. The student's repo and
the fixer's branch are never written to, and no file of the student's changes.

The marker line holds an outcome and, for a failure, the exception type: never a message,
which may hold a connection string.
"""

import re
from dataclasses import dataclass

from hw_checker.adapters.docker_runner.schema_startup_source import (
    MARKER_PREFIX,
    STARTUP_SOURCE,
)
from hw_checker.domain import SchemaOutcome
from hw_checker.domain.execution import SCHEMA_FAILURE_PATTERN

STARTUP_FILE_NAME = "HwCheckerSchemaStartup.cs"
HOSTING_STARTUP_VARIABLE = "ASPNETCORE_HOSTINGSTARTUPASSEMBLIES"
NO_MARKER = "no_marker"
"""The failure reason when the app never printed the marker line."""

_MARKER_LINE = re.compile(
    rf"^{re.escape(MARKER_PREFIX)}"
    r"(?P<outcome>created|migrated|no_context|failed(?: (?P<type>\S+))?)[ \t\r]*(?:\n|\Z)",
    re.MULTILINE,
)
_FAILURE_TYPE = re.compile(SCHEMA_FAILURE_PATTERN)
_UNKNOWN_TYPE = "unknown"


@dataclass(frozen=True)
class SchemaResult:
    """What the schema step answered: an outcome, and for `failed` the reason code."""

    outcome: SchemaOutcome
    failure: str | None = None


def startup_file(web_folder: str) -> dict[str, bytes]:
    """The file to add to the run's copy: its path in the repo (the Web project's folder) and
    its content. The same for every repo."""
    path = f"{web_folder}/{STARTUP_FILE_NAME}" if web_folder else STARTUP_FILE_NAME
    return {path: STARTUP_SOURCE.encode("utf-8")}


def startup_environment(assembly_name: str) -> dict[str, str]:
    """The environment that makes the host run the added file: the Web project's assembly."""
    return {HOSTING_STARTUP_VARIABLE: assembly_name}


def parse_schema_marker(log: str) -> SchemaResult | None:
    """The answer in the app's log, or None when the marker line is not there (yet).

    Other output around the line is ignored; the first marker line counts. A failure whose
    type is missing or not a type's name is recorded as `unknown`, never as its text.
    """
    found = _MARKER_LINE.search(log)
    if found is None:
        return None
    outcome = found["outcome"].split(" ")[0]
    if outcome != SchemaOutcome.FAILED.value:
        return SchemaResult(SchemaOutcome(outcome))
    exception_type = found["type"]
    usable = exception_type is not None and _FAILURE_TYPE.fullmatch(exception_type) is not None
    return SchemaResult(SchemaOutcome.FAILED, exception_type if usable else _UNKNOWN_TYPE)


def without_marker(log: str) -> str:
    """`log` without the marker line: it is the runner's, not part of the app's output."""
    return _MARKER_LINE.sub("", log, count=1)
