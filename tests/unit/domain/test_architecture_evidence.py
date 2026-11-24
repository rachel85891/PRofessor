import pytest
from pydantic import ValidationError

from hw_checker.domain import (
    ArchitectureSignal,
    ArchitectureSignalKind,
    ProjectEvidence,
    ServiceLifetime,
)


def test_registration_may_omit_its_types_but_not_its_method() -> None:
    signal = ArchitectureSignal(
        kind=ArchitectureSignalKind.REGISTRATION_CANDIDATE,
        file_path="Program.cs",
        line=1,
        registration_method="AddScoped",
        lifetime=ServiceLifetime.SCOPED,
    )
    assert signal.subject is None

    with pytest.raises(ValidationError, match="method and lifetime"):
        ArchitectureSignal(
            kind=ArchitectureSignalKind.REGISTRATION_CANDIDATE,
            file_path="Program.cs",
            line=1,
            subject="IClock",
        )


def test_other_signals_need_a_subject_and_no_registration_fields() -> None:
    with pytest.raises(ValidationError, match="needs a subject"):
        ArchitectureSignal(
            kind=ArchitectureSignalKind.CONSTRUCTOR_CANDIDATE, file_path="Timer.cs", line=1
        )
    with pytest.raises(ValidationError, match="no registration fields"):
        ArchitectureSignal(
            kind=ArchitectureSignalKind.INTERFACE_DECLARATION,
            file_path="IClock.cs",
            line=1,
            subject="IClock",
            lifetime=ServiceLifetime.SCOPED,
        )


def test_only_a_type_declaration_has_a_namespace() -> None:
    declared = ArchitectureSignal(
        kind=ArchitectureSignalKind.TYPE_DECLARATION,
        file_path="Entities/User.cs",
        line=3,
        subject="User",
        namespace="Shop.Entities",
    )
    assert declared.namespace == "Shop.Entities"

    with pytest.raises(ValidationError, match="only a type declaration has a namespace"):
        ArchitectureSignal(
            kind=ArchitectureSignalKind.DB_SET_PROPERTY,
            file_path="Data/Context.cs",
            line=3,
            subject="User",
            name="Users",
            namespace="Shop",
        )


def test_a_project_has_no_root_namespace_by_default() -> None:
    assert ProjectEvidence(file_path="A/A.csproj").root_namespace is None


def test_only_a_db_set_property_has_a_name() -> None:
    named = ArchitectureSignal(
        kind=ArchitectureSignalKind.DB_SET_PROPERTY,
        file_path="Data/Context.cs",
        line=3,
        subject="User",
        related_subject="ShopContext",
        name="Users",
    )
    assert named.name == "Users"

    with pytest.raises(ValidationError, match="only a db_set_property signal has a name"):
        ArchitectureSignal(
            kind=ArchitectureSignalKind.TYPE_DECLARATION,
            file_path="Users.cs",
            line=1,
            subject="User",
            name="Users",
        )
