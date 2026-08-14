"""Strict semantic catalog models."""

from __future__ import annotations

import hashlib
import json
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from semplan.contracts import Aggregation, CanonicalId, Locale, Operator

REQUIRED_METRIC_IDS = {
    "gross_revenue",
    "net_revenue",
    "contribution_margin",
    "contribution_margin_pct",
    "order_count",
    "average_order_value",
    "expense_amount",
    "budget_amount",
    "budget_variance",
    "budget_variance_pct",
    "active_contract_value",
    "active_customer_count",
}

REQUIRED_DIMENSION_IDS = {
    "date",
    "year",
    "quarter",
    "month",
    "week",
    "region",
    "country",
    "customer_segment",
    "channel",
    "product",
    "category",
    "subcategory",
    "brand",
    "department",
    "cost_center",
    "expense_category",
    "supplier",
    "contract_risk",
    "payment_method",
}


class StrictCatalogModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class LocalizedLabels(StrictCatalogModel):
    en_us: str = Field(alias="en-US", min_length=1)
    pt_br: str = Field(alias="pt-BR", min_length=1)


class SqlReference(StrictCatalogModel):
    view: CanonicalId
    expression: CanonicalId


class SourceReference(StrictCatalogModel):
    view: CanonicalId
    column: CanonicalId


class MetricEntry(StrictCatalogModel):
    id: CanonicalId
    labels: LocalizedLabels
    definition: str = Field(min_length=1)
    unit: Literal["usd", "count", "ratio"]
    aggregation: Aggregation
    eligible_dimensions: list[CanonicalId] = Field(min_length=1)
    required_joins: list[CanonicalId] = Field(default_factory=list)
    depends_on: list[CanonicalId] = Field(default_factory=list)
    null_behavior: str = Field(min_length=1)
    sql: SqlReference


class DimensionEntry(StrictCatalogModel):
    id: CanonicalId
    labels: LocalizedLabels
    source: SourceReference
    cardinality_estimate: int = Field(gt=0)
    allowed_operators: list[Operator] = Field(min_length=1)


class JoinSide(StrictCatalogModel):
    table: CanonicalId
    column: CanonicalId


class JoinEntry(StrictCatalogModel):
    id: CanonicalId
    left: JoinSide
    right: JoinSide


class MetricsFile(StrictCatalogModel):
    schema_version: Literal["1.0"]
    metrics: list[MetricEntry]


class DimensionsFile(StrictCatalogModel):
    schema_version: Literal["1.0"]
    dimensions: list[DimensionEntry]


class FiltersFile(StrictCatalogModel):
    schema_version: Literal["1.0"]
    operators: list[Operator]


class JoinsFile(StrictCatalogModel):
    schema_version: Literal["1.0"]
    joins: list[JoinEntry]


class SynonymsFile(StrictCatalogModel):
    schema_version: Literal["1.0"]
    locale: Locale
    synonyms: dict[str, CanonicalId]


class Catalog(StrictCatalogModel):
    metrics: dict[str, MetricEntry]
    dimensions: dict[str, DimensionEntry]
    operators: set[Operator]
    joins: dict[str, JoinEntry]
    synonyms: dict[Locale, dict[str, str]]

    @model_validator(mode="after")
    def validate_references(self) -> Catalog:
        missing_metrics = REQUIRED_METRIC_IDS.difference(self.metrics)
        if missing_metrics:
            raise ValueError(f"Missing required metrics: {sorted(missing_metrics)}")

        missing_dimensions = REQUIRED_DIMENSION_IDS.difference(self.dimensions)
        if missing_dimensions:
            raise ValueError(f"Missing required dimensions: {sorted(missing_dimensions)}")

        if set(self.operators) != set(Operator):
            raise ValueError("Catalog operator set must match governed operator enum")

        join_ids = set(self.joins)
        for metric in self.metrics.values():
            unknown_dimensions = set(metric.eligible_dimensions).difference(self.dimensions)
            if unknown_dimensions:
                raise ValueError(f"Metric {metric.id} references unknown dimensions")
            unknown_joins = set(metric.required_joins).difference(join_ids)
            if unknown_joins:
                raise ValueError(f"Metric {metric.id} references unknown joins")
            unknown_dependencies = set(metric.depends_on).difference(self.metrics)
            if unknown_dependencies:
                raise ValueError(f"Metric {metric.id} references unknown metric dependencies")

        known_ids = set(self.metrics).union(self.dimensions)
        for locale, synonyms in self.synonyms.items():
            if not synonyms:
                raise ValueError(f"Locale {locale} has no synonyms")
            unknown_targets = set(synonyms.values()).difference(known_ids)
            if unknown_targets:
                raise ValueError(f"Locale {locale} references unknown synonym targets")

        return self

    def canonical_json(self) -> str:
        payload = self.model_dump(mode="json", by_alias=True)
        payload["operators"] = sorted(payload["operators"])
        return json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True)

    def sha256(self) -> str:
        return hashlib.sha256(self.canonical_json().encode("utf-8")).hexdigest()
