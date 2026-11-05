"""Finds the request body model of a probed action in student C# code, as text.

Students define their own `User` class, with their own field names, types and required
fields, so a fixed probe body is often rejected with 400 although the code works. The body
is adapted to the model found here (see `body_adapter`). Only plain classes with public
auto-properties are read; records with a primary constructor, models that inherit from
another model, and `dynamic` or JSON-document parameters are unsupported, and the probe body
is then sent as written. Models are looked up in every project of the solution, since a
student may keep them in a separate project (for example `Entities`); when several projects
declare the name, the one the controller's `using` lines point to is used. Student code is
never compiled or executed.
"""

import re
from dataclasses import dataclass
from pathlib import Path

from hw_checker.adapters.docker_runner.csharp_source import MaskedSource, mask_source, source_files
from hw_checker.adapters.docker_runner.discovery import ProjectFolder
from hw_checker.domain import DeclaredRoute

SIMPLE_TYPES = frozenset(
    {
        "string",
        "int",
        "long",
        "short",
        "byte",
        "uint",
        "ulong",
        "ushort",
        "sbyte",
        "double",
        "float",
        "decimal",
        "bool",
        "char",
        "DateTime",
        "DateTimeOffset",
        "DateOnly",
        "TimeOnly",
        "TimeSpan",
        "Guid",
        "CancellationToken",
    }
)
"""Parameter types bound from the route or query string, never from the body."""
VALUE_TYPES = SIMPLE_TYPES - {"string"}
UNSUPPORTED_BODY_TYPES = frozenset(
    {"dynamic", "object", "JsonElement", "JsonDocument", "JsonNode", "JsonObject", "IFormFile"}
)
TYPE_KEYWORDS = frozenset({"class", "struct", "record", "interface", "enum"})
NON_BODY_ATTRIBUTES = frozenset(
    {"FromRoute", "FromQuery", "FromServices", "FromHeader", "FromForm", "FromKeyedServices"}
)

_CLASS = re.compile(
    r"\b(?P<kind>class|record(?:\s+class|\s+struct)?|struct)\s+(?P<name>[A-Za-z_]\w*)"
    r"\s*(?P<generic><[^<>{};]*>)?\s*(?P<parameters>\()?"
)
_PROPERTY_HEADER = re.compile(
    r"(?P<attributes>(?:\[[^\]]*\]\s*)*)(?P<modifiers>(?:\b(?:public|required|virtual|override|"
    r"new|sealed)\b\s*)+)(?P<type>[A-Za-z_][\w.]*(?:<[^<>;{}]*>)?(?:\[\])?\??)\s+"
    r"(?P<name>[A-Za-z_]\w*)\s*$"
)
_REQUIRED_ATTRIBUTE = re.compile(r"\[\s*(?:[\w.]*\.)?Required(?:Attribute)?\b")
_EMAIL_ATTRIBUTE = re.compile(r"[\[,]\s*(?:[\w.]*\.)?EmailAddress(?:Attribute)?\b")
_PARAMETER = re.compile(
    r"^\s*(?P<attributes>(?:\[[^\]]*\]\s*)*)(?:(?:this|ref|in|out|params)\s+)?"
    r"(?P<type>[A-Za-z_][\w.]*(?:<[^<>;{}]*>)?(?:\[\])?\??)\s+(?P<name>[A-Za-z_]\w*)"
    r"\s*(?:=.*)?$",
    re.DOTALL,
)
_ATTRIBUTE_NAME = re.compile(r"\[\s*(?:[\w.]*\.)?(?P<name>\w+?)(?:Attribute)?\s*[(\]]")
_NAMESPACE = re.compile(r"\bnamespace\s+(?P<name>[A-Za-z_][\w.]*)")
_USING = re.compile(
    r"^\s*(?P<global>global\s+)?using\s+(?!static\b)(?P<name>[A-Za-z_][\w.]*)\s*;", re.MULTILINE
)
_INTERFACE_NAME = re.compile(r"^I[A-Z]")


@dataclass(frozen=True)
class BodyProperty:
    """One public property of a body model, as the student wrote it."""

    name: str
    type_name: str
    """The C# type without a trailing `?`, for example `int` or `string`."""
    nullable: bool
    required: bool
    """`required`, `[Required]`, or (with `<Nullable>enable`) a non-nullable reference type."""
    email: bool = False
    """The property has `[EmailAddress]`, so only an e-mail address passes validation."""


@dataclass(frozen=True)
class BodyModel:
    """The class a probed action binds its request body to."""

    class_name: str
    properties: tuple[BodyProperty, ...]


@dataclass(frozen=True)
class ModelUnavailable:
    """Why no body model could be read for the action; the body is then sent as written."""

    reason: str


@dataclass(frozen=True)
class _ClassDeclaration:
    name: str
    file_path: str
    header: str
    body: str
    primary_constructor: bool
    namespace: str = ""


class ProjectSources:
    """The solution's `.cs` files, read once and masked, for model lookups.

    `nullable` says, per file, whether its project has `<Nullable>enable`; a file missing
    from it uses the Web project's setting. `web_files` are the Web project's files, whose
    `global using` lines apply to every controller.
    """

    def __init__(
        self,
        sources: dict[str, MaskedSource],
        nullable: bool,
        file_nullable: dict[str, bool] | None = None,
        web_files: frozenset[str] | None = None,
    ) -> None:
        self.sources = sources
        self.nullable = nullable
        self._file_nullable = file_nullable or {}
        web = sources.keys() if web_files is None else web_files
        self._global_usings = frozenset(
            match["name"]
            for path in web
            if path in sources
            for match in _USING.finditer(sources[path].text)
            if match["global"]
        )
        self._classes: dict[str, list[_ClassDeclaration]] = {}
        for file_path, masked in sources.items():
            for declaration in _class_declarations(masked.text, file_path):
                self._classes.setdefault(declaration.name, []).append(declaration)

    @classmethod
    def read(
        cls, working_copy: Path, web: ProjectFolder, others: tuple[ProjectFolder, ...] = ()
    ) -> "ProjectSources":
        """Read the `.cs` files of the Web project and of the solution's other projects
        (`bin/` and `obj/` skipped). A file belongs to the innermost project folder that
        holds it."""
        projects = sorted((web, *others), key=lambda project: -len(project.folder))
        sources: dict[str, MaskedSource] = {}
        file_nullable: dict[str, bool] = {}
        web_files: set[str] = set()
        for project in projects:
            for source in source_files(working_copy / project.folder):
                relative = source.relative_to(working_copy).as_posix()
                if relative in sources:
                    continue  # already read with an inner project
                try:
                    text = source.read_bytes().decode("utf-8")
                except UnicodeDecodeError:
                    continue
                sources[relative] = mask_source(text)
                file_nullable[relative] = project.nullable
                if project == web:
                    web_files.add(relative)
        return cls(sources, web.nullable, file_nullable, frozenset(web_files))

    def body_model(self, route: DeclaredRoute) -> BodyModel | ModelUnavailable:
        """The body model of the action that declares `route`."""
        masked = self.sources.get(route.file_path)
        if masked is None:
            return ModelUnavailable(f"source file {route.file_path} was not read")
        parameters = _action_parameters(masked.text, route.line, route.action)
        if parameters is None:
            return ModelUnavailable(f"parameters of {route.action} not found")
        body_type = _body_parameter_type(parameters)
        if isinstance(body_type, ModelUnavailable):
            return body_type
        return self.model(body_type, route.file_path)

    def model(self, type_name: str, used_from: str | None = None) -> BodyModel | ModelUnavailable:
        """The model class named `type_name`, read from the solution's sources.

        When several files declare the name, the declaration in a namespace that the file
        `used_from` sees (its `using` lines, the Web project's `global using` lines, its own
        namespace and the ones around it, or the namespace written in `type_name`) is used;
        if that does not single one out, the model is ambiguous.
        """
        written = type_name.removesuffix("?")
        bare = written.rsplit(".", 1)[-1]
        if bare in UNSUPPORTED_BODY_TYPES:
            return ModelUnavailable(f"body parameter of type {bare}")
        if "<" in bare or bare.endswith("[]"):
            return ModelUnavailable(f"body parameter of type {bare} (a collection or generic)")
        declarations = self._classes.get(bare, [])
        if not declarations:
            return ModelUnavailable(f"class {bare} not found in the solution")
        if len(declarations) > 1:
            declarations = self._visible(declarations, written, used_from)
            if len(declarations) != 1:
                places = ", ".join(sorted({d.file_path for d in self._classes[bare]}))
                return ModelUnavailable(f"several classes named {bare} ({places})")
        (declaration,) = declarations
        if declaration.primary_constructor:
            return ModelUnavailable(f"{bare} is declared with a primary constructor")
        base = _base_class(declaration.header)
        if base is not None:
            return ModelUnavailable(f"{bare} inherits from {base}")
        nullable = self._file_nullable.get(declaration.file_path, self.nullable)
        return BodyModel(bare, tuple(_properties(declaration.body, nullable)))

    def _visible(
        self, declarations: list[_ClassDeclaration], written: str, used_from: str | None
    ) -> list[_ClassDeclaration]:
        """The declarations whose namespace the file `used_from` can see."""
        qualifier = written.rpartition(".")[0]
        if qualifier:
            return [d for d in declarations if _ends_with_namespace(d.namespace, qualifier)]
        masked = None if used_from is None else self.sources.get(used_from)
        if masked is None:
            return declarations
        seen = set(self._global_usings)
        seen.update(match["name"] for match in _USING.finditer(masked.text))
        own = _namespace_at(masked.text, len(masked.text))
        parts = own.split(".") if own else []
        seen.update(".".join(parts[:end]) for end in range(1, len(parts) + 1))
        return [d for d in declarations if d.namespace in seen]


def _class_declarations(text: str, file_path: str) -> list[_ClassDeclaration]:
    declarations = []
    for match in _CLASS.finditer(text):
        opening = text.find("{", match.end())
        semicolon = text.find(";", match.end())
        primary = match["parameters"] is not None
        if opening < 0 or (0 <= semicolon < opening):
            if primary:  # record User(int Id, string Name);
                namespace = _namespace_at(text, match.start())
                declarations.append(
                    _ClassDeclaration(match["name"], file_path, "", "", True, namespace)
                )
            continue
        closing = _matching(text, opening, "{", "}")
        header = text[match.end() : opening]
        declarations.append(
            _ClassDeclaration(
                match["name"],
                file_path,
                header,
                text[opening + 1 : closing],
                primary,
                _namespace_at(text, match.start()),
            )
        )
    return declarations


def _namespace_at(text: str, position: int) -> str:
    """The namespace declared last before `position` ("" when there is none)."""
    found = ""
    for match in _NAMESPACE.finditer(text, 0, position):
        found = match["name"]
    return found


def _ends_with_namespace(namespace: str, qualifier: str) -> bool:
    return namespace == qualifier or namespace.endswith(f".{qualifier}")


def _matching(text: str, opening: int, open_char: str, close_char: str) -> int:
    depth = 0
    for index in range(opening, len(text)):
        if text[index] == open_char:
            depth += 1
        elif text[index] == close_char:
            depth -= 1
            if depth == 0:
                return index
    return len(text)


def _base_class(header: str) -> str | None:
    """The base class in `class X : Base, IThing`, ignoring interfaces and `where` clauses."""
    if ":" not in header:
        return None
    bases = header.split(":", 1)[1].split(" where ", 1)[0]
    for base in (part.strip() for part in bases.split(",")):
        name = base.split("<", 1)[0].rsplit(".", 1)[-1]
        if name and not _INTERFACE_NAME.match(name):
            return name
    return None


def _properties(body: str, nullable_context: bool) -> list[BodyProperty]:
    """Public instance auto-properties at the top level of a class body."""
    found: list[BodyProperty] = []
    depth = 0
    header_start = 0
    index = 0
    while index < len(body):
        character = body[index]
        if character == "{":
            if depth == 0:
                block_end = _matching(body, index, "{", "}")
                header = body[header_start:index]
                if "get" in body[index:block_end]:
                    prop = _property(header, nullable_context)
                    if prop is not None:
                        found.append(prop)
                index = block_end + 1
                header_start = _skip_initializer(body, index)
                index = header_start
                continue
            depth += 1
        elif character == ";" and depth == 0:
            header_start = index + 1
        index += 1
    return found


def _skip_initializer(body: str, index: int) -> int:
    """After `{ get; set; }`, skip an initializer such as ` = "";` to the next member."""
    rest = body[index:]
    stripped = rest.lstrip()
    if stripped.startswith("="):
        semicolon = body.find(";", index)
        return len(body) if semicolon < 0 else semicolon + 1
    return index


def _property(header: str, nullable_context: bool) -> BodyProperty | None:
    match = _PROPERTY_HEADER.search(header.strip())
    if match is None or "static" in header.split() or match["type"] in TYPE_KEYWORDS:
        return None  # not a property, a static one, or a nested type declaration
    written = match["type"]
    nullable = written.endswith("?")
    type_name = written.removesuffix("?")
    is_reference = type_name not in VALUE_TYPES
    required = (
        "required" in match["modifiers"].split()
        or bool(_REQUIRED_ATTRIBUTE.search(match["attributes"]))
        or (nullable_context and is_reference and not nullable)
    )
    email = bool(_EMAIL_ATTRIBUTE.search(match["attributes"]))
    return BodyProperty(match["name"], type_name, nullable, required, email)


def _action_parameters(text: str, line: int, action: str) -> list[str] | None:
    """The parameter texts of the action declared after the attributes on `line`."""
    start = _line_offset(text, line)
    pattern = re.compile(rf"\b{re.escape(action)}\s*(?:<[^<>;{{}}]*>)?\s*\(")
    match = pattern.search(text, start)
    if match is None:
        return None
    opening = match.end() - 1
    closing = _matching(text, opening, "(", ")")
    inside = text[opening + 1 : closing]
    return [part for part in _split_top_level(inside) if part.strip()]


def _line_offset(text: str, line: int) -> int:
    offset = 0
    for _ in range(line - 1):
        offset = text.find("\n", offset) + 1
        if offset == 0:
            return len(text)
    return offset


def _split_top_level(text: str) -> list[str]:
    parts: list[str] = []
    depth = 0
    current: list[str] = []
    for character in text:
        if character in "(<[":
            depth += 1
        elif character in ")>]":
            depth -= 1
        if character == "," and depth == 0:
            parts.append("".join(current))
            current = []
        else:
            current.append(character)
    parts.append("".join(current))
    return parts


def _body_parameter_type(parameters: list[str]) -> str | ModelUnavailable:
    """The `[FromBody]` parameter's type, or the single complex parameter's type."""
    complex_types: list[str] = []
    for parameter in parameters:
        match = _PARAMETER.match(parameter)
        if match is None:
            return ModelUnavailable(f"parameter not understood: {' '.join(parameter.split())}")
        attributes = {m["name"] for m in _ATTRIBUTE_NAME.finditer(match["attributes"])}
        type_name = match["type"]
        if "FromBody" in attributes:
            return type_name
        if attributes & NON_BODY_ATTRIBUTES:
            continue
        if type_name.removesuffix("?").rsplit(".", 1)[-1] not in SIMPLE_TYPES:
            complex_types.append(type_name)
    if len(complex_types) == 1:
        return complex_types[0]
    if not complex_types:
        return ModelUnavailable("the action has no body parameter")
    return ModelUnavailable("several complex parameters and no [FromBody]")
