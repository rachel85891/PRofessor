from pathlib import Path

import pytest

from hw_checker.adapters.docker_runner.routes import (
    combine_templates,
    extract_route_table,
    routes_in_source,
)
from hw_checker.domain import DeclaredRoute, HttpMethod, RouteTable, UnsupportedRouting

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
