"""The layer signals (stage 21): class declarations with their base lists, every
constructor parameter, and object creations; on synthetic code."""

from pathlib import Path

from hw_checker.adapters.architecture_evidence import LocalArchitectureEvidenceReader
from hw_checker.domain import (
    ArchitectureEvidence,
    ArchitectureSignal,
    ArchitectureSignalKind,
    EvidenceCoverageStatus,
    EvidenceIssueKind,
)

DECLARATION = ArchitectureSignalKind.TYPE_DECLARATION
PARAMETER = ArchitectureSignalKind.CONSTRUCTOR_PARAMETER
CREATION = ArchitectureSignalKind.OBJECT_CREATION


def scan(tmp_path: Path, text: str, name: str = "Code.cs") -> ArchitectureEvidence:
    (tmp_path / name).write_text(text, encoding="utf-8", newline="\n")
    return LocalArchitectureEvidenceReader().scan(tmp_path)


def of_kind(
    evidence: ArchitectureEvidence, kind: ArchitectureSignalKind
) -> list[ArchitectureSignal]:
    return [signal for signal in evidence.signals if signal.kind is kind]


def test_classes_and_records_give_their_base_lists_and_keywords(tmp_path: Path) -> None:
    evidence = scan(
        tmp_path,
        "namespace Shop;\n"
        "[ApiController]\n"
        "public sealed partial class UsersController : ControllerBase, IDisposable\n"
        "{\n"
        "    private class Cache { }\n"
        "    public void Dispose() { }\n"
        "}\n"
        "public record Point(int X, int Y) : Shape(X), IShape;\n"
        "public record class Box<T>(T Item) where T : class { }\n"
        "internal static class Extensions { }\n"
        "public class Repository<T> : IRepository<Dictionary<string, T>>\n"
        "    where T : class, new()\n"
        "{\n"
        "}\n"
        "public struct Size { }\n"
        "public record struct Pair(int A, int B);\n"
        "public interface IShape { }\n",
    )

    assert [
        (signal.line, signal.subject, signal.related_subjects, signal.keywords)
        for signal in of_kind(evidence, DECLARATION)
    ] == [
        (
            3,
            "UsersController",
            ("ControllerBase", "IDisposable"),
            ("public", "sealed", "partial", "class"),
        ),
        (5, "Cache", (), ("private", "class")),
        (8, "Point", ("Shape", "IShape"), ("public", "record")),
        (9, "Box", (), ("public", "record", "class")),
        (10, "Extensions", (), ("internal", "static", "class")),
        (11, "Repository", ("IRepository<Dictionary<string,T>>",), ("public", "class")),
    ]
    assert evidence.signal_coverage.status is EvidenceCoverageStatus.COMPLETE


def test_a_primary_constructor_comes_before_the_base_list(tmp_path: Path) -> None:
    evidence = scan(
        tmp_path,
        "public class UserService(IUserRepository repository, int size = 10)\n"
        "    : ServiceBase(size), IUserService\n"
        "{\n"
        "}\n",
    )

    [declaration] = of_kind(evidence, DECLARATION)
    assert declaration.related_subjects == ("ServiceBase", "IUserService")


def test_every_constructor_parameter_is_a_signal(tmp_path: Path) -> None:
    evidence = scan(
        tmp_path,
        "public interface IUserService { }\n"
        "public class UsersController : ControllerBase\n"
        "{\n"
        "    public UsersController(UserService service, ILogger<UsersController> log,\n"
        "        [FromServices] IUserService users, int retries = 3) { }\n"
        "}\n",
    )

    assert [
        (signal.line, signal.subject, signal.related_subject)
        for signal in of_kind(evidence, PARAMETER)
    ] == [  # the reader orders the signals of one line by subject
        (4, "ILogger<UsersController>", "UsersController"),
        (4, "IUserService", "UsersController"),
        (4, "UserService", "UsersController"),
        (4, "int", "UsersController"),
    ]
    interface_parameters = of_kind(evidence, ArchitectureSignalKind.CONSTRUCTOR_INTERFACE_PARAMETER)
    assert [signal.subject for signal in interface_parameters] == ["IUserService"]


def test_object_creations_name_their_type_and_enclosing_type(tmp_path: Path) -> None:
    evidence = scan(
        tmp_path,
        "public class UserService\n"
        "{\n"
        "    private readonly UserRepository _repository = new UserRepository();\n"
        "    public User Create(string name)\n"
        "    {\n"
        "        var user = new User { Name = name };\n"
        "        var users = new List<User>();\n"
        '        var other = new Data.Repositories.OrderRepository("orders.txt");\n'
        "        return user;\n"
        "    }\n"
        "    private class Cache\n"
        "    {\n"
        "        private readonly Dictionary<int, User> _items = new Dictionary<int, User>();\n"
        "    }\n"
        "}\n",
        name="UserService.cs",
    )
    (tmp_path / "Program.cs").write_text(
        "var repository = new UserRepository();\n", encoding="utf-8"
    )
    evidence = LocalArchitectureEvidenceReader().scan(tmp_path)

    assert [
        (signal.file_path, signal.line, signal.subject, signal.related_subject)
        for signal in of_kind(evidence, CREATION)
    ] == [
        ("Program.cs", 1, "UserRepository", None),
        ("UserService.cs", 3, "UserRepository", "UserService"),
        ("UserService.cs", 6, "User", "UserService"),
        ("UserService.cs", 7, "List<User>", "UserService"),
        ("UserService.cs", 8, "Data.Repositories.OrderRepository", "UserService"),
        ("UserService.cs", 13, "Dictionary<int,User>", "Cache"),
    ]


def test_target_typed_anonymous_and_array_creations_name_no_class(tmp_path: Path) -> None:
    evidence = scan(
        tmp_path,
        "public class Factory\n"
        "{\n"
        "    private readonly List<int> _ids = new();\n"
        "    public object Make()\n"
        "    {\n"
        "        int[] numbers = new int[3];\n"
        '        var names = new[] { "a" };\n'
        "        return new { Id = 1 };\n"
        "    }\n"
        '    public new string ToString() => "";\n'
        "}\n",
    )

    assert of_kind(evidence, CREATION) == []
    assert evidence.signal_coverage.status is EvidenceCoverageStatus.COMPLETE


def test_comments_and_strings_give_no_layer_signals(tmp_path: Path) -> None:
    evidence = scan(
        tmp_path,
        "// public class Hidden : IHidden { }\n"
        'var text = "new UserRepository() and class Fake : IFake { }";\n'
        "/* var x = new UserService(); */\n",
    )

    assert evidence.signals == ()


def test_a_generic_constraint_is_not_a_declaration(tmp_path: Path) -> None:
    evidence = scan(
        tmp_path,
        "public class Store<T> where T : class\n{\n}\n"
        "public static class Helpers\n"
        "{\n"
        "    public static T First<T>(List<T> items) where T : class => items[0];\n"
        "}\n",
    )

    assert [signal.subject for signal in of_kind(evidence, DECLARATION)] == ["Store", "Helpers"]


def test_an_unreadable_base_list_lowers_coverage_and_gives_no_declaration(
    tmp_path: Path,
) -> None:
    evidence = scan(tmp_path, "public class Odd : (int, int) { }\n")

    assert of_kind(evidence, DECLARATION) == []
    assert evidence.signal_coverage.status is EvidenceCoverageStatus.PARTIAL
    assert [issue.kind for issue in evidence.signal_coverage.issues] == [
        EvidenceIssueKind.AMBIGUOUS_SYNTAX
    ]


def test_known_limit_an_expression_bodied_constructor_is_not_read(tmp_path: Path) -> None:
    """Kept from stage 19, documented in docs/local-architecture-evidence.md."""
    evidence = scan(
        tmp_path,
        "public interface IClock { }\n"
        "public class Timer\n"
        "{\n"
        "    private readonly IClock _clock;\n"
        "    public Timer(IClock clock) => _clock = clock;\n"
        "}\n",
    )

    assert of_kind(evidence, ArchitectureSignalKind.CONSTRUCTOR_CANDIDATE) == []
    assert of_kind(evidence, PARAMETER) == []
