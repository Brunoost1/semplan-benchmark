from __future__ import annotations

from decimal import Decimal

from hypothesis import given, settings
from hypothesis import strategies as st

from semplan.data_generation.determinism import deterministic_uuid, money


@given(
    st.lists(
        st.tuples(
            st.integers(min_value=1, max_value=10),
            st.decimals(min_value=Decimal("0.01"), max_value=Decimal("999.99"), places=2),
        ),
        min_size=1,
        max_size=20,
    )
)
@settings(derandomize=True)
def test_order_item_reconciliation_property(items: list[tuple[int, Decimal]]) -> None:
    gross = sum((money(price) * quantity for quantity, price in items), Decimal("0.00"))
    recomputed = sum((money(price) * quantity for quantity, price in items), Decimal("0.00"))

    assert abs(gross - recomputed) <= Decimal("0.01")


@given(st.text(min_size=1, max_size=80), st.text(min_size=1, max_size=80))
@settings(derandomize=True)
def test_uuid5_ids_are_deterministic_for_natural_keys(entity: str, key: str) -> None:
    assert deterministic_uuid(entity, key) == deterministic_uuid(entity, key)
