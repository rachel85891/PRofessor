from pathlib import Path

from hw_checker.adapters.docker_runner.code_facts import (
    CollectedFacts,
    extract_code_facts,
    facts_in_source,
)
from hw_checker.domain import MemberFact, TypeFact, TypeKind

SOURCE = """
using Microsoft.AspNetCore.Mvc;

namespace Shop
{
    // public class Commented { public string Hidden { get; set; } }
    public class UsersController : ControllerBase
    {
        private const string FilePath = "users.txt";
        private static readonly List<User> cache = new() { new User { Name = "x" } };
        private string note = "public int Fake { get; set; }";

        public UsersController() { }

        [HttpGet]
        public IActionResult GetAll()
        {
            var users = Load();
            List<User> sorted = users.OrderBy(u => u.Name).ToList();
            foreach (var item in sorted) { Console.WriteLine(item); }
            using var stream = File.OpenRead(FilePath);
            const int Limit = 10;
            int Helper(int value) => value * 2;
            return Ok(sorted);
        }

        public int Count() => cache.Count;

        public override string ToString() => "users";

        public static bool operator ==(UsersController a, UsersController b) => true;

        ~UsersController() { }
    }

    public class User : IEquatable<User>, IComparable
    {
        public int Id { get; set; }
        public string Name { get; set; } = "";
        public string FullName => Name;
        public static int Total { get; set; }
        private string Secret { get; set; } = "";
        public override int GetHashCode() => Id;
        bool IEquatable<User>.Equals(User? other) => false;
        public string this[int index] => Name;

        public class Address
        {
            public string city { get; set; } = "";
        }
    }

    public interface IStore
    {
        string Location { get; }
        void Save(User user);
    }

    public struct Point { public int X { get; set; } }
    public record Order(int Id, string Item);
    public record class Invoice { public decimal Total { get; init; } }
    public enum Role { Admin, Student }
    public class Home : Microsoft.AspNetCore.Mvc.Controller { }
    public class Plain : IController { }
}
"""


def collected() -> CollectedFacts:
    return facts_in_source(SOURCE, "Controllers/UsersController.cs")


def test_every_type_kind_and_controller_detection() -> None:
    types = {(t.name, t.kind, t.is_controller) for t in collected().types}

    assert types == {
        ("UsersController", TypeKind.CLASS, True),
        ("User", TypeKind.CLASS, False),
        ("Address", TypeKind.CLASS, False),
        ("IStore", TypeKind.INTERFACE, False),
        ("Point", TypeKind.STRUCT, False),
        ("Order", TypeKind.RECORD, False),
        ("Invoice", TypeKind.RECORD, False),
        ("Role", TypeKind.ENUM, False),
        ("Home", TypeKind.CLASS, True),
        ("Plain", TypeKind.CLASS, False),
    }


def test_public_properties_and_what_is_left_out() -> None:
    properties = [(p.name, p.declaring_type) for p in collected().properties]

    assert properties == [
        ("Id", "User"),
        ("Name", "User"),
        ("FullName", "User"),
        ("Total", "User"),
        ("city", "Address"),
        ("Location", "IStore"),
        ("X", "Point"),
        ("Total", "Invoice"),
    ]


def test_methods_skip_constructors_operators_overrides_and_explicit_implementations() -> None:
    methods = [(m.name, m.declaring_type) for m in collected().methods]

    assert methods == [
        ("GetAll", "UsersController"),
        ("Count", "UsersController"),
        ("Save", "IStore"),
    ]


def test_locals_inside_method_bodies_only() -> None:
    locals_ = [(m.name, m.declaring_type) for m in collected().locals]

    assert locals_ == [
        ("users", "UsersController"),
        ("sorted", "UsersController"),
        ("item", "UsersController"),
        ("stream", "UsersController"),
    ]


def test_lines_point_at_the_name() -> None:
    facts = collected()
    lines = SOURCE.splitlines()

    named: list[TypeFact | MemberFact] = [
        *facts.types,
        *facts.properties,
        *facts.methods,
        *facts.locals,
    ]
    for fact in named:
        assert fact.name in lines[fact.line - 1], fact


def test_top_level_statements_have_locals_without_a_type() -> None:
    program = (
        "var builder = WebApplication.CreateBuilder(args);\n"
        "var App = builder.Build();\nApp.Run();\n"
    )

    facts = facts_in_source(program, "Program.cs")

    assert [(m.name, m.declaring_type, m.line) for m in facts.locals] == [
        ("builder", None, 1),
        ("App", None, 2),
    ]
    assert facts.types == [] and facts.methods == []


def test_file_scoped_namespace() -> None:
    facts = facts_in_source(
        "namespace Shop;\npublic class loguser\n{\n"
        '    public string email { get; set; } = "";\n}\n',
        "Models/loguser.cs",
    )

    assert [t.name for t in facts.types] == ["loguser"]
    assert [(p.name, p.line) for p in facts.properties] == [("email", 4)]


def write(root: Path, relative: str, text: str) -> None:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def test_generated_and_tool_folders_are_skipped(tmp_path: Path) -> None:
    project = tmp_path / "repo" / "Api"
    user = "public class User { public string Name { get; set; } }"
    write(project, "Models/User.cs", user)
    for skipped in (
        "bin/Debug/Gen.cs",
        "obj/Gen.cs",
        "Properties/AssemblyInfo.cs",
        "Migrations/20261026_Init.cs",
        "Forms/Main.Designer.cs",
        "Views/Page.g.cs",
    ):
        write(project, skipped, "public class bad_name { public string bad_prop { get; set; } }")
    write(project, "Auto.cs", "// <auto-generated/>\npublic class bad_auto { }")

    facts = extract_code_facts(tmp_path / "repo", "Api")

    assert [t.name for t in facts.types] == ["User"]
    assert [(f.file_path, f.types) for f in facts.files] == [("Api/Models/User.cs", ("User",))]


def test_files_list_the_types_they_declare(tmp_path: Path) -> None:
    write(tmp_path / "repo", "Models/Users.cs", "public class UsersController : ControllerBase { }")
    write(tmp_path / "repo", "Models/Pair.cs", "public class A { }\npublic class B { }")
    write(tmp_path / "repo", "Program.cs", "var app = WebApplication.Create();")

    facts = extract_code_facts(tmp_path / "repo", "")

    assert {f.file_path: f.types for f in facts.files} == {
        "Models/Pair.cs": ("A", "B"),
        "Models/Users.cs": ("UsersController",),
        "Program.cs": (),
    }


def test_lists_are_bounded_and_marked_cut(tmp_path: Path) -> None:
    properties = "\n".join(f"    public int P{index} {{ get; set; }}" for index in range(600))
    write(tmp_path / "repo", "Big.cs", f"public class Big\n{{\n{properties}\n}}\n")

    facts = extract_code_facts(tmp_path / "repo", "")

    assert len(facts.properties) == 500
    assert facts.truncated == ("properties",)
    assert facts.properties[-1].name == "P499"
