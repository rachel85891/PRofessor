"""Check registry: deterministic checks declared with `@check` and collected explicitly.

The decorator only marks a function; nothing is registered globally. A `CheckRegistry` is
built from an explicit collection of marked functions, so two registries never share state.
"""

import types
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Union, get_args, get_origin

from pydantic import BaseModel, ConfigDict, ValidationError
from pydantic.fields import FieldInfo
from pydantic_core import ErrorDetails

from hw_checker.domain import (
    Assignment,
    CheckDescription,
    CheckParamDescription,
    ExecutionResult,
    Finding,
    ParamValue,
    Requirement,
)
from hw_checker.domain.errors import CheckRegistrationError, NotFoundError

_SPEC_ATTRIBUTE = "__hw_checker_check__"

_TYPE_NAMES: dict[object, str] = {str: "text", int: "integer", float: "number", bool: "true/false"}


class CheckInput(BaseModel):
    """What a check receives. Provisional: stage 8 finalizes it.

    Structure checks run without an execution result, so `execution` is optional.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    repo_dir: Path
    assignment: Assignment
    requirement: Requirement
    execution: ExecutionResult | None = None


CheckFunction = Callable[..., tuple[Finding, ...]]
"""A check: called with a `CheckInput` and its validated params model, returns findings.

Provisional: stage 8 finalizes the signature.
"""


@dataclass(frozen=True)
class CheckSpec:
    """Metadata attached to a check function by `@check`."""

    name: str
    params_model: type[BaseModel]
    description: str


def check[F: CheckFunction](
    name: str, params_model: type[BaseModel], description: str
) -> Callable[[F], F]:
    """Mark a function as the check `name`; return it unchanged. Registers nothing."""

    def mark(function: F) -> F:
        setattr(function, _SPEC_ATTRIBUTE, CheckSpec(name, params_model, description))
        return function

    return mark


@dataclass(frozen=True)
class RegisteredCheck:
    """A check function together with its metadata."""

    spec: CheckSpec
    function: CheckFunction


class CheckRegistry:
    """The checks available in a run, built from an explicit list. Implements `CheckCatalog`."""

    def __init__(self, checks: Iterable[CheckFunction]) -> None:
        """Collect the marked `checks`.

        Raises:
            CheckRegistrationError: a function is not marked with `@check`, two checks share
                a name, or a params model is not frozen with `extra="forbid"`.
        """
        self._checks: dict[str, RegisteredCheck] = {}
        for function in checks:
            spec = _spec_of(function)
            if spec.name in self._checks:
                raise CheckRegistrationError(f"two checks are named '{spec.name}'")
            _require_strict_model(spec)
            self._checks[spec.name] = RegisteredCheck(spec=spec, function=function)

    def get(self, name: str) -> RegisteredCheck:
        """Return the check named `name`.

        Raises:
            NotFoundError: no check has that name.
        """
        registered = self._checks.get(name)
        if registered is None:
            raise NotFoundError(f"unknown check '{name}'; {self._known_names()}")
        return registered

    def check_names(self) -> frozenset[str]:
        """Return the names of all registered checks."""
        return frozenset(self._checks)

    def validate_params(self, check_name: str, params: Mapping[str, ParamValue]) -> tuple[str, ...]:
        """Return plain-language problems with `params` for `check_name`; empty if valid."""
        registered = self._checks.get(check_name)
        if registered is None:
            return (f"unknown check '{check_name}'; {self._known_names()}",)
        try:
            registered.spec.params_model.model_validate(dict(params))
        except ValidationError as error:
            return tuple(_describe_param_error(item) for item in error.errors())
        return ()

    def describe(self) -> tuple[CheckDescription, ...]:
        """Describe every registered check and its params, ordered by name."""
        return tuple(
            CheckDescription(
                name=name,
                description=registered.spec.description,
                params=tuple(
                    _describe_field(field_name, field)
                    for field_name, field in registered.spec.params_model.model_fields.items()
                ),
            )
            for name, registered in sorted(self._checks.items())
        )

    def _known_names(self) -> str:
        names = ", ".join(sorted(self._checks)) or "none"
        return f"registered checks: {names}"


def _spec_of(function: CheckFunction) -> CheckSpec:
    spec = getattr(function, _SPEC_ATTRIBUTE, None)
    if not isinstance(spec, CheckSpec):
        name = getattr(function, "__qualname__", repr(function))
        raise CheckRegistrationError(f"{name} is not marked with @check")
    return spec


def _require_strict_model(spec: CheckSpec) -> None:
    config = spec.params_model.model_config
    if config.get("extra") != "forbid" or not config.get("frozen"):
        raise CheckRegistrationError(
            f"check '{spec.name}': params model {spec.params_model.__name__} must be frozen "
            'with extra="forbid"'
        )


def _describe_param_error(item: ErrorDetails) -> str:
    location = ".".join(str(part) for part in item["loc"]) or "params"
    if item["type"] == "extra_forbidden":
        return f"param '{location}': unknown param; check the spelling"
    if item["type"] == "missing":
        return f"param '{location}': this required param is missing"
    return f"param '{location}': {item['msg']}"


def _describe_field(name: str, field: FieldInfo) -> CheckParamDescription:
    required = field.is_required()
    default = None if required else field.get_default(call_default_factory=True)
    return CheckParamDescription(
        name=name,
        type=_type_name(field.annotation),
        required=required,
        default=default if _is_param_value(default) else None,
    )


def _type_name(annotation: object) -> str:
    if annotation in _TYPE_NAMES:
        return _TYPE_NAMES[annotation]
    origin = get_origin(annotation)
    arguments = [argument for argument in get_args(annotation) if argument is not type(None)]
    if origin in (tuple, list) and arguments:
        return f"list of {_type_name(arguments[0])}"
    if origin in (Union, types.UnionType) and len(arguments) == 1:
        return _type_name(arguments[0])
    return getattr(annotation, "__name__", str(annotation))


def _is_param_value(value: object) -> bool:
    scalar = (bool, int, float, str)
    if isinstance(value, scalar):
        return True
    return isinstance(value, tuple) and all(isinstance(item, scalar) for item in value)
