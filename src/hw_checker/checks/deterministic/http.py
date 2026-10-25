"""Evaluator for `http` requirements: one finding per requirement for failed probes."""

from hw_checker.checks.deterministic.findings import deterministic_finding
from hw_checker.checks.deterministic.messages import (
    HTTP_PROBES_FAILED,
    PROBE_NO_ANSWER,
    PROBE_WRONG_STATUS,
    STATUS_SEPARATOR,
    Localized,
)
from hw_checker.domain import (
    CheckInput,
    Finding,
    NotEvaluatedReason,
    ProbeResult,
    RouteResolution,
    StepStatus,
)


def evaluate_http(check_input: CheckInput) -> tuple[Finding, ...] | NotEvaluatedReason:
    """Return one finding naming every failed probe of the requirement.

    Only a successful run is judged, so a build failure never cascades into HTTP findings.
    A probe passes when its status is in the probe's `allowed_statuses`, and only then.
    When a hard-coded path could not be adapted to the container, an unhealthy run or a
    failing probe may be our environment's fault, so it is not evaluated instead. A probe
    that was not sent because none of its paths is declared in the code makes the whole
    requirement not evaluated (`route_not_found`): the route may just be named differently.
    A failing probe whose body did not fit the student's model (a field matched no property
    or could not be converted) is not evaluated either (`body_mismatch`): the request, not
    the student's code, may be what failed.
    """
    execution = check_input.execution
    if execution is None:
        return NotEvaluatedReason.NO_EXECUTION
    if execution.prepare_status is StepStatus.FAILED:
        return NotEvaluatedReason.ENVIRONMENT_ISSUE
    if execution.build_status is StepStatus.FAILED:
        kind = execution.failure_kind
        if kind is not None and kind.is_student_issue:
            return NotEvaluatedReason.BUILD_FAILED
        return NotEvaluatedReason.ENVIRONMENT_ISSUE  # a build timeout or environment problem
    if execution.run_status is not StepStatus.SUCCEEDED:
        if execution.has_unresolved_paths:
            return NotEvaluatedReason.ENVIRONMENT_ISSUE
        return NotEvaluatedReason.RUN_NOT_HEALTHY
    results = [
        result
        for result in execution.probe_results
        if result.requirement_id == check_input.requirement.id
        and result.assignment_id == check_input.assignment.id
    ]
    if not results:
        return NotEvaluatedReason.ENVIRONMENT_ISSUE  # the runner did not probe this requirement
    if any(result.route_resolution is RouteResolution.ROUTE_NOT_FOUND for result in results):
        return NotEvaluatedReason.ROUTE_NOT_FOUND
    failed = [result for result in results if not result.passed]
    if not failed:
        return ()
    if any(result.body_mismatched for result in failed):
        return NotEvaluatedReason.BODY_MISMATCH
    if execution.has_unresolved_paths:
        return NotEvaluatedReason.ENVIRONMENT_ISSUE
    lines = [_describe(result) for result in failed]
    probes = Localized(
        he="\n".join(line.he for line in lines), en="\n".join(line.en for line in lines)
    )
    facts = tuple(
        f"{r.probe.method.value} {r.probe.path} {r.actual_status or 'none'}" for r in failed
    )
    return (deterministic_finding(check_input, HTTP_PROBES_FAILED.render(probes=probes), facts),)


def _describe(result: ProbeResult) -> Localized:
    probe = result.probe
    separator = STATUS_SEPARATOR.render()
    expected = Localized(
        he=separator.he.join(str(status) for status in probe.allowed_statuses),
        en=separator.en.join(str(status) for status in probe.allowed_statuses),
    )
    fields = {"method": probe.method.value, "path": probe.path, "expected": expected}
    if result.actual_status is None:
        return PROBE_NO_ANSWER.render(**fields)
    return PROBE_WRONG_STATUS.render(**fields, actual=result.actual_status)
