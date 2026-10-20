import pytest

from hw_checker.config.errors import ConfigurationError
from hw_checker.domain import errors
from hw_checker.domain.errors import HwCheckerError

DOMAIN_ERRORS = [
    errors.RosterError,
    errors.RepoUnavailableError,
    errors.GitOperationError,
    errors.BuildError,
    errors.ExecutionEnvironmentError,
    errors.LLMError,
    errors.PublishError,
    errors.InvalidAssignmentError,
    errors.StorageError,
    errors.NotFoundError,
]


@pytest.mark.parametrize("error_type", [*DOMAIN_ERRORS, ConfigurationError])
def test_every_error_is_a_hw_checker_error(error_type: type[Exception]) -> None:
    assert issubclass(error_type, HwCheckerError)


def test_hw_checker_error_is_an_exception() -> None:
    assert issubclass(HwCheckerError, Exception)


def test_execution_environment_error_does_not_shadow_builtin() -> None:
    assert errors.ExecutionEnvironmentError.__name__ != EnvironmentError.__name__
    assert not issubclass(errors.ExecutionEnvironmentError, OSError)


def test_configuration_error_can_be_caught_as_hw_checker_error() -> None:
    with pytest.raises(HwCheckerError, match="bad value"):
        raise ConfigurationError("bad value")
