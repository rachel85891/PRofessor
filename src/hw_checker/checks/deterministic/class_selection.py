"""Choosing classes by data: by name, by base list, or as a `DbContext`'s entities.

`ClassSelector` is a params model any check can embed (ADR 0023: which classes a rule is
about is the requirement's data, not code). The selection is the union of what is given. A
class name declared more than once is never guessed: it is left out of the selection and
named, so the finding can say so. `LeftOut` is the same mechanism, read from the layer
checks' `exclude`, `exclude_bases` and `exclude_db_entities`.
"""

import re
from dataclasses import dataclass
from typing import Self

from pydantic import BaseModel, ConfigDict, model_validator

from hw_checker.checks.deterministic.layer_support import ClassesLeftOutParams, Types, subject
from hw_checker.checks.deterministic.layers import Regex
from hw_checker.domain import ArchitectureSignal


class ClassSelector(BaseModel):
    """`names`: patterns on the class name; `bases`: patterns on the simple names in the
    class's base list (`IdentityDbContext<User>` is `IdentityDbContext`);
    `entities_of_bases`: patterns on base names, selecting the types named as `T` in a
    `DbSet<T>` property of a class with a matching base (a `DbContext`'s entities). All are
    searched, case-sensitive. The selection is the union; at least one is required."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    names: tuple[Regex, ...] = ()
    bases: tuple[Regex, ...] = ()
    entities_of_bases: tuple[Regex, ...] = ()

    @model_validator(mode="after")
    def _selects_something(self) -> Self:
        if not (self.names or self.bases or self.entities_of_bases):
            raise ValueError("give names, bases or entities_of_bases")
        return self


class ClassMatcher:
    """Whether a class name is matched by name patterns, base patterns, or as an entity of a
    class whose base matches `entities_of_bases`."""

    def __init__(
        self,
        types: Types,
        *,
        names: tuple[str, ...] = (),
        bases: tuple[str, ...] = (),
        entities_of_bases: tuple[str, ...] = (),
    ) -> None:
        self._names = [re.compile(pattern) for pattern in names]
        self._bases = [re.compile(pattern) for pattern in bases]
        self._types = types
        entity_bases = [re.compile(pattern) for pattern in entities_of_bases]
        self._entities = frozenset(
            entity
            for entity, context in types.db_sets
            if context is not None and self._has_base(context, entity_bases)
        )

    def __call__(self, name: str) -> bool:
        if any(pattern.search(name) for pattern in self._names):
            return True
        return self._has_base(name, self._bases) or name in self._entities

    def _has_base(self, name: str, patterns: list[re.Pattern[str]]) -> bool:
        bases = self._types.bases_of(name) if patterns else ()
        return any(pattern.search(base) for pattern in patterns for base in bases)


class LeftOut(ClassMatcher):
    """Whether a class is left out by the params' `exclude`, `exclude_bases` and
    `exclude_db_entities`."""

    def __init__(self, params: ClassesLeftOutParams, types: Types) -> None:
        super().__init__(
            types,
            names=params.exclude,
            bases=params.exclude_bases,
            entities_of_bases=params.exclude_bases if params.exclude_db_entities else (),
        )


@dataclass(frozen=True)
class Selection:
    """The classes a selector chose, and the chosen names left out for being declared more
    than once."""

    classes: tuple[ArchitectureSignal, ...]
    ambiguous: tuple[str, ...]


def select_classes(types: Types, selector: ClassSelector) -> Selection:
    """The class declarations (not records) of every project that `selector` chooses."""
    matcher = ClassMatcher(
        types,
        names=selector.names,
        bases=selector.bases,
        entities_of_bases=selector.entities_of_bases,
    )
    chosen: list[ArchitectureSignal] = []
    ambiguous: list[str] = []
    for declaration in types.all_classes():
        name = subject(declaration)
        if not matcher(name):
            continue
        if types.declared_more_than_once(name):
            ambiguous.append(name)
        else:
            chosen.append(declaration)
    return Selection(tuple(chosen), tuple(dict.fromkeys(ambiguous)))
