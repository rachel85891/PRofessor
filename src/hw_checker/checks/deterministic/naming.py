"""C# naming-convention checks, read from the code facts the runner recorded during prepare.

All six are taught conventions that never lower the grade (their requirements are minor).
Each gives one finding listing up to `MAX_EXAMPLES` examples with a suggested name and the
total count. Names a student cannot choose (overrides, generated files) are already left out
of the code facts. Without code facts the requirement is not evaluated, never a finding.
"""

import re
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import PurePosixPath

from pydantic import BaseModel, ConfigDict

from hw_checker.checks.deterministic.build import evidence_at
from hw_checker.checks.deterministic.findings import deterministic_finding
from hw_checker.checks.deterministic.messages import (
    NAMING_CONTROLLER_SUFFIX,
    NAMING_EXAMPLE,
    NAMING_EXAMPLE_NO_SUGGESTION,
    NAMING_FILES,
    NAMING_LOCALS,
    NAMING_METHODS,
    NAMING_PROPERTIES,
    NAMING_TYPES,
    Localized,
    MessageTemplate,
)
from hw_checker.checks.registry import check
from hw_checker.domain import (
    CheckInput,
    CodeFacts,
    Finding,
    NotEvaluatedReason,
    StepStatus,
)

MAX_EXAMPLES = 5
PROGRAM_FILE = "Program.cs"
CONTROLLER_SUFFIX = "Controller"
_UNDERSCORE_LETTER = re.compile(r"_+([A-Za-z0-9]?)")


class NoParams(BaseModel):
    """The naming checks take no params."""

    model_config = ConfigDict(frozen=True, extra="forbid")


@dataclass(frozen=True)
class Violation:
    """One name that breaks the convention, where it is, and the suggested name (if any)."""

    name: str
    file_path: str
    line: int
    suggestion: str | None


def is_pascal_case(name: str) -> bool:
    """A capital first letter and no underscore; an all-capitals name such as `ID` counts."""
    return name[:1].isupper() and "_" not in name


def is_camel_case(name: str) -> bool:
    """A lowercase first letter, or a leading underscore."""
    return name[:1].islower() or name.startswith("_")


def pascal_suggestion(name: str) -> str | None:
    """`email` → `Email`, `first_name` → `FirstName`; None when that would not help."""
    joined = _UNDERSCORE_LETTER.sub(lambda match: match[1].upper(), name)
    suggestion = joined[:1].upper() + joined[1:]
    return suggestion if _sensible(suggestion, name) and is_pascal_case(suggestion) else None


def camel_suggestion(name: str) -> str | None:
    """`FilePath` → `filePath`; None for all-capital names, where it would not help."""
    if name.isupper():
        return None
    joined = _UNDERSCORE_LETTER.sub(lambda match: match[1].upper(), name.lstrip("_"))
    suggestion = joined[:1].lower() + joined[1:]
    return suggestion if _sensible(suggestion, name) else None


def _sensible(suggestion: str, name: str) -> bool:
    return bool(suggestion) and suggestion != name and suggestion[:1].isalpha()


def _facts(check_input: CheckInput) -> CodeFacts | NotEvaluatedReason:
    execution = check_input.execution
    if execution is None:
        return NotEvaluatedReason.NO_EXECUTION
    if execution.prepare_status is StepStatus.FAILED or execution.code_facts is None:
        return NotEvaluatedReason.ENVIRONMENT_ISSUE
    return execution.code_facts


def _report(
    check_input: CheckInput,
    find: Callable[[CodeFacts], list[Violation]],
    template: MessageTemplate,
) -> tuple[Finding, ...] | NotEvaluatedReason:
    facts = _facts(check_input)
    if isinstance(facts, NotEvaluatedReason):
        return facts
    violations = find(facts)
    if not violations:
        return ()
    examples = [_example(v) for v in violations[:MAX_EXAMPLES]]
    joined = Localized(he="; ".join(e.he for e in examples), en="; ".join(e.en for e in examples))
    message = template.render(examples=joined, count=len(violations))
    first = violations[0]
    evidence = evidence_at(check_input.repo_dir, first.file_path, first.line)
    facts_for_id = tuple(f"{v.name}@{v.file_path}:{v.line}" for v in violations)
    return (deterministic_finding(check_input, message, facts_for_id, evidence),)


def _example(violation: Violation) -> Localized:
    fields: dict[str, str | int] = {
        "name": violation.name,
        "file": violation.file_path,
        "line": violation.line,
    }
    if violation.suggestion is None:
        return NAMING_EXAMPLE_NO_SUGGESTION.render(**fields)
    return NAMING_EXAMPLE.render(**fields, suggestion=violation.suggestion)


def _file_name(file_path: str) -> str:
    return PurePosixPath(file_path).name


# ---------------------------------------------------------------- the six checks


def type_violations(facts: CodeFacts) -> list[Violation]:
    return [
        Violation(t.name, t.file_path, t.line, pascal_suggestion(t.name))
        for t in facts.types
        if not is_pascal_case(t.name)
    ]


def property_violations(facts: CodeFacts) -> list[Violation]:
    return [
        Violation(p.name, p.file_path, p.line, pascal_suggestion(p.name))
        for p in facts.properties
        if not is_pascal_case(p.name)
    ]


def method_violations(facts: CodeFacts) -> list[Violation]:
    return [
        Violation(m.name, m.file_path, m.line, pascal_suggestion(m.name))
        for m in facts.methods
        if not is_pascal_case(m.name)
        and m.name != "Main"
        and not (m.declaring_type is None and _file_name(m.file_path) == PROGRAM_FILE)
    ]


def local_violations(facts: CodeFacts) -> list[Violation]:
    return [
        Violation(v.name, v.file_path, v.line, camel_suggestion(v.name))
        for v in facts.locals
        if not is_camel_case(v.name)
    ]


def file_violations(facts: CodeFacts) -> list[Violation]:
    lines = {(t.file_path, t.name): t.line for t in facts.types}
    found: list[Violation] = []
    for file in facts.files:
        name = _file_name(file.file_path)
        if name == PROGRAM_FILE or len(file.types) != 1:
            continue
        (type_name,) = file.types
        stem = name.removesuffix(".cs")
        if stem == type_name and is_pascal_case(stem):
            continue
        wanted = type_name if is_pascal_case(type_name) else pascal_suggestion(type_name)
        suggestion = None if wanted is None else f"{wanted}.cs"
        line = lines.get((file.file_path, type_name), 1)
        found.append(Violation(name, file.file_path, line, suggestion))
    return found


def controller_violations(facts: CodeFacts) -> list[Violation]:
    found: list[Violation] = []
    for t in facts.types:
        if not t.is_controller or t.name.endswith(CONTROLLER_SUFFIX):
            continue
        base = t.name if is_pascal_case(t.name) else pascal_suggestion(t.name)
        suggestion = None if base is None else base + CONTROLLER_SUFFIX
        found.append(Violation(t.name, t.file_path, t.line, suggestion))
    return found


@check("pascal_case_types", NoParams, "Class and type names use PascalCase.")
def pascal_case_types(
    check_input: CheckInput, params: NoParams
) -> tuple[Finding, ...] | NotEvaluatedReason:
    return _report(check_input, type_violations, NAMING_TYPES)


@check("pascal_case_properties", NoParams, "Public properties use PascalCase.")
def pascal_case_properties(
    check_input: CheckInput, params: NoParams
) -> tuple[Finding, ...] | NotEvaluatedReason:
    return _report(check_input, property_violations, NAMING_PROPERTIES)


@check("pascal_case_methods", NoParams, "Method names use PascalCase.")
def pascal_case_methods(
    check_input: CheckInput, params: NoParams
) -> tuple[Finding, ...] | NotEvaluatedReason:
    return _report(check_input, method_violations, NAMING_METHODS)


@check("camel_case_locals", NoParams, "Local variables use camelCase.")
def camel_case_locals(
    check_input: CheckInput, params: NoParams
) -> tuple[Finding, ...] | NotEvaluatedReason:
    return _report(check_input, local_violations, NAMING_LOCALS)


@check(
    "file_matches_type",
    NoParams,
    "A file that declares one type is named after it, in PascalCase.",
)
def file_matches_type(
    check_input: CheckInput, params: NoParams
) -> tuple[Finding, ...] | NotEvaluatedReason:
    return _report(check_input, file_violations, NAMING_FILES)


@check("controller_suffix", NoParams, "Controller class names end with Controller.")
def controller_suffix(
    check_input: CheckInput, params: NoParams
) -> tuple[Finding, ...] | NotEvaluatedReason:
    return _report(check_input, controller_violations, NAMING_CONTROLLER_SUFFIX)


NAMING_CHECKS = (
    pascal_case_types,
    pascal_case_properties,
    pascal_case_methods,
    camel_case_locals,
    file_matches_type,
    controller_suffix,
)
