"""Lexical C# signals for dependency injection: interfaces, constructors and registrations.

Comments and strings are masked first. Interfaces are collected from every file before any
constructor is matched, so a parameter matches an interface declared in another project.
"""

import re
from typing import Final

from hw_checker.adapters.docker_runner.code_facts import CollectedFacts, facts_in_source
from hw_checker.adapters.docker_runner.csharp_source import mask_source
from hw_checker.domain import (
    ArchitectureSignal,
    ArchitectureSignalKind,
    EvidenceCoverageIssue,
    EvidenceIssueKind,
    ServiceLifetime,
    TypeKind,
)

MAX_IDENTIFIER_CHARS: Final = 256
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
        closing = _matching_close(masked, opening)
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
        closing = _matching_close(masked, opening)
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
        for raw_header, raw_offset in _top_level_member_headers(body):
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
    return signals, issues


def _constructor_signals(
    type_name: str,
    parameters: str,
    file_path: str,
    line: int,
    declarations: dict[str, int],
    signals: list[ArchitectureSignal],
    issues: list[EvidenceCoverageIssue],
) -> None:
    """A classic or primary constructor, and each parameter typed by a declared interface."""
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


def _top_level_member_headers(body: str) -> tuple[tuple[str, int], ...]:
    headers: list[tuple[str, int]] = []
    depth = 0
    start = 0
    for index, character in enumerate(body):
        if character == "{" and depth == 0:
            headers.append((body[start:index], start))
            depth = 1
        elif character == "{":
            depth += 1
        elif character == "}":
            depth -= 1
            if depth == 0:
                start = index + 1
        elif character == ";" and depth == 0:
            headers.append((body[start:index], start))
            start = index + 1
    return tuple(headers)


def _matching_close(source: str, opening: int) -> int | None:
    """The index closing the `{` or `(` at `opening`."""
    open_character = source[opening]
    close_character = "}" if open_character == "{" else ")"
    depth = 0
    for index in range(opening, len(source)):
        if source[index] == open_character:
            depth += 1
        elif source[index] == close_character:
            depth -= 1
            if depth == 0:
                return index
    return None


def _balanced_csharp(source: str) -> bool:
    pairs = {")": "(", "]": "[", "}": "{"}
    stack: list[str] = []
    for character in source:
        if character in "([{":
            stack.append(character)
        elif character in pairs and (not stack or stack.pop() != pairs[character]):
            return False
    return not stack
