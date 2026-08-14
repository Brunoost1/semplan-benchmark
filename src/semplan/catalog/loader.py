"""Load and validate the governed semantic catalog without a database."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError

from semplan.catalog.models import (
    Catalog,
    DimensionsFile,
    FiltersFile,
    JoinsFile,
    MetricsFile,
    SynonymsFile,
)
from semplan.errors import ErrorCode, ProjectError


def _read_yaml(path: Path) -> dict[str, Any]:
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ProjectError(
            ErrorCode.CFG_INVALID,
            f"Cannot read catalog file: {path}",
            detail={"path": str(path), "reason": str(exc)},
        ) from exc
    if not isinstance(raw, dict):
        raise ProjectError(
            ErrorCode.CFG_INVALID,
            f"Catalog file must contain a mapping: {path}",
            detail={"path": str(path)},
        )
    return raw


def load_catalog(root: Path) -> Catalog:
    try:
        metrics_file = MetricsFile.model_validate(_read_yaml(root / "metrics.yaml"))
        dimensions_file = DimensionsFile.model_validate(_read_yaml(root / "dimensions.yaml"))
        filters_file = FiltersFile.model_validate(_read_yaml(root / "filters.yaml"))
        joins_file = JoinsFile.model_validate(_read_yaml(root / "joins.yaml"))
        synonym_files = [
            SynonymsFile.model_validate(_read_yaml(path))
            for path in sorted((root / "synonyms").glob("*.yaml"))
        ]

        metric_ids = [metric.id for metric in metrics_file.metrics]
        dimension_ids = [dimension.id for dimension in dimensions_file.dimensions]
        join_ids = [join.id for join in joins_file.joins]
        if len(metric_ids) != len(set(metric_ids)):
            raise ValueError("Duplicate metric IDs")
        if len(dimension_ids) != len(set(dimension_ids)):
            raise ValueError("Duplicate dimension IDs")
        if len(join_ids) != len(set(join_ids)):
            raise ValueError("Duplicate join IDs")

        return Catalog.model_validate(
            {
                "metrics": {metric.id: metric for metric in metrics_file.metrics},
                "dimensions": {dimension.id: dimension for dimension in dimensions_file.dimensions},
                "operators": set(filters_file.operators),
                "joins": {join.id: join for join in joins_file.joins},
                "synonyms": {
                    synonym_file.locale: synonym_file.synonyms for synonym_file in synonym_files
                },
            }
        )
    except (ValidationError, ValueError) as exc:
        raise ProjectError(
            ErrorCode.CATALOG_UNKNOWN_ID,
            "Catalog validation failed",
            detail={"reason": str(exc)},
        ) from exc
