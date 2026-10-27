from pathlib import Path

import pytest

from hw_checker.checks.deterministic.naming import (
    NoParams,
    Violation,
    camel_case_locals,
    camel_suggestion,
    controller_suffix,
    controller_violations,
    correct_type_name,
    file_matches_type,
    file_violations,
    is_camel_case,
    is_pascal_case,
    local_violations,
    method_violations,
    pascal_case_methods,
    pascal_case_properties,
    pascal_case_types,
    pascal_suggestion,
    property_violations,
    type_violations,
)
from hw_checker.checks.registry import CheckFunction
from hw_checker.domain import (
    Assignment,
    CheckInput,
    CodeFacts,
    ExecutionResult,
    FailureKind,
    FileFact,
    Finding,
    MemberFact,
    NotEvaluatedReason,
    Requirement,
    RequirementKind,
    Severity,
    StepStatus,
    TypeFact,
    TypeKind,
)

REQUIREMENT = Requirement(
    id="gen-naming",
    title="Naming",
    severity=Severity.MINOR,
    kind=RequirementKind.CUSTOM,
    check="pascal_case_properties",
)
ASSIGNMENT = Assignment(id="_general", order=0, title="General", requirements=(REQUIREMENT,))


def run(
    function: CheckFunction, facts: CodeFacts | None, repo: Path = Path("repo")
) -> tuple[Finding, ...] | NotEvaluatedReason:
    execution = ExecutionResult(
        build_status=StepStatus.SUCCEEDED, run_status=StepStatus.SUCCEEDED, code_facts=facts
    )
    check_input = CheckInput(
        repo_dir=repo, assignment=ASSIGNMENT, requirement=REQUIREMENT, execution=execution
    )
    return function(check_input, NoParams())


def only_finding(outcome: tuple[Finding, ...] | NotEvaluatedReason) -> Finding:
    assert isinstance(outcome, tuple)
    (finding,) = outcome
    return finding


def member(
    name: str, line: int = 5, file: str = "Models/User.cs", owner: str | None = "User"
) -> MemberFact:
    return MemberFact(name=name, declaring_type=owner, file_path=file, line=line)


def type_fact(
    name: str, file: str = "Models/User.cs", line: int = 3, controller: bool = False
) -> TypeFact:
    return TypeFact(
        name=name, kind=TypeKind.CLASS, file_path=file, line=line, is_controller=controller
    )


# ---------------------------------------------------------------- the rules


@pytest.mark.parametrize(
    ("name", "pascal"),
    [
        ("Email", True),
        ("ID", True),
        ("URL", True),
        ("UserID", True),
        ("email", False),
        ("first_Name", False),
        ("First_name", False),
        ("_Name", False),
        ("l_email", False),
    ],
)
def test_pascal_case(name: str, pascal: bool) -> None:
    assert is_pascal_case(name) is pascal


@pytest.mark.parametrize(
    ("name", "camel"),
    [("filePath", True), ("_cache", True), ("i", True), ("FilePath", False), ("ID", False)],
)
def test_camel_case(name: str, camel: bool) -> None:
    assert is_camel_case(name) is camel


@pytest.mark.parametrize(
    ("name", "suggestion"),
    [
        ("email", "Email"),
        ("firstName", "FirstName"),
        ("First_name", "FirstName"),
        ("l_email", "LEmail"),
        ("first__name", "FirstName"),
        ("_", None),
        ("_1x", None),
    ],
)
def test_pascal_suggestion(name: str, suggestion: str | None) -> None:
    assert pascal_suggestion(name) == suggestion


@pytest.mark.parametrize(
    ("name", "suggestion"),
    [("FilePath", "filePath"), ("File_Path", "filePath"), ("ID", None), ("URL", None)],
)
def test_camel_suggestion(name: str, suggestion: str | None) -> None:
    assert camel_suggestion(name) == suggestion


# ---------------------------------------------------------------- the checks


def test_clean_code_gives_no_finding() -> None:
    facts = CodeFacts(
        types=(
            type_fact("User"),
            type_fact("UsersController", "Controllers/UsersController.cs", controller=True),
        ),
        properties=(member("Id"), member("ID"), member("Email")),
        methods=(member("GetAll", owner="UsersController"), member("Main", owner="Program")),
        locals=(member("users"), member("_cache"), member("i")),
        files=(
            FileFact(file_path="Models/User.cs", types=("User",)),
            FileFact(file_path="Controllers/UsersController.cs", types=("UsersController",)),
        ),
    )

    for function in (
        pascal_case_types,
        pascal_case_properties,
        pascal_case_methods,
        camel_case_locals,
        file_matches_type,
        controller_suffix,
    ):
        assert run(function, facts) == (), function


def test_one_property_violation_quotes_it_with_evidence(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    (repo / "Models").mkdir(parents=True)
    (repo / "Models" / "User.cs").write_text(
        "namespace Shop;\npublic class User\n{\n    public int Id { get; set; }\n"
        '    public string email { get; set; } = "";\n}\n',
        encoding="utf-8",
    )

    finding = only_finding(
        run(pascal_case_properties, CodeFacts(properties=(member("email"),)), repo)
    )

    assert finding.message_en == (
        "In C#, public properties are written in PascalCase. "
        "For example: email → Email (Models/User.cs, line 5). Total: 1."
    )
    assert "email → Email (Models/User.cs, שורה 5)" in finding.message_he
    assert finding.severity is Severity.MINOR
    assert finding.evidence is not None
    assert (finding.evidence.file_path, finding.evidence.line_start) == ("Models/User.cs", 5)
    assert finding.evidence.quote == 'public string email { get; set; } = "";'


def test_more_than_five_violations_quote_five_and_count_all() -> None:
    names = ["email", "firstName", "First_name", "l_email", "phone", "city", "zip"]
    facts = CodeFacts(properties=tuple(member(name, line) for line, name in enumerate(names, 4)))

    finding = only_finding(run(pascal_case_properties, facts))

    assert finding.message_en.endswith("Total: 7.")
    assert finding.message_en.count("(Models/User.cs, line") == 5
    assert "city" not in finding.message_en and "zip" not in finding.message_en
    assert "First_name → FirstName" in finding.message_en
    assert finding.evidence is None  # the repo folder does not exist here


def test_types_methods_and_locals() -> None:
    facts = CodeFacts(
        types=(type_fact("loguser"), type_fact("userLogin")),
        methods=(member("getAll", 12, owner="UsersController"),),
        locals=(member("FilePath", 20), member("URL", 21)),
    )

    assert only_finding(run(pascal_case_types, facts)).message_en == (
        "In C#, class and type names are written in PascalCase (a capital first letter, no "
        "underscores). For example: loguser → Loguser (Models/User.cs, line 3); "
        "userLogin → UserLogin (Models/User.cs, line 3). Total: 2."
    )
    assert (
        "getAll → GetAll (Models/User.cs, line 12)"
        in only_finding(run(pascal_case_methods, facts)).message_en
    )
    locals_message = only_finding(run(camel_case_locals, facts)).message_en
    assert "FilePath → filePath (Models/User.cs, line 20)" in locals_message
    assert "URL (Models/User.cs, line 21)" in locals_message  # no sensible suggestion
    assert locals_message.endswith("Total: 2.")


def test_methods_skip_main_and_top_level_program_functions() -> None:
    facts = CodeFacts(
        methods=(
            member("Main", owner="Program", file="Program.cs"),
            member("configure", owner=None, file="Program.cs"),
            member("helper", owner="Startup", file="Startup.cs"),
        )
    )

    finding = only_finding(run(pascal_case_methods, facts))

    assert "helper → Helper (Startup.cs" in finding.message_en
    assert "configure" not in finding.message_en and finding.message_en.endswith("Total: 1.")


def test_file_names_match_their_single_type() -> None:
    facts = CodeFacts(
        types=(
            type_fact("UsersController", "Controllers/Users.cs", 7, controller=True),
            type_fact("loguser", "Models/loguser.cs", 4),
            type_fact("LoginDetails", "Models/loginDetails.cs", 2),
            type_fact("User", "Models/User.cs"),
            type_fact("A", "Models/Pair.cs"),
            type_fact("B", "Models/Pair.cs"),
        ),
        files=(
            FileFact(file_path="Controllers/Users.cs", types=("UsersController",)),
            FileFact(file_path="Models/loguser.cs", types=("loguser",)),
            FileFact(file_path="Models/loginDetails.cs", types=("LoginDetails",)),
            FileFact(file_path="Models/User.cs", types=("User",)),
            FileFact(file_path="Models/Pair.cs", types=("A", "B")),
            FileFact(file_path="Program.cs", types=("Program",)),
            FileFact(file_path="Helpers.cs", types=()),
        ),
    )

    finding = only_finding(run(file_matches_type, facts))

    assert finding.message_en == (
        "In C#, each file is named after the type it declares, in PascalCase. For example: "
        "Users.cs → UsersController.cs (Controllers/Users.cs, line 7); "
        "loguser.cs → Loguser.cs (Models/loguser.cs, line 4); "
        "loginDetails.cs → LoginDetails.cs (Models/loginDetails.cs, line 2). Total: 3."
    )


def test_controllers_end_with_controller() -> None:
    facts = CodeFacts(
        types=(
            type_fact("User", "Controllers/User.cs", 8, controller=True),
            type_fact("usersController", "Controllers/Users.cs", 6, controller=True),
            type_fact("Account", "Models/Account.cs"),
        )
    )

    finding = only_finding(run(controller_suffix, facts))

    assert finding.message_en == (
        "In ASP.NET Core, controller class names end with Controller. For example: "
        "User → UserController (Controllers/User.cs, line 8). Total: 1."
    )


def test_ids_are_stable_and_follow_the_violations() -> None:
    first = only_finding(run(pascal_case_properties, CodeFacts(properties=(member("email"),))))
    again = only_finding(run(pascal_case_properties, CodeFacts(properties=(member("email"),))))
    other = only_finding(run(pascal_case_properties, CodeFacts(properties=(member("phone"),))))

    assert first.id == again.id != other.id


@pytest.mark.parametrize(
    ("execution", "reason"),
    [
        (None, NotEvaluatedReason.NO_EXECUTION),
        (
            ExecutionResult(
                prepare_status=StepStatus.FAILED,
                build_status=StepStatus.SKIPPED,
                run_status=StepStatus.SKIPPED,
                failure_kind=FailureKind.PROJECT_NOT_FOUND,
            ),
            NotEvaluatedReason.ENVIRONMENT_ISSUE,
        ),
        (
            ExecutionResult(build_status=StepStatus.SUCCEEDED, run_status=StepStatus.SUCCEEDED),
            NotEvaluatedReason.ENVIRONMENT_ISSUE,
        ),
    ],
)
def test_without_code_facts_the_requirement_is_not_evaluated(
    execution: ExecutionResult | None, reason: NotEvaluatedReason
) -> None:
    check_input = CheckInput(
        repo_dir=Path("repo"), assignment=ASSIGNMENT, requirement=REQUIREMENT, execution=execution
    )

    for function in (
        pascal_case_types,
        pascal_case_properties,
        pascal_case_methods,
        camel_case_locals,
        file_matches_type,
        controller_suffix,
    ):
        assert function(check_input, NoParams()) is reason


# ---------------------------------------------------------------- files and correct type names


def one_type_file(type_name: str, file_path: str, controller: bool) -> CodeFacts:
    return CodeFacts(
        types=(type_fact(type_name, file_path, 6, controller=controller),),
        files=(FileFact(file_path=file_path, types=(type_name,)),),
    )


def test_a_wrongly_named_class_in_a_correctly_named_file_is_reported_once() -> None:
    facts = one_type_file("usersController", "Controllers/UsersController.cs", controller=True)

    assert run(file_matches_type, facts) == ()
    assert (
        "usersController → UsersController"
        in only_finding(run(pascal_case_types, facts)).message_en
    )
    assert run(controller_suffix, facts) == ()


def test_a_controller_without_the_suffix_in_a_file_with_it_is_reported_once() -> None:
    facts = one_type_file("User", "Controllers/UserController.cs", controller=True)

    assert run(file_matches_type, facts) == ()
    assert (
        "User → UserController (Controllers/UserController.cs, line 6)"
        in only_finding(run(controller_suffix, facts)).message_en
    )


def test_a_file_named_after_neither_name_suggests_the_correct_one() -> None:
    controller = one_type_file("UsersController", "Controllers/Users.cs", controller=True)
    lowercase = one_type_file("loguser", "Models/loguser.cs", controller=False)
    suffixless = one_type_file("User", "Controllers/User.cs", controller=True)

    assert (
        "Users.cs → UsersController.cs"
        in only_finding(run(file_matches_type, controller)).message_en
    )
    assert "loguser.cs → Loguser.cs" in only_finding(run(file_matches_type, lowercase)).message_en
    assert (
        "User.cs → UserController.cs" in only_finding(run(file_matches_type, suffixless)).message_en
    )


@pytest.mark.parametrize(
    ("name", "controller", "correct"),
    [
        ("User", False, "User"),
        ("loguser", False, "Loguser"),
        ("User", True, "UserController"),
        ("usersController", True, "UsersController"),
        ("first_name", False, "FirstName"),
        ("_", False, None),
    ],
)
def test_correct_type_name(name: str, controller: bool, correct: str | None) -> None:
    assert correct_type_name(name, controller) == correct


BROAD = CodeFacts(
    types=(
        type_fact("usersController", "Controllers/UsersController.cs", controller=True),
        type_fact("User", "Controllers/UserController.cs", controller=True),
        type_fact("Admin", "Controllers/Admin.cs", controller=True),
        type_fact("loguser", "Models/loguser.cs"),
        type_fact("ID", "Models/ID.cs"),
        type_fact("_", "Models/_.cs"),
        type_fact("Order", "Models/order.cs"),
    ),
    properties=(member("email"), member("ID"), member("first_name"), member("_x")),
    methods=(member("getAll"), member("Main", owner="Program"), member("_")),
    locals=(member("FilePath"), member("URL"), member("_cache"), member("x")),
    files=tuple(
        FileFact(file_path=path, types=(name,))
        for name, path in (
            ("usersController", "Controllers/UsersController.cs"),
            ("User", "Controllers/UserController.cs"),
            ("Admin", "Controllers/Admin.cs"),
            ("loguser", "Models/loguser.cs"),
            ("ID", "Models/ID.cs"),
            ("_", "Models/_.cs"),
            ("Order", "Models/order.cs"),
        )
    ),
)


def test_no_check_suggests_the_name_it_already_has() -> None:
    finders = (
        type_violations,
        property_violations,
        method_violations,
        local_violations,
        file_violations,
        controller_violations,
    )
    violations: list[Violation] = [v for find in finders for v in find(BROAD)]

    assert violations, "the broad sample must contain violations"
    assert all(v.suggestion != v.name for v in violations), [
        v for v in violations if v.suggestion == v.name
    ]
    for function in (
        pascal_case_types,
        pascal_case_properties,
        pascal_case_methods,
        camel_case_locals,
        file_matches_type,
        controller_suffix,
    ):
        outcome = run(function, BROAD)
        assert isinstance(outcome, tuple)
        for finding in outcome:
            for example in finding.message_en.split("For example: ", 1)[1].split("; "):
                if " → " in example:
                    current, suggested = example.split(" (")[0].split(" → ")
                    assert current != suggested, finding.message_en
