from __future__ import annotations

from pathlib import Path

import pytest

from semplan.data_generation import (
    compare_dataset_dirs,
    generate_dataset,
    manifest_hash,
    validate_dataset_dir,
    write_dataset,
)
from semplan.data_generation.determinism import child_seed, deterministic_uuid
from semplan.data_generation.models import TABLE_ORDER


def test_child_seeds_are_named_and_stable() -> None:
    assert child_seed(20260806, "orders") == child_seed(20260806, "orders")
    assert child_seed(20260806, "orders") != child_seed(20260806, "expenses")


def test_deterministic_ids_use_uuid5_shape() -> None:
    value = deterministic_uuid("customer", "fixture")

    assert value == deterministic_uuid("customer", "fixture")
    assert len(value) == 36


def test_generate_small_dataset_has_required_tables() -> None:
    dataset = generate_dataset("small", 20260806)

    assert set(dataset.rows) == set(TABLE_ORDER)
    assert len(dataset.rows["orders"]) == dataset.profile.orders
    assert len(dataset.private_ledger["labels"]) > 0


def test_dataset_writer_and_validator_are_byte_equivalent(tmp_path: Path) -> None:
    left = tmp_path / "left"
    right = tmp_path / "right"
    write_dataset(generate_dataset("small", 20260806), left, overwrite=False)
    write_dataset(generate_dataset("small", 20260806), right, overwrite=False)
    validate_dataset_dir(left, write_report=True)
    validate_dataset_dir(right, write_report=True)

    comparison = compare_dataset_dirs(left, right)

    assert comparison["byte_equivalent"] is True
    assert manifest_hash(left) == manifest_hash(right)


def test_dataset_writer_refuses_existing_output_without_overwrite(tmp_path: Path) -> None:
    output = tmp_path / "dataset"
    write_dataset(generate_dataset("small", 20260806), output, overwrite=False)

    with pytest.raises(FileExistsError):
        write_dataset(generate_dataset("small", 20260806), output, overwrite=False)
