"""Lexical C# signals for layers and dependency injection: interfaces, classes and their
base lists, constructors and their parameters, object creations and registrations.

Comments and strings are masked first. Interfaces are collected from every file before any
constructor is matched, so a parameter matches an interface declared in another project.
"""

import re
from typing import Final

from hw_checker.adapters.architecture_evidence.csharp_methods import (
    MAX_IDENTIFIER_CHARS,
    TypeBody,
    matching_close,
    method_signals,
    top_level_member_headers,
)
from hw_checker.adapters.docker_runner.code_facts import CollectedFacts, facts_in_source
from hw_checker.adapters.docker_runner.csharp_source import mask_source
from hw_checker.domain import (
    ArchitectureSignal,
    ArchitectureSignalKind,
    DeclaringKind,
    EvidenceCoverageIssue,
    EvidenceIssueKind,
    ServiceLifetime,
    TypeKind,
)

_PRIMARY_CONSTRUCTOR = re.compile(
    r"\b(?:class|struct|record(?:\s+(?:class|struct))?)\s+(?P<name>[A-Za-z_]\w*)"
    r"(?:\s*<[^;{}()]*>)?\s*\("
)
_ALIAS = re.compile(r"(?m)^\s*(?:global\s+)?using\s+[A-Za-z_]\w*\s*=")
_PREPROCESSOR = re.compile(r"(?m)^\s*#\s*(?:if|elif|else|endif)\b")
_TYPE_DECLARATION = re.compile(
    r"\b(?P<kind>class|record(?:\s+class)?)\s+(?P<name>[A-Za-z_]\w*)"
    r"(?:\s*<[^;{}]*>)?[^;{}]*\{"
)
_CONSTRUCTOR = re.compile(
    r"^\s*(?:(?:public|private|protected|internal|unsafe|extern)\s+)*"
    r"(?P<name>[A-Za-z_]\w*)\s*\((?P<parameters>[^(){}]*)\)"
    r"\s*(?::\s*(?:base|this)\s*\([^{}]*\)\s*)?$"
)
_ATTRIBUTE_SECTIONS = re.compile(r"^\s*(?:\[[^\[\]]*\]\s*)+")
"""Leading `[...]` attribute sections; their strings are already masked."""
_PARAMETER = re.compile(
    r"(?:(?:this|params|ref|in|out|scoped)\s+)*"
    r"(?P<type>[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*(?:<[^<>]*>)?(?:\[\])?\??)"
    r"\s+[A-Za-z_]\w*(?:\s*=\s*[^,]+)?"
)
_REGISTRATION = re.compile(
    r"\b(?P<name>Add(?:Singleton|Scoped|Transient)(?:Keyed)?|"
    r"AddKeyed(?:Singleton|Scoped|Transient))\s*(?=[<(])"
)
_LIFETIMES: Final = {
    "Singleton": ServiceLifetime.SINGLETON,
    "Scoped": ServiceLifetime.SCOPED,
    "Transient": ServiceLifetime.TRANSIENT,
}
_TYPE_TEXT = r"[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*(?:\s*<[^()<>]*(?:<[^()<>]*>[^()<>]*)*>)?"
_TYPE_ARGUMENT = re.compile(rf"{_TYPE_TEXT}\??")
_EMPTY_CALL = re.compile(r"\s*\(\s*\)")
_TYPEOF_CALL = re.compile(
    rf"\(\s*typeof\s*\(\s*(?P<service>{_TYPE_TEXT})\s*\)\s*"
    rf"(?:,\s*typeof\s*\(\s*(?P<implementation>{_TYPE_TEXT})\s*\)\s*)?\)"
)
_ANY_TYPE = re.compile(
    r"\b(?P<keyword>record(?:\s+(?:class|struct))?|class|struct|interface)\s+"
    r"(?P<name>[A-Za-z_]\w*)"
)
"""Every type with a body, for the enclosing type of an object creation."""
_DECLARED_KEYWORDS = frozenset({"class", "record", "record class"})
"""The declarations that give `type_declaration`: classes and records, not structs."""
_NOT_A_NAME = frozenset({"class", "struct", "where", "interface", "record"})
_MODIFIER = re.compile(
    r"(?:public|private|protected|internal|static|abstract|sealed|partial|file|unsafe|"
    r"readonly|new)$"
)
_BASE_TYPE = re.compile(rf"{_TYPE_TEXT}")
_WHERE = re.compile(r"\bwhere\b")
_CREATION = re.compile(r"\bnew\s+(?P<type>[A-Za-z_]\w*(?:\s*\.\s*[A-Za-z_]\w*)*)")
_DB_SET = re.compile(
    r"\bDbSet\s*<\s*(?P<entity>[A-Za-z_]\w*(?:\s*\.\s*[A-Za-z_]\w*)*)\s*>\s*\??\s+"
    r"(?P<name>[A-Za-z_]\w*)\s*(?:\{|=>)"
)
"""A `DbSet<T>` property: its type, its name, then `{` or `=>`."""
_NAMESPACE_WORD = re.compile(r"\bnamespace\b")
_NAMESPACE = re.compile(
    r"\bnamespace\s+(?P<name>[A-Za-z_]\w*(?:\s*\.\s*[A-Za-z_]\w*)*)\s*(?P<end>[;{])"
)
"""A namespace declaration: its name, then `;` (file-scoped) or `{` (block)."""
_SPACE = re.compile(r"\s+")
_LEADING_SPACE = re.compile(r"\s*")
_MODIFIERS_WINDOW: Final = 200
"""How far before a type keyword its modifiers are looked for."""


def signals_in_sources(
    sources: list[tuple[str, str]],
) -> tuple[tuple[ArchitectureSignal, ...], tuple[EvidenceCoverageIssue, ...]]:
    """Two passes: interfaces from every file first, then constructors matched against them."""
    issues: list[EvidenceCoverageIssue] = []
    readable: list[tuple[str, str]] = []
    collected = CollectedFacts()
    for file_path, text in sources:
        masked = mask_source(text).text
        issues.extend(_lexical_issues(masked, file_path))
        if not _balanced_csharp(masked):
            issues.append(
                EvidenceCoverageIssue(kind=EvidenceIssueKind.AMBIGUOUS_SYNTAX, file_path=file_path)
            )
            continue
        facts_in_source(text, file_path, collected)
        readable.append((file_path, masked))

    signals: list[ArchitectureSignal] = []
    declarations: dict[str, int] = {}
    for fact in collected.types:
        if fact.kind is not TypeKind.INTERFACE:
            continue
        if len(fact.name) > MAX_IDENTIFIER_CHARS:
            issues.append(
                EvidenceCoverageIssue(
                    kind=EvidenceIssueKind.AMBIGUOUS_SYNTAX,
                    file_path=fact.file_path,
                    line=fact.line,
                )
            )
            continue
        declarations[fact.name] = declarations.get(fact.name, 0) + 1
        signals.append(
            ArchitectureSignal(
                kind=ArchitectureSignalKind.INTERFACE_DECLARATION,
                file_path=fact.file_path,
                line=fact.line,
                subject=fact.name,
            )
        )

    for file_path, masked in readable:
        file_signals, file_issues = _signals_in_file(masked, file_path, declarations)
        signals.extend(file_signals)
        issues.extend(file_issues)
    return tuple(signals), tuple(issues)


def _lexical_issues(masked: str, file_path: str) -> list[EvidenceCoverageIssue]:
    issues: list[EvidenceCoverageIssue] = []
    if _PREPROCESSOR.search(masked):
        issues.append(
            EvidenceCoverageIssue(
                kind=EvidenceIssueKind.PREPROCESSOR_CONDITION, file_path=file_path
            )
        )
    if _ALIAS.search(masked):
        issues.append(
            EvidenceCoverageIssue(kind=EvidenceIssueKind.USING_ALIAS, file_path=file_path)
        )
    return issues


def _signals_in_file(
    masked: str, file_path: str, declarations: dict[str, int]
) -> tuple[list[ArchitectureSignal], list[EvidenceCoverageIssue]]:
    signals: list[ArchitectureSignal] = []
    issues: list[EvidenceCoverageIssue] = []
    for match in _REGISTRATION.finditer(masked):
        service, implementation = _registered_types(masked, match.end())
        signals.append(
            ArchitectureSignal(
                kind=ArchitectureSignalKind.REGISTRATION_CANDIDATE,
                file_path=file_path,
                line=masked.count("\n", 0, match.start("name")) + 1,
                subject=service,
                related_subject=implementation,
                registration_method=match["name"],
                lifetime=next(
                    lifetime for word, lifetime in _LIFETIMES.items() if word in match["name"]
                ),
            )
        )

    for primary in _PRIMARY_CONSTRUCTOR.finditer(masked):
        opening = primary.end() - 1
        closing = matching_close(masked, opening)
        line = masked.count("\n", 0, primary.start("name")) + 1
        if closing is None or len(primary["name"]) > MAX_IDENTIFIER_CHARS:
            issues.append(
                EvidenceCoverageIssue(
                    kind=EvidenceIssueKind.AMBIGUOUS_SYNTAX, file_path=file_path, line=line
                )
            )
            continue
        _constructor_signals(
            primary["name"],
            masked[opening + 1 : closing],
            file_path,
            line,
            declarations,
            signals,
            issues,
        )

    for declaration in _TYPE_DECLARATION.finditer(masked):
        type_name = declaration["name"]
        if len(type_name) > MAX_IDENTIFIER_CHARS:
            issues.append(
                EvidenceCoverageIssue(
                    kind=EvidenceIssueKind.AMBIGUOUS_SYNTAX,
                    file_path=file_path,
                    line=masked.count("\n", 0, declaration.start()) + 1,
                )
            )
            continue
        opening = declaration.end() - 1
        closing = matching_close(masked, opening)
        if closing is None:
            issues.append(
                EvidenceCoverageIssue(
                    kind=EvidenceIssueKind.AMBIGUOUS_SYNTAX,
                    file_path=file_path,
                    line=masked.count("\n", 0, opening) + 1,
                )
            )
            continue
        body = masked[opening + 1 : closing]
        for raw_header, raw_offset in top_level_member_headers(body):
            header = _ATTRIBUTE_SECTIONS.sub("", raw_header, count=1)
            offset = raw_offset + len(raw_header) - len(header)
            constructor = _CONSTRUCTOR.fullmatch(header)
            if constructor is None or constructor["name"] != type_name:
                continue
            line = masked.count("\n", 0, opening + 1 + offset + constructor.start("name")) + 1
            _constructor_signals(
                type_name,
                constructor["parameters"],
                file_path,
                line,
                declarations,
                signals,
                issues,
            )
    namespaces = _namespace_spans(masked)
    unreadable = _NAMESPACE_WORD.search(masked) if namespaces is None else None
    if unreadable is not None:
        issues.append(
            EvidenceCoverageIssue(
                kind=EvidenceIssueKind.AMBIGUOUS_SYNTAX,
                file_path=file_path,
                line=masked.count("\n", 0, unreadable.start()) + 1,
            )
        )
    bodies = _type_signals(masked, file_path, namespaces, signals, issues)
    _creation_signals(masked, file_path, bodies, signals, issues)
    _db_set_signals(masked, file_path, bodies, signals)
    if namespaces is not None:
        methods, method_issues = method_signals(masked, file_path, bodies)
        signals.extend(methods)
        issues.extend(method_issues)
    return signals, issues


def _type_signals(
    masked: str,
    file_path: str,
    namespaces: list[tuple[int, int, str]] | None,
    signals: list[ArchitectureSignal],
    issues: list[EvidenceCoverageIssue],
) -> list[TypeBody]:
    """A `type_declaration` per class and record, with its base list and namespace; returns
    every type body as (opening, closing, name), for the enclosing type of an object
    creation. When the file's namespaces cannot be read (`namespaces` is None) it gives no
    declaration: a namespace is never guessed."""
    bodies: list[TypeBody] = []
    for match in _ANY_TYPE.finditer(masked):
        name = match["name"]
        if name in _NOT_A_NAME:
            continue
        line = masked.count("\n", 0, match.start("name")) + 1
        header_end = _header_end(masked, match.end())
        if header_end is None:
            continue  # not a declaration with a body or `;` (such as `where T : class`)
        if masked[header_end] == "{":
            closing = matching_close(masked, header_end)
            if closing is not None:
                bodies.append(TypeBody(header_end, closing, name, _declaring_kind(match)))
        keyword = _SPACE.sub(" ", match["keyword"])
        if keyword not in _DECLARED_KEYWORDS:
            continue
        if namespaces is None:
            continue
        bases = _base_list(masked, match.end(), header_end)
        if bases is None or len(name) > MAX_IDENTIFIER_CHARS:
            issues.append(
                EvidenceCoverageIssue(
                    kind=EvidenceIssueKind.AMBIGUOUS_SYNTAX, file_path=file_path, line=line
                )
            )
            continue
        signals.append(
            ArchitectureSignal(
                kind=ArchitectureSignalKind.TYPE_DECLARATION,
                file_path=file_path,
                line=line,
                subject=name,
                related_subjects=bases,
                keywords=(*_modifiers_before(masked, match.start()), *keyword.split()),
                namespace=_namespace_at(namespaces, match.start()),
            )
        )
    return bodies


def _declaring_kind(match: re.Match[str]) -> DeclaringKind:
    """What a type keyword declares; any `record` form is a record."""
    keyword = match["keyword"]
    return DeclaringKind.RECORD if keyword.startswith("record") else DeclaringKind(keyword)


def _namespace_spans(masked: str) -> list[tuple[int, int, str]] | None:
    """Each namespace declaration as (start, end, name) of the text it covers: a block
    namespace its braces, a file-scoped one the rest of the file. None when they cannot be
    read reliably: a `namespace` the pattern does not match, an unclosed block, two
    file-scoped namespaces, or a file-scoped one beside a block one."""
    found = list(_NAMESPACE.finditer(masked))
    if len(found) != len(_NAMESPACE_WORD.findall(masked)):
        return None
    spans: list[tuple[int, int, str]] = []
    for match in found:
        name = _SPACE.sub("", match["name"])
        if len(name) > MAX_IDENTIFIER_CHARS:
            return None
        if match["end"] == ";":
            spans.append((match.end(), len(masked), name))
            continue
        closing = matching_close(masked, match.end() - 1)
        if closing is None:
            return None
        spans.append((match.end(), closing, name))
    file_scoped = [span for span, match in zip(spans, found, strict=True) if match["end"] == ";"]
    if len(file_scoped) > 1 or (file_scoped and len(spans) > 1):
        return None
    return spans


def _namespace_at(spans: list[tuple[int, int, str]] | None, position: int) -> str | None:
    """The namespace holding `position`, nested block namespaces joined by `.`; None for the
    global namespace."""
    names = [name for start, end, name in sorted(spans or []) if start <= position < end]
    return ".".join(names) or None


def _header_end(masked: str, position: int) -> int | None:
    """The `{` or `;` that ends a type's header, skipping its generic parameters and its
    primary constructor; None when a `)`, `}` or `=` comes first (not a declaration)."""
    index = position
    while index < len(masked):
        character = masked[index]
        if character in "{;":
            return index
        if character == "(":
            closing = matching_close(masked, index)
            if closing is None:
                return None
            index = closing + 1
            continue
        if character in ")}=":
            return None
        index += 1
    return None


def _base_list(masked: str, name_end: int, header_end: int) -> tuple[str, ...] | None:
    """The base types after `:` as written, without generic constraints or a base call's
    arguments; None when one cannot be read."""
    header = masked[name_end:header_end]
    if header.lstrip().startswith("<"):
        start = header.index("<")
        closing = _closing_angle(header, start)
        if closing is None:
            return None
        header = header[closing + 1 :]
    if header.lstrip().startswith("("):
        start = header.index("(")
        closing = matching_close(header, start)
        if closing is None:
            return None
        header = header[closing + 1 :]
    header = _WHERE.split(header, maxsplit=1)[0].strip()
    if not header:
        return ()
    if not header.startswith(":"):
        return None
    parts = _split_top_level(_without_arguments(header[1:]))
    if parts is None:
        return None
    bases = tuple(_SPACE.sub("", part) for part in parts)
    if any(not _BASE_TYPE.fullmatch(base) or len(base) > MAX_IDENTIFIER_CHARS for base in bases):
        return None
    return bases


def _without_arguments(text: str) -> str:
    """`Base(x, y), IX` as `Base, IX`: a record's base call keeps only the base's name."""
    result: list[str] = []
    depth = 0
    for character in text:
        if character == "(":
            depth += 1
        elif character == ")":
            depth -= 1
        elif depth == 0:
            result.append(character)
    return "".join(result)


def _after_space(masked: str, position: int) -> int:
    space = _LEADING_SPACE.match(masked, position)
    return position if space is None else space.end()


def _modifiers_before(masked: str, position: int) -> tuple[str, ...]:
    """The modifiers written directly before a type keyword (`public static`)."""
    words = masked[max(0, position - _MODIFIERS_WINDOW) : position].split()
    found: list[str] = []
    while words and _MODIFIER.match(words[-1]):
        found.insert(0, words.pop())
    return tuple(found)


def _creation_signals(
    masked: str,
    file_path: str,
    bodies: list[TypeBody],
    signals: list[ArchitectureSignal],
    issues: list[EvidenceCoverageIssue],
) -> None:
    """An `object_creation` per `new T(…)` and `new T { … }`. A target-typed `new()`, an
    anonymous `new { … }` and an array `new T[…]` name no class, so they give none."""
    for match in _CREATION.finditer(masked):
        end = match.end()
        following = _after_space(masked, end)
        if masked.startswith("<", following):
            closing = _closing_angle(masked, following)
            if closing is None:
                continue
            end = closing + 1
            following = _after_space(masked, end)
        if not masked.startswith(("(", "{"), following):
            continue
        type_text = _SPACE.sub("", masked[match.start("type") : end])
        line = masked.count("\n", 0, match.start("type")) + 1
        if len(type_text) > MAX_IDENTIFIER_CHARS:
            issues.append(
                EvidenceCoverageIssue(
                    kind=EvidenceIssueKind.AMBIGUOUS_SYNTAX, file_path=file_path, line=line
                )
            )
            continue
        enclosing = [body for body in bodies if body[0] < match.start() < body[1]]
        signals.append(
            ArchitectureSignal(
                kind=ArchitectureSignalKind.OBJECT_CREATION,
                file_path=file_path,
                line=line,
                subject=type_text,
                related_subject=max(enclosing).name if enclosing else None,
            )
        )


def _db_set_signals(
    masked: str,
    file_path: str,
    bodies: list[TypeBody],
    signals: list[ArchitectureSignal],
) -> None:
    """A `db_set_property` per `DbSet<T>` property of a type (`public virtual
    DbSet<Models.User> Users { get; set; }` gives `User`); one outside a type gives none."""
    for match in _DB_SET.finditer(masked):
        entity = _SPACE.sub("", match["entity"]).split(".")[-1]
        enclosing = [body for body in bodies if body[0] < match.start() < body[1]]
        name = match["name"]
        if not enclosing or max(len(entity), len(name)) > MAX_IDENTIFIER_CHARS:
            continue
        signals.append(
            ArchitectureSignal(
                kind=ArchitectureSignalKind.DB_SET_PROPERTY,
                file_path=file_path,
                line=masked.count("\n", 0, match.start("entity")) + 1,
                subject=entity,
                related_subject=max(enclosing).name,
                name=name,
            )
        )


def _constructor_signals(
    type_name: str,
    parameters: str,
    file_path: str,
    line: int,
    declarations: dict[str, int],
    signals: list[ArchitectureSignal],
    issues: list[EvidenceCoverageIssue],
) -> None:
    """A classic or primary constructor, each of its parameters, and each parameter typed
    by a declared interface."""
    signals.append(
        ArchitectureSignal(
            kind=ArchitectureSignalKind.CONSTRUCTOR_CANDIDATE,
            file_path=file_path,
            line=line,
            subject=type_name,
        )
    )
    parameter_types = _parameter_types(parameters)
    if parameter_types is None:
        issues.append(
            EvidenceCoverageIssue(
                kind=EvidenceIssueKind.AMBIGUOUS_SYNTAX, file_path=file_path, line=line
            )
        )
        return
    for parameter_type in parameter_types:
        signals.append(
            ArchitectureSignal(
                kind=ArchitectureSignalKind.CONSTRUCTOR_PARAMETER,
                file_path=file_path,
                line=line,
                subject=parameter_type,
                related_subject=type_name,
            )
        )
        count = declarations.get(parameter_type.rstrip("?").split("<", 1)[0].split(".")[-1], 0)
        if not count:
            continue
        signals.append(
            ArchitectureSignal(
                kind=ArchitectureSignalKind.CONSTRUCTOR_INTERFACE_PARAMETER,
                file_path=file_path,
                line=line,
                subject=parameter_type,
                related_subject=type_name,
            )
        )
        if count > 1:
            issues.append(
                EvidenceCoverageIssue(
                    kind=EvidenceIssueKind.DUPLICATE_TYPE_NAME, file_path=file_path, line=line
                )
            )


def _registered_types(masked: str, position: int) -> tuple[str | None, str | None]:
    """The service and implementation types of `<IX, X>()` or `(typeof(IX), typeof(X))`.

    Any other call shape (a factory, an instance, a key) names no type here: (None, None).
    """
    if masked.startswith("<", position):
        closing = _closing_angle(masked, position)
        if closing is None or not _EMPTY_CALL.match(masked, closing + 1):
            return None, None
        arguments = _split_top_level(masked[position + 1 : closing])
        if arguments is None or len(arguments) > 2:
            return None, None
        if any(not _TYPE_ARGUMENT.fullmatch(argument) for argument in arguments):
            return None, None
        types = arguments
    else:
        typed = _TYPEOF_CALL.match(masked, position)
        if typed is None:
            return None, None
        types = [name for name in (typed["service"], typed["implementation"]) if name]
    if any(len(name) > MAX_IDENTIFIER_CHARS for name in types):
        return None, None
    return types[0], types[1] if len(types) == 2 else None


def _closing_angle(source: str, opening: int) -> int | None:
    depth = 0
    for index in range(opening, len(source)):
        character = source[index]
        if character == "<":
            depth += 1
        elif character == ">":
            depth -= 1
            if depth == 0:
                return index
        elif character in "(){};":
            return None
    return None


def _split_top_level(text: str) -> list[str] | None:
    """Split on commas outside `<>`; None when the angle brackets are unbalanced."""
    parts: list[str] = []
    start = depth = 0
    for index, character in enumerate(text):
        if character == "<":
            depth += 1
        elif character == ">":
            depth -= 1
            if depth < 0:
                return None
        elif character == "," and depth == 0:
            parts.append(text[start:index].strip())
            start = index + 1
    if depth:
        return None
    parts.append(text[start:].strip())
    return parts


def _parameter_types(parameters: str) -> tuple[str, ...] | None:
    if not parameters.strip():
        return ()
    parts = _split_top_level(parameters)
    if parts is None:
        return None
    result: list[str] = []
    for part in parts:
        match = _PARAMETER.fullmatch(_ATTRIBUTE_SECTIONS.sub("", part, count=1))
        if match is None:
            return None
        if len(match["type"]) > MAX_IDENTIFIER_CHARS:
            return None
        result.append(match["type"])
    return tuple(result)


def _balanced_csharp(source: str) -> bool:
    pairs = {")": "(", "]": "[", "}": "{"}
    stack: list[str] = []
    for character in source:
        if character in "([{":
            stack.append(character)
        elif character in pairs and (not stack or stack.pop() != pairs[character]):
            return False
    return not stack
