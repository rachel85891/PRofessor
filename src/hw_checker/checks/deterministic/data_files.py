"""Data files the code opens that are not in the repo, read from the execution result.

When a hard-coded path names a data file the repo lacks (the student has it on their own
machine only), the runner creates it empty in its own copy, so the app can start and be
probed, and records it (`PathRewrite.created_path`, design §9 step 4). This check turns each
such file into one finding, once per path, however many literals name it.
"""

from pydantic import BaseModel, ConfigDict

from hw_checker.checks.deterministic.findings import deterministic_finding
from hw_checker.checks.deterministic.messages import DATA_FILE_NOT_IN_REPO
from hw_checker.checks.registry import check
from hw_checker.domain import CheckInput, Finding, NotEvaluatedReason, StepStatus


class DataFilesParams(BaseModel):
    """The check takes no params."""

    model_config = ConfigDict(frozen=True, extra="forbid")


@check(
    "data_files_in_repo",
    DataFilesParams,
    "Every data file the code opens by a hard-coded path is in the repo (the run created "
    "none empty because it was missing).",
)
def data_files_in_repo(
    check_input: CheckInput, params: DataFilesParams
) -> tuple[Finding, ...] | NotEvaluatedReason:
    """One finding per data file the run created empty because the repo lacks it. Not
    evaluated when the repo was not run, or its paths were never read."""
    execution = check_input.execution
    if execution is None:
        return NotEvaluatedReason.NO_EXECUTION
    if execution.prepare_status is StepStatus.FAILED and not execution.path_rewrites:
        return NotEvaluatedReason.NO_EXECUTION
    created = dict.fromkeys(
        rewrite.created_path for rewrite in execution.path_rewrites if rewrite.created_path
    )
    return tuple(
        deterministic_finding(check_input, DATA_FILE_NOT_IN_REPO.render(path=path), (path,))
        for path in created
    )
