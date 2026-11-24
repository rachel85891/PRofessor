"""Generic layer and dependency-injection checks on the architecture evidence (stage 21).

The mechanism is here; the roles, patterns and pairs are each requirement's params (ADR
0023). Every check reads the evidence's projects by role (`layers.py`). When the coverage
it needs is unavailable, the check is not evaluated (`evidence_unavailable`); when a role
it needs has no project, it is not evaluated either (`dependency_failed`: the layer
projects requirement reports that). Type names are matched by their simple name, as the
reader gives them; a name declared more than once is never guessed: it is left out and the
finding says so. A finding lists up to `MAX_EXAMPLES` examples and the total.
"""

import re
from typing import Literal

from pydantic import Field

from hw_checker.checks.deterministic.class_selection import LeftOut
from hw_checker.checks.deterministic.findings import deterministic_finding
from hw_checker.checks.deterministic.layer_support import (
    NOTHING,
    PUBLIC,
    ClassesLeftOutParams,
    Problem,
    Role,
    RolesParams,
    Types,
    ambiguous_note,
    code_text,
    joined,
    line_evidence,
    make_finding,
    read_evidence,
    read_layers,
    signals_of,
    simple_name,
    subject,
)
from hw_checker.checks.deterministic.layers import Layers, Regex, layers, project_name
from hw_checker.checks.deterministic.messages import (
    CODE_OUTSIDE_LAYER,
    CONCRETE_CONSTRUCTION,
    CONSTRUCTOR_TAKES_CLASS,
    CREATION_EXAMPLE,
    INTERFACES_UNREGISTERED,
    LAYER_PROJECTS_FOUND,
    LAYER_PROJECTS_MISSING,
    LAYER_REFERENCE_FORBIDDEN,
    LAYER_REFERENCE_MISSING,
    LAYER_REFERENCE_MISSING_ANY_PATH,
    LAYER_REFERENCES,
    LAYER_UNASSIGNED,
    NO_INTERFACE,
    NOT_PUBLIC,
    NOT_PUBLIC_EXAMPLE,
    TAKES_CLASS_EXAMPLE,
    UNREGISTERED_EXAMPLE,
    UNTYPED_REGISTRATIONS,
    Localized,
)
from hw_checker.checks.deterministic.repository import (
    checked_files,
    code,
    read_source,
)
from hw_checker.checks.deterministic.source_text import scan_source
from hw_checker.checks.registry import CheckResult, check
from hw_checker.domain import (
    ArchitectureEvidence,
    ArchitectureSignal,
    ArchitectureSignalKind,
    CheckInput,
    CheckPassed,
    NotEvaluatedReason,
    ProjectReferenceStatus,
)

ANY_ROLE = "*"
"""In a forbidden reference pair: any other role."""
SOURCE_SUFFIX = ".cs"
NONE_FOUND = Localized(he="אין", en="none")
_OR = Localized(he=" או ", en=" or ")
_INTERNAL = "internal"
_NOT_READ = frozenset({"static", "private", "protected", "file"})
"""A static class cannot implement an interface; the others are nested or file-local
helpers, not layer classes."""
_REFERENCED = frozenset({ProjectReferenceStatus.MATCHED, ProjectReferenceStatus.CASE_MISMATCHED})
"""Reference statuses that name a project of the repo."""


RolePair = tuple[Role, Role]


class LayerProjectsParams(RolesParams):
    """`required`: the roles that must each have a project."""

    required: tuple[Role, ...] = Field(min_length=1)

    def named_roles(self) -> tuple[str, ...]:
        return self.required


class LayerReferencesParams(RolesParams):
    """`required`: [source, target] role pairs some project of the source must reference
    a project of the target (with `transitive_required`, also through other projects);
    `forbidden`: pairs no project may reference directly (target `*`: any other role)."""

    required: tuple[RolePair, ...] = ()
    forbidden: tuple[tuple[Role, Role | Literal["*"]], ...] = ()
    transitive_required: bool = False

    def named_roles(self) -> tuple[str, ...]:
        pairs = (*self.required, *self.forbidden)
        return tuple(role for pair in pairs for role in pair if role != ANY_ROLE)


class CodeOnlyInLayerParams(RolesParams):
    """`patterns`: C# regular expressions, matched on code with comments and strings
    masked; `allowed`: the roles whose projects may hold such code."""

    patterns: tuple[Regex, ...] = Field(min_length=1)
    allowed: tuple[Role, ...] = Field(min_length=1)

    def named_roles(self) -> tuple[str, ...]:
        return self.allowed


class ImplementsInterfaceParams(ClassesLeftOutParams):
    """`role`: the role whose public classes must implement an interface declared in the
    repo; `exclude` and `exclude_bases`: the classes left out."""

    role: Role

    def named_roles(self) -> tuple[str, ...]:
        return (self.role,)


class ClassesPublicParams(ClassesLeftOutParams):
    """`role`: the role whose classes must be `public`; `exclude` and `exclude_bases`: the
    classes left out."""

    role: Role

    def named_roles(self) -> tuple[str, ...]:
        return (self.role,)


class ConstructorTakesInterfaceParams(RolesParams):
    """`consumer`: the role whose constructors are read; `dependency`: the role whose
    classes they must take by interface."""

    consumer: Role
    dependency: Role

    def named_roles(self) -> tuple[str, ...]:
        return (self.consumer, self.dependency)


class InterfacesRegisteredParams(RolesParams):
    """`consumers`: the roles whose constructors' interfaces must be registered."""

    consumers: tuple[Role, ...] = Field(min_length=1)

    def named_roles(self) -> tuple[str, ...]:
        return self.consumers


class NoConcreteConstructionParams(ClassesLeftOutParams):
    """`in`: the roles whose classes must not create, with `new`, a class of the `of`
    roles; `exclude` and `exclude_bases`: the created classes not counted."""

    in_: tuple[Role, ...] = Field(min_length=1, alias="in")
    of: tuple[Role, ...] = Field(min_length=1)

    def named_roles(self) -> tuple[str, ...]:
        return (*self.in_, *self.of)


# ---------------------------------------------------------------- the checks


@check(
    "layer_projects",
    LayerProjectsParams,
    "Each required layer role has a project (roles are found by SDK or project name).",
    reads_architecture=True,
)
def layer_projects(check_input: CheckInput, params: LayerProjectsParams) -> CheckResult:
    """One finding naming the roles with no project and the unassigned projects."""
    evidence = read_evidence(check_input, signals=False)
    if evidence is None:
        return NotEvaluatedReason.EVIDENCE_UNAVAILABLE
    found = layers(evidence, params.roles)
    unassigned = _unassigned(found)
    missing = found.missing(params.required)
    if not missing:
        listed = ", ".join(f"{role} {_projects(found.projects(role))}" for role in params.required)
        return CheckPassed(
            message=LAYER_PROJECTS_FOUND.render(layers=listed, unassigned=unassigned).en
        )
    projects = ", ".join(code(project_name(p)) for p in evidence.project_files)
    message = LAYER_PROJECTS_MISSING.render(
        missing=joined(missing),
        projects=projects or NONE_FOUND,
        unassigned=unassigned,
    )
    return (deterministic_finding(check_input, message, [*missing, *evidence.project_files]),)


@check(
    "layer_references",
    LayerReferencesParams,
    "The project references follow the layers: each required [source, target] role pair is "
    "referenced (with transitive_required, also through other projects), and no forbidden "
    "pair is referenced directly (target * is any other role).",
    reads_architecture=True,
)
def layer_references(check_input: CheckInput, params: LayerReferencesParams) -> CheckResult:
    """One finding listing the missing required references and the forbidden ones. A
    forbidden one is located at its `<ProjectReference>` line in the source project, and
    the first located one is the finding's evidence: the note goes on that line."""
    found = read_layers(check_input, params, signals=False)
    if not isinstance(found, tuple):
        return found
    evidence, roles = found
    edges = _reference_edges(evidence)
    problems: list[Problem] = []
    missing = (
        LAYER_REFERENCE_MISSING_ANY_PATH
        if params.transitive_required
        else (LAYER_REFERENCE_MISSING)
    )
    for source_role, target_role in params.required:
        if not _references(
            edges,
            roles.projects(source_role),
            roles.projects(target_role),
            transitive=params.transitive_required,
        ):
            problems.append(
                Problem(
                    missing.render(
                        source=_projects(roles.projects(source_role)),
                        target=_projects(roles.projects(target_role)),
                        source_role=source_role,
                        target_role=target_role,
                    )
                )
            )
    for source, target, line in edges:
        from_role = roles.role_of_project.get(source)
        to_role = roles.role_of_project.get(target)
        if from_role is None or to_role is None:
            continue
        if any(
            from_role == forbidden_source
            and (to_role == forbidden_target or _other(forbidden_target, from_role, to_role))
            for forbidden_source, forbidden_target in params.forbidden
        ):
            problems.append(
                Problem(
                    LAYER_REFERENCE_FORBIDDEN.render(
                        source=code(project_name(source)),
                        target=code(project_name(target)),
                        source_role=from_role,
                        target_role=to_role,
                    ),
                    source if line else "",
                    line or 0,
                )
            )
    located = next((problem for problem in problems if problem.file), None)
    quoted = line_evidence(check_input, located) if located else None
    return make_finding(check_input, LAYER_REFERENCES, problems, quoted)


@check(
    "code_only_in_layer",
    CodeOnlyInLayerParams,
    "Code matching patterns (C# regexes on code without comments and strings) is only in "
    "the projects of the allowed roles.",
    reads_architecture=True,
)
def code_only_in_layer(check_input: CheckInput, params: CodeOnlyInLayerParams) -> CheckResult:
    """One finding locating each match in a project of another role. A file in no project,
    or in an unassigned one, is not judged."""
    found = read_layers(check_input, params, signals=True)
    if not isinstance(found, tuple):
        return found
    _, roles = found
    patterns = [re.compile(pattern) for pattern in params.patterns]
    problems: list[Problem] = []
    for path in checked_files(check_input):
        if not path.lower().endswith(SOURCE_SUFFIX):
            continue
        role = roles.role_of(path)
        if role is None or role in params.allowed:
            continue
        text = read_source(check_input.repo_dir, path)
        if text is None:
            continue
        scan = scan_source(text)
        matches = sorted(
            (match.start(), match.group(0))
            for pattern in patterns
            for match in pattern.finditer(scan.masked)
        )
        problems += [
            Problem(Localized(he=code(matched), en=code(matched)), path, scan.line_of(start))
            for start, matched in matches
        ]
    return make_finding(check_input, CODE_OUTSIDE_LAYER, problems, allowed=joined(params.allowed))


@check(
    "implements_interface",
    ImplementsInterfaceParams,
    "Every class in the role's projects (public, or internal: `classes_public` asks for "
    "public) implements an interface declared in the repo (directly or through its base "
    "class); exclude: class name patterns left out; exclude_bases: a class whose base list "
    "has a matching name is left out."
    " exclude_db_entities: the entities of a left-out DbContext (DbSet<T>) are left out too.",
    reads_architecture=True,
)
def implements_interface(check_input: CheckInput, params: ImplementsInterfaceParams) -> CheckResult:
    """One finding listing the classes of the role with no declared interface. A class
    without `public` is read too (developer decision 2026-11-19); a static class (which
    cannot implement one), a record and a nested `private` or `protected` class are not
    read."""
    found = read_layers(check_input, params, signals=True)
    if not isinstance(found, tuple):
        return found
    evidence, roles = found
    types = Types(evidence)
    left_out = LeftOut(params, types)
    problems: list[Problem] = []
    ambiguous: list[str] = []
    for declaration in _layer_classes(types, roles, params.role):
        name = subject(declaration)
        if left_out(name):
            continue
        if types.interfaces_of(name):
            continue
        if types.declared_more_than_once(name):
            ambiguous.append(name)
            continue
        problems.append(Problem(code_text(name), declaration.file_path, declaration.line))
    return make_finding(
        check_input, NO_INTERFACE, problems, ambiguous=ambiguous_note(ambiguous), role=params.role
    )


@check(
    "classes_public",
    ClassesPublicParams,
    "Every class in the role's projects is public; exclude: class name patterns left out; "
    "exclude_bases: a class whose base list has a matching name is left out."
    " exclude_db_entities: the entities of a left-out DbContext (DbSet<T>) are left out too.",
    reads_architecture=True,
)
def classes_public(check_input: CheckInput, params: ClassesPublicParams) -> CheckResult:
    """One finding listing the classes of the role without `public`: "`UserService` isn't
    `public`; make it `public class UserService`". The classes read are the ones
    `implements_interface` reads; a partial class with a public part is public."""
    found = read_layers(check_input, params, signals=True)
    if not isinstance(found, tuple):
        return found
    evidence, roles = found
    types = Types(evidence)
    left_out = LeftOut(params, types)
    problems = [
        Problem(
            NOT_PUBLIC_EXAMPLE.render(name=code_text(name), declaration=_public(declaration, name)),
            declaration.file_path,
            declaration.line,
        )
        for declaration in _layer_classes(types, roles, params.role)
        if not types.public(name := subject(declaration)) and not left_out(name)
    ]
    return make_finding(check_input, NOT_PUBLIC, problems, role=params.role)


@check(
    "constructor_takes_interface",
    ConstructorTakesInterfaceParams,
    "No constructor in the consumer role's projects takes a class of the dependency role "
    "that implements an interface; it takes the interface.",
    reads_architecture=True,
)
def constructor_takes_interface(
    check_input: CheckInput, params: ConstructorTakesInterfaceParams
) -> CheckResult:
    """One finding: "`UsersController` takes `UserService`; take `IUserService`"."""
    found = read_layers(check_input, params, signals=True)
    if not isinstance(found, tuple):
        return found
    evidence, roles = found
    types = Types(evidence)
    dependencies = {
        subject(declaration) for declaration in types.classes_in(roles, (params.dependency,))
    }
    problems: list[Problem] = []
    ambiguous: list[str] = []
    for parameter in signals_of(evidence, ArchitectureSignalKind.CONSTRUCTOR_PARAMETER):
        taken = simple_name(subject(parameter))
        if roles.role_of(parameter.file_path) != params.consumer or taken not in dependencies:
            continue
        interfaces = types.interfaces_of(taken)
        if not interfaces:
            continue
        if types.declared_more_than_once(taken):
            ambiguous.append(taken)
            continue
        text = TAKES_CLASS_EXAMPLE.render(
            consumer=code(parameter.related_subject or ""),
            taken=code(taken),
            interface=joined([code(name) for name in interfaces], _OR),
        )
        problems.append(Problem(text, parameter.file_path, parameter.line))
    return make_finding(
        check_input,
        CONSTRUCTOR_TAKES_CLASS,
        problems,
        ambiguous=ambiguous_note(ambiguous),
        consumer=params.consumer,
        dependency=params.dependency,
    )


@check(
    "interfaces_registered",
    InterfacesRegisteredParams,
    "Every interface declared in the repo that a constructor in the consumers' projects "
    "takes has a registration (AddScoped, AddTransient or AddSingleton) with that service "
    "type.",
    reads_architecture=True,
)
def interfaces_registered(
    check_input: CheckInput, params: InterfacesRegisteredParams
) -> CheckResult:
    """One finding listing each constructor's unregistered interface; it adds how many
    registrations name no type (a factory or a key), which may register one of them."""
    found = read_layers(check_input, params, signals=True)
    if not isinstance(found, tuple):
        return found
    evidence, roles = found
    types = Types(evidence)
    registrations = signals_of(evidence, ArchitectureSignalKind.REGISTRATION_CANDIDATE)
    registered = {simple_name(signal.subject) for signal in registrations if signal.subject}
    untyped = sum(signal.subject is None for signal in registrations)
    problems: list[Problem] = []
    ambiguous: list[str] = []
    for parameter in signals_of(evidence, ArchitectureSignalKind.CONSTRUCTOR_INTERFACE_PARAMETER):
        interface = simple_name(subject(parameter))
        if roles.role_of(parameter.file_path) not in params.consumers or interface in registered:
            continue
        if types.interface_declarations[interface] > 1:
            ambiguous.append(interface)
            continue
        text = UNREGISTERED_EXAMPLE.render(
            consumer=code(parameter.related_subject or ""), interface=code(interface)
        )
        problems.append(Problem(text, parameter.file_path, parameter.line))
    untyped_note = UNTYPED_REGISTRATIONS.render(count=untyped) if untyped else NOTHING
    return make_finding(
        check_input,
        INTERFACES_UNREGISTERED,
        problems,
        untyped=untyped_note,
        ambiguous=ambiguous_note(ambiguous),
    )


@check(
    "no_concrete_construction",
    NoConcreteConstructionParams,
    "No class in the `in` roles' projects creates a class of the `of` roles with new "
    "(top-level statements, where registrations are written, are not read); exclude and "
    "exclude_bases: created classes not counted, by name or by a name in their base list."
    " exclude_db_entities: the entities of a left-out DbContext (DbSet<T>) are left out too.",
    reads_architecture=True,
)
def no_concrete_construction(
    check_input: CheckInput, params: NoConcreteConstructionParams
) -> CheckResult:
    """One finding: "`UserService` creates `UserRepository`"."""
    found = read_layers(check_input, params, signals=True)
    if not isinstance(found, tuple):
        return found
    evidence, roles = found
    types = Types(evidence)
    left_out = LeftOut(params, types)
    created_classes = {
        name for d in types.classes_in(roles, params.of) if not left_out(name := subject(d))
    }
    problems: list[Problem] = []
    ambiguous: list[str] = []
    for creation in signals_of(evidence, ArchitectureSignalKind.OBJECT_CREATION):
        created = simple_name(subject(creation))
        if creation.related_subject is None or created not in created_classes:
            continue
        if roles.role_of(creation.file_path) not in params.in_:
            continue
        if types.declared_more_than_once(created):
            ambiguous.append(created)
            continue
        text = CREATION_EXAMPLE.render(
            creator=code(creation.related_subject), created=code(created)
        )
        problems.append(Problem(text, creation.file_path, creation.line))
    return make_finding(
        check_input,
        CONCRETE_CONSTRUCTION,
        problems,
        ambiguous=ambiguous_note(ambiguous),
        of=joined(params.of, _OR),
    )


LAYER_CHECKS = (
    layer_projects,
    layer_references,
    code_only_in_layer,
    implements_interface,
    classes_public,
    constructor_takes_interface,
    interfaces_registered,
    no_concrete_construction,
)


# ---------------------------------------------------------------- helpers


def _layer_classes(types: Types, roles: Layers, role: str) -> list[ArchitectureSignal]:
    """The classes of `role`'s projects that `implements_interface` and `classes_public`
    read: public or internal (written or by default), not static and not records."""
    return [
        declaration
        for declaration in types.classes_in(roles, (role,))
        if not _NOT_READ & set(declaration.keywords)
    ]


def _public(declaration: ArchitectureSignal, name: str) -> str:
    """The declaration made `public`: `internal sealed class X` as `public sealed class X`."""
    keywords = [word for word in declaration.keywords if word != _INTERNAL]
    return " ".join([PUBLIC, *keywords, name])


def _references(
    edges: list[tuple[str, str, int | None]],
    sources: tuple[str, ...],
    targets: tuple[str, ...],
    *,
    transitive: bool,
) -> bool:
    """Whether a project of `sources` references one of `targets`: directly, or with
    `transitive` through other projects (SDK-style references flow through)."""
    following: dict[str, set[str]] = {}
    for source, target, _ in edges:
        following.setdefault(source, set()).add(target)
    reached: set[str] = set()
    frontier = {target for source in sources for target in following.get(source, ())}
    while frontier:
        if frontier & set(targets):
            return True
        if not transitive:
            return False
        reached |= frontier
        frontier = {
            target for project in frontier for target in following.get(project, ())
        } - reached
    return False


def _reference_edges(evidence: ArchitectureEvidence) -> list[tuple[str, str, int | None]]:
    """The project references as (source, target, line) project files of the inventory and
    the reference's line in the source project (None when unknown). A
    reference written with other letter case (`case_mismatched`) is still a reference here:
    the build and file-reference checks own the letter case."""
    by_lower = {project.lower(): project for project in evidence.project_files}
    edges: list[tuple[str, str, int | None]] = []
    for reference in evidence.project_references:
        if reference.status not in _REFERENCED or reference.target_project is None:
            continue
        target = by_lower.get(reference.target_project.lower())
        if target is not None:
            edges.append((reference.source_project, target, reference.line))
    return edges


def _other(target: str, source_role: str, target_role: str) -> bool:
    return target == ANY_ROLE and target_role != source_role


def _projects(projects: tuple[str, ...]) -> str:
    return "/".join(code(project_name(project)) for project in projects)


def _unassigned(found: Layers) -> Localized:
    if not found.unassigned:
        return NOTHING
    projects = ", ".join(code(project_name(project)) for project in found.unassigned)
    return LAYER_UNASSIGNED.render(projects=projects)
