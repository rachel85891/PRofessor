"""Evaluator for `build` requirements: a finding only for a build failure the student caused."""

import re
from dataclasses import dataclass
from pathlib import Path

from pydantic import ValidationError

from hw_checker.checks.deterministic.findings import deterministic_finding
from hw_checker.checks.deterministic.messages import BUILD_FAILED, BUILD_FAILED_NO_DETAIL
from hw_checker.domain import CheckInput, Evidence, Finding, NotEvaluatedReason, StepStatus

CONTAINER_SOURCE_ROOT = "/work/src/"
"""Where the runner copies the working copy; compiler paths start with it."""

_COMPILER_ERROR = re.compile(
    re.escape(CONTAINER_SOURCE_ROOT)
    + r"(?P<path>[^\s(]+)\((?P<line>\d+),\d+\): error (?P<code>CS\d{4}): (?P<text>[^\[\r\n]+)"
)


@dataclass(frozen=True)
class CompilerError:
    """The first compiler error of a build log, located in the working copy."""

    path: str
    line: int
    code: str
    text: str


def evaluate_build(check_input: CheckInput) -> tuple[Finding, ...] | NotEvaluatedReason:
    """Return a finding when the build failed because of the student's code.

    A build failure that is not a student issue (environment, timeout, no project) is not
    evaluated rather than reported.
    """
    execution = check_input.execution
    if execution is None:
        return NotEvaluatedReason.NO_EXECUTION
    if execution.prepare_status is StepStatus.FAILED:
        return NotEvaluatedReason.ENVIRONMENT_ISSUE
    if execution.build_status is not StepStatus.FAILED:
        return ()
    kind = execution.failure_kind
    if kind is None or not kind.is_student_issue:
        return NotEvaluatedReason.ENVIRONMENT_ISSUE
    error = first_compiler_error(execution.logs_excerpt)
    if error is None:
        message = BUILD_FAILED_NO_DETAIL.render()
        return (deterministic_finding(check_input, message, (kind.value, "no detail")),)
    message = BUILD_FAILED.render(error=f"{error.code}: {error.text.strip()}")
    facts = (kind.value, error.code, error.path, str(error.line))
    evidence = _evidence(check_input.repo_dir, error)
    return (deterministic_finding(check_input, message, facts, evidence),)


def first_compiler_error(log: str) -> CompilerError | None:
    """The first `error CSxxxx` in a build log whose file lies in the working copy."""
    match = _COMPILER_ERROR.search(log)
    if match is None:
        return None
    return CompilerError(
        path=match["path"], line=int(match["line"]), code=match["code"], text=match["text"]
    )


def _evidence(repo_dir: Path, error: CompilerError) -> Evidence | None:
    """Evidence only when the file and line really exist in the working copy."""
    root = repo_dir.resolve()
    path = (root / error.path).resolve()
    if not path.is_relative_to(root) or not path.is_file():
        return None
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    if not 1 <= error.line <= len(lines) or not lines[error.line - 1].strip():
        return None
    try:
        return Evidence(
            file_path=error.path,
            line_start=error.line,
            line_end=error.line,
            quote=lines[error.line - 1].strip(),
        )
    except ValidationError:
        return None
