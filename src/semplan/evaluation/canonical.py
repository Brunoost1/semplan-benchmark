"""Canonical result values, comparisons, and deterministic rendering helpers."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any

from semplan.contracts import GoldAnswer, ScalarValue, ToleranceSpec


def canonical_value(value: object, unit: str | None) -> ScalarValue:
    """Convert database scalars into stable benchmark comparison values."""

    if isinstance(value, Decimal):
        if unit == "usd":
            return f"{value.quantize(Decimal('0.01')):.2f}"
        if unit == "ratio":
            return f"{value.quantize(Decimal('0.000001')):.6f}"
        if unit == "count":
            return int(value)
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, (str, int, bool)) or value is None:
        return value
    raise TypeError(f"Unsupported canonical value type: {type(value).__name__}")


def canonicalize_row(row: dict[str, object], units: dict[str, str]) -> dict[str, ScalarValue]:
    return {key: canonical_value(value, units.get(key)) for key, value in row.items()}


def rows_equal(
    left: list[dict[str, ScalarValue]],
    right: list[dict[str, ScalarValue]],
    tolerances: dict[str, ToleranceSpec] | None = None,
) -> bool:
    """Compare canonical rows with exact or declared Decimal tolerance."""

    if len(left) != len(right):
        return False
    for left_row, right_row in zip(left, right, strict=True):
        if set(left_row) != set(right_row):
            return False
        for field, left_value in left_row.items():
            if not _values_equal(
                left_value, right_row[field], tolerances.get(field) if tolerances else None
            ):
                return False
    return True


def gold_rows_equal(rows: list[dict[str, ScalarValue]], answer: GoldAnswer) -> bool:
    return rows_equal(rows, answer.rows, answer.tolerances)


def _values_equal(left: ScalarValue, right: ScalarValue, tolerance: ToleranceSpec | None) -> bool:
    if left == right:
        return True
    if tolerance is None:
        return False
    if not _is_decimal_like(left) or not _is_decimal_like(right):
        return False
    left_decimal = Decimal(str(left))
    right_decimal = Decimal(str(right))
    delta = abs(left_decimal - right_decimal)
    if delta <= tolerance.absolute:
        return True
    if right_decimal == 0:
        return False
    return delta / abs(right_decimal) <= tolerance.relative


def _is_decimal_like(value: Any) -> bool:
    if isinstance(value, bool) or value is None:
        return False
    try:
        Decimal(str(value))
    except Exception:
        return False
    return True
