import random
from pathlib import Path

import pytest

from hw_checker.adapters.docker_runner.routes import (
    ControllerChoice,
    ProbeRoute,
    choose_primary_controller,
    combine_templates,
    extract_route_table,
    resolve_probe_path,
    routes_in_source,
    template_matches,
)
from hw_checker.domain import (
    DeclaredRoute,
    HttpMethod,
    HttpProbe,
    RouteResolution,
    RouteTable,
    UnsupportedRouting,
)

GET, POST, PUT, DELETE, PATCH = (
    HttpMethod.GET,
    HttpMethod.POST,
    HttpMethod.PUT,
    HttpMethod.DELETE,
    HttpMethod.PATCH,
)


def pairs(source: str) -> list[tuple[HttpMethod | None, str]]:
    routes, unsupported = routes_in_source(source, "Controllers/C.cs")
    assert unsupported == []
    return [(route.method, route.template) for route in routes]


def controller(name: str, body: str, route: str = '[Route("api/[controller]")]') -> str:
    return f"""
using Microsoft.AspNetCore.Mvc;

namespace Shop.Controllers
{{
    {route}
    [ApiController]
    public class {name} : ControllerBase
    {{
{body}
    }}
}}
"""


CRUD = """
        [HttpGet]
        public IActionResult Get() => Ok();

        [HttpGet("{id}")]
        public IActionResult Get(int id) => Ok();

        [HttpPost]
        public IActionResult Post([FromBody] User user) => Ok();

        [HttpPut("{id:int}")]
        public IActionResult Put(int id, [FromBody] User user) => Ok();

        [HttpDelete("{id}")]
        public IActionResult Delete(int id) => Ok();

        [HttpPatch("{id}")]
        public IActionResult Patch(int id) => Ok();
"""


ONE_GET = """
        [HttpGet]
        public IActionResult Get() => Ok();
"""


@pytest.mark.parametrize(
    ("class_name", "prefix"),
    [("UsersController", "api/Users"), ("UserController", "api/User"), ("User", "api/User")],
)
def test_controller_token_comes_from_the_class_name(class_name: str, prefix: str) -> None:
    assert pairs(controller(class_name, CRUD)) == [
        (GET, prefix),
        (GET, f"{prefix}/{{id}}"),
        (POST, prefix),
        (PUT, f"{prefix}/{{id:int}}"),
        (DELETE, f"{prefix}/{{id}}"),
        (PATCH, f"{prefix}/{{id}}"),
    ]


def test_space_before_the_parenthesis_and_name_arguments() -> None:
    body = """
        [HttpPost ("login")]
        public IActionResult Login() => Ok();

        [HttpGet("{id}", Name = "GetUser")]
        public IActionResult GetOne(int id) => Ok();

        [HttpGet(Name = "ListUsers")]
        public IActionResult List() => Ok();
"""

    assert pairs(controller("UsersController", body)) == [
        (POST, "api/Users/login"),
        (GET, "api/Users/{id}"),
        (GET, "api/Users"),
    ]


def test_route_on_an_action_and_the_action_token() -> None:
    body = """
        [Route("[action]")]
        [HttpPost]
        public async Task<IActionResult> SignInAsync() => Ok();

        [Route("ping")]
        public IActionResult Ping() => Ok();
"""

    assert pairs(controller("UsersController", body)) == [
        (POST, "api/Users/SignIn"),
        (None, "api/Users/ping"),
    ]


def test_template_starting_with_a_slash_replaces_the_class_route() -> None:
    body = """
        [HttpGet("/health")]
        public IActionResult Health() => Ok();

        [HttpGet("~/status")]
        public IActionResult Status() => Ok();
"""

    assert pairs(controller("UsersController", body)) == [(GET, "health"), (GET, "status")]


def test_class_route_without_a_token_and_class_without_a_route() -> None:
    with_route = controller("AccountController", ONE_GET, route='[Route("api/accounts")]')
    without_route = controller(
        "AuthController",
        '        [HttpPost("api/auth/login")]\n        public IActionResult L() => Ok();',
        route="",
    )

    assert pairs(with_route) == [(GET, "api/accounts")]
    assert pairs(without_route) == [(POST, "api/auth/login")]


def test_several_attributes_in_one_bracket_and_qualified_names() -> None:
    body = """
        [HttpPost, Route("register")]
        public IActionResult Register() => Ok();

        [Microsoft.AspNetCore.Mvc.HttpGetAttribute("all")]
        public IActionResult All() => Ok();
"""

    assert pairs(controller("UsersController", body)) == [
        (POST, "api/Users/register"),
        (GET, "api/Users/all"),
    ]


def test_unusual_templates_are_kept_as_written() -> None:
    body = """
        [HttpGet("{email} {password}")]
        public IActionResult Login(string email, string password) => Ok();
"""

    assert pairs(controller("UsersController", body)) == [(GET, "api/Users/{email} {password}")]


def test_several_controllers_in_one_file() -> None:
    source = controller("UsersController", ONE_GET) + controller("OrdersController", ONE_GET)

    assert pairs(source) == [(GET, "api/Users"), (GET, "api/Orders")]


def test_comments_and_strings_that_look_like_attributes_are_ignored() -> None:
    body = """
        // [HttpDelete("all")]
        /* [HttpPut("x")]
           public IActionResult X() => Ok(); */
        [HttpGet]
        public IActionResult Get() => Ok("[HttpPost(\\"fake\\")]");

        private string example = "[HttpPatch]";
"""

    assert pairs(controller("UsersController", body)) == [(GET, "api/Users")]


def test_route_records_its_controller_action_file_and_line() -> None:
    routes, _ = routes_in_source(controller("UserController", ONE_GET), "Api/Controllers/U.cs")

    assert routes == [
        DeclaredRoute(
            method=GET,
            template="api/User",
            controller="UserController",
            action="Get",
            file_path="Api/Controllers/U.cs",
            line=11,
        )
    ]


def test_minimal_apis_and_conventional_routing_are_unsupported() -> None:
    source = """
var app = builder.Build();
app.MapControllers();
app.MapOpenApi();
app.MapGet("/api/users", () => Results.Ok());
app.MapPost<User>("/api/users", (User u) => Results.Ok());
app.MapControllerRoute(name: "default", pattern: "{controller}/{action}");
app.Run();
"""

    routes, unsupported = routes_in_source(source, "Program.cs")

    assert routes == []
    assert [(item.line, item.form) for item in unsupported] == [
        (5, "minimal API (MapGet)"),
        (6, "minimal API (MapPost)"),
        (7, "conventional routing (MapControllerRoute)"),
    ]


@pytest.mark.parametrize(
    ("attribute", "form"),
    [
        ("[HttpGet(Routes.Users)]", "[HttpGet] with a route template that is not a string literal"),
        (
            '[HttpGet($"{Prefix}/x")]',
            "[HttpGet] with a route template that is not a string literal",
        ),
        ('[AcceptVerbs("GET", "POST")]', "[AcceptVerbs] attribute"),
        ('[HttpGet("[area]/x")]', "route token [area]"),
    ],
)
def test_unreadable_route_forms_are_unsupported(attribute: str, form: str) -> None:
    body = f"        {attribute}\n        public IActionResult X() => Ok();\n"

    routes, unsupported = routes_in_source(controller("UsersController", body), "C.cs")

    assert routes == []
    assert [item.form for item in unsupported] == [form]


def test_body_less_records_and_parameter_attributes_do_not_confuse_the_reader() -> None:
    source = "public record User(int Id, string Name);\n" + controller(
        "UsersController",
        "        [HttpPost]\n"
        "        public IActionResult Post([FromBody] User user, [FromQuery] int x) => Ok();\n",
    )

    assert pairs(source) == [(POST, "api/Users")]


def test_extraction_reads_the_project_folder_and_skips_bin_and_obj(tmp_path: Path) -> None:
    project = tmp_path / "repo" / "WebApi" / "WebApi"
    (project / "Controllers").mkdir(parents=True)
    (project / "bin" / "Debug").mkdir(parents=True)
    (project / "obj").mkdir()
    (project / "Controllers" / "UserController.cs").write_text(
        controller("UserController", ONE_GET), encoding="utf-8"
    )
    (project / "bin" / "Debug" / "Old.cs").write_text(
        controller("OldController", ONE_GET), encoding="utf-8"
    )
    (project / "obj" / "Gen.cs").write_text(controller("GenController", ONE_GET), encoding="utf-8")
    (tmp_path / "repo" / "Other.cs").write_text(
        controller("OutsideController", ONE_GET), encoding="utf-8"
    )

    table = extract_route_table(tmp_path / "repo", "WebApi/WebApi")

    assert [(r.template, r.file_path) for r in table.routes] == [
        ("api/User", "WebApi/WebApi/Controllers/UserController.cs")
    ]
    assert table.usable


def test_table_is_usable_only_with_routes_and_nothing_unsupported() -> None:
    route = DeclaredRoute(
        method=GET, template="api/User", controller="U", action="Get", file_path="U.cs", line=1
    )
    odd = UnsupportedRouting(file_path="Program.cs", line=3, form="minimal API (MapGet)")

    assert RouteTable(routes=(route,)).usable
    assert not RouteTable().usable
    assert not RouteTable(routes=(route,), unsupported=(odd,)).usable


@pytest.mark.parametrize(
    ("class_template", "action_template", "expected"),
    [
        ("api/[controller]", None, "api/[controller]"),
        ("api/[controller]/", "{id}", "api/[controller]/{id}"),
        ("/api/users", "/login", "login"),
        (None, None, ""),
        (None, "x", "x"),
        ("api", "~/y/", "y"),
    ],
)
def test_combine_templates(
    class_template: str | None, action_template: str | None, expected: str
) -> None:
    assert combine_templates(class_template, action_template) == expected


# ---------------------------------------------------------------- matching


@pytest.mark.parametrize(
    ("template", "path", "matches"),
    [
        ("api/Users", "/api/users", True),
        ("api/Users", "/API/USERS/", True),
        ("api/Users", "/api/user", False),
        ("api/Users/{id}", "/api/users/3", True),
        ("api/Users/{id:int}", "/api/users/3", True),
        ("api/Users/{id:int:min(1)}", "/api/users/3", True),
        ("api/Users/{id}", "/api/users", False),
        ("api/Users/{id?}", "/api/users", True),
        ("api/Users/{id?}", "/api/users/3", True),
        ("api/Users/{page=1}", "/api/users", True),
        ("api/Users/{id}", "/api/users/3/orders", False),
        ("files/{*rest}", "/files/a/b/c", True),
        ("api/Users/login", "/api/users/LOGIN", True),
        ("api/Users/{email} {password}", "/api/users/login", False),
        ("api/Users", "/api/users?page=2", True),
        ("", "/", True),
        ("", "/api", False),
    ],
)
def test_template_matching(template: str, path: str, matches: bool) -> None:
    assert template_matches(template, path) is matches


def route(method: HttpMethod | None, template: str) -> DeclaredRoute:
    return DeclaredRoute(
        method=method, template=template, controller="C", action="A", file_path="C.cs", line=1
    )


TABLE = RouteTable(
    routes=(
        route(GET, "api/User"),
        route(GET, "api/User/{id:int}"),
        route(POST, "api/User"),
        route(POST, "api/User/signin"),
        route(None, "api/User/ping"),
    )
)


def http_probe(method: HttpMethod, path: str, *alternatives: str) -> HttpProbe:
    return HttpProbe(
        requirement_id="hw01-r1",
        assignment_id="hw01",
        method=method,
        path=path,
        path_alternatives=alternatives,
        expected_status=200,
    )


@pytest.mark.parametrize(
    ("probe", "expected"),
    [
        (http_probe(GET, "/api/users", "/api/user"), "/api/user"),
        (http_probe(GET, "/api/users/3", "/api/user/3"), "/api/user/3"),
        (http_probe(GET, "/api/User/7"), "/api/User/7"),
        (
            http_probe(
                POST, "/api/users/login", "/api/user/login", "/api/users/signin", "/api/user/signin"
            ),
            "/api/user/signin",
        ),
        (http_probe(POST, "/api/user/signin", "/api/user"), "/api/user/signin"),
        (http_probe(DELETE, "/api/user/ping"), "/api/user/ping"),
    ],
)
def test_first_candidate_declared_for_the_method_wins(probe: HttpProbe, expected: str) -> None:
    resolved = resolve_probe_path(probe, TABLE)

    assert (resolved.path, resolved.resolution, resolved.controller) == (
        expected,
        RouteResolution.RESOLVED,
        "C",
    )


@pytest.mark.parametrize(
    "probe",
    [
        http_probe(DELETE, "/api/users/3", "/api/user/3"),
        http_probe(PUT, "/api/user"),
        http_probe(GET, "/api/users/abc/orders"),
    ],
)
def test_probe_without_a_declared_route_is_not_sent(probe: HttpProbe) -> None:
    assert resolve_probe_path(probe, TABLE) == ProbeRoute(
        None, RouteResolution.ROUTE_NOT_FOUND, None
    )


@pytest.mark.parametrize(
    "table",
    [
        None,
        RouteTable(),
        RouteTable(
            routes=TABLE.routes,
            unsupported=(
                UnsupportedRouting(file_path="Program.cs", line=4, form="minimal API (MapGet)"),
            ),
        ),
    ],
)
def test_unusable_table_falls_back_to_the_first_path(table: RouteTable | None) -> None:
    probe = http_probe(DELETE, "/api/users/3", "/api/user/3")

    assert resolve_probe_path(probe, table) == ProbeRoute(
        "/api/users/3", RouteResolution.UNAVAILABLE, None
    )
    assert choose_primary_controller([probe], table) == ControllerChoice(None, ())


# ---------------------------------------------------------------- primary controller


def controller_route(controller: str, method: HttpMethod, template: str) -> DeclaredRoute:
    return DeclaredRoute(
        method=method,
        template=template,
        controller=controller,
        action="A",
        file_path=f"Controllers/{controller}.cs",
        line=1,
    )


USERS = "UsersController"
USER = "UserController"
TWO_CONTROLLERS = (
    controller_route(USERS, GET, "api/Users"),
    controller_route(USERS, GET, "api/Users/{id}"),
    controller_route(USERS, POST, "api/Users"),
    controller_route(USERS, PUT, "api/Users/{id}"),
    controller_route(USERS, DELETE, "api/Users/{id}"),
    controller_route(USER, PUT, "api/User/{id}"),
    controller_route(USER, DELETE, "api/User/{id}"),
    controller_route(USER, POST, "api/User/login"),
)
# Each probe names /api/user first, so resolving one by one sends PUT and DELETE to the
# UserController, away from the user the POST created in the UsersController.
STEERED_PROBES = (
    http_probe(POST, "/api/user", "/api/users"),
    http_probe(GET, "/api/user/3", "/api/users/3"),
    http_probe(PUT, "/api/user/3", "/api/users/3"),
    http_probe(DELETE, "/api/user/3", "/api/users/3"),
    http_probe(POST, "/api/users/login", "/api/user/login"),
)


def resolve_all(
    probes: tuple[HttpProbe, ...], table: RouteTable
) -> tuple[ControllerChoice, list[tuple[str | None, str | None]]]:
    choice = choose_primary_controller(probes, table)
    routes = [resolve_probe_path(probe, table, choice.primary) for probe in probes]
    return choice, [(route.path, route.controller) for route in routes]


def test_probes_go_to_the_controller_serving_most_and_fall_back_for_the_rest() -> None:
    choice, sent = resolve_all(STEERED_PROBES, RouteTable(routes=TWO_CONTROLLERS))

    assert choice == ControllerChoice(USERS, (USER, USERS))
    assert sent == [
        ("/api/users", USERS),
        ("/api/users/3", USERS),
        ("/api/users/3", USERS),
        ("/api/users/3", USERS),
        ("/api/user/login", USER),
    ]


def test_without_a_preference_probes_resolve_one_by_one_as_before() -> None:
    table = RouteTable(routes=TWO_CONTROLLERS)

    own = [resolve_probe_path(probe, table) for probe in STEERED_PROBES]

    assert [(route.path, route.controller) for route in own] == [
        ("/api/users", USERS),
        ("/api/users/3", USERS),
        ("/api/user/3", USER),
        ("/api/user/3", USER),
        ("/api/user/login", USER),
    ]


def test_a_tie_goes_to_the_controller_of_the_earliest_probe() -> None:
    table = RouteTable(
        routes=(
            controller_route("BController", GET, "api/b"),
            controller_route("AController", POST, "api/a"),
        )
    )
    probes = (http_probe(GET, "/api/b"), http_probe(POST, "/api/a"))

    assert choose_primary_controller(probes, table).primary == "BController"
    assert choose_primary_controller(probes[::-1], table).primary == "AController"


def test_a_tie_with_no_earliest_resolution_goes_to_the_first_name() -> None:
    table = RouteTable(
        routes=(
            controller_route("BController", GET, "api/items"),
            controller_route("AController", GET, "api/items"),
        )
    )

    choice = choose_primary_controller((http_probe(GET, "/api/items"),), table)

    assert choice == ControllerChoice("AController", ("AController", "BController"))


def test_resolution_names_the_declared_route_it_used() -> None:
    table = RouteTable(routes=TWO_CONTROLLERS)
    choice = choose_primary_controller(STEERED_PROBES, table)

    put = resolve_probe_path(STEERED_PROBES[2], table, choice.primary)
    login = resolve_probe_path(STEERED_PROBES[4], table, choice.primary)

    assert put.route == controller_route(USERS, PUT, "api/Users/{id}")
    assert login.route == controller_route(USER, POST, "api/User/login")
    assert resolve_probe_path(http_probe(PATCH, "/api/x"), table).route is None


def test_a_single_controller_resolves_exactly_as_before() -> None:
    probes = (
        http_probe(GET, "/api/users", "/api/user"),
        http_probe(
            POST, "/api/users/login", "/api/user/login", "/api/users/signin", "/api/user/signin"
        ),
        http_probe(DELETE, "/api/users/3", "/api/user/3"),
    )

    choice, sent = resolve_all(probes, TABLE)

    assert choice == ControllerChoice("C", ("C",))
    assert sent == [
        (route.path, route.controller) for route in (resolve_probe_path(p, TABLE) for p in probes)
    ]


def test_no_controller_serving_any_probe_gives_no_primary() -> None:
    choice = choose_primary_controller((http_probe(PATCH, "/api/x"),), TABLE)

    assert choice == ControllerChoice(None, ())


@pytest.mark.parametrize("seed", range(6))
def test_choice_does_not_depend_on_the_route_table_order(seed: int) -> None:
    shuffled = list(TWO_CONTROLLERS)
    random.Random(seed).shuffle(shuffled)

    choice, sent = resolve_all(STEERED_PROBES, RouteTable(routes=tuple(shuffled)))

    assert (choice, sent) == resolve_all(STEERED_PROBES, RouteTable(routes=TWO_CONTROLLERS))
