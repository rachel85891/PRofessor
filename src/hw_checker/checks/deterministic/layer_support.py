"""What the layer checks and the pattern check share: params bases, the evidence's types by
name, the classes a check leaves out, and how a finding lists its problems.

Everything here is generic; the roles, patterns and wording are each requirement's data
(ADR 0023). A type name declared more than once is never guessed: callers leave it out
and the finding says so.
"""

import re
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Annotated, Self

from pydantic import AfterValidator, BaseModel, ConfigDict, model_validator

from hw_checker.checks.deterministic.findings import deterministic_finding
from hw_checker.checks.deterministic.layers import Layers, Regex, Roles, layers
from hw_checker.checks.deterministic.messages import (
    AMBIGUOUS_NAMES,
    LOCATED_EXAMPLE,
    Localized,
    MessageTemplate,
)
from hw_checker.checks.deterministic.repository import (
    MAX_EXAMPLES,
    code,
    joined_examples,
    read_source,
)
from hw_checker.domain import (
    ArchitectureEvidence,
    ArchitectureSignal,
    ArchitectureSignalKind,
    CheckInput,
    Evidence,
    EvidenceCoverageStatus,
    Finding,
    NotEvaluatedReason,
)

NOTHING = Localized(he="", en="")
PUBLIC = "public"


def _role_name(value: str) -> str:
    if not re.fullmatch(r"[a-z][a-z0-9_]*", value):
        raise ValueError("a role is a lowercase name such as services")
    return value


Role = Annotated[str, AfterValidator(_role_name)]


class RolesParams(BaseModel):
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


class ClassesLeftOutParams(RolesParams):
    """`exclude`: class name patterns left out; `exclude_bases`: patterns on the simple
    names in a class's base list (`IdentityDbContext<User>` is `IdentityDbContext`), a
    class with a matching base left out. Both are searched, case-sensitive; empty leaves
    nothing out. `exclude_db_entities`: the types named as `T` in a `DbSet<T>` property of
    a class `exclude_bases` leaves out (a `DbContext`'s entities) are left out too. Which
    classes are not layer classes is the assignment's data."""

    exclude: tuple[Regex, ...] = ()
    exclude_bases: tuple[Regex, ...] = ()
    exclude_db_entities: bool = False


@dataclass(frozen=True)
class Problem:
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


def simple_name(type_text: str) -> str:
    """`Data.IRepository<User>?` as `IRepository`: no namespace, generic arguments,
    nullable mark or array brackets."""
    return type_text.split("<", 1)[0].rstrip("?[]").split(".")[-1]


def subject(signal: ArchitectureSignal) -> str:
    return signal.subject or ""


def signals_of(
    evidence: ArchitectureEvidence, kind: ArchitectureSignalKind
) -> list[ArchitectureSignal]:
    return [signal for signal in evidence.signals if signal.kind is kind]


class Types:
    """The evidence's interfaces and classes by simple name."""

    def __init__(self, evidence: ArchitectureEvidence) -> None:
        self.interface_declarations = Counter(
            simple_name(subject(signal))
            for signal in signals_of(evidence, ArchitectureSignalKind.INTERFACE_DECLARATION)
        )
        self.declarations = signals_of(evidence, ArchitectureSignalKind.TYPE_DECLARATION)
        self.db_sets = tuple(
            (subject(signal), signal.related_subject)
            for signal in signals_of(evidence, ArchitectureSignalKind.DB_SET_PROPERTY)
        )
        """Each `DbSet<T>` property as (`T`'s simple name, the type that declares it)."""
        self._by_name: dict[str, list[ArchitectureSignal]] = {}
        for declaration in self.declarations:
            self._by_name.setdefault(subject(declaration), []).append(declaration)

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

    def public(self, name: str) -> bool:
        """Whether a declaration of the type `name` is `public` (one part of a partial
        class is enough)."""
        return any(PUBLIC in declaration.keywords for declaration in self._by_name.get(name, []))

    def declared_more_than_once(self, name: str) -> bool:
        """A type name declared twice (the parts of one partial class count once), or
        both as a type and as an interface."""
        found = self._by_name.get(name, [])
        whole = sum("partial" not in declaration.keywords for declaration in found)
        partial = any("partial" in declaration.keywords for declaration in found)
        return whole + partial + self.interface_declarations[name] > 1

    def bases_of(self, name: str) -> tuple[str, ...]:
        """The simple names in the base lists of the class `name`'s declarations."""
        return tuple(
            simple_name(base)
            for declaration in self._by_name.get(name, [])
            for base in declaration.related_subjects
        )

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


class LeftOut:
    """Whether a class is left out by the params' `exclude`, `exclude_bases` and
    `exclude_db_entities`."""

    def __init__(self, params: ClassesLeftOutParams, types: Types) -> None:
        self._names = [re.compile(pattern) for pattern in params.exclude]
        self._bases = [re.compile(pattern) for pattern in params.exclude_bases]
        self._types = types
        self._entities = (
            frozenset(
                entity
                for entity, context in types.db_sets
                if context is not None and self._by_base(context)
            )
            if params.exclude_db_entities
            else frozenset()
        )

    def __call__(self, name: str) -> bool:
        if any(pattern.search(name) for pattern in self._names):
            return True
        return self._by_base(name) or name in self._entities

    def _by_base(self, name: str) -> bool:
        bases = self._types.bases_of(name) if self._bases else ()
        return any(pattern.search(base) for pattern in self._bases for base in bases)


def read_evidence(check_input: CheckInput, *, signals: bool) -> ArchitectureEvidence | None:
    """The evidence when the coverage the check reads (inventory, and signals when
    `signals`) is available; None otherwise."""
    evidence = check_input.architecture
    unavailable = EvidenceCoverageStatus.UNAVAILABLE
    if evidence is None or evidence.inventory_coverage.status is unavailable:
        return None
    if signals and evidence.signal_coverage.status is unavailable:
        return None
    return evidence


def read_layers(
    check_input: CheckInput, params: RolesParams, *, signals: bool
) -> tuple[ArchitectureEvidence, Layers] | NotEvaluatedReason:
    """The evidence and its projects by role, or why the check is not evaluated."""
    evidence = read_evidence(check_input, signals=signals)
    if evidence is None:
        return NotEvaluatedReason.EVIDENCE_UNAVAILABLE
    found = layers(evidence, params.roles)
    if found.missing(params.named_roles()):
        return NotEvaluatedReason.DEPENDENCY_FAILED
    return evidence, found


def make_finding(
    check_input: CheckInput,
    template: MessageTemplate,
    problems: list[Problem],
    evidence: Evidence | None = None,
    **fields: str | int | Localized,
) -> tuple[Finding, ...]:
    """One finding listing up to `MAX_EXAMPLES` problems and their total; none when there
    are no problems."""
    if not problems:
        return ()
    examples = joined_examples(problem.example() for problem in problems[:MAX_EXAMPLES])
    message = template.render(examples=examples, count=len(problems), **fields)
    facts = [p.fact for p in problems]
    return (deterministic_finding(check_input, message, facts, evidence),)


def line_evidence(check_input: CheckInput, problem: Problem) -> Evidence | None:
    """The problem's line as a finding's evidence, quoted from the repo; None when the
    line cannot be read."""
    text = read_source(check_input.repo_dir, problem.file)
    lines = text.splitlines() if text is not None else []
    quote = lines[problem.line - 1].strip() if 0 < problem.line <= len(lines) else ""
    if not quote:
        return None
    return Evidence(
        file_path=problem.file, line_start=problem.line, line_end=problem.line, quote=quote
    )


def joined(items: Iterable[str], separator: Localized | None = None) -> Localized:
    words = list(items)
    if separator is None:
        return Localized(he=", ".join(words), en=", ".join(words))
    return Localized(he=separator.he.join(words), en=separator.en.join(words))


def code_text(name: str) -> Localized:
    return Localized(he=code(name), en=code(name))


def ambiguous_note(names: list[str]) -> Localized:
    """The finding's note on type names left out because they are declared more than once."""
    if not names:
        return NOTHING
    return AMBIGUOUS_NAMES.render(names=", ".join(code(name) for name in dict.fromkeys(names)))
