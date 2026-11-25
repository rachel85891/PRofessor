"""Method signals in the architecture evidence (HW05-G5), on synthetic code."""

from pathlib import Path

import pytest
from pydantic import ValidationError

from hw_checker.adapters.architecture_evidence import LocalArchitectureEvidenceReader
from hw_checker.domain import (
    ArchitectureSignal,
    ArchitectureSignalKind,
    DeclaringKind,
    EvidenceIssueKind,
)

Method = tuple[str, str, str, tuple[str, ...], str]


def scan(tmp_path: Path, source: str):  # type: ignore[no-untyped-def]
    (tmp_path / "A.cs").write_text(source, encoding="utf-8", newline="\n")
    return LocalArchitectureEvidenceReader().scan(tmp_path)


def methods(tmp_path: Path, source: str) -> list[Method]:
    """(type, name, return type, keywords, declared in) of each method signal."""
    return [
        (
            signal.related_subject or "",
            signal.subject or "",
            signal.return_type or "",
            signal.keywords,
            signal.declared_in or "",
        )
        for signal in scan(tmp_path, source).signals
        if signal.kind is ArchitectureSignalKind.METHOD_DECLARATION
    ]


def in_class(body: str) -> str:
    return f"namespace N;\npublic class C\n{{\n{body}\n}}\n"


def test_a_plain_method_and_its_fields(tmp_path: Path) -> None:
    evidence = scan(tmp_path, in_class("    public async Task<IActionResult> Get(int id)\n    { }"))

    [signal] = [s for s in evidence.signals if s.kind is ArchitectureSignalKind.METHOD_DECLARATION]
    assert (signal.subject, signal.related_subject, signal.line) == ("Get", "C", 4)
    assert signal.keywords == ("public", "async")
    assert signal.return_type == "Task<IActionResult>"
    assert signal.declared_in is DeclaringKind.CLASS


@pytest.mark.parametrize(
    ("member", "name", "return_type", "keywords"),
    [
        ("public ValueTask Run() { }", "Run", "ValueTask", ("public",)),
        ("public void Run() { }", "Run", "void", ("public",)),
        ("public User Get() => null;", "Get", "User", ("public",)),
        ("public IEnumerable<User> List() { }", "List", "IEnumerable<User>", ("public",)),
        ("public User? Find() { }", "Find", "User?", ("public",)),
        ("public User[] All() { }", "All", "User[]", ("public",)),
        ("public List<User>? Some() { }", "Some", "List<User>?", ("public",)),
        ("public (int Id, string Name) Pair() { }", "Pair", "(int Id, string Name)", ("public",)),
        (
            "public Task<(int A, int B)> Two() { }",
            "Two",
            "Task<(int A, int B)>",
            ("public",),
        ),
        ("public Task  <  int>  Spaced() { }", "Spaced", "Task < int>", ("public",)),
        ("public Task<T> Get<T>(T x) where T : class, new() { }", "Get", "Task<T>", ("public",)),
        ('public Task Do(int a = 5, string b = "x") { }', "Do", "Task", ("public",)),
        ("public Task Many(\n int a,\n int b\n) { }", "Many", "Task", ("public",)),
        ("public async partial Task Part() { }", "Part", "Task", ("public", "async", "partial")),
        ('[HttpGet("x")] public Task Same() { }', "Same", "Task", ("public",)),
        ('[HttpGet("x")]\n[Authorize]\n    public Task Two2() { }', "Two2", "Task", ("public",)),
        ("[Foo(new int[0])] public Task Nested() { }", "Nested", "Task", ("public",)),
        ("Task Hidden() { }", "Hidden", "Task", ("private",)),
        ("protected internal Task P() { }", "P", "Task", ("protected", "internal")),
        ("public static new Task S() { }", "S", "Task", ("public", "static", "new")),
        ("public override async Task O() { }", "O", "Task", ("public", "override", "async")),
        ("public extern int E();", "E", "int", ("public", "extern")),
        (
            "public System.Threading.Tasks.Task Q() { }",
            "Q",
            "System.Threading.Tasks.Task",
            ("public",),
        ),
        ("public ref int R() => ref x;", "R", "ref int", ("public",)),
        ("public Task G() => Run(() => { return 1; });", "G", "Task", ("public",)),
        ("public abstract Task A();", "A", "Task", ("public", "abstract")),
        ("Task IRepo.Explicit() { }", "Explicit", "Task", ()),
        ("Task<User> IRepo<User>.Explicit2() { }", "Explicit2", "Task<User>", ()),
    ],
)
def test_the_listed_shapes_are_read(
    tmp_path: Path, member: str, name: str, return_type: str, keywords: tuple[str, ...]
) -> None:
    found = methods(tmp_path, in_class(member))

    assert found == [("C", name, return_type, keywords, "class")]


@pytest.mark.parametrize(
    "member",
    [
        "public C(IClock clock) { }",
        "public C(IClock clock) : base(clock) { }",
        "[Obsolete] public C() { }",
        "~C() { }",
        "static C() { }",
        "public static bool operator ==(C a, C b) { }",
        "public static implicit operator int(C a) { }",
        "public int Count { get; set; }",
        "public int Count => Compute(1);",
        "public Task<int> Total { get { return Compute(1); } }",
        "public int this[int i] => i;",
        "public event EventHandler Changed;",
        "public delegate Task Handler(int x);",
        "private int _x = Compute(1);",
        "public List<int> Items = new() { 1, 2 };",
        "public Func<int> F = () => { return 1; };",
        "public class Inner { }",
        "public record Rec(int X);",
        "public class WithPrimary(int x) : Base(x) { }",
        "public enum Color { Red }",
        "public int X { get; } = Compute(1);",
    ],
)
def test_these_members_are_not_methods(tmp_path: Path, member: str) -> None:
    found = methods(tmp_path, in_class(member))

    assert [m for m in found if m[0] == "C"] == []


def test_statements_inside_a_method_are_not_methods(tmp_path: Path) -> None:
    body = (
        "    public void Run()\n    {\n"
        "        if (a) { Foo(1); }\n        while (b) { }\n"
        "        foreach (var x in xs) { Bar(x); }\n        switch (y) { }\n"
        "        using (var s = new S()) { }\n        lock (o) { }\n"
        "        try { } catch (Exception e) { }\n"
        "        var v = new List<int>(3);\n"
        "        Task Local() { return null; }\n"
        "        Func<int> f = () => { return 1; };\n"
        "    }\n"
    )

    assert methods(tmp_path, in_class(body)) == [("C", "Run", "void", ("public",), "class")]


def test_interface_members_are_public_without_a_modifier(tmp_path: Path) -> None:
    source = (
        "namespace N;\npublic interface IRepo\n{\n"
        "    Task<User> Get();\n    User Find(int id);\n    private void Help() { }\n"
        "    static Task Make() => null;\n}\n"
    )

    assert methods(tmp_path, source) == [
        ("IRepo", "Get", "Task<User>", ("public",), "interface"),
        ("IRepo", "Find", "User", ("public",), "interface"),
        ("IRepo", "Help", "void", ("private",), "interface"),
        ("IRepo", "Make", "Task", ("public", "static"), "interface"),
    ]


def test_records_and_structs(tmp_path: Path) -> None:
    source = (
        "namespace N;\npublic record R(int X)\n{\n    public int Twice() => X * 2;\n}\n"
        "public struct S\n{\n    public readonly int Get() => 1;\n}\n"
        "public record struct P(int X)\n{\n    public int Y() => 1;\n}\n"
    )

    assert methods(tmp_path, source) == [
        ("R", "Twice", "int", ("public",), "record"),
        ("S", "Get", "int", ("public", "readonly"), "struct"),
        ("P", "Y", "int", ("public",), "record"),
    ]


def test_two_types_in_one_file_and_a_nested_type(tmp_path: Path) -> None:
    source = (
        "namespace N\n{\n    public class A\n    {\n        public Task One() { }\n"
        "        public class B\n        {\n            public Task Inner() { }\n        }\n"
        "        public Task Two() { }\n    }\n"
        "    public class D\n    {\n        public Task Three() { }\n    }\n}\n"
    )

    assert [(m[0], m[1]) for m in methods(tmp_path, source)] == [
        ("A", "One"),
        ("B", "Inner"),
        ("A", "Two"),
        ("D", "Three"),
    ]


def test_a_file_in_the_global_namespace_is_read(tmp_path: Path) -> None:
    found = methods(tmp_path, "public class C\n{\n    public Task Get() { }\n}\n")

    assert [m[1] for m in found] == ["Get"]


def test_a_file_whose_braces_cannot_be_balanced_gives_an_issue_and_no_signals(
    tmp_path: Path,
) -> None:
    evidence = scan(tmp_path, "namespace N;\npublic class C\n{\n    public Task Get() { }\n")

    assert not [s for s in evidence.signals if s.kind is ArchitectureSignalKind.METHOD_DECLARATION]
    assert EvidenceIssueKind.AMBIGUOUS_SYNTAX in {i.kind for i in evidence.signal_coverage.issues}


def test_a_file_whose_namespaces_cannot_be_read_gives_an_issue_and_no_signals(
    tmp_path: Path,
) -> None:
    evidence = scan(
        tmp_path, "namespace A;\nnamespace B;\npublic class C\n{\n  Task Get() { }\n}\n"
    )

    assert not [s for s in evidence.signals if s.kind is ArchitectureSignalKind.METHOD_DECLARATION]
    assert EvidenceIssueKind.AMBIGUOUS_SYNTAX in {i.kind for i in evidence.signal_coverage.issues}


def test_comments_and_strings_do_not_make_methods(tmp_path: Path) -> None:
    body = '    // public Task Fake() { }\n    private string s = "public Task Fake2() { }";\n'

    assert methods(tmp_path, in_class(body)) == []


def test_a_header_whose_parentheses_cannot_be_matched_is_an_issue(tmp_path: Path) -> None:
    evidence = scan(tmp_path, in_class("    public Task Get(Foo x = new() { }) { }"))

    assert not [s for s in evidence.signals if s.kind is ArchitectureSignalKind.METHOD_DECLARATION]
    assert EvidenceIssueKind.AMBIGUOUS_SYNTAX in {i.kind for i in evidence.signal_coverage.issues}


def test_a_too_long_return_type_is_an_issue_not_a_crash(tmp_path: Path) -> None:
    long_type = "List<" + "A" * 300 + ">"
    evidence = scan(tmp_path, in_class(f"    public {long_type} Get() {{ }}"))

    assert not [s for s in evidence.signals if s.kind is ArchitectureSignalKind.METHOD_DECLARATION]
    assert EvidenceIssueKind.AMBIGUOUS_SYNTAX in {i.kind for i in evidence.signal_coverage.issues}


def test_the_method_fields_are_refused_on_other_kinds() -> None:
    with pytest.raises(ValidationError, match="only a method declaration"):
        ArchitectureSignal(
            kind=ArchitectureSignalKind.TYPE_DECLARATION,
            file_path="A.cs",
            line=1,
            subject="C",
            return_type="Task",
        )
    with pytest.raises(ValidationError, match="only a method declaration"):
        ArchitectureSignal(
            kind=ArchitectureSignalKind.DB_SET_PROPERTY,
            file_path="A.cs",
            line=1,
            subject="C",
            declared_in=DeclaringKind.CLASS,
        )
    with pytest.raises(ValidationError, match="needs its return type"):
        ArchitectureSignal(
            kind=ArchitectureSignalKind.METHOD_DECLARATION, file_path="A.cs", line=1, subject="M"
        )
    with pytest.raises(ValidationError, match="only a type declaration has related subjects"):
        ArchitectureSignal(
            kind=ArchitectureSignalKind.METHOD_DECLARATION,
            file_path="A.cs",
            line=1,
            subject="M",
            return_type="Task",
            declared_in=DeclaringKind.CLASS,
            related_subjects=("X",),
        )
