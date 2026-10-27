"""Evaluator for `http` requirements: one finding per requirement for failed probes."""

from dataclasses import dataclass

from hw_checker.checks.deterministic.findings import deterministic_finding
from hw_checker.checks.deterministic.messages import (
    HTTP_PROBES_FAILED,
    HTTP_PROBES_WRONG_ANSWER,
    PROBE_MISSING_VALUES,
    PROBE_NO_ANSWER,
    PROBE_PLAIN_VALUES_LIST,
    PROBE_WRONG_SHAPE,
    PROBE_WRONG_STATUS,
    SHAPE_NAMES,
    STATUS_SEPARATOR,
    Localized,
)
from hw_checker.domain import (
    CheckInput,
    ExpectedJson,
    Finding,
    NotEvaluatedReason,
    ProbeResult,
    ResponseShape,
    RouteResolution,
    StepStatus,
)

UNJUDGEABLE_WHEN_NOT_SUCCESS = frozenset({ResponseShape.NOT_JSON, ResponseShape.EMPTY})


@dataclass(frozen=True)
class _Failure:
    """Why one probe failed: the message line and the facts behind its finding id."""

    line: Localized
    fact: str
    content: bool


def evaluate_http(check_input: CheckInput) -> tuple[Finding, ...] | NotEvaluatedReason:
    """Return one finding naming every failed probe of the requirement.

    Only a successful run is judged, so a build failure never cascades into HTTP findings.
    A probe passes when its status is in the probe's `allowed_statuses` and its response
    holds what the probe expects (`expect_json`, `expect_values`). A response whose content
    cannot be judged (cut at the size limit, an empty list where objects are expected, or no
    JSON body on a non-success answer) passes the content part; it is never a finding.
    When a hard-coded path could not be adapted to the container, an unhealthy run or a
    failing probe may be our environment's fault, so it is not evaluated instead. A probe
    that was not sent because none of its paths is declared in the code makes the whole
    requirement not evaluated (`route_not_found`): the route may just be named differently.
    A failing probe whose body did not fit the student's model (a field matched no property
    or could not be converted) is not evaluated either (`body_mismatch`): the request, not
    the student's code, may be what failed. A probe that was not sent because an earlier
    probe failed to capture a value it needs gives `dependency_failed`: the earlier probe
    is judged on its own, so the failure is never counted twice.
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
    if any(result.route_resolution is RouteResolution.DEPENDENCY_MISSING for result in results):
        return NotEvaluatedReason.DEPENDENCY_FAILED
    failures = [(result, failure) for result in results if (failure := _failure(result))]
    if not failures:
        return ()
    if any(result.body_mismatched for result, _ in failures):
        return NotEvaluatedReason.BODY_MISMATCH
    if execution.has_unresolved_paths:
        return NotEvaluatedReason.ENVIRONMENT_ISSUE
    lines = [failure.line for _, failure in failures]
    probes = Localized(
        he="\n".join(line.he for line in lines), en="\n".join(line.en for line in lines)
    )
    header = (
        HTTP_PROBES_WRONG_ANSWER
        if any(failure.content for _, failure in failures)
        else HTTP_PROBES_FAILED
    )
    facts = tuple(failure.fact for _, failure in failures)
    return (deterministic_finding(check_input, header.render(probes=probes), facts),)


def _failure(result: ProbeResult) -> _Failure | None:
    """Why `result` failed: a wrong status first, then wrong content; None when it passed."""
    probe = result.probe
    if not result.passed:
        fact = f"{probe.method.value} {probe.path} {result.actual_status or 'none'}"
        return _Failure(_describe(result), fact, content=False)
    shape = result.response_shape
    if shape is None or shape is ResponseShape.CUT:
        return None
    status = result.actual_status or 0
    if shape in UNJUDGEABLE_WHEN_NOT_SUCCESS and not 200 <= status < 300:
        return None
    fields = {"method": probe.method.value, "path": probe.path}
    head = f"{probe.method.value} {probe.path}"
    expected = probe.expect_json
    if expected is ExpectedJson.ARRAY_OF_OBJECTS and shape is ResponseShape.ARRAY_OF_SCALARS:
        return _Failure(PROBE_PLAIN_VALUES_LIST.render(**fields), f"{head} shape {shape}", True)
    if expected is not None and not _shape_fits(expected, shape):
        line = PROBE_WRONG_SHAPE.render(
            **fields, expected=SHAPE_NAMES[expected.value], actual=SHAPE_NAMES[shape.value]
        )
        return _Failure(line, f"{head} shape {shape}", True)
    if result.missing_values:
        values = ", ".join(f'"{value}"' for value in result.missing_values)
        line = PROBE_MISSING_VALUES.render(**fields, values=values)
        return _Failure(line, f"{head} missing {'|'.join(result.missing_values)}", True)
    return None


def _shape_fits(expected: ExpectedJson, shape: ResponseShape) -> bool:
    if expected is ExpectedJson.OBJECT:
        return shape is ResponseShape.OBJECT
    # An empty list cannot show whether its items would be objects.
    return shape in (ResponseShape.ARRAY_OF_OBJECTS, ResponseShape.EMPTY_ARRAY)


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
