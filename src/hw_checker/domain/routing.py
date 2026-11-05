"""Route templates: whether a request path matches a route a controller declares."""

import re

from hw_checker.domain.assignment import HttpMethod
from hw_checker.domain.execution import RouteTable

_PARAMETER = re.compile(r"^\{(?P<inner>[^{}]+)\}$")


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


def declares_route(table: RouteTable, method: HttpMethod, path: str) -> bool:
    """True when `table` has a route for `method` (or for every method) matching `path`."""
    return any(
        route.method in (None, method) and template_matches(route.template, path)
        for route in table.routes
    )
