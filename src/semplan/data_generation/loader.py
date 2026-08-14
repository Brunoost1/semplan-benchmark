"""Direct PostgreSQL loader for generated JSONL table files."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path
from uuid import UUID

from sqlalchemy import MetaData, create_engine

from semplan.data_generation.models import TABLE_ORDER, RowValue
from semplan.data_generation.validator import load_table_rows
from semplan.db import admin_database_url

DATE_COLUMNS_BY_TABLE = {
    "calendar": {"date"},
    "customers": {"created_at"},
    "orders": {"order_date"},
    "payments": {"payment_date"},
    "expenses": {"expense_date"},
    "budgets": {"month"},
    "contracts": {"start_date", "end_date"},
}
UUID_COLUMNS = {
    "customer_id",
    "product_id",
    "order_id",
    "order_item_id",
    "payment_id",
    "department_id",
    "cost_center_id",
    "expense_id",
    "budget_id",
    "supplier_id",
    "contract_id",
}
MONEY_COLUMNS = {
    "unit_cost",
    "list_price",
    "gross_amount",
    "discount_amount",
    "unit_price",
    "fee_amount",
    "refunded_amount",
    "amount",
    "budget_amount",
    "annual_value",
}


def load_dataset(dataset_dir: Path, database_url: str | None = None) -> dict[str, int]:
    rows = load_table_rows(dataset_dir)
    engine = create_engine(database_url or admin_database_url())
    metadata = MetaData()
    metadata.reflect(engine)
    loaded: dict[str, int] = {}
    try:
        with engine.begin() as connection:
            for table_name in reversed(TABLE_ORDER):
                connection.execute(metadata.tables[table_name].delete())
            for table_name in TABLE_ORDER:
                table = metadata.tables[table_name]
                table_rows = [_coerce_row(table_name, row) for row in rows[table_name]]
                if table_rows:
                    connection.execute(table.insert(), table_rows)
                loaded[table_name] = len(table_rows)
    finally:
        engine.dispose()
    return loaded


def _coerce_row(table_name: str, row: dict[str, RowValue]) -> dict[str, object]:
    coerced: dict[str, object] = {}
    date_columns = DATE_COLUMNS_BY_TABLE.get(table_name, set())
    for key, value in row.items():
        if value is None:
            coerced[key] = None
        elif key in UUID_COLUMNS:
            coerced[key] = UUID(str(value))
        elif key in date_columns:
            coerced[key] = date.fromisoformat(str(value))
        elif key in MONEY_COLUMNS:
            coerced[key] = Decimal(str(value))
        else:
            coerced[key] = value
    return coerced
