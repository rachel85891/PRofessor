import pytest
from pydantic import TypeAdapter, ValidationError

from hw_checker.domain.base import HttpStatusCode, Identifier, NonBlankStr, RepoRelativePath

identifier = TypeAdapter(Identifier)
non_blank = TypeAdapter(NonBlankStr)
repo_path = TypeAdapter(RepoRelativePath)
status_code = TypeAdapter(HttpStatusCode)


def test_identifier_accepts_plain_id() -> None:
    assert identifier.validate_python("hw03-r1") == "hw03-r1"


@pytest.mark.parametrize("value", ["", " hw03", "hw03 ", "\thw03"])
def test_identifier_rejects_empty_or_padded(value: str) -> None:
    with pytest.raises(ValidationError):
        identifier.validate_python(value)


def test_non_blank_accepts_text() -> None:
    assert non_blank.validate_python(" some text ") == " some text "


@pytest.mark.parametrize("value", ["", "   ", "\n\t"])
def test_non_blank_rejects_blank(value: str) -> None:
    with pytest.raises(ValidationError):
        non_blank.validate_python(value)


@pytest.mark.parametrize("value", ["Program.cs", "src/Api/Controllers/ProductsController.cs"])
def test_repo_path_accepts_relative_forward_slash_paths(value: str) -> None:
    assert repo_path.validate_python(value) == value


@pytest.mark.parametrize(
    ("value", "reason"),
    [
        ("/etc/passwd", "absolute"),
        ("C:\\repo\\Program.cs", "forward slashes"),
        ("C:/repo/Program.cs", "drive letter"),
        ("src\\Program.cs", "forward slashes"),
        ("../outside.cs", "'..'"),
        ("src/../../outside.cs", "'..'"),
        ("  ", "blank"),
    ],
)
def test_repo_path_rejects_unsafe_paths(value: str, reason: str) -> None:
    with pytest.raises(ValidationError, match=reason):
        repo_path.validate_python(value)


@pytest.mark.parametrize("value", [100, 200, 599])
def test_status_code_accepts_valid_range(value: int) -> None:
    assert status_code.validate_python(value) == value


@pytest.mark.parametrize("value", [99, 600, 0])
def test_status_code_rejects_out_of_range(value: int) -> None:
    with pytest.raises(ValidationError):
        status_code.validate_python(value)
