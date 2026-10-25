from pathlib import Path

import pytest

from hw_checker.adapters.docker_runner.body_models import (
    BodyModel,
    BodyProperty,
    ModelUnavailable,
    ProjectSources,
)
from hw_checker.adapters.docker_runner.csharp_source import mask_source
from hw_checker.adapters.docker_runner.routes import routes_in_source
from hw_checker.domain import DeclaredRoute

MODELS = """
namespace Shop.Models
{
    public class User
    {
        public int Id { get; set; }
        public string Name { get; set; } = "";
        public string? Nickname { get; set; }
        public required string Email { get; set; }
        [Required]
        public int Password { get; set; }
        public bool IsAdmin { get; set; }
        public DateTime BirthDate { get; set; }
        public Guid Token { get; set; }
        public int? Age { get; set; }
        public static int Count { get; set; }
        public string Comment;
        public string FullName => Name;
        private string Secret { get; set; } = "";

        public class Address
        {
            public string City { get; set; } = "";
        }

        public string Describe() { return Name; }
    }

    public class LoginDto
    {
        public string UserName { get; set; } = "";
        public string Password { get; set; } = "";
    }

    public class Admin : User
    {
        public string Level { get; set; } = "";
    }

    public class Tagged : IEntity, IComparable<Tagged>
    {
        public string Tag { get; set; } = "";
    }

    public record Point(int X, int Y);

    public record Item(string Title)
    {
        public int Count { get; set; }
    }
}
"""

USER_PROPERTIES = (
    ("Id", "int", False),
    ("Name", "string", False),
    ("Nickname", "string", True),
    ("Email", "string", False),
    ("Password", "int", False),
    ("IsAdmin", "bool", False),
    ("BirthDate", "DateTime", False),
    ("Token", "Guid", False),
    ("Age", "int", True),
)


def controller(*actions: str) -> str:
    body = "\n".join(f"        {action}" for action in actions)
    return (
        '[Route("api/[controller]")]\n[ApiController]\n'
        f"public class UsersController : ControllerBase\n{{\n{body}\n}}\n"
    )


def sources(
    controller_text: str, nullable: bool = True
) -> tuple[ProjectSources, list[DeclaredRoute]]:
    files = {"Models/Models.cs": MODELS, "Controllers/UsersController.cs": controller_text}
    project = ProjectSources({path: mask_source(text) for path, text in files.items()}, nullable)
    routes, unsupported = routes_in_source(controller_text, "Controllers/UsersController.cs")
    assert unsupported == []
    return project, routes


def model_of(action: str, nullable: bool = True) -> BodyModel | ModelUnavailable:
    project, (route,) = sources(controller(action), nullable)
    return project.body_model(route)


def test_from_body_parameter_gives_the_class_properties() -> None:
    model = model_of("[HttpPost] public IActionResult Post([FromBody] User user) => Ok();")

    assert isinstance(model, BodyModel)
    assert model.class_name == "User"
    assert [(p.name, p.type_name, p.nullable) for p in model.properties] == list(USER_PROPERTIES)


def test_nullable_enabled_makes_non_nullable_references_required() -> None:
    model = model_of("[HttpPost] public IActionResult Post([FromBody] User user) => Ok();")

    assert isinstance(model, BodyModel)
    assert [p.name for p in model.properties if p.required] == ["Name", "Email", "Password"]


def test_nullable_disabled_keeps_only_explicit_requirements() -> None:
    model = model_of(
        "[HttpPost] public IActionResult Post([FromBody] User user) => Ok();", nullable=False
    )

    assert isinstance(model, BodyModel)
    assert [p.name for p in model.properties if p.required] == ["Email", "Password"]


def test_single_complex_parameter_is_inferred_as_the_body() -> None:
    model = model_of('[HttpPut("{id}")] public IActionResult Put(int id, LoginDto dto) => Ok();')

    assert model == BodyModel(
        "LoginDto",
        (
            BodyProperty("UserName", "string", nullable=False, required=True),
            BodyProperty("Password", "string", nullable=False, required=True),
        ),
    )


def test_attributes_on_other_parameters_are_respected() -> None:
    action = (
        '[HttpPost("{id}")] public IActionResult P([FromRoute] int id, [FromQuery] User filter, '
        "[FromServices] ILogger<UsersController> log, Shop.Models.LoginDto body, "
        "CancellationToken token = default) => Ok();"
    )

    model = model_of(action)

    assert isinstance(model, BodyModel) and model.class_name == "LoginDto"


@pytest.mark.parametrize(
    ("action", "reason"),
    [
        ("[HttpGet] public IActionResult Get() => Ok();", "the action has no body parameter"),
        ('[HttpGet("{id}")] public IActionResult Get(int id) => Ok();', "no body parameter"),
        (
            "[HttpPost] public IActionResult P([FromBody] Admin a) => Ok();",
            "Admin inherits from User",
        ),
        (
            "[HttpPost] public IActionResult P([FromBody] Point p) => Ok();",
            "Point is declared with a primary constructor",
        ),
        (
            "[HttpPost] public IActionResult P([FromBody] Item i) => Ok();",
            "Item is declared with a primary constructor",
        ),
        (
            "[HttpPost] public IActionResult P([FromBody] dynamic d) => Ok();",
            "body parameter of type dynamic",
        ),
        (
            "[HttpPost] public IActionResult P([FromBody] JsonElement e) => Ok();",
            "body parameter of type JsonElement",
        ),
        (
            "[HttpPost] public IActionResult P([FromBody] List<User> u) => Ok();",
            "a collection or generic",
        ),
        (
            "[HttpPost] public IActionResult P([FromBody] Order o) => Ok();",
            "class Order not found in the project",
        ),
        (
            "[HttpPost] public IActionResult P(User a, LoginDto b) => Ok();",
            "several complex parameters",
        ),
    ],
)
def test_unsupported_models_say_why(action: str, reason: str) -> None:
    model = model_of(action)

    assert isinstance(model, ModelUnavailable)
    assert reason in model.reason


def test_interfaces_are_not_a_base_model() -> None:
    model = model_of("[HttpPost] public IActionResult P([FromBody] Tagged t) => Ok();")

    assert model == BodyModel("Tagged", (BodyProperty("Tag", "string", False, True),))


def test_two_classes_with_the_same_name_are_unsupported() -> None:
    project, (route,) = sources(
        controller("[HttpPost] public IActionResult P([FromBody] User u) => Ok();")
    )
    project = ProjectSources(
        {
            **project.sources,
            "Other/User.cs": mask_source("public class User { public int A { get; set; } }"),
        },
        nullable=True,
    )

    model = project.body_model(route)

    assert model == ModelUnavailable("several classes named User")


def test_comments_and_strings_do_not_add_properties() -> None:
    text = (
        "public class Note\n{\n"
        "    // public string Hidden { get; set; }\n"
        '    public string Text { get; set; } = "public int Fake { get; set; }";\n'
        "}\n"
    )
    project = ProjectSources({"Note.cs": mask_source(text)}, nullable=False)

    assert project.model("Note") == BodyModel(
        "Note", (BodyProperty("Text", "string", False, False),)
    )


def test_email_address_attribute_is_read() -> None:
    text = (
        "public class LoginModel\n{\n"
        '    [Required, EmailAddress]\n    public string LogInEmail { get; set; } = "";\n'
        '    [Required]\n    public string LogInPassword { get; set; } = "";\n}\n'
    )
    project = ProjectSources({"Login.cs": mask_source(text)}, nullable=False)

    assert project.model("LoginModel") == BodyModel(
        "LoginModel",
        (
            BodyProperty("LogInEmail", "string", False, True, email=True),
            BodyProperty("LogInPassword", "string", False, True),
        ),
    )


def test_reading_the_project_folder_skips_bin_and_obj(tmp_path: Path) -> None:
    project = tmp_path / "repo" / "Api"
    (project / "Models").mkdir(parents=True)
    (project / "obj").mkdir()
    (project / "Models" / "User.cs").write_text(
        "public class User { public int Id { get; set; } }", encoding="utf-8"
    )
    (project / "obj" / "User.cs").write_text(
        "public class User { public int Copy { get; set; } }", encoding="utf-8"
    )

    read = ProjectSources.read(tmp_path / "repo", "Api", nullable=True)

    assert read.model("User") == BodyModel("User", (BodyProperty("Id", "int", False, False),))
