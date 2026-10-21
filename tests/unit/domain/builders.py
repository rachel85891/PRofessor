"""Small builders for valid domain objects; tests override only what they check."""

from typing import Any

from hw_checker.domain import (
    Evidence,
    Finding,
    FindingSource,
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
        "requirement_id": "hw03-r2",
        "assignment_id": "hw03",
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


def evidence(**overrides: Any) -> Evidence:
    values: dict[str, Any] = {
        "file_path": "Controllers/ProductsController.cs",
        "line_start": 12,
        "line_end": 14,
        "quote": "return Ok(product);",
    }
    return Evidence.model_validate(values | overrides)


def finding(**overrides: Any) -> Finding:
    values: dict[str, Any] = {
        "id": "f-1",
        "requirement_id": "hw03-r2",
        "assignment_id": "hw03",
        "severity": Severity.MAJOR,
        "message_he": "הפעולה מחזירה 200 עם null",
        "message_en": "The action returns 200 with null.",
        "evidence": evidence(),
        "source": FindingSource.LLM,
        "confidence": 0.8,
    }
    return Finding.model_validate(values | overrides)
