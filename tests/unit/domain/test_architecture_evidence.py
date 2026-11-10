import pytest
from pydantic import ValidationError

from hw_checker.domain import ArchitectureSignal, ArchitectureSignalKind, ServiceLifetime


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
