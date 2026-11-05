"""Client-file checks: the pages and scripts in a client folder (such as `wwwroot`).

They read the files only; nothing is run. A finding means the page really breaks: when a
check cannot tell (a value built at run time, a page it cannot resolve), it says nothing.
Each finding lists up to `MAX_EXAMPLES` examples and the total. JavaScript is read with
regexes over code whose comments and strings are masked (see `source_text`), not parsed;
the limits of each check are in its docstring.
"""

import posixpath
import re
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import PurePosixPath

from pydantic import BaseModel, ConfigDict

from hw_checker.checks.deterministic.build import evidence_at
from hw_checker.checks.deterministic.findings import deterministic_finding
from hw_checker.checks.deterministic.messages import (
    CLIENT_ELEMENT_IDS,
    CLIENT_FETCH_ROUTES,
    CLIENT_FILE_REFS,
    CLIENT_REF_CASE,
    CLIENT_REF_MISSING,
    LOCATED_EXAMPLE,
    Localized,
    MessageTemplate,
)
from hw_checker.checks.deterministic.repository import MAX_EXAMPLES, code, joined_examples
from hw_checker.checks.deterministic.structure import RepoFolder
from hw_checker.checks.deterministic.web_sources import (
    Page,
    Script,
    Site,
    client_sites,
)
from hw_checker.checks.registry import check
from hw_checker.domain import CheckInput, Finding, HttpMethod, NotEvaluatedReason
from hw_checker.domain.routing import declares_route

PLACEHOLDER = "{…}"
_LOOKUP = re.compile(r"\b(?P<call>getElementById|querySelectorAll|querySelector)\s*\(\s*")
_ID_SELECTOR = re.compile(r"#(?P<id>[A-Za-z_][\w-]*)")
_PLAIN_ID = re.compile(r"[^\s#.\[\]>+~:,()'\"`]+")
_HTML_WRITE = re.compile(
    r"\.(?:innerHTML|outerHTML)\s*\+?=\s*(?![\s\"'`])"
    r"|insertAdjacentHTML\s*\([^,()]*,\s*(?![\s\"'`])"
    r"|document\s*\.\s*write(?:ln)?\s*\(\s*(?![\s\"'`])"
    r"|\.id\s*=\s*(?![\s\"'`=])"
)
_SET_ATTRIBUTE = re.compile(r"\bsetAttribute\s*\(\s*")
_ID_BEFORE_HOLE = re.compile(r"\bid\s*=\s*[\"']?$", re.IGNORECASE)
_FETCH = re.compile(r"(?<![\w$.])fetch\s*\(")
_METHOD_KEY = re.compile(r"(?<![\w$])method\s*:\s*")
_METHOD_SHORTHAND = re.compile(r"(?<![\w$])method\s*[,}]")
_LOCALHOST = re.compile(r"^https?://localhost(?::\d+)?(?=/|$)", re.IGNORECASE)
_URL_START = re.compile(r"^(?:[A-Za-z][A-Za-z0-9+.-]*:|//)")


class ClientParams(BaseModel):
    """`root`: the client folder, such as `wwwroot`, found at any depth (case ignored)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    root: RepoFolder


@dataclass(frozen=True)
class Problem:
    """One example for a finding: its text, where it is, and a stable fact for the id."""

    example: Localized
    file: str
    line: int
    fact: str


def _report(
    check_input: CheckInput, template: MessageTemplate, problems: list[Problem]
) -> tuple[Finding, ...]:
    if not problems:
        return ()
    examples = joined_examples(problem.example for problem in problems[:MAX_EXAMPLES])
    message = template.render(examples=examples, count=len(problems))
    first = problems[0]
    evidence = evidence_at(check_input.repo_dir, first.file, first.line)
    facts = [problem.fact for problem in problems]
    return (deterministic_finding(check_input, message, facts, evidence),)


# ---------------------------------------------------------------- file references


@check(
    "client_file_refs",
    ClientParams,
    "Every local <script src>, <link rel=stylesheet href> and <img src> in a page under "
    "root names a file that exists, with the exact letter case.",
)
def client_file_refs(check_input: CheckInput, params: ClientParams) -> tuple[Finding, ...]:
    """One finding listing references to missing files, and those that differ only in
    letter case (a Linux server tells them apart). External URLs, templates and
    references without a file extension are skipped, and so are pages with `<base>`."""
    problems: list[Problem] = []
    for site in client_sites(check_input, params.root):
        exact = set(site.files)
        by_case = {path.casefold(): path for path in site.files}
        for page in site.pages:
            if page.has_base:
                continue
            for reference in page.references:
                target = site.resolve(page, reference.value)
                if target is None or target in exact:
                    continue
                fields: dict[str, str | int] = {
                    "ref": code(reference.value),
                    "file": page.path,
                    "line": reference.line,
                }
                actual = by_case.get(target.casefold())
                example = (
                    CLIENT_REF_MISSING.render(**fields)
                    if actual is None
                    else CLIENT_REF_CASE.render(**fields, actual=code(actual))
                )
                fact = f"{page.path}:{reference.line}:{reference.value}"
                problems.append(Problem(example, page.path, reference.line, fact))
    return _report(check_input, CLIENT_FILE_REFS, problems)


# ---------------------------------------------------------------- element ids


@check(
    "client_element_ids",
    ClientParams,
    "Every literal id a script under root looks up (getElementById, querySelector('#id')) "
    "is declared by a page that loads the script.",
)
def client_element_ids(check_input: CheckInput, params: ClientParams) -> tuple[Finding, ...]:
    """One finding listing ids a script looks up that no page loading it declares.

    Only `getElementById("x")`, `querySelector("#x")` and `querySelectorAll("#x")` with
    one literal argument count. Skipped, because the id may exist at run time: a script no
    page loads; ids whose text appears in another string of the page's scripts (as in
    `el.id = "x"` or `'<div id="x">'`); and every lookup of a page whose scripts write HTML
    from a non-literal value, or set an id from a non-literal value.
    """
    problems: list[Problem] = []
    for site in client_sites(check_input, params.root):
        groups = _pages_by_script(site)
        for script, pages in groups:
            scripts = _scripts_of(site, pages)
            if any(_builds_ids(other) for other in scripts):
                continue
            declared = frozenset().union(*(page.ids for page in pages))
            for element_id, offset, literal_start in _lookups(script):
                if element_id in declared or _mentioned(element_id, scripts, script, literal_start):
                    continue
                line = script.line_of(offset)
                example = LOCATED_EXAMPLE.render(item=code(element_id), file=script.path, line=line)
                fact = f"{script.path}:{line}:{element_id}"
                problems.append(Problem(example, script.path, line, fact))
    return _report(check_input, CLIENT_ELEMENT_IDS, _unique(problems))


def _pages_by_script(site: Site) -> list[tuple[Script, tuple[Page, ...]]]:
    """Each script with the pages that load it; pages with `<base>` are left out."""
    pages = [page for page in site.pages if not page.has_base]
    groups: list[tuple[Script, tuple[Page, ...]]] = []
    for site_path, script in sorted(site.scripts.items()):
        loading = tuple(page for page in pages if site_path in site.loaded_scripts(page))
        if loading:
            groups.append((script, loading))
    for page in pages:
        groups.extend((inline, (page,)) for inline in page.inline_scripts)
    return groups


def _scripts_of(site: Site, pages: Iterable[Page]) -> list[Script]:
    scripts: list[Script] = []
    for page in pages:
        scripts.extend(site.scripts[path] for path in site.loaded_scripts(page))
        scripts.extend(page.inline_scripts)
    return scripts


def _lookups(script: Script) -> list[tuple[str, int, int]]:
    """(id, offset, literal start) for each lookup of a literal id in `script`."""
    scan = script.scan
    found: list[tuple[str, int, int]] = []
    for match in _LOOKUP.finditer(scan.masked):
        literal = scan.literal_at(match.end())
        if literal is None or not literal.fixed:
            continue
        if not scan.masked[literal.end :].lstrip().startswith(")"):
            continue
        text = literal.text
        if match["call"] == "getElementById":
            element_id = text if _PLAIN_ID.fullmatch(text) else None
        else:
            selector = _ID_SELECTOR.fullmatch(text)
            element_id = selector["id"] if selector else None
        if element_id is not None:
            found.append((element_id, match.start(), literal.start))
    return found


def _builds_ids(script: Script) -> bool:
    """True when `script` may create ids at run time that cannot be read from its text."""
    scan = script.scan
    if _HTML_WRITE.search(scan.masked):
        return True
    for match in _SET_ATTRIBUTE.finditer(scan.masked):
        literal = scan.literal_at(match.end())
        if literal is not None and literal.text.strip().lower() == "id":
            rest = scan.masked[literal.end :].lstrip()
            if rest.startswith(",") and not rest[1:].lstrip().startswith(('"', "'", "`")):
                return True
    for literal in scan.literals:
        parts = literal.parts
        for index, part in enumerate(parts):
            if part is None or not _ID_BEFORE_HOLE.search(part):
                continue
            if index + 1 < len(parts) or scan.masked[literal.end :].lstrip().startswith("+"):
                return True
    return False


def _mentioned(element_id: str, scripts: list[Script], script: Script, lookup_start: int) -> bool:
    """True when `element_id` appears as a word in a string of `scripts` other than the
    lookup's own literal."""
    word = re.compile(rf"(?<![\w-]){re.escape(element_id)}(?![\w-])")
    for other in scripts:
        for literal in other.scan.literals:
            if other is script and literal.start == lookup_start:
                continue
            if word.search(literal.text):
                return True
    return False


def _unique(problems: list[Problem]) -> list[Problem]:
    """Problems in order, each fact once (a script may be loaded by several pages)."""
    return list({problem.fact: problem for problem in problems}.values())


# ---------------------------------------------------------------- fetch routes


@check(
    "client_fetch_routes",
    ClientParams,
    "Every fetch URL in a script under root matches a route the API declares for its method.",
)
def client_fetch_routes(
    check_input: CheckInput, params: ClientParams
) -> tuple[Finding, ...] | NotEvaluatedReason:
    """One finding listing `fetch` calls whose path matches no declared route.

    The URL is a string, a template literal or a `+` concatenation that starts with fixed
    text; each `${…}` or non-literal part is a route parameter. Skipped: a URL that starts
    with a variable, a host other than localhost, a parameter inside a path segment
    (`user${id}`), a path with a file extension (a static file), a relative URL of a script
    loaded from a subfolder page, and a call whose method is not a literal. Without the
    route table (not read, or with routing it cannot read) it is not evaluated.
    """
    execution = check_input.execution
    if execution is None:
        return NotEvaluatedReason.NO_EXECUTION
    table = execution.routes
    if table is None or not table.usable:
        return NotEvaluatedReason.ENVIRONMENT_ISSUE
    problems: list[Problem] = []
    for site in client_sites(check_input, params.root):
        for script, at_root in _scripts_with_location(site):
            for call in _fetch_calls(script, at_root):
                if declares_route(table, call.method, call.path):
                    continue
                item = f"{call.method.value} {call.path}"
                line = script.line_of(call.offset)
                example = LOCATED_EXAMPLE.render(item=code(item), file=script.path, line=line)
                problems.append(Problem(example, script.path, line, f"{script.path}:{line}:{item}"))
    return _report(check_input, CLIENT_FETCH_ROUTES, problems)


@dataclass(frozen=True)
class FetchCall:
    """A `fetch` whose method and path could be read; the path starts with `/`."""

    method: HttpMethod
    path: str
    offset: int


def _scripts_with_location(site: Site) -> list[tuple[Script, bool]]:
    """Every script, with whether all pages that load it sit in the client folder's root
    (only then can a relative URL be resolved)."""
    located: list[tuple[Script, bool]] = []
    pages = [page for page in site.pages if not page.has_base]
    for site_path, script in sorted(site.scripts.items()):
        loading = [page for page in pages if site_path in site.loaded_scripts(page)]
        located.append((script, bool(loading) and all(_at_root(page) for page in loading)))
    for page in pages:
        located.extend((inline, _at_root(page)) for inline in page.inline_scripts)
    return located


def _at_root(page: Page) -> bool:
    return "/" not in page.site_path


def _fetch_calls(script: Script, at_root: bool) -> list[FetchCall]:
    masked = script.scan.masked
    calls: list[FetchCall] = []
    for match in _FETCH.finditer(masked):
        close = _closing(masked, match.end() - 1)
        if close is None:
            continue
        arguments = _split_arguments(masked, match.end(), close)
        if not arguments:
            continue
        path = _url_path(script, *arguments[0], at_root)
        method = _method(script, *arguments[1]) if len(arguments) > 1 else HttpMethod.GET
        if path is not None and method is not None:
            calls.append(FetchCall(method, path, match.start()))
    return calls


def _closing(masked: str, opening: int) -> int | None:
    """The offset of the bracket closing the one at `opening`."""
    depth = 0
    for index in range(opening, len(masked)):
        if masked[index] in "([{":
            depth += 1
        elif masked[index] in ")]}":
            depth -= 1
            if depth == 0:
                return index
    return None


def _split_arguments(masked: str, start: int, end: int) -> list[tuple[int, int]]:
    """The (start, end) offsets of each top-level argument between `start` and `end`."""
    spans: list[tuple[int, int]] = []
    depth = 0
    piece = start
    for index in range(start, end):
        char = masked[index]
        if char in "([{":
            depth += 1
        elif char in ")]}":
            depth -= 1
        elif char == "," and depth == 0:
            spans.append((piece, index))
            piece = index + 1
    spans.append((piece, end))
    return [(s, e) for s, e in spans if masked[s:e].strip()]


def _url_parts(script: Script, start: int, end: int) -> list[str | None]:
    """The URL expression as fixed text, with None for each hole: a `${…}`, or an operand
    of `+` that is not a literal."""
    scan = script.scan
    parts: list[str | None] = []
    for s, e in _split_on_plus(scan.masked, start, end):
        operand_start = s + len(scan.masked[s:e]) - len(scan.masked[s:e].lstrip())
        literal = scan.literal_at(operand_start)
        if literal is not None and scan.masked[literal.end : e].strip() == "":
            parts.extend(literal.parts)
        else:
            parts.append(None)
    return parts


def _split_on_plus(masked: str, start: int, end: int) -> list[tuple[int, int]]:
    spans: list[tuple[int, int]] = []
    depth = 0
    piece = start
    for index in range(start, end):
        char = masked[index]
        if char in "([{":
            depth += 1
        elif char in ")]}":
            depth -= 1
        elif char == "+" and depth == 0:
            spans.append((piece, index))
            piece = index + 1
    spans.append((piece, end))
    return spans


def _url_path(script: Script, start: int, end: int, at_root: bool) -> str | None:
    """The request path of a fetch URL, with `{…}` for each parameter; None when it cannot
    be told."""
    parts = _url_parts(script, start, end)
    if not parts or parts[0] is None:
        return None
    text = "".join(PLACEHOLDER if part is None else part for part in parts)
    text = re.split(r"[?#]", text, maxsplit=1)[0].strip()
    localhost = _LOCALHOST.match(text)
    if localhost is not None:
        text = text[localhost.end() :] or "/"
    elif _URL_START.match(text):
        return None
    if not text.startswith("/") and not at_root:
        return None
    text = posixpath.normpath(posixpath.join("/", text))
    segments = [segment for segment in text.split("/") if segment]
    if any(PLACEHOLDER in segment and segment != PLACEHOLDER for segment in segments):
        return None
    if segments and "." in PurePosixPath(segments[-1]).name and PLACEHOLDER not in segments[-1]:
        return None
    return "/" + "/".join(segments)


def _method(script: Script, start: int, end: int) -> HttpMethod | None:
    """The method an options object literal names; GET when it names none; None when it
    cannot be told."""
    scan = script.scan
    options = scan.masked[start:end].strip()
    if not options.startswith("{"):
        return None
    region = scan.masked[start:end]
    if _METHOD_SHORTHAND.search(region):
        return None
    key = _METHOD_KEY.search(region)
    if key is None:
        return HttpMethod.GET
    literal = scan.literal_at(start + key.end())
    if literal is None or not literal.fixed:
        return None
    try:
        return HttpMethod(literal.text.strip().upper())
    except ValueError:
        return None
