"""Evaluator for `build` requirements: the project builds and starts without crashing.

A finding only for a failure the student caused: a student build failure, or a startup crash
after a successful build.
"""

import re
from dataclasses import dataclass
from pathlib import Path

from pydantic import ValidationError

from hw_checker.checks.deterministic.findings import deterministic_finding
from hw_checker.checks.deterministic.messages import (
    BUILD_FAILED,
    BUILD_FAILED_NO_DETAIL,
    STARTUP_CRASHED,
    STARTUP_CRASHED_NO_DETAIL,
)
from hw_checker.domain import (
    CheckInput,
    Evidence,
    ExecutionResult,
    FailureKind,
    Finding,
    NotEvaluatedReason,
    StepStatus,
)

CONTAINER_SOURCE_ROOT = "/work/src/"
"""Where the runner copies the working copy; compiler and stack trace paths start with it."""

_COMPILER_ERROR = re.compile(
    re.escape(CONTAINER_SOURCE_ROOT)
    + r"(?P<path>[^\s(]+)\((?P<line>\d+),\d+\): error (?P<code>CS\d{4}): (?P<text>[^\[\r\n]+)"
)
_UNHANDLED_EXCEPTION = re.compile(
    r"Unhandled exception\.?[ \t]*(?:(?P<type>[\w.`]+(?:Exception|Error))\b)?[^\r\n]*"
)
_STACK_FRAME = re.compile(
    r" in " + re.escape(CONTAINER_SOURCE_ROOT) + r"(?P<path>[^\r\n:]+):line (?P<line>\d+)"
)


@dataclass(frozen=True)
class CompilerError:
    """The first compiler error of a build log, located in the working copy."""

    path: str
    line: int
    code: str
    text: str


@dataclass(frozen=True)
class StartupCrash:
    """The first unhandled exception of a run log, and where in the working copy it was thrown."""

    line: str
    exception_type: str | None
    path: str | None
    source_line: int | None


def evaluate_build(check_input: CheckInput) -> tuple[Finding, ...] | NotEvaluatedReason:
    """Return a finding when the project does not build, or builds but crashes on startup.

    A failure that is not a student issue (environment, timeout, no project) is not
    evaluated rather than reported.
    """
    execution = check_input.execution
    if execution is None:
        return NotEvaluatedReason.NO_EXECUTION
    if execution.prepare_status is StepStatus.FAILED:
        return NotEvaluatedReason.ENVIRONMENT_ISSUE
    if execution.build_status is StepStatus.FAILED:
        return _build_failure(check_input, execution)
    if execution.run_status is StepStatus.FAILED:
        return _run_failure(check_input, execution)
    return ()


def _build_failure(
    check_input: CheckInput, execution: ExecutionResult
) -> tuple[Finding, ...] | NotEvaluatedReason:
    kind = execution.failure_kind
    if kind is None or not kind.is_student_issue:
        return NotEvaluatedReason.ENVIRONMENT_ISSUE
    error = first_compiler_error(execution.logs_excerpt)
    if error is None:
        message = BUILD_FAILED_NO_DETAIL.render()
        return (deterministic_finding(check_input, message, (kind.value, "no detail")),)
    message = BUILD_FAILED.render(error=f"{error.code}: {error.text.strip()}")
    facts = (kind.value, error.code, error.path, str(error.line))
    evidence = _evidence(check_input.repo_dir, error.path, error.line)
    return (deterministic_finding(check_input, message, facts, evidence),)


def _run_failure(
    check_input: CheckInput, execution: ExecutionResult
) -> tuple[Finding, ...] | NotEvaluatedReason:
    kind = execution.failure_kind
    if kind is FailureKind.DB_UNREACHABLE:
        return ()  # the app started; reaching the database is not what `build` checks
    if kind is not FailureKind.STARTUP_FAILED:
        return NotEvaluatedReason.ENVIRONMENT_ISSUE
    crash = first_startup_crash(execution.logs_excerpt)
    if crash is None:
        message = STARTUP_CRASHED_NO_DETAIL.render()
        return (deterministic_finding(check_input, message, (kind.value, "no detail")),)
    message = STARTUP_CRASHED.render(exception=crash.line)
    facts = (kind.value, crash.exception_type or "no exception type")
    evidence = None
    if crash.path is not None and crash.source_line is not None:
        evidence = _evidence(check_input.repo_dir, crash.path, crash.source_line)
    return (deterministic_finding(check_input, message, facts, evidence),)


def first_compiler_error(log: str) -> CompilerError | None:
    """The first `error CSxxxx` in a build log whose file lies in the working copy."""
    match = _COMPILER_ERROR.search(log)
    if match is None:
        return None
    return CompilerError(
        path=match["path"], line=int(match["line"]), code=match["code"], text=match["text"]
    )


def first_startup_crash(log: str) -> StartupCrash | None:
    """The first `Unhandled exception` line of a run log, with the first stack frame after
    it that lies in the working copy."""
    match = _UNHANDLED_EXCEPTION.search(log)
    if match is None:
        return None
    frame = _STACK_FRAME.search(log, match.end())
    return StartupCrash(
        line=match[0].strip(),
        exception_type=match["type"],
        path=None if frame is None else frame["path"],
        source_line=None if frame is None else int(frame["line"]),
    )


def _evidence(repo_dir: Path, relative_path: str, line: int) -> Evidence | None:
    """Evidence only when the file and line really exist in the working copy."""
    root = repo_dir.resolve()
    path = (root / relative_path).resolve()
    if not path.is_relative_to(root) or not path.is_file():
        return None
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    if not 1 <= line <= len(lines) or not lines[line - 1].strip():
        return None
    try:
        return Evidence(
            file_path=relative_path,
            line_start=line,
            line_end=line,
            quote=lines[line - 1].strip(),
        )
    except ValidationError:
        return None
