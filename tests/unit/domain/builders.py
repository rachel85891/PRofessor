"""Small builders for valid domain objects; tests override only what they check."""

from typing import Any

from hw_checker.domain import (
    HttpMethod,
    HttpProbe,
    RepoTarget,
    Requirement,
    RequirementKind,
    Severity,
    Student,
)


def student(**overrides: Any) -> Student:
    values: dict[str, Any] = {"name": "Dana Levi", "github_url": "https://github.com/dana/api"}
    return Student.model_validate(values | overrides)


def repo_target(**overrides: Any) -> RepoTarget:
    values: dict[str, Any] = {
        "repo_id": "dana-api",
        "owner": "dana",
        "name": "api",
        "url": "https://github.com/dana/api",
        "student": student(),
        "pair_id": "pair-1",
    }
    return RepoTarget.model_validate(values | overrides)


def probe(**overrides: Any) -> HttpProbe:
    values: dict[str, Any] = {
        "method": HttpMethod.GET,
        "path": "/api/products",
        "expected_status": 200,
    }
    return HttpProbe.model_validate(values | overrides)


def requirement(**overrides: Any) -> Requirement:
    values: dict[str, Any] = {
        "id": "hw03-r2",
        "title": "GET by id returns NotFound when product is missing",
        "severity": Severity.MAJOR,
        "kind": RequirementKind.CODE,
    }
    return Requirement.model_validate(values | overrides)
