"""VM values (docs/bytecode.md GC section). Immediates and heap objects."""

from __future__ import annotations

from dataclasses import dataclass, field


class I32(int):
    """Preserve the declared width through unions and generic containers."""


class U32(int):
    pass


class U64(int):
    pass


class Unit:
    __slots__ = ()

    def __repr__(self) -> str:
        return "unit"


class NoneValue:
    __slots__ = ()

    def __repr__(self) -> str:
        return "none"


UNIT = Unit()
NONE = NoneValue()


@dataclass
class Some:
    value: object


@dataclass
class Record:
    type_id: int
    fields: list


@dataclass
class EnumVal:
    type_id: int
    variant: int
    fields: list = field(default_factory=list)


class Secret:
    """Opaque (docs/security.md): never printed, compared, logged, or encoded.

    It is a nominal (kind 3) type, so it carries the interned `type_id` that
    `VALUE_TYPE` needs to dispatch a `Secret | NotFound` match.
    """

    __slots__ = ("text", "type_id")

    def __init__(self, text: str, type_id: int = -1) -> None:
        self.text = text
        self.type_id = type_id

    def __repr__(self) -> str:
        return "Secret"
