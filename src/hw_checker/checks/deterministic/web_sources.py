"""Reads a client folder (such as `wwwroot`): its pages, scripts and what they refer to.

Pages are parsed with the standard library's HTML parser; scripts with the lexer in
`source_text`. Nothing is run. The limits:

- Only `<script src>`, `<link rel="stylesheet" href>` and `<img src>` count as references;
  a page with a `<base href>` is skipped (its references cannot be resolved).
- A reference with a scheme (`https:`, `data:`), `//`, a template (`{{`, `${`, `@`) or no
  file extension in its last part (an API call, not a file) is not a file reference.
- Inline `<script>` blocks are scripts of their page; event attributes (`onclick="…"`)
  are not read.
"""

import posixpath
import re
from dataclasses import dataclass, field
from html.parser import HTMLParser
from pathlib import Path, PurePosixPath
from urllib.parse import unquote

from hw_checker.checks.deterministic.repository import checked_files, read_source
from hw_checker.checks.deterministic.source_text import SourceScan, scan_source
from hw_checker.domain import CheckInput

PAGE_SUFFIXES = (".html", ".htm")
SCRIPT_SUFFIX = ".js"
_SCHEME = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]*:")
_TEMPLATE_MARKERS = ("{{", "${", "@")


@dataclass(frozen=True)
class Reference:
    """A file a page refers to: the tag, the value as written and its line."""

    tag: str
    value: str
    line: int


@dataclass(frozen=True)
class Script:
    """A script: an external `.js` file, or an inline block of a page.

    `path` is the repo-relative file it is in; `first_line` is the line of the file where
    the script's text starts, so `line_of` gives file lines.
    """

    path: str
    scan: SourceScan
    first_line: int = 1

    def line_of(self, offset: int) -> int:
        """The file line of `offset` in the script's text."""
        return self.first_line + self.scan.line_of(offset) - 1


@dataclass(frozen=True)
class Page:
    """An HTML page of a client folder."""

    path: str
    """Repo-relative."""
    site_path: str
    """Relative to the client folder."""
    references: tuple[Reference, ...]
    ids: frozenset[str]
    inline_scripts: tuple[Script, ...]
    has_base: bool


@dataclass(frozen=True)
class Site:
    """A client folder: its repo-relative `root`, files relative to it, pages and scripts."""

    root: str
    files: tuple[str, ...]
    pages: tuple[Page, ...]
    scripts: dict[str, Script] = field(default_factory=dict)
    """External scripts by their path relative to `root`."""

    def repo_path(self, site_path: str) -> str:
        """The repo-relative path of a file given relative to the client folder."""
        return f"{self.root}/{site_path}"

    def resolve(self, page: Page, value: str) -> str | None:
        """The path relative to the client folder that `value` on `page` names; None when it
        is not a local file reference."""
        return resolve_reference(PurePosixPath(page.site_path).parent.as_posix(), value)

    def loaded_scripts(self, page: Page) -> tuple[str, ...]:
        """The external scripts (paths relative to the client folder) `page` loads, with
        letter case ignored: a name that differs only in case is `client_file_refs`'s
        finding, and the script's own problems are still checked against the page."""
        by_case = {path.casefold(): path for path in self.scripts}
        loaded = (
            self.resolve(page, reference.value)
            for reference in page.references
            if reference.tag == "script"
        )
        found = (by_case.get(path.casefold()) for path in loaded if path is not None)
        return tuple(path for path in found if path is not None)


def resolve_reference(page_folder: str, value: str) -> str | None:
    """Resolve `value` as a browser would, relative to the client folder (as the server
    root; `..` above it stays at it). None for anything that is not a local file
    reference."""
    text = value.strip()
    if not text or text.startswith(("#", "//")) or _SCHEME.match(text):
        return None
    if any(marker in text for marker in _TEMPLATE_MARKERS):
        return None
    text = unquote(re.split(r"[?#]", text, maxsplit=1)[0])
    if text.startswith("~/"):
        text = text[1:]
    folder = "" if text.startswith("/") else ("" if page_folder == "." else page_folder)
    joined = posixpath.normpath(posixpath.join("/", folder, text.lstrip("/")))
    relative = joined.lstrip("/")
    if not relative or "." not in PurePosixPath(relative).name:
        return None
    return relative


def client_sites(check_input: CheckInput, root: str) -> tuple[Site, ...]:
    """Every folder named `root` (found at any depth, ignoring case) with its pages and
    scripts, from the checked commit's files."""
    by_root: dict[str, list[str]] = {}
    root_parts = [part.casefold() for part in root.split("/")]
    for path in checked_files(check_input):
        parts = path.split("/")
        for index in range(len(parts) - len(root_parts)):
            window = [part.casefold() for part in parts[index : index + len(root_parts)]]
            if window == root_parts:
                site_root = "/".join(parts[: index + len(root_parts)])
                by_root.setdefault(site_root, []).append("/".join(parts[index + len(root_parts) :]))
                break
    return tuple(
        _site(check_input.repo_dir, site_root, tuple(files))
        for site_root, files in sorted(by_root.items())
    )


def _site(repo_dir: Path, root: str, files: tuple[str, ...]) -> Site:
    pages: list[Page] = []
    scripts: dict[str, Script] = {}
    for site_path in files:
        lower = site_path.lower()
        if not lower.endswith((*PAGE_SUFFIXES, SCRIPT_SUFFIX)):
            continue
        repo_path = f"{root}/{site_path}"
        text = read_source(repo_dir, repo_path)
        if text is None:
            continue
        if lower.endswith(SCRIPT_SUFFIX):
            scripts[site_path] = Script(repo_path, scan_source(text))
        else:
            pages.append(parse_page(repo_path, site_path, text))
    return Site(root, files, tuple(pages), scripts)


def parse_page(path: str, site_path: str, text: str) -> Page:
    """Parse one HTML page."""
    parser = _PageParser()
    parser.feed(text)
    parser.close()
    inline = tuple(
        Script(path, scan_source(content), line) for content, line in parser.inline_scripts
    )
    return Page(
        path=path,
        site_path=site_path,
        references=tuple(parser.references),
        ids=frozenset(parser.ids),
        inline_scripts=inline,
        has_base=parser.has_base,
    )


class _PageParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.references: list[Reference] = []
        self.ids: set[str] = set()
        self.inline_scripts: list[tuple[str, int]] = []
        self.has_base = False
        self._in_inline_script = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = {name: value or "" for name, value in attrs}
        line = self.getpos()[0]
        if values.get("id"):
            self.ids.add(values["id"])
        if tag == "base" and "href" in values:
            self.has_base = True
        elif tag == "script":
            if "src" in values:
                self.references.append(Reference("script", values["src"], line))
            else:
                self._in_inline_script = True
        elif tag == "link" and "stylesheet" in values.get("rel", "").lower().split():
            if "href" in values:
                self.references.append(Reference("link", values["href"], line))
        elif tag == "img" and "src" in values:
            self.references.append(Reference("img", values["src"], line))

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)
        self._in_inline_script = False

    def handle_endtag(self, tag: str) -> None:
        if tag == "script":
            self._in_inline_script = False

    def handle_data(self, data: str) -> None:
        if self._in_inline_script and data.strip():
            self.inline_scripts.append((data, self.getpos()[0]))
