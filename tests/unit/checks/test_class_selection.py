"""The generic class selector (HW05-G3) on made-up code: names, bases, a DbContext's
entities, their union, and the params it refuses."""

from pathlib import Path

import pytest
from pydantic import ValidationError

from hw_checker.adapters.architecture_evidence import LocalArchitectureEvidenceReader
from hw_checker.checks.deterministic.class_selection import (
    ClassSelector,
    Selection,
    select_classes,
)
from hw_checker.checks.deterministic.layer_support import Types

CODE = (
    "namespace Shop;\n"
    "public class User { }\n"
    "public class Order { }\n"
    "public record Tag;\n"
    "public class UserService : IUserService { }\n"
    "public class ShopContext : IdentityDbContext<User>\n"
    "{\n"
    "    public DbSet<Order> Orders { get; set; }\n"
    "    public DbSet<Tag> Tags { get; set; }\n"
    "}\n"
    "public class Other : DbContext\n"
    "{\n"
    "    public DbSet<Audit> Audits { get; set; }\n"
    "}\n"
    "public class Audit { }\n"
)


def select(
    tmp_path: Path, selector: ClassSelector, code: str = CODE, second_file: str = ""
) -> Selection:
    (tmp_path / "Code.cs").write_text(code, encoding="utf-8")
    if second_file:
        (tmp_path / "More.cs").write_text(second_file, encoding="utf-8")
    evidence = LocalArchitectureEvidenceReader().scan(tmp_path)
    return select_classes(Types(evidence), selector)


def names(selection: Selection) -> list[str]:
    return [declaration.subject or "" for declaration in selection.classes]


def test_names_select_by_the_class_name(tmp_path: Path) -> None:
    selection = select(tmp_path, ClassSelector(names=("^User",)))

    assert names(selection) == ["User", "UserService"]
    assert selection.ambiguous == ()


def test_bases_select_by_a_simple_name_in_the_base_list(tmp_path: Path) -> None:
    selection = select(tmp_path, ClassSelector(bases=("DbContext$",)))

    assert names(selection) == ["ShopContext", "Other"]


def test_entities_of_bases_select_the_db_sets_types_of_such_a_class(tmp_path: Path) -> None:
    selection = select(tmp_path, ClassSelector(entities_of_bases=("^IdentityDbContext$",)))

    assert names(selection) == ["Order"]


def test_entities_of_bases_select_only_classes_not_records(tmp_path: Path) -> None:
    selection = select(tmp_path, ClassSelector(entities_of_bases=("DbContext",)))

    assert names(selection) == ["Order", "Audit"]


def test_the_selection_is_the_union_of_what_is_given(tmp_path: Path) -> None:
    selection = select(
        tmp_path,
        ClassSelector(
            names=("^User$",), bases=("^IUserService$",), entities_of_bases=("^Identity",)
        ),
    )

    assert sorted(names(selection)) == ["Order", "User", "UserService"]


def test_a_class_matched_twice_is_selected_once(tmp_path: Path) -> None:
    selection = select(tmp_path, ClassSelector(names=("^Order$",), entities_of_bases=("Identity",)))

    assert names(selection) == ["Order"]


def test_a_name_declared_more_than_once_is_left_out_and_named(tmp_path: Path) -> None:
    selection = select(
        tmp_path,
        ClassSelector(names=("^User$", "^Audit$")),
        second_file="namespace Other { public class User { } }\n",
    )

    assert names(selection) == ["Audit"]
    assert selection.ambiguous == ("User",)


def test_the_parts_of_a_partial_class_are_not_a_duplicate(tmp_path: Path) -> None:
    code = "public partial class User { }\npublic partial class User { }\n"

    selection = select(tmp_path, ClassSelector(names=("^User$",)), code)

    assert names(selection) == ["User", "User"]
    assert selection.ambiguous == ()


def test_nothing_matching_selects_nothing(tmp_path: Path) -> None:
    selection = select(tmp_path, ClassSelector(names=("^Missing$",)))

    assert selection == Selection((), ())


def test_a_selector_with_nothing_given_is_refused() -> None:
    with pytest.raises(ValidationError, match="give names, bases or entities_of_bases"):
        ClassSelector()


@pytest.mark.parametrize("field", ["names", "bases", "entities_of_bases"])
def test_a_bad_regex_is_refused(field: str) -> None:
    with pytest.raises(ValidationError, match="not a valid regular expression"):
        ClassSelector.model_validate({field: ["("]})


def test_an_unknown_field_is_refused() -> None:
    with pytest.raises(ValidationError, match="Extra inputs"):
        ClassSelector.model_validate({"names": ["A"], "base": ["B"]})
