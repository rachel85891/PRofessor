"""Builds deterministic findings with stable ids."""

import hashlib
from collections.abc import Iterable

from hw_checker.checks.deterministic.messages import Localized
from hw_checker.domain import CheckInput, Evidence, Finding, FindingSource

DETERMINISTIC_CONFIDENCE = 1.0
ID_DIGEST_LENGTH = 16


def deterministic_finding(
    check_input: CheckInput,
    message: Localized,
    facts: Iterable[str],
    evidence: Evidence | None = None,
) -> Finding:
    """A finding for `check_input.requirement`, with an id stable across re-runs.

    The id is derived from the assignment, the requirement and the `facts` of the failure,
    so the same failure gives the same id, and a different failure a different one.
    """
    requirement = check_input.requirement
    key = "\n".join([check_input.assignment.id, requirement.id, *facts])
    digest = hashlib.sha256(key.encode("utf-8")).hexdigest()[:ID_DIGEST_LENGTH]
    return Finding(
        id=f"{requirement.id}-{digest}",
        requirement_id=requirement.id,
        assignment_id=check_input.assignment.id,
        severity=requirement.severity,
        message_he=message.he,
        message_en=message.en,
        evidence=evidence,
        source=FindingSource.DETERMINISTIC,
        confidence=DETERMINISTIC_CONFIDENCE,
    )
