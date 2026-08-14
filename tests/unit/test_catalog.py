from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from semplan.catalog import load_catalog
from semplan.catalog.models import REQUIRED_DIMENSION_IDS, REQUIRED_METRIC_IDS
from semplan.errors import ErrorCode, ProjectError

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _copy_catalog(target: Path) -> None:
    source = PROJECT_ROOT / "catalog"
    (target / "synonyms").mkdir(parents=True)
    for filename in ["metrics.yaml", "dimensions.yaml", "filters.yaml", "joins.yaml"]:
        (target / filename).write_text(
            (source / filename).read_text(encoding="utf-8"), encoding="utf-8"
        )
    for filename in ["en-US.yaml", "pt-BR.yaml"]:
        (target / f"synonyms/{filename}").write_text(
            (source / f"synonyms/{filename}").read_text(encoding="utf-8"),
            encoding="utf-8",
        )


def _write_yaml(path: Path, payload: object) -> None:
    path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")


def test_catalog_loads_without_database() -> None:
    catalog = load_catalog(PROJECT_ROOT / "catalog")

    assert REQUIRED_METRIC_IDS.issubset(catalog.metrics)
    assert REQUIRED_DIMENSION_IDS.issubset(catalog.dimensions)
    assert catalog.sha256() == load_catalog(PROJECT_ROOT / "catalog").sha256()


def test_catalog_rejects_unknown_synonym_target(tmp_path: Path) -> None:
    target = tmp_path / "catalog"
    _copy_catalog(target)
    (target / "synonyms/en-US.yaml").write_text(
        """
schema_version: "1.0"
locale: en-US
synonyms:
  bogus: missing_metric
""",
        encoding="utf-8",
    )

    with pytest.raises(ProjectError) as exc_info:
        load_catalog(target)

    assert exc_info.value.to_record().code is ErrorCode.CATALOG_UNKNOWN_ID


def test_catalog_rejects_missing_catalog_file(tmp_path: Path) -> None:
    with pytest.raises(ProjectError) as exc_info:
        load_catalog(tmp_path / "missing")

    assert exc_info.value.to_record().code is ErrorCode.CFG_INVALID


def test_catalog_rejects_non_mapping_file(tmp_path: Path) -> None:
    target = tmp_path / "catalog"
    _copy_catalog(target)
    (target / "metrics.yaml").write_text("- not\n- a\n- mapping\n", encoding="utf-8")

    with pytest.raises(ProjectError) as exc_info:
        load_catalog(target)

    assert exc_info.value.to_record().code is ErrorCode.CFG_INVALID


def test_catalog_rejects_duplicate_metric_ids(tmp_path: Path) -> None:
    target = tmp_path / "catalog"
    _copy_catalog(target)
    payload = yaml.safe_load((target / "metrics.yaml").read_text(encoding="utf-8"))
    payload["metrics"].append(payload["metrics"][0])
    _write_yaml(target / "metrics.yaml", payload)

    with pytest.raises(ProjectError) as exc_info:
        load_catalog(target)

    assert exc_info.value.to_record().code is ErrorCode.CATALOG_UNKNOWN_ID


def test_catalog_rejects_missing_required_metric(tmp_path: Path) -> None:
    target = tmp_path / "catalog"
    _copy_catalog(target)
    payload = yaml.safe_load((target / "metrics.yaml").read_text(encoding="utf-8"))
    payload["metrics"] = [
        metric for metric in payload["metrics"] if metric["id"] != "gross_revenue"
    ]
    _write_yaml(target / "metrics.yaml", payload)

    with pytest.raises(ProjectError):
        load_catalog(target)


def test_catalog_rejects_bad_operator_set(tmp_path: Path) -> None:
    target = tmp_path / "catalog"
    _copy_catalog(target)
    payload = yaml.safe_load((target / "filters.yaml").read_text(encoding="utf-8"))
    payload["operators"] = ["EQ"]
    _write_yaml(target / "filters.yaml", payload)

    with pytest.raises(ProjectError):
        load_catalog(target)


def test_catalog_rejects_unknown_dimension_reference(tmp_path: Path) -> None:
    target = tmp_path / "catalog"
    _copy_catalog(target)
    payload = yaml.safe_load((target / "metrics.yaml").read_text(encoding="utf-8"))
    payload["metrics"][0]["eligible_dimensions"] = ["missing_dimension"]
    _write_yaml(target / "metrics.yaml", payload)

    with pytest.raises(ProjectError):
        load_catalog(target)


def test_catalog_rejects_unknown_join_reference(tmp_path: Path) -> None:
    target = tmp_path / "catalog"
    _copy_catalog(target)
    payload = yaml.safe_load((target / "metrics.yaml").read_text(encoding="utf-8"))
    payload["metrics"][0]["required_joins"] = ["missing_join"]
    _write_yaml(target / "metrics.yaml", payload)

    with pytest.raises(ProjectError):
        load_catalog(target)


def test_catalog_rejects_unknown_metric_dependency(tmp_path: Path) -> None:
    target = tmp_path / "catalog"
    _copy_catalog(target)
    payload = yaml.safe_load((target / "metrics.yaml").read_text(encoding="utf-8"))
    payload["metrics"][0]["depends_on"] = ["missing_metric"]
    _write_yaml(target / "metrics.yaml", payload)

    with pytest.raises(ProjectError):
        load_catalog(target)


def test_catalog_rejects_empty_synonyms(tmp_path: Path) -> None:
    target = tmp_path / "catalog"
    _copy_catalog(target)
    payload = yaml.safe_load((target / "synonyms/en-US.yaml").read_text(encoding="utf-8"))
    payload["synonyms"] = {}
    _write_yaml(target / "synonyms/en-US.yaml", payload)

    with pytest.raises(ProjectError):
        load_catalog(target)
