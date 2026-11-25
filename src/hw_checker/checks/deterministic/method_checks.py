"""A generic check on the methods of selected classes (HW05-G5): every public method returns
a `Task` and, in a class, is marked `async`.

The mechanism is here; which roles, which classes and methods are left out and how serious a
finding is are each requirement's data (ADR 0023). The check reads the `method_declaration`
signals of the architecture evidence. When that evidence is unavailable it is not evaluated
(`evidence_unavailable`); when a role it names has no project, or those projects hold no
class or interface, it is not evaluated either (`dependency_failed`).
"""

import re
from typing import Final, Literal

from pydantic import Field

from hw_checker.checks.deterministic.class_selection import LeftOut
from hw_checker.checks.deterministic.layer_support import (
    ClassesLeftOutParams,
    Problem,
    Role,
    Types,
    line_evidence,
    make_finding,
    read_layers,
    signals_of,
    subject,
)
from hw_checker.checks.deterministic.layers import Regex
from hw_checker.checks.deterministic.messages import (
    METHOD_EXAMPLE,
    METHOD_LACKS_SEPARATOR,
    METHOD_NOT_MARKED_ASYNC,
    METHOD_NOT_TASK,
    METHODS_NOT_ASYNC,
    Localized,
)
from hw_checker.checks.deterministic.repository import code
from hw_checker.checks.registry import CheckResult, check
from hw_checker.domain import (
    ArchitectureSignal,
    ArchitectureSignalKind,
    CheckInput,
    DeclaringKind,
    Evidence,
    NotEvaluatedReason,
)

Rule = Literal["returns_task", "async_modifier"]
Modifier = Literal[
    "public",
    "private",
    "protected",
    "internal",
    "static",
    "async",
    "override",
    "virtual",
    "abstract",
    "sealed",
    "new",
    "partial",
    "extern",
    "unsafe",
    "readonly",
]
_PUBLIC: Final = "public"
_ASYNC: Final = "async"
_NO_BODY: Final = frozenset({"abstract", "extern"})
"""Methods with these modifiers have no body to mark `async`, like an interface member."""
_TASK = re.compile(r"(?:global::)?(?:\w+\.)*(?:Task|ValueTask)(?:<.*>)?")
_SPACE = re.compile(r"\s+")


class MethodsAsyncParams(ClassesLeftOutParams):
    """`in`: the roles whose classes' and interfaces' public methods are read; `exclude`,
    `exclude_bases`, `exclude_db_entities`: classes left out; `exclude_methods`: patterns on
    a method's name left out; `exclude_modifiers`: methods with one of these modifiers left
    out; `require`: what must hold (both by default)."""

    in_: tuple[Role, ...] = Field(min_length=1, alias="in")
    exclude_methods: tuple[Regex, ...] = ()
    exclude_modifiers: tuple[Modifier, ...] = ()
    require: tuple[Rule, ...] = Field(default=("returns_task", "async_modifier"), min_length=1)

    def named_roles(self) -> tuple[str, ...]:
        return self.in_


@check(
    "methods_async",
    MethodsAsyncParams,
    "Every public method of the classes and interfaces in the `in` roles' projects returns "
    "Task, Task<T>, ValueTask or ValueTask<T> (require: returns_task) and, in a class, is "
    "marked async (require: async_modifier; an abstract or extern method and an interface "
    "member are only asked for the return type). Only public methods are read (an interface "
    "member without a modifier is public, a class member without one is private). exclude, "
    "exclude_bases, exclude_db_entities: classes left out; exclude_methods: method name "
    "patterns left out; exclude_modifiers: methods with one of these modifiers left out.",
    reads_architecture=True,
)
def methods_async(check_input: CheckInput, params: MethodsAsyncParams) -> CheckResult:
    """One finding listing each public method that lacks a required rule, with its class,
    name, file and line and what it lacks; the first one's line is the finding's evidence."""
    found = read_layers(check_input, params, signals=True)
    if not isinstance(found, tuple):
        return found
    evidence, roles = found
    types = Types(evidence)
    declared_files = [
        *(declaration.file_path for declaration in types.declarations),
        *(
            signal.file_path
            for signal in signals_of(evidence, ArchitectureSignalKind.INTERFACE_DECLARATION)
        ),
    ]
    if not any(roles.role_of(path) in params.in_ for path in declared_files):
        return NotEvaluatedReason.DEPENDENCY_FAILED
    left_out = LeftOut(params, types)
    excluded_names = [re.compile(pattern) for pattern in params.exclude_methods]
    problems: list[Problem] = []
    for method in signals_of(evidence, ArchitectureSignalKind.METHOD_DECLARATION):
        if roles.role_of(method.file_path) not in params.in_ or _PUBLIC not in method.keywords:
            continue
        if left_out(method.related_subject or "") or set(params.exclude_modifiers) & set(
            method.keywords
        ):
            continue
        if any(pattern.search(subject(method)) for pattern in excluded_names):
            continue
        lacks = _lacks(method, params.require)
        if lacks:
            text = METHOD_EXAMPLE.render(
                owner=code(method.related_subject or ""),
                name=code(subject(method)),
                lacks=_joined(lacks),
            )
            problems.append(Problem(text, method.file_path, method.line))
    return make_finding(
        check_input, METHODS_NOT_ASYNC, problems, _first_line(check_input, problems)
    )


METHOD_CHECKS = (methods_async,)


def _lacks(method: ArchitectureSignal, require: tuple[Rule, ...]) -> list[Localized]:
    """What the method lacks of the required rules."""
    lacking: list[Localized] = []
    return_type = method.return_type or ""
    if "returns_task" in require and not _TASK.fullmatch(_SPACE.sub("", return_type)):
        lacking.append(METHOD_NOT_TASK.render(return_type=code(return_type)))
    if "async_modifier" in require and _needs_async(method) and _ASYNC not in method.keywords:
        lacking.append(METHOD_NOT_MARKED_ASYNC.render())
    return lacking


def _joined(lacks: list[Localized]) -> Localized:
    separator = METHOD_LACKS_SEPARATOR
    return Localized(
        he=separator.he.join(item.he for item in lacks),
        en=separator.en.join(item.en for item in lacks),
    )


def _needs_async(method: ArchitectureSignal) -> bool:
    """A class, record or struct method with a body can be marked `async`."""
    return method.declared_in is not DeclaringKind.INTERFACE and not (
        _NO_BODY & set(method.keywords)
    )


def _first_line(check_input: CheckInput, problems: list[Problem]) -> Evidence | None:
    located = next((problem for problem in problems if problem.file), None)
    return line_evidence(check_input, located) if located else None
