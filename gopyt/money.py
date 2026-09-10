"""Checked fixed-point money natives; see docs/money-amendment-2026-09-10.md."""
from __future__ import annotations

import re

from gopyt.values import I32, U32, U64, EnumVal, Record

MIN_UNITS = -(2**63)
MAX_UNITS = 2**63 - 1
MAX_SCALE = 18
MAX_TEXT = 80
DECIMAL = re.compile(r'-?(?:0|[1-9][0-9]*)(?:\.[0-9]{1,36})?\Z', re.ASCII)
CURRENCY = re.compile(r'[A-Z]{3}\Z', re.ASCII)


class MoneyError(ValueError):
    pass


def integer(value: object) -> int:
    if not isinstance(value, int) or isinstance(value, (bool, I32, U32, U64)) or not MIN_UNITS <= value <= MAX_UNITS:
        raise MoneyError('money integer outside i64 range')
    return value


def scale_value(value: object) -> int:
    value = integer(value)
    if not 0 <= value <= MAX_SCALE:
        raise MoneyError('money scale must be 0..18')
    return value


def currency_value(value: object) -> str:
    if not isinstance(value, str) or CURRENCY.fullmatch(value) is None:
        raise MoneyError('currency must be three uppercase ASCII letters')
    return value


def money_value(vm, value: object) -> tuple[int, int, str]:
    if (not isinstance(value, Record) or value.type_id != vm.type_id_of('core.money.Money')
            or len(value.fields) != 3):
        raise MoneyError('invalid Money record')
    units, scale, currency = value.fields
    return integer(units), scale_value(scale), currency_value(currency)


def make(vm, units: int, scale: int, currency: str) -> Record:
    return Record(vm.type_id_of('core.money.Money'),
                  [integer(units), scale_value(scale), currency_value(currency)])


def rounding_value(vm, value: object) -> int:
    if (not isinstance(value, EnumVal) or value.type_id != vm.type_id_of('core.money.Rounding')
            or type(value.variant) is not int or not 0 <= value.variant < 6 or value.fields):
        raise MoneyError('invalid money rounding mode')
    return value.variant


def rounded(numerator: int, denominator: int, mode: int) -> int:
    """Round one exact rational using signed quotient/remainder, without floats."""
    if denominator == 0:
        raise MoneyError('money division by zero')
    negative = (numerator < 0) != (denominator < 0)
    quotient, remainder = divmod(abs(numerator), abs(denominator))
    if remainder:
        if mode == 0:
            raise MoneyError('money rounding required')
        twice = remainder * 2
        increment = ((mode == 1 and (twice > abs(denominator)
                                      or (twice == abs(denominator) and quotient % 2 == 1)))
                     or (mode == 2 and twice >= abs(denominator))
                     or (mode == 4 and negative) or (mode == 5 and not negative))
        quotient += int(increment)
    return -quotient if negative else quotient


def parse(vm, text: str, scale: int, currency: str, rounding: EnumVal) -> Record:
    scale = scale_value(scale)
    currency = currency_value(currency)
    mode = rounding_value(vm, rounding)
    if not isinstance(text, str) or len(text) > MAX_TEXT or DECIMAL.fullmatch(text) is None:
        raise MoneyError('money text requires a bounded ASCII decimal')
    whole, dot, fraction = text.partition('.')
    coefficient = int(whole + fraction)
    units = rounded(coefficient * 10**scale, 10**len(fraction), mode)
    return make(vm, units, scale, currency)


def format_value(vm, value: Record) -> str:
    units, scale, _currency = money_value(vm, value)
    digits = str(abs(units)).zfill(scale + 1)
    text = digits if scale == 0 else digits[:-scale] + '.' + digits[-scale:]
    return ('-' if units < 0 else '') + text


def pair(vm, left: Record, right: Record) -> tuple[int, int, int, str]:
    a, scale, currency = money_value(vm, left)
    b, other_scale, other_currency = money_value(vm, right)
    if currency != other_currency:
        raise MoneyError('money currency mismatch')
    if scale != other_scale:
        raise MoneyError('money scale mismatch; rescale explicitly')
    return a, b, scale, currency


def add(vm, left: Record, right: Record) -> Record:
    a, b, scale, currency = pair(vm, left, right)
    return make(vm, a + b, scale, currency)


def subtract(vm, left: Record, right: Record) -> Record:
    a, b, scale, currency = pair(vm, left, right)
    return make(vm, a - b, scale, currency)


def compare(vm, left: Record, right: Record) -> int:
    a, b, _scale, _currency = pair(vm, left, right)
    return (a > b) - (a < b)


def rescale(vm, value: Record, scale: int, rounding: EnumVal) -> Record:
    units, old_scale, currency = money_value(vm, value)
    scale = scale_value(scale)
    mode = rounding_value(vm, rounding)
    result = rounded(units * 10**scale, 10**old_scale, mode)
    return make(vm, result, scale, currency)


def multiply_ratio(vm, value: Record, numerator: int, denominator: int,
                   rounding: EnumVal) -> Record:
    units, scale, currency = money_value(vm, value)
    numerator, denominator = integer(numerator), integer(denominator)
    mode = rounding_value(vm, rounding)
    result = rounded(units * numerator, denominator, mode)
    return make(vm, result, scale, currency)


def install(table: dict) -> None:
    def wrap(operation):
        def call(vm, args, func):
            try:
                return operation(vm, *args)
            except MoneyError as error:
                return Record(vm.type_id_of('core.status.ConvertError'), [str(error)])
        return call

    for name, operation in {'make': make, 'parse': parse, 'format': format_value,
                            'add': add, 'subtract': subtract, 'compare': compare,
                            'rescale': rescale, 'multiply_ratio': multiply_ratio}.items():
        table['core.money.' + name] = wrap(operation)
