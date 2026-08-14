"""Synthetic dataset profiles and manifest models."""

from __future__ import annotations

from datetime import date
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class StrictDataModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class DataProfile(StrictDataModel):
    name: Literal["small", "full"]
    customers: int = Field(gt=0)
    products: int = Field(gt=0)
    orders: int = Field(gt=0)
    departments: int = Field(gt=0)
    cost_centers: int = Field(gt=0)
    expenses: int = Field(gt=0)
    suppliers: int = Field(gt=0)
    contracts: int = Field(gt=0)
    budget_categories: int = Field(gt=0)
    min_items_per_order: int = Field(gt=0)
    max_items_per_order: int = Field(gt=0)


PROFILES: dict[str, DataProfile] = {
    "small": DataProfile(
        name="small",
        customers=500,
        products=60,
        orders=5_000,
        departments=5,
        cost_centers=12,
        expenses=5_000,
        suppliers=80,
        contracts=320,
        budget_categories=4,
        min_items_per_order=2,
        max_items_per_order=4,
    ),
    "full": DataProfile(
        name="full",
        customers=20_000,
        products=1_200,
        orders=250_000,
        departments=15,
        cost_centers=60,
        expenses=180_000,
        suppliers=2_000,
        contracts=8_000,
        budget_categories=4,
        min_items_per_order=3,
        max_items_per_order=5,
    ),
}

DATASET_SCHEMA_VERSION = "1.0"
DATASET_VERSION = "0.1.0"
GENERATOR_VERSION = "0.1.0"
REFERENCE_DATE = date(2026, 8, 1)
START_DATE = date(2022, 1, 1)
END_DATE = date(2026, 12, 31)

TABLE_ORDER = [
    "calendar",
    "customers",
    "products",
    "departments",
    "cost_centers",
    "suppliers",
    "orders",
    "order_items",
    "payments",
    "expenses",
    "budgets",
    "contracts",
]

TABLE_COLUMNS: dict[str, list[str]] = {
    "calendar": [
        "date",
        "year",
        "quarter",
        "month",
        "month_name",
        "iso_week",
        "weekday",
        "is_business_day",
    ],
    "customers": ["customer_id", "created_at", "segment", "region", "country_code", "status"],
    "products": [
        "product_id",
        "category",
        "subcategory",
        "brand",
        "unit_cost",
        "list_price",
        "active",
    ],
    "departments": ["department_id", "name"],
    "cost_centers": ["cost_center_id", "department_id", "code", "name"],
    "suppliers": ["supplier_id", "region", "category", "risk_level", "status"],
    "orders": [
        "order_id",
        "customer_id",
        "order_date",
        "channel",
        "status",
        "gross_amount",
        "discount_amount",
    ],
    "order_items": [
        "order_item_id",
        "order_id",
        "product_id",
        "quantity",
        "unit_price",
        "unit_cost",
    ],
    "payments": [
        "payment_id",
        "order_id",
        "payment_date",
        "method",
        "fee_amount",
        "refunded_amount",
        "status",
    ],
    "expenses": [
        "expense_id",
        "expense_date",
        "department_id",
        "cost_center_id",
        "category",
        "amount",
        "status",
    ],
    "budgets": ["budget_id", "month", "cost_center_id", "category", "budget_amount"],
    "contracts": [
        "contract_id",
        "supplier_id",
        "start_date",
        "end_date",
        "annual_value",
        "status",
        "risk_level",
    ],
}

RowValue = str | int | bool | None
TableRows = dict[str, list[dict[str, RowValue]]]


class GeneratedDataset(StrictDataModel):
    profile: DataProfile
    seed: int
    child_seeds: dict[str, int]
    rows: TableRows
    pattern_manifest: dict[str, object]
    private_ledger: dict[str, object]
