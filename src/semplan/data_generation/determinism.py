"""Deterministic helpers for generated records and canonical files."""

from __future__ import annotations

import hashlib
import random
import uuid
from decimal import ROUND_HALF_EVEN, Decimal

PROJECT_NAMESPACE = uuid.UUID("2a09a15b-f79f-5a42-8a27-0efc4b29b2d9")
CENT = Decimal("0.01")


def child_seed(root_seed: int, name: str) -> int:
    payload = f"{root_seed}:{name}".encode()
    return int.from_bytes(hashlib.sha256(payload).digest()[:8], "big")


def child_rng(root_seed: int, name: str) -> random.Random:
    return random.Random(child_seed(root_seed, name))


def deterministic_uuid(entity: str, natural_key: str) -> str:
    return str(uuid.uuid5(PROJECT_NAMESPACE, f"{entity}:{natural_key}"))


def money(value: Decimal) -> Decimal:
    return value.quantize(CENT, rounding=ROUND_HALF_EVEN)


def money_str(value: Decimal) -> str:
    return f"{money(value):.2f}"


def decimal_cents(cents: int) -> Decimal:
    return Decimal(cents) / Decimal(100)
