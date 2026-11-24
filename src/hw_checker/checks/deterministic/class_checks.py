"""Generic checks on where selected classes are (HW05-G3): in which layer's project, and in
which namespace.

The mechanism is here; which classes (a `ClassSelector`), which role and how serious a
finding is are each requirement's data (ADR 0023). Both checks read the layers' projects
(`layers.py`) and the type declarations of the architecture evidence. When that evidence is
unavailable they are not evaluated (`evidence_unavailable`); when the role they name has no
project, or no class is selected, they are not evaluated either (`dependency_failed`: the
requirement that says the thing must exist reports that). A class name declared more than
once is never guessed: it is left out and the finding says so.
"""

import re

from pydantic import Field

from hw_checker.checks.deterministic.class_selection import ClassSelector, select_classes
from hw_checker.checks.deterministic.layer_support import (
    Problem,
    Role,
    RolesParams,
    Types,
    ambiguous_note,
    code_text,
    line_evidence,
    make_finding,
    read_layers,
    signals_of,
    simple_name,
    subject,
)
from hw_checker.checks.deterministic.layers import Layers, project_name
from hw_checker.checks.deterministic.messages import (
    CLASS_GLOBAL_NAMESPACE_EXAMPLE,
    CLASS_IN_NO_PROJECT,
    CLASS_IN_OTHER_ROLE,
    CLASS_IN_UNASSIGNED_PROJECT,
    CLASS_NAMESPACE_EXAMPLE,
    CLASSES_NOT_IN_ROLE,
    CONSTRUCTOR_TAKES_NONE_OF,
    NAMESPACE_NOT_PROJECT,
)
from hw_checker.checks.deterministic.repository import code
from hw_checker.checks.registry import CheckResult, check
from hw_checker.domain import (
    ArchitectureEvidence,
    ArchitectureSignal,
    ArchitectureSignalKind,
    CheckInput,
    Evidence,
    NotEvaluatedReason,
)

_NOT_IDENTIFIER_CHARACTER = re.compile(r"\W")
_SEGMENT_SEPARATOR = "."
_REPLACEMENT = "_"


class ClassesInRoleParams(RolesParams):
    """`role`: the role whose project(s) must hold the classes `select` chooses."""

    role: Role
    select: ClassSelector

    def named_roles(self) -> tuple[str, ...]:
        return (self.role,)


class NamespaceMatchesProjectParams(RolesParams):
    """`select`: the classes whose namespace is checked; `role`: when given, only the
    selected classes in that role's project(s)."""

    select: ClassSelector
    role: Role | None = Field(default=None)

    def named_roles(self) -> tuple[str, ...]:
        return () if self.role is None else (self.role,)


class ConstructorTakesClassParams(RolesParams):
    """`consumer`: the role whose classes' constructors are read; `select`: the classes
    one of them must take."""

    consumer: Role
    select: ClassSelector

    def named_roles(self) -> tuple[str, ...]:
        return (self.consumer,)


@check(
    "classes_in_role",
    ClassesInRoleParams,
    "The classes chosen by select (names, bases, entities_of_bases: a DbContext's "
    "DbSet<T> entities; the union) are all in the project(s) of the given role. A class in "
    "another role's project, an unassigned project or no project is a finding. No class "
    "selected: not evaluated.",
    reads_architecture=True,
)
def classes_in_role(check_input: CheckInput, params: ClassesInRoleParams) -> CheckResult:
    """One finding listing each selected class outside the role's projects, with where it is;
    the first one's line is the finding's evidence."""
    found = read_layers(check_input, params, signals=True)
    if not isinstance(found, tuple):
        return found
    evidence, roles = found
    selection = select_classes(Types(evidence), params.select)
    if not selection.classes:
        return NotEvaluatedReason.DEPENDENCY_FAILED
    problems = [
        problem
        for declaration in selection.classes
        if (problem := _misplaced(declaration, roles, params.role)) is not None
    ]
    return make_finding(
        check_input,
        CLASSES_NOT_IN_ROLE,
        problems,
        _first_line(check_input, problems),
        role=params.role,
        ambiguous=ambiguous_note(list(selection.ambiguous)),
    )


@check(
    "namespace_matches_project",
    NamespaceMatchesProjectParams,
    "The namespace of each class chosen by select is exactly its project's namespace "
    "(<RootNamespace>, else the project file's name with characters that cannot be in a "
    "namespace replaced by _). A subfolder is fine when the namespace stays the project's; "
    "an extra or different segment, or no namespace, is a finding. role: only the selected "
    "classes in that role's projects. No class selected: not evaluated.",
    reads_architecture=True,
)
def namespace_matches_project(
    check_input: CheckInput, params: NamespaceMatchesProjectParams
) -> CheckResult:
    """One finding listing each selected class whose namespace is not its project's, with the
    expected one; the first one's line is the finding's evidence."""
    found = read_layers(check_input, params, signals=True)
    if not isinstance(found, tuple):
        return found
    evidence, roles = found
    selection = select_classes(Types(evidence), params.select)
    in_scope = [
        declaration
        for declaration in selection.classes
        if roles.project_of(declaration.file_path) is not None
        and (params.role is None or roles.role_of(declaration.file_path) == params.role)
    ]
    if not in_scope:
        return NotEvaluatedReason.DEPENDENCY_FAILED
    problems = [
        problem
        for declaration in in_scope
        if (problem := _wrong_namespace(declaration, roles, evidence)) is not None
    ]
    return make_finding(
        check_input,
        NAMESPACE_NOT_PROJECT,
        problems,
        _first_line(check_input, problems),
        ambiguous=ambiguous_note(list(selection.ambiguous)),
    )


@check(
    "constructor_takes_class",
    ConstructorTakesClassParams,
    "At least one class in the consumer role's projects takes a class chosen by select "
    "(names, bases, entities_of_bases) as a constructor parameter, normal or primary "
    "(generic wrappers such as IOptions<T> do not count). Otherwise one finding naming the "
    "selected classes. No class selected: not evaluated.",
    reads_architecture=True,
)
def constructor_takes_class(
    check_input: CheckInput, params: ConstructorTakesClassParams
) -> CheckResult:
    """Passes when some constructor of the consumer role takes a selected class; otherwise one
    finding with no location (there is nothing to point at) naming the selected classes."""
    found = read_layers(check_input, params, signals=True)
    if not isinstance(found, tuple):
        return found
    evidence, roles = found
    selection = select_classes(Types(evidence), params.select)
    if not selection.classes:
        return NotEvaluatedReason.DEPENDENCY_FAILED
    wanted = {subject(declaration) for declaration in selection.classes}
    taken = any(
        roles.role_of(parameter.file_path) == params.consumer
        and simple_name(subject(parameter)) in wanted
        for parameter in signals_of(evidence, ArchitectureSignalKind.CONSTRUCTOR_PARAMETER)
    )
    if taken:
        return ()
    problems = [Problem(code_text(name)) for name in sorted(wanted)]
    return make_finding(
        check_input,
        CONSTRUCTOR_TAKES_NONE_OF,
        problems,
        consumer=params.consumer,
        ambiguous=ambiguous_note(list(selection.ambiguous)),
    )


CLASS_CHECKS = (classes_in_role, namespace_matches_project, constructor_takes_class)


def project_namespace(project: str, root_namespace: str | None) -> str:
    """The namespace .NET gives a project's code: its `<RootNamespace>`, else its file name
    without `.csproj` with each character that cannot be in an identifier (a space, `-`)
    replaced by `_`, and `_` before a segment that starts with a digit."""
    if root_namespace:
        return root_namespace
    segments = project_name(project).split(_SEGMENT_SEPARATOR)
    return _SEGMENT_SEPARATOR.join(_identifier(segment) for segment in segments)


def _identifier(segment: str) -> str:
    cleaned = _NOT_IDENTIFIER_CHARACTER.sub(_REPLACEMENT, segment)
    return _REPLACEMENT + cleaned if cleaned[:1].isdigit() else cleaned


def _misplaced(declaration: ArchitectureSignal, roles: Layers, role: str) -> Problem | None:
    """Where a selected class is when it is not in a project of `role`; None when it is."""
    name = code(subject(declaration))
    project = roles.project_of(declaration.file_path)
    if project is None:
        text = CLASS_IN_NO_PROJECT.render(name=name)
    elif project not in roles.role_of_project:
        text = CLASS_IN_UNASSIGNED_PROJECT.render(name=name, project=code(project_name(project)))
    elif roles.role_of_project[project] != role:
        text = CLASS_IN_OTHER_ROLE.render(
            name=name,
            project=code(project_name(project)),
            found_role=roles.role_of_project[project],
        )
    else:
        return None
    return Problem(text, declaration.file_path, declaration.line)


def _wrong_namespace(
    declaration: ArchitectureSignal, roles: Layers, evidence: ArchitectureEvidence
) -> Problem | None:
    """The selected class with its namespace and the project's; None when they are equal.
    Only called for a class in a project."""
    project = roles.project_of(declaration.file_path) or ""
    roots = {entry.file_path: entry.root_namespace for entry in evidence.projects}
    expected = project_namespace(project, roots.get(project))
    if declaration.namespace == expected:
        return None
    fields = {
        "name": code(subject(declaration)),
        "project": code(project_name(project)),
        "expected": code(expected),
    }
    if declaration.namespace is None:
        text = CLASS_GLOBAL_NAMESPACE_EXAMPLE.render(**fields)
    else:
        text = CLASS_NAMESPACE_EXAMPLE.render(found=code(declaration.namespace), **fields)
    return Problem(text, declaration.file_path, declaration.line)


def _first_line(check_input: CheckInput, problems: list[Problem]) -> Evidence | None:
    located = next((problem for problem in problems if problem.file), None)
    return line_evidence(check_input, located) if located else None
