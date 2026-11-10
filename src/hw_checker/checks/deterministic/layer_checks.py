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
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Annotated, Literal, Self

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, model_validator

from hw_checker.checks.deterministic.findings import deterministic_finding
from hw_checker.checks.deterministic.layers import Layers, Regex, Roles, layers, project_name
from hw_checker.checks.deterministic.messages import (
    AMBIGUOUS_NAMES,
    CODE_OUTSIDE_LAYER,
    CONCRETE_CONSTRUCTION,
    CONSTRUCTOR_TAKES_CLASS,
    CREATION_EXAMPLE,
    INTERFACES_UNREGISTERED,
    LAYER_PROJECTS_FOUND,
    LAYER_PROJECTS_MISSING,
    LAYER_REFERENCE_FORBIDDEN,
    LAYER_REFERENCE_MISSING,
    LAYER_REFERENCES,
    LAYER_UNASSIGNED,
    LOCATED_EXAMPLE,
    NO_INTERFACE,
    TAKES_CLASS_EXAMPLE,
    UNREGISTERED_EXAMPLE,
    UNTYPED_REGISTRATIONS,
    Localized,
    MessageTemplate,
)
from hw_checker.checks.deterministic.repository import (
    MAX_EXAMPLES,
    checked_files,
    code,
    joined_examples,
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
    EvidenceCoverageStatus,
    Finding,
    NotEvaluatedReason,
    ProjectReferenceStatus,
)

ANY_ROLE = "*"
"""In a forbidden reference pair: any other role."""
SOURCE_SUFFIX = ".cs"
NOTHING = Localized(he="", en="")
NONE_FOUND = Localized(he="אין", en="none")
_OR = Localized(he=" או ", en=" or ")
_REFERENCED = frozenset({ProjectReferenceStatus.MATCHED, ProjectReferenceStatus.CASE_MISMATCHED})
"""Reference statuses that name a project of the repo."""


def _role_name(value: str) -> str:
    if not re.fullmatch(r"[a-z][a-z0-9_]*", value):
        raise ValueError("a role is a lowercase name such as services")
    return value


Role = Annotated[str, AfterValidator(_role_name)]
RolePair = tuple[Role, Role]


class _RolesParams(BaseModel):
    """`roles` plus the roles the check names, each of which `roles` must define."""

    model_config = ConfigDict(frozen=True, extra="forbid", populate_by_name=True)

    roles: Roles

    def named_roles(self) -> tuple[str, ...]:
        """The roles the check's own params name."""
        return ()

    @model_validator(mode="after")
    def _named_roles_are_defined(self) -> Self:
        unknown = [role for role in self.named_roles() if role not in self.roles]
        if unknown:
            raise ValueError(f"roles not defined in roles: {', '.join(dict.fromkeys(unknown))}")
        return self


class LayerProjectsParams(_RolesParams):
    """`required`: the roles that must each have a project."""

    required: tuple[Role, ...] = Field(min_length=1)

    def named_roles(self) -> tuple[str, ...]:
        return self.required


class LayerReferencesParams(_RolesParams):
    """`required`: [source, target] role pairs some project of the source must reference
    a project of the target (with `transitive_required`, also through other projects);
    `forbidden`: pairs no project may reference directly (target `*`: any other role)."""

    required: tuple[RolePair, ...] = ()
    forbidden: tuple[tuple[Role, Role | Literal["*"]], ...] = ()
    transitive_required: bool = False

    def named_roles(self) -> tuple[str, ...]:
        pairs = (*self.required, *self.forbidden)
        return tuple(role for pair in pairs for role in pair if role != ANY_ROLE)


class CodeOnlyInLayerParams(_RolesParams):
    """`patterns`: C# regular expressions, matched on code with comments and strings
    masked; `allowed`: the roles whose projects may hold such code."""

    patterns: tuple[Regex, ...] = Field(min_length=1)
    allowed: tuple[Role, ...] = Field(min_length=1)

    def named_roles(self) -> tuple[str, ...]:
        return self.allowed


class ImplementsInterfaceParams(_RolesParams):
    """`role`: the role whose public classes must implement an interface declared in the
    repo; `exclude`: class name patterns left out (searched, case-sensitive)."""

    role: Role
    exclude: tuple[Regex, ...] = ()

    def named_roles(self) -> tuple[str, ...]:
        return (self.role,)


class ConstructorTakesInterfaceParams(_RolesParams):
    """`consumer`: the role whose constructors are read; `dependency`: the role whose
    classes they must take by interface."""

    consumer: Role
    dependency: Role

    def named_roles(self) -> tuple[str, ...]:
        return (self.consumer, self.dependency)


class InterfacesRegisteredParams(_RolesParams):
    """`consumers`: the roles whose constructors' interfaces must be registered."""

    consumers: tuple[Role, ...] = Field(min_length=1)

    def named_roles(self) -> tuple[str, ...]:
        return self.consumers


class NoConcreteConstructionParams(_RolesParams):
    """`in`: the roles whose classes must not create, with `new`, a class of the `of`
    roles."""

    in_: tuple[Role, ...] = Field(min_length=1, alias="in")
    of: tuple[Role, ...] = Field(min_length=1)

    def named_roles(self) -> tuple[str, ...]:
        return (*self.in_, *self.of)


@dataclass(frozen=True)
class _Problem:
    """One located example of a finding."""

    text: Localized
    file: str = ""
    line: int = 0

    @property
    def fact(self) -> str:
        return f"{self.file}:{self.line}:{self.text.en}"

    def example(self) -> Localized:
        if not self.file:
            return self.text
        return LOCATED_EXAMPLE.render(item=self.text, file=self.file, line=self.line)


# ---------------------------------------------------------------- the checks


@check(
    "layer_projects",
    LayerProjectsParams,
    "Each required layer role has a project (roles are found by SDK or project name).",
    reads_architecture=True,
)
def layer_projects(check_input: CheckInput, params: LayerProjectsParams) -> CheckResult:
    """One finding naming the roles with no project and the unassigned projects."""
    evidence = _evidence(check_input, signals=False)
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
        missing=_joined(missing),
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
    """One finding listing the missing required references and the forbidden ones."""
    found = _layers(check_input, params, signals=False)
    if not isinstance(found, tuple):
        return found
    evidence, roles = found
    edges = _reference_edges(evidence)
    problems: list[_Problem] = []
    for source_role, target_role in params.required:
        if not _references(
            edges,
            roles.projects(source_role),
            roles.projects(target_role),
            transitive=params.transitive_required,
        ):
            problems.append(
                _Problem(
                    LAYER_REFERENCE_MISSING.render(
                        source=_projects(roles.projects(source_role)),
                        target=_projects(roles.projects(target_role)),
                        source_role=source_role,
                        target_role=target_role,
                    )
                )
            )
    for source, target in edges:
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
                _Problem(
                    LAYER_REFERENCE_FORBIDDEN.render(
                        source=code(project_name(source)),
                        target=code(project_name(target)),
                        source_role=from_role,
                        target_role=to_role,
                    )
                )
            )
    return _finding(check_input, LAYER_REFERENCES, problems)


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
    found = _layers(check_input, params, signals=True)
    if not isinstance(found, tuple):
        return found
    _, roles = found
    patterns = [re.compile(pattern) for pattern in params.patterns]
    problems: list[_Problem] = []
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
            _Problem(Localized(he=code(matched), en=code(matched)), path, scan.line_of(start))
            for start, matched in matches
        ]
    return _finding(check_input, CODE_OUTSIDE_LAYER, problems, allowed=_joined(params.allowed))


@check(
    "implements_interface",
    ImplementsInterfaceParams,
    "Every public class in the role's projects implements an interface declared in the "
    "repo (directly or through its base class); exclude: class name patterns left out.",
    reads_architecture=True,
)
def implements_interface(check_input: CheckInput, params: ImplementsInterfaceParams) -> CheckResult:
    """One finding listing the public classes of the role with no declared interface. A
    static class (which cannot implement one) and a record are not read."""
    found = _layers(check_input, params, signals=True)
    if not isinstance(found, tuple):
        return found
    evidence, roles = found
    types = _Types(evidence)
    excluded = [re.compile(pattern) for pattern in params.exclude]
    problems: list[_Problem] = []
    ambiguous: list[str] = []
    for declaration in types.classes_in(roles, (params.role,)):
        name = _subject(declaration)
        if "public" not in declaration.keywords or "static" in declaration.keywords:
            continue
        if any(pattern.search(name) for pattern in excluded):
            continue
        if types.interfaces_of(name):
            continue
        if types.declared_more_than_once(name):
            ambiguous.append(name)
            continue
        problems.append(_Problem(_code(name), declaration.file_path, declaration.line))
    return _finding(
        check_input, NO_INTERFACE, problems, ambiguous=_ambiguous(ambiguous), role=params.role
    )


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
    found = _layers(check_input, params, signals=True)
    if not isinstance(found, tuple):
        return found
    evidence, roles = found
    types = _Types(evidence)
    dependencies = {
        _subject(declaration) for declaration in types.classes_in(roles, (params.dependency,))
    }
    problems: list[_Problem] = []
    ambiguous: list[str] = []
    for parameter in _signals(evidence, ArchitectureSignalKind.CONSTRUCTOR_PARAMETER):
        taken = simple_name(_subject(parameter))
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
            interface=_joined([code(name) for name in interfaces], _OR),
        )
        problems.append(_Problem(text, parameter.file_path, parameter.line))
    return _finding(
        check_input,
        CONSTRUCTOR_TAKES_CLASS,
        problems,
        ambiguous=_ambiguous(ambiguous),
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
    found = _layers(check_input, params, signals=True)
    if not isinstance(found, tuple):
        return found
    evidence, roles = found
    types = _Types(evidence)
    registrations = _signals(evidence, ArchitectureSignalKind.REGISTRATION_CANDIDATE)
    registered = {simple_name(signal.subject) for signal in registrations if signal.subject}
    untyped = sum(signal.subject is None for signal in registrations)
    problems: list[_Problem] = []
    ambiguous: list[str] = []
    for parameter in _signals(evidence, ArchitectureSignalKind.CONSTRUCTOR_INTERFACE_PARAMETER):
        interface = simple_name(_subject(parameter))
        if roles.role_of(parameter.file_path) not in params.consumers or interface in registered:
            continue
        if types.interface_declarations[interface] > 1:
            ambiguous.append(interface)
            continue
        text = UNREGISTERED_EXAMPLE.render(
            consumer=code(parameter.related_subject or ""), interface=code(interface)
        )
        problems.append(_Problem(text, parameter.file_path, parameter.line))
    untyped_note = UNTYPED_REGISTRATIONS.render(count=untyped) if untyped else NOTHING
    return _finding(
        check_input,
        INTERFACES_UNREGISTERED,
        problems,
        untyped=untyped_note,
        ambiguous=_ambiguous(ambiguous),
    )


@check(
    "no_concrete_construction",
    NoConcreteConstructionParams,
    "No class in the `in` roles' projects creates a class of the `of` roles with new "
    "(top-level statements, where registrations are written, are not read).",
    reads_architecture=True,
)
def no_concrete_construction(
    check_input: CheckInput, params: NoConcreteConstructionParams
) -> CheckResult:
    """One finding: "`UserService` creates `UserRepository`"."""
    found = _layers(check_input, params, signals=True)
    if not isinstance(found, tuple):
        return found
    evidence, roles = found
    types = _Types(evidence)
    created_classes = {_subject(d) for d in types.classes_in(roles, params.of)}
    problems: list[_Problem] = []
    ambiguous: list[str] = []
    for creation in _signals(evidence, ArchitectureSignalKind.OBJECT_CREATION):
        created = simple_name(_subject(creation))
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
        problems.append(_Problem(text, creation.file_path, creation.line))
    return _finding(
        check_input,
        CONCRETE_CONSTRUCTION,
        problems,
        ambiguous=_ambiguous(ambiguous),
        of=_joined(params.of, _OR),
    )


LAYER_CHECKS = (
    layer_projects,
    layer_references,
    code_only_in_layer,
    implements_interface,
    constructor_takes_interface,
    interfaces_registered,
    no_concrete_construction,
)


# ---------------------------------------------------------------- helpers


def simple_name(type_text: str) -> str:
    """`Data.IRepository<User>?` as `IRepository`: no namespace, generic arguments,
    nullable mark or array brackets."""
    return type_text.split("<", 1)[0].rstrip("?[]").split(".")[-1]


class _Types:
    """The evidence's interfaces and classes by simple name."""

    def __init__(self, evidence: ArchitectureEvidence) -> None:
        self.interface_declarations = Counter(
            simple_name(_subject(signal))
            for signal in _signals(evidence, ArchitectureSignalKind.INTERFACE_DECLARATION)
        )
        self.declarations = _signals(evidence, ArchitectureSignalKind.TYPE_DECLARATION)
        self._by_name: dict[str, list[ArchitectureSignal]] = {}
        for declaration in self.declarations:
            self._by_name.setdefault(_subject(declaration), []).append(declaration)

    def classes_in(self, roles: Layers, role_names: Iterable[str]) -> list[ArchitectureSignal]:
        """The class declarations (not records) in the projects of `role_names`."""
        wanted = set(role_names)
        return [
            declaration
            for declaration in self.declarations
            if "class" in declaration.keywords
            and "record" not in declaration.keywords
            and roles.role_of(declaration.file_path) in wanted
        ]

    def declared_more_than_once(self, name: str) -> bool:
        """A type name declared twice (the parts of one partial class count once), or
        both as a type and as an interface."""
        found = self._by_name.get(name, [])
        whole = sum("partial" not in declaration.keywords for declaration in found)
        partial = any("partial" in declaration.keywords for declaration in found)
        return whole + partial + self.interface_declarations[name] > 1

    def interfaces_of(self, name: str, seen: frozenset[str] = frozenset()) -> tuple[str, ...]:
        """The interfaces declared in the repo that the class `name` implements, directly or
        through a base class declared in the repo."""
        found: list[str] = []
        for declaration in self._by_name.get(name, []):
            for base in declaration.related_subjects:
                base_name = simple_name(base)
                if self.interface_declarations[base_name]:
                    found.append(base_name)
                elif base_name in self._by_name and base_name not in seen | {name}:
                    found += self.interfaces_of(base_name, seen | {name})
        return tuple(dict.fromkeys(found))


def _evidence(check_input: CheckInput, *, signals: bool) -> ArchitectureEvidence | None:
    """The evidence when the coverage the check reads (inventory, and signals when
    `signals`) is available; None otherwise."""
    evidence = check_input.architecture
    unavailable = EvidenceCoverageStatus.UNAVAILABLE
    if evidence is None or evidence.inventory_coverage.status is unavailable:
        return None
    if signals and evidence.signal_coverage.status is unavailable:
        return None
    return evidence


def _layers(
    check_input: CheckInput, params: _RolesParams, *, signals: bool
) -> tuple[ArchitectureEvidence, Layers] | NotEvaluatedReason:
    """The evidence and its projects by role, or why the check is not evaluated."""
    evidence = _evidence(check_input, signals=signals)
    if evidence is None:
        return NotEvaluatedReason.EVIDENCE_UNAVAILABLE
    found = layers(evidence, params.roles)
    if found.missing(params.named_roles()):
        return NotEvaluatedReason.DEPENDENCY_FAILED
    return evidence, found


def _finding(
    check_input: CheckInput,
    template: MessageTemplate,
    problems: list[_Problem],
    **fields: str | int | Localized,
) -> tuple[Finding, ...]:
    if not problems:
        return ()
    examples = joined_examples(problem.example() for problem in problems[:MAX_EXAMPLES])
    message = template.render(examples=examples, count=len(problems), **fields)
    return (deterministic_finding(check_input, message, [p.fact for p in problems]),)


def _references(
    edges: list[tuple[str, str]],
    sources: tuple[str, ...],
    targets: tuple[str, ...],
    *,
    transitive: bool,
) -> bool:
    """Whether a project of `sources` references one of `targets`: directly, or with
    `transitive` through other projects (SDK-style references flow through)."""
    following: dict[str, set[str]] = {}
    for source, target in edges:
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


def _reference_edges(evidence: ArchitectureEvidence) -> list[tuple[str, str]]:
    """The project references as (source, target) project files of the inventory. A
    reference written with other letter case (`case_mismatched`) is still a reference here:
    the build and file-reference checks own the letter case."""
    by_lower = {project.lower(): project for project in evidence.project_files}
    edges: list[tuple[str, str]] = []
    for reference in evidence.project_references:
        if reference.status not in _REFERENCED or reference.target_project is None:
            continue
        target = by_lower.get(reference.target_project.lower())
        if target is not None:
            edges.append((reference.source_project, target))
    return edges


def _signals(
    evidence: ArchitectureEvidence, kind: ArchitectureSignalKind
) -> list[ArchitectureSignal]:
    return [signal for signal in evidence.signals if signal.kind is kind]


def _subject(signal: ArchitectureSignal) -> str:
    return signal.subject or ""


def _other(target: str, source_role: str, target_role: str) -> bool:
    return target == ANY_ROLE and target_role != source_role


def _projects(projects: tuple[str, ...]) -> str:
    return "/".join(code(project_name(project)) for project in projects)


def _unassigned(found: Layers) -> Localized:
    if not found.unassigned:
        return NOTHING
    projects = ", ".join(code(project_name(project)) for project in found.unassigned)
    return LAYER_UNASSIGNED.render(projects=projects)


def _joined(items: Iterable[str], separator: Localized | None = None) -> Localized:
    words = list(items)
    if separator is None:
        return Localized(he=", ".join(words), en=", ".join(words))
    return Localized(he=separator.he.join(words), en=separator.en.join(words))


def _code(name: str) -> Localized:
    return Localized(he=code(name), en=code(name))


def _ambiguous(names: list[str]) -> Localized:
    if not names:
        return NOTHING
    return AMBIGUOUS_NAMES.render(names=", ".join(code(name) for name in dict.fromkeys(names)))
