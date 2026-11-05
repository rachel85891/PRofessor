from pathlib import Path

import pytest
from pydantic import ValidationError

from hw_checker.checks.deterministic.client import (
    ClientParams,
    client_element_ids,
    client_fetch_routes,
    client_file_refs,
)
from hw_checker.checks.deterministic.web_sources import resolve_reference
from hw_checker.domain import (
    Assignment,
    CheckInput,
    DeclaredRoute,
    ExecutionResult,
    Finding,
    HttpMethod,
    NotEvaluatedReason,
    Requirement,
    RequirementKind,
    RouteTable,
    Severity,
    StepStatus,
    TrackedFiles,
    UnsupportedRouting,
)

REQUIREMENT = Requirement(
    id="hw01-client",
    title="Client pages work",
    severity=Severity.MAJOR,
    kind=RequirementKind.CUSTOM,
    check="client_file_refs",
    params={"root": "wwwroot"},
)
ASSIGNMENT = Assignment(id="hw01", order=1, title="Users", requirements=(REQUIREMENT,))
PARAMS = ClientParams(root="wwwroot")
WEB = "Api/wwwroot"


def route(method: HttpMethod | None, template: str) -> DeclaredRoute:
    return DeclaredRoute(
        method=method,
        template=template,
        controller="UsersController",
        action="Action",
        file_path="Api/Controllers/UsersController.cs",
        line=1,
    )


ROUTES = RouteTable(
    routes=(
        route(HttpMethod.GET, "api/Users/{id}"),
        route(HttpMethod.POST, "api/Users"),
        route(HttpMethod.POST, "api/Users/login"),
        route(HttpMethod.PUT, "api/Users/{id:int}"),
    )
)


def execution(routes: RouteTable | None = ROUTES) -> ExecutionResult:
    return ExecutionResult(
        build_status=StepStatus.SUCCEEDED, run_status=StepStatus.SUCCEEDED, routes=routes
    )


def check_input(
    repo: Path,
    run: ExecutionResult | None = None,
    tracked: tuple[str, ...] | None = None,
) -> CheckInput:
    return CheckInput(
        repo_dir=repo,
        assignment=ASSIGNMENT,
        requirement=REQUIREMENT,
        execution=run,
        tracked=None if tracked is None else TrackedFiles(paths=tracked),
    )


def write(root: Path, relative: str, text: str) -> None:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def only_finding(result: tuple[Finding, ...] | NotEvaluatedReason) -> Finding:
    assert isinstance(result, tuple)
    (finding,) = result
    return finding


def page(scripts: str = "", body: str = "") -> str:
    return (
        f"<!DOCTYPE html>\n<html>\n<head>\n{scripts}\n</head>\n<body>\n{body}\n</body>\n</html>\n"
    )


def test_root_must_be_a_relative_folder() -> None:
    with pytest.raises(ValidationError):
        ClientParams(root="C:/wwwroot")


@pytest.mark.parametrize(
    ("folder", "value", "resolved"),
    [
        (".", "js/app.js", "js/app.js"),
        ("pages", "../js/app.js?v=2", "js/app.js"),
        ("pages", "/css/site.css", "css/site.css"),
        (".", "~/img/logo%20new.png", "img/logo new.png"),
        (".", "https://cdn.example.com/x.js", None),
        (".", "//cdn.example.com/x.js", None),
        (".", "data:image/png;base64,AA", None),
        (".", "../../above.js", "above.js"),
        (".", "{{ asset }}", None),
        (".", "api/users/photo", None),
    ],
)
def test_references_resolve_like_a_browser(folder: str, value: str, resolved: str | None) -> None:
    assert resolve_reference(folder, value) == resolved


# ---------------------------------------------------------------- client_file_refs


def test_references_to_existing_files_pass(tmp_path: Path) -> None:
    write(tmp_path, f"{WEB}/index.html", page('<script src="js/app.js"></script>'))
    write(tmp_path, f"{WEB}/js/app.js", "let a = 1;\n")
    write(tmp_path, f"{WEB}/pages/x.html", page('<link rel="stylesheet" href="../css/s.css">'))
    write(tmp_path, f"{WEB}/css/s.css", "body {}\n")

    assert client_file_refs(check_input(tmp_path), PARAMS) == ()


def test_missing_and_case_only_references_are_listed(tmp_path: Path) -> None:
    head = '<script src="js/Update.js"></script>\n<script src="js/gone.js"></script>'
    write(tmp_path, f"{WEB}/update.html", page(head, '<img src="img/logo.png">'))
    write(tmp_path, f"{WEB}/js/update.js", "let a = 1;\n")
    write(tmp_path, f"{WEB}/img/logo.png", "png")

    finding = only_finding(client_file_refs(check_input(tmp_path), PARAMS))

    assert finding.message_en == (
        "Pages refer to files that do not exist, so the page breaks: "
        f"`js/Update.js` ({WEB}/update.html, line 4): the file is named `js/update.js`; "
        "a server tells upper and lower case apart; "
        f"`js/gone.js` ({WEB}/update.html, line 5): no such file. Total: 2."
    )
    assert finding.severity is Severity.MAJOR
    assert finding.evidence is not None and "Update.js" in finding.evidence.quote


def test_references_that_cannot_be_told_are_skipped(tmp_path: Path) -> None:
    refs = (
        '<script src="https://cdn.example.com/lib.js"></script>\n'
        '<link rel="icon" href="favicon.ico">\n'
        '<img src="/api/users/1/photo">'
    )
    write(tmp_path, f"{WEB}/index.html", page(refs))
    write(tmp_path, f"{WEB}/base.html", page('<base href="/app/">\n<script src="x.js"></script>'))

    assert client_file_refs(check_input(tmp_path), PARAMS) == ()


def test_untracked_pages_are_not_read(tmp_path: Path) -> None:
    write(tmp_path, f"{WEB}/index.html", page('<script src="gone.js"></script>'))

    assert client_file_refs(check_input(tmp_path, tracked=()), PARAMS) == ()


# ---------------------------------------------------------------- client_element_ids


def test_ids_declared_by_the_loading_page_pass(tmp_path: Path) -> None:
    body = '<input id="email">\n<button id="send">Send</button>'
    write(tmp_path, f"{WEB}/index.html", page('<script src="app.js"></script>', body))
    script = (
        'const email = document.getElementById("email").value;\n'
        "document.querySelector('#send').onclick = () => {};\n"
    )
    write(tmp_path, f"{WEB}/app.js", script)

    assert client_element_ids(check_input(tmp_path), PARAMS) == ()


def test_an_id_no_loading_page_declares_is_a_finding(tmp_path: Path) -> None:
    write(
        tmp_path,
        f"{WEB}/update.html",
        page('<script src="update.js"></script>', '<input id="name">'),
    )
    script = (
        "// document.getElementById('commented')\n"
        'const first = document.getElementById("firstname").value;\n'
        "const all = document.querySelectorAll(`#rows`);\n"
    )
    write(tmp_path, f"{WEB}/update.js", script)

    finding = only_finding(client_element_ids(check_input(tmp_path), PARAMS))

    assert finding.message_en == (
        "Scripts look up an element id that no page loading them declares, so the lookup "
        f"returns null: `firstname` ({WEB}/update.js, line 2); "
        f"`rows` ({WEB}/update.js, line 3). Total: 2."
    )
    assert finding.evidence is not None and "firstname" in finding.evidence.quote


def test_inline_scripts_are_checked_against_their_page(tmp_path: Path) -> None:
    inline = '<script>\n  const x = document.getElementById("missing");\n</script>'
    write(tmp_path, f"{WEB}/index.html", page(body=f'<p id="here"></p>\n{inline}'))

    finding = only_finding(client_element_ids(check_input(tmp_path), PARAMS))

    assert f"`missing` ({WEB}/index.html, line 9)" in finding.message_en


def test_an_id_set_only_at_run_time_is_not_reported(tmp_path: Path) -> None:
    write(tmp_path, f"{WEB}/index.html", page('<script src="app.js"></script>'))
    script = (
        'const row = document.createElement("div");\n'
        'row.id = "result";\n'
        "document.body.appendChild(row);\n"
        'document.getElementById("result").textContent = "ok";\n'
    )
    write(tmp_path, f"{WEB}/app.js", script)

    assert client_element_ids(check_input(tmp_path), PARAMS) == ()


def test_pages_whose_scripts_build_ids_dynamically_are_skipped(tmp_path: Path) -> None:
    write(tmp_path, f"{WEB}/list.html", page('<script src="list.js"></script>', '<ul id="list">'))
    script = (
        'const list = document.getElementById("list");\n'
        "list.innerHTML = users.map(u => `<li id=\"${u.id}\">${u.name}</li>`).join('');\n"
        'document.getElementById("7").remove();\n'
    )
    write(tmp_path, f"{WEB}/list.js", script)

    assert client_element_ids(check_input(tmp_path), PARAMS) == ()


def test_scripts_no_page_loads_and_runtime_selectors_are_skipped(tmp_path: Path) -> None:
    write(tmp_path, f"{WEB}/index.html", page('<script src="app.js"></script>'))
    write(
        tmp_path,
        f"{WEB}/app.js",
        "document.getElementById(name);\ndocument.querySelector(`#${x}`);\n",
    )
    write(tmp_path, f"{WEB}/orphan.js", 'document.getElementById("nowhere");\n')

    assert client_element_ids(check_input(tmp_path), PARAMS) == ()


# ---------------------------------------------------------------- client_fetch_routes


def fetch_site(tmp_path: Path, script: str, page_path: str = "index.html") -> None:
    prefix = "../" if "/" in page_path else ""
    write(tmp_path, f"{WEB}/{page_path}", page(f'<script src="{prefix}app.js"></script>'))
    write(tmp_path, f"{WEB}/app.js", script)


def test_fetch_urls_matching_declared_routes_pass(tmp_path: Path) -> None:
    script = (
        "const res = await fetch(`api/users/${id}`);\n"
        'await fetch("/api/Users", { method: "POST", body });\n'
        "await fetch('http://localhost:5000/api/users/login', {\n  method: 'post',\n});\n"
        'await fetch("api/users/" + user.id, { method: "PUT" });\n'
        'await fetch("data/countries.json");\n'
    )
    fetch_site(tmp_path, script)

    assert client_fetch_routes(check_input(tmp_path, execution()), PARAMS) == ()


def test_a_template_literal_fetch_to_an_undeclared_route_is_a_finding(tmp_path: Path) -> None:
    script = (
        "const res = await fetch(`/api/user/${id}`);\n"
        'await fetch("api/users/register", { method: "POST" });\n'
        'await fetch("api/users", { method: "DELETE" });\n'
    )
    fetch_site(tmp_path, script)

    finding = only_finding(client_fetch_routes(check_input(tmp_path, execution()), PARAMS))

    assert finding.message_en == (
        "Requests in the client code that match no route the API declares: "
        f"`GET /api/user/{{…}}` ({WEB}/app.js, line 1); "
        f"`POST /api/users/register` ({WEB}/app.js, line 2); "
        f"`DELETE /api/users` ({WEB}/app.js, line 3). Total: 3."
    )


def test_fetch_urls_that_cannot_be_told_are_skipped(tmp_path: Path) -> None:
    script = (
        "await fetch(url);\n"
        'await fetch(baseUrl + "/nothing");\n'
        'await fetch("https://api.example.com/nothing");\n'
        "await fetch(`/api/user${id}`);\n"
        'await fetch("/api/nothing", options);\n'
        'await fetch("/api/nothing", { method: verb });\n'
        'await fetch("/api/nothing", { method });\n'
    )
    fetch_site(tmp_path, script)

    assert client_fetch_routes(check_input(tmp_path, execution()), PARAMS) == ()


def test_relative_urls_of_a_subfolder_page_are_skipped(tmp_path: Path) -> None:
    fetch_site(tmp_path, 'await fetch("api/nothing");\n', page_path="pages/index.html")

    assert client_fetch_routes(check_input(tmp_path, execution()), PARAMS) == ()


def test_without_a_usable_route_table_fetch_routes_are_not_evaluated(tmp_path: Path) -> None:
    fetch_site(tmp_path, 'await fetch("/api/nothing");\n')
    minimal = RouteTable(
        routes=ROUTES.routes,
        unsupported=(UnsupportedRouting(file_path="Api/Program.cs", line=1, form="MapGet"),),
    )

    assert client_fetch_routes(check_input(tmp_path), PARAMS) is NotEvaluatedReason.NO_EXECUTION
    for table in (None, minimal):
        result = client_fetch_routes(check_input(tmp_path, execution(table)), PARAMS)
        assert result is NotEvaluatedReason.ENVIRONMENT_ISSUE


def test_a_script_loaded_with_other_letter_case_is_still_checked(tmp_path: Path) -> None:
    write(
        tmp_path,
        f"{WEB}/update.html",
        page('<script src="Update.js"></script>', '<input id="name">'),
    )
    script = (
        'const first = document.querySelector("#firstname");\n'
        "const hello = `Hello ${user.firstname}`;\n"
        'const again = document.querySelector("#firstname").value;\n'
    )
    write(tmp_path, f"{WEB}/update.js", script)

    finding = only_finding(client_element_ids(check_input(tmp_path), PARAMS))

    assert f"`firstname` ({WEB}/update.js, line 1)" in finding.message_en
    assert f"`firstname` ({WEB}/update.js, line 3)" in finding.message_en
    assert "Total: 2." in finding.message_en
