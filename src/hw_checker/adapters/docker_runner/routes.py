"""Reads the controller route table from student C# code, as text, for resolving probe paths.

Every observed project uses attribute routing: `[Route("api/[controller]")]` on the class
and `[HttpGet]`, `[HttpGet("{id}")]`, `[HttpPost("login")]` on the actions. Those forms are
read; anything else that declares routes (minimal APIs, conventional routing, a template
that is not a string literal) is recorded as unsupported, never guessed. Comments and
string contents are blanked first, so nothing inside them is taken for an attribute.
Student code is never compiled or executed.
"""

import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path

from hw_checker.adapters.docker_runner.csharp_source import (
    MaskedSource,
    mask_source,
    source_files,
)
from hw_checker.domain import (
    DeclaredRoute,
    HttpMethod,
    HttpProbe,
    RouteResolution,
    RouteTable,
    UnsupportedRouting,
)
from hw_checker.domain.execution import MAX_ROUTE_TEXT_CHARS

HTTP_ATTRIBUTES = {
    "HttpGet": HttpMethod.GET,
    "HttpPost": HttpMethod.POST,
    "HttpPut": HttpMethod.PUT,
    "HttpDelete": HttpMethod.DELETE,
    "HttpPatch": HttpMethod.PATCH,
}
ROUTE_ATTRIBUTE = "Route"
UNSUPPORTED_ATTRIBUTES = frozenset({"AcceptVerbs", "HttpHead", "HttpOptions"})

_CLASS = re.compile(r"\b(?:class|record)\s+(?P<name>[A-Za-z_]\w*)")
_METHOD_NAME = re.compile(r"(?P<name>[A-Za-z_]\w*)\s*(?:<[^<>;{}]*>)?\s*\(")
_ATTRIBUTE_ITEM = re.compile(
    r"^\s*(?:\w+\s*:\s*)?(?P<name>[A-Za-z_][\w.]*)\s*(?:\((?P<args>.*)\))?\s*$", re.DOTALL
)
_NAMED_ARGUMENT = re.compile(r"^\s*[A-Za-z_]\w*\s*[=:]")
_LITERAL_ONLY = re.compile(r'^\s*[@$]*"+\s*"+\s*$')
_MINIMAL_API = re.compile(
    r"\.\s*(?P<name>Map(?:Get|Post|Put|Delete|Patch|Methods|Group|Fallback))\s*(?:<[^<>]*>)?\s*\("
)
_CONVENTIONAL = re.compile(
    r"\b(?P<name>MapControllerRoute|MapDefaultControllerRoute|MapAreaControllerRoute"
    r"|UseMvcWithDefaultRoute|UseMvc)\s*\("
)
_TOKEN = re.compile(r"\[(?P<name>[^\[\]]*)\]")
_PARAMETER = re.compile(r"^\{(?P<inner>[^{}]+)\}$")


@dataclass
class _Attribute:
    name: str
    template: str | None
    template_problem: str | None
    position: int


@dataclass
class _Target:
    """What a run of attribute groups is attached to: a class or a method."""

    position: int
    attributes: list[_Attribute] = field(default_factory=list)
    class_name: str | None = None
    method_name: str | None = None


@dataclass(frozen=True)
class _Class:
    name: str
    start: int
    end: int


def extract_route_table(working_copy: Path, project_folder: str) -> RouteTable:
    """The routes declared in the Web project's `.cs` files (`bin/` and `obj/` skipped)."""
    routes: list[DeclaredRoute] = []
    unsupported: list[UnsupportedRouting] = []
    for source in source_files(working_copy / project_folder):
        try:
            text = source.read_bytes().decode("utf-8")
        except UnicodeDecodeError:
            continue
        relative = source.relative_to(working_copy).as_posix()
        found, problems = routes_in_source(text, relative)
        routes.extend(found)
        unsupported.extend(problems)
    return RouteTable(routes=tuple(routes), unsupported=tuple(unsupported))


def routes_in_source(
    text: str, file_path: str
) -> tuple[list[DeclaredRoute], list[UnsupportedRouting]]:
    """The routes declared in one source file, and the routing forms it could not read."""
    masked = mask_source(text)
    unsupported = [
        UnsupportedRouting(file_path=file_path, line=masked.line_of(match.start()), form=form)
        for match, form in _unsupported_calls(masked.text)
    ]
    classes = _classes(masked.text)
    targets = _targets(masked)
    class_attributes = {
        target.class_name: target.attributes for target in targets if target.class_name
    }
    routes: list[DeclaredRoute] = []
    for target in targets:
        if target.class_name is not None:
            unsupported.extend(_attribute_problems(target, masked, file_path))
            continue
        problems = _attribute_problems(target, masked, file_path)
        unsupported.extend(problems)
        if problems or not _declares_routes(target):
            continue
        owner = _innermost_class(classes, target.position)
        line = masked.line_of(target.position)
        if owner is None or target.method_name is None:
            unsupported.append(
                UnsupportedRouting(
                    file_path=file_path,
                    line=line,
                    form="route attribute outside a controller class",
                )
            )
            continue
        built, problem = _action_routes(
            owner.name, target.method_name, class_attributes.get(owner.name, []), target
        )
        if problem is not None:
            unsupported.append(UnsupportedRouting(file_path=file_path, line=line, form=problem))
            continue
        routes.extend(
            DeclaredRoute(
                method=method,
                template=template,
                controller=owner.name,
                action=target.method_name,
                file_path=file_path,
                line=line,
            )
            for method, template in built
        )
    return routes, unsupported


@dataclass(frozen=True)
class ProbeRoute:
    """Where a probe goes: the path (None when it is not sent), how it was chosen, and the
    controller and declared route it matched (None when no route was used)."""

    path: str | None
    resolution: RouteResolution
    controller: str | None
    route: DeclaredRoute | None = None


@dataclass(frozen=True)
class ControllerChoice:
    """The controller probes are steered to, and every controller that could serve a probe
    (sorted by name). `primary` is None when no controller can serve any probe."""

    primary: str | None
    serving: tuple[str, ...]


def choose_primary_controller(
    probes: Sequence[HttpProbe], table: RouteTable | None
) -> ControllerChoice:
    """The controller that can serve the most probes, so one student's probes share state.

    A student may have two controllers (`UserController` and `UsersController`, each with
    its own data), and probes resolved one by one would split between them: a user created
    in one is not found by the other. A tie goes to the controller that the earliest probe
    resolves to on its own, then to the first name in order, so the choice never depends on
    the order of the route table.
    """
    if table is None or not table.usable:
        return ControllerChoice(None, ())
    counts: dict[str, int] = {}
    for probe in probes:
        serving = {
            controller
            for candidate in probe.candidate_paths
            for controller in _matching_controllers(table, probe, candidate)
        }
        for controller in serving:
            counts[controller] = counts.get(controller, 0) + 1
    if not counts:
        return ControllerChoice(None, ())
    best = max(counts.values())
    tied = sorted(controller for controller, count in counts.items() if count == best)
    primary = tied[0]
    if len(tied) > 1:
        for probe in probes:
            own = resolve_probe_path(probe, table).controller
            if own in tied:
                primary = own
                break
    return ControllerChoice(primary, tuple(sorted(counts)))


def resolve_probe_path(
    probe: HttpProbe, table: RouteTable | None, primary: str | None = None
) -> ProbeRoute:
    """The path to send `probe` to, how it was chosen, and the controller it reaches.

    The first candidate (`path`, then `path_alternatives`) that matches a route of the
    `primary` controller for the probe's method wins; failing that, the first candidate
    that matches a route of any controller (the first controller name in order when several
    match). The candidate is sent as written, with its own values. With no match the probe
    is not sent. When the table is not usable (empty, or with routing forms it could not
    read), the probe is sent to its first path, as before route resolution.
    """
    if table is None or not table.usable:
        return ProbeRoute(probe.path, RouteResolution.UNAVAILABLE, None)
    if primary is not None:
        for candidate in probe.candidate_paths:
            on_primary = [
                route
                for route in _matching_routes(table, probe, candidate)
                if route.controller == primary
            ]
            if on_primary:
                return ProbeRoute(candidate, RouteResolution.RESOLVED, primary, on_primary[0])
    for candidate in probe.candidate_paths:
        routes = _matching_routes(table, probe, candidate)
        if routes:
            return ProbeRoute(candidate, RouteResolution.RESOLVED, routes[0].controller, routes[0])
    return ProbeRoute(None, RouteResolution.ROUTE_NOT_FOUND, None)


def _matching_routes(table: RouteTable, probe: HttpProbe, candidate: str) -> list[DeclaredRoute]:
    """The routes for the probe's method matching `candidate`, in a fixed order: by
    controller name, then routes for the exact method before method-less ones, then place."""
    routes = [
        route
        for route in table.routes
        if route.method in (None, probe.method) and template_matches(route.template, candidate)
    ]
    return sorted(
        routes, key=lambda r: (r.controller, r.method is None, r.file_path, r.line, r.template)
    )


def _matching_controllers(table: RouteTable, probe: HttpProbe, candidate: str) -> list[str]:
    """The controllers (sorted by name) with a route for the probe's method matching `candidate`."""
    return sorted({route.controller for route in _matching_routes(table, probe, candidate)})


def template_matches(template: str, path: str) -> bool:
    """True when the request `path` matches the route `template`.

    Literal segments are compared ignoring case. A `{param}` segment (with a constraint such
    as `{id:int}` or not) matches any one segment; `{id?}` and `{id=1}` may also be absent
    at the end, and `{*rest}` matches whatever remains. A segment mixing text and
    parameters (such as `{email} {password}`) is compared as text. Leading and trailing
    slashes, and the path's query string, are ignored. A `{name}` segment in the request
    path (a value captured by an earlier probe, not filled in yet) matches any one segment.
    """
    path_segments = _segments(path.split("?", 1)[0].split("#", 1)[0])
    template_segments = _segments(template)
    index = 0
    for segment in template_segments:
        parameter = _PARAMETER.match(segment)
        if parameter is not None:
            inner = parameter["inner"]
            if inner.startswith("*"):
                return True
            if index < len(path_segments):
                index += 1
                continue
            if inner.endswith("?") or "=" in inner:
                continue
            return False
        if index >= len(path_segments):
            return False
        if not _PARAMETER.match(path_segments[index]) and (
            segment.casefold() != path_segments[index].casefold()
        ):
            return False
        index += 1
    return index == len(path_segments)


def _segments(value: str) -> list[str]:
    return [segment for segment in value.strip("/").split("/") if segment]


def controller_token(class_name: str) -> str:
    """The `[controller]` value: the class name without a `Controller` suffix."""
    if class_name.endswith("Controller") and class_name != "Controller":
        return class_name.removesuffix("Controller")
    return class_name


def action_token(method_name: str) -> str:
    """The `[action]` value: ASP.NET Core drops an `Async` suffix from action names."""
    if method_name.endswith("Async") and method_name != "Async":
        return method_name.removesuffix("Async")
    return method_name


def combine_templates(class_template: str | None, action_template: str | None) -> str:
    """Join a class and an action template; an action template starting with `/` or `~/`
    replaces the class template."""
    if action_template is not None and action_template.startswith(("/", "~/")):
        return action_template.removeprefix("~").strip("/")
    parts = [part.strip("/") for part in (class_template, action_template) if part]
    return "/".join(part for part in parts if part)


def _unsupported_calls(text: str) -> list[tuple[re.Match[str], str]]:
    found = [(match, f"minimal API ({match['name']})") for match in _MINIMAL_API.finditer(text)]
    found += [
        (match, f"conventional routing ({match['name']})") for match in _CONVENTIONAL.finditer(text)
    ]
    return sorted(found, key=lambda item: item[0].start())


def _classes(text: str) -> list[_Class]:
    classes = []
    for match in _CLASS.finditer(text):
        opening = text.find("{", match.end())
        semicolon = text.find(";", match.end())
        if opening < 0 or (0 <= semicolon < opening):
            continue  # a declaration without a body, like `record User(int Id);`
        classes.append(_Class(match["name"], match.start(), _matching_brace(text, opening)))
    return classes


def _matching_brace(text: str, opening: int) -> int:
    depth = 0
    for index in range(opening, len(text)):
        if text[index] == "{":
            depth += 1
        elif text[index] == "}":
            depth -= 1
            if depth == 0:
                return index
    return len(text)


def _innermost_class(classes: list[_Class], position: int) -> _Class | None:
    containing = [item for item in classes if item.start < position < item.end]
    return max(containing, key=lambda item: item.start, default=None)


def _targets(masked: MaskedSource) -> list[_Target]:
    """Attribute groups (`[...]` at the start of a declaration), grouped by what follows."""
    text = masked.text
    targets: list[_Target] = []
    index = 0
    while (start := text.find("[", index)) >= 0:
        if not _starts_declaration(text, start):
            index = start + 1
            continue
        target = _Target(position=start)
        position = start
        while position < len(text) and text[position] == "[":
            end = _matching_bracket(text, position)
            if end < 0:
                break
            attributes = _parse_group(masked, position + 1, end)
            if attributes is None:
                break
            target.attributes.extend(attributes)
            position = _skip_space(text, end + 1)
        if not target.attributes:
            index = start + 1
            continue
        _describe_target(text, position, target)
        targets.append(target)
        index = max(position, start + 1)
    return targets


def _starts_declaration(text: str, bracket: int) -> bool:
    before = text[:bracket].rstrip()
    return not before or before[-1] in ";{}]"


def _matching_bracket(text: str, opening: int) -> int:
    depth = 0
    for index in range(opening, len(text)):
        if text[index] == "[":
            depth += 1
        elif text[index] == "]":
            depth -= 1
            if depth == 0:
                return index
        elif text[index] in "{};":
            return -1
    return -1


def _skip_space(text: str, index: int) -> int:
    while index < len(text) and text[index].isspace():
        index += 1
    return index


def _split_top_level(text: str, start: int, end: int) -> list[tuple[int, int]]:
    """Comma-separated parts of `text[start:end]`, ignoring commas inside parentheses."""
    parts: list[tuple[int, int]] = []
    depth = 0
    part_start = start
    for index in range(start, end):
        character = text[index]
        if character in "([":
            depth += 1
        elif character in ")]":
            depth -= 1
        elif character == "," and depth == 0:
            parts.append((part_start, index))
            part_start = index + 1
    parts.append((part_start, end))
    return parts


def _parse_group(masked: MaskedSource, start: int, end: int) -> list[_Attribute] | None:
    attributes = []
    for item_start, item_end in _split_top_level(masked.text, start, end):
        match = _ATTRIBUTE_ITEM.match(masked.text[item_start:item_end])
        if match is None:
            return None  # not an attribute list, for example a collection expression
        name = match["name"].rsplit(".", 1)[-1].removesuffix("Attribute")
        template: str | None = None
        problem: str | None = None
        if match["args"] is not None:
            args_start = item_start + match.start("args")
            template, problem = _template_argument(
                masked, args_start, args_start + len(match["args"])
            )
        attributes.append(_Attribute(name, template, problem, item_start))
    return attributes


def _template_argument(masked: MaskedSource, start: int, end: int) -> tuple[str | None, str | None]:
    """The positional template of an attribute, or why it cannot be read."""
    for part_start, part_end in _split_top_level(masked.text, start, end):
        part = masked.text[part_start:part_end]
        if not part.strip() or _NAMED_ARGUMENT.match(part):
            continue  # Name = "...", Order = 1
        literals = masked.literal_in(part_start, part_end)
        if len(literals) == 1 and _LITERAL_ONLY.match(part) and not literals[0].has_holes:
            value = literals[0].value
            if len(value) > MAX_ROUTE_TEXT_CHARS:
                return None, "route template that is too long"
            return value, None
        return None, "route template that is not a string literal"
    return None, None


def _describe_target(text: str, position: int, target: _Target) -> None:
    ends = [index for index in (text.find(c, position) for c in "{;=") if index >= 0]
    segment = text[position : min(ends) if ends else len(text)]
    class_match = _CLASS.search(segment)
    if class_match is not None:
        target.class_name = class_match["name"]
        return
    method_match = _METHOD_NAME.search(segment)
    if method_match is not None:
        target.method_name = method_match["name"]


def _declares_routes(target: _Target) -> bool:
    return any(a.name in HTTP_ATTRIBUTES or a.name == ROUTE_ATTRIBUTE for a in target.attributes)


def _attribute_problems(
    target: _Target, masked: MaskedSource, file_path: str
) -> list[UnsupportedRouting]:
    problems = []
    for attribute in target.attributes:
        relevant = attribute.name in HTTP_ATTRIBUTES or attribute.name == ROUTE_ATTRIBUTE
        if attribute.name in UNSUPPORTED_ATTRIBUTES:
            form = f"[{attribute.name}] attribute"
        elif relevant and attribute.template_problem is not None:
            form = f"[{attribute.name}] with a {attribute.template_problem}"
        else:
            continue
        line = masked.line_of(attribute.position)
        problems.append(UnsupportedRouting(file_path=file_path, line=line, form=form))
    return problems


def _action_routes(
    class_name: str,
    method_name: str,
    class_attributes: list[_Attribute],
    target: _Target,
) -> tuple[list[tuple[HttpMethod | None, str]], str | None]:
    """Every (method, template) the action answers, or why they cannot be read."""
    class_templates: list[str | None] = [
        a.template for a in class_attributes if a.name == ROUTE_ATTRIBUTE
    ] or [None]
    action_routes = [a.template for a in target.attributes if a.name == ROUTE_ATTRIBUTE]
    verbs = [
        (HTTP_ATTRIBUTES[a.name], a.template)
        for a in target.attributes
        if a.name in HTTP_ATTRIBUTES
    ]
    pairs: list[tuple[HttpMethod | None, str | None]] = []
    for verb, template in verbs:
        templates = [template] if template is not None else (action_routes or [None])
        pairs.extend((verb, item) for item in templates)
    if not verbs:
        pairs.extend((None, item) for item in action_routes)
    built: list[tuple[HttpMethod | None, str]] = []
    for method, action_template in pairs:
        for class_template in class_templates:
            combined = combine_templates(class_template, action_template)
            replaced, problem = _replace_tokens(combined, class_name, method_name)
            if problem is not None:
                return [], problem
            if (method, replaced) not in built:
                built.append((method, replaced))
    return built, None


def _replace_tokens(template: str, class_name: str, method_name: str) -> tuple[str, str | None]:
    problem: str | None = None

    def replace(match: re.Match[str]) -> str:
        nonlocal problem
        token = match["name"].strip().lower()
        if token == "controller":
            return controller_token(class_name)
        if token == "action":
            return action_token(method_name)
        problem = f"route token [{match['name']}]"
        return match[0]

    return _TOKEN.sub(replace, template), problem
