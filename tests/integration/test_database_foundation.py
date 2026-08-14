from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import DBAPIError

from semplan.catalog import load_catalog
from semplan.contracts import SemanticRequestEnvelope
from semplan.data_generation import generate_dataset, load_dataset, write_dataset
from semplan.data_generation.models import TABLE_ORDER
from semplan.db import admin_database_url, readonly_database_url
from semplan.executor import execute_semantic_plan
from semplan.normalizer import ReferenceContext, normalize_semantic_request

pytestmark = pytest.mark.allow_network

PROJECT_ROOT = Path(__file__).resolve().parents[2]

REQUIRED_TABLES = {
    "customers",
    "products",
    "orders",
    "order_items",
    "payments",
    "departments",
    "cost_centers",
    "expenses",
    "budgets",
    "suppliers",
    "contracts",
    "calendar",
}

REQUIRED_VIEWS = {
    "analytics_order_facts",
    "analytics_expense_facts",
    "analytics_budget_facts",
    "analytics_contract_facts",
    "dim_calendar",
    "dim_customers",
    "dim_products",
    "dim_departments",
    "dim_cost_centers",
    "dim_suppliers",
}


@pytest.fixture(autouse=True)
def reset_database_rows() -> None:
    engine = create_engine(admin_database_url())
    try:
        with engine.begin() as connection:
            for table in reversed(TABLE_ORDER):
                connection.execute(text(f"DELETE FROM {table}"))
    finally:
        engine.dispose()


def test_migration_created_required_tables_and_views() -> None:
    engine = create_engine(admin_database_url())
    try:
        with engine.connect() as connection:
            tables = {
                row[0]
                for row in connection.execute(
                    text(
                        """
                        SELECT table_name
                        FROM information_schema.tables
                        WHERE table_schema = 'public'
                          AND table_type = 'BASE TABLE'
                        """
                    )
                )
            }
            views = {
                row[0]
                for row in connection.execute(
                    text(
                        """
                        SELECT table_name
                        FROM information_schema.views
                        WHERE table_schema = 'public'
                        """
                    )
                )
            }
    finally:
        engine.dispose()

    assert REQUIRED_TABLES.issubset(tables)
    assert REQUIRED_VIEWS.issubset(views)


def test_readonly_role_can_select_governed_view() -> None:
    engine = create_engine(readonly_database_url())
    try:
        with engine.connect() as connection:
            count = connection.execute(
                text("SELECT count(*) FROM analytics_order_facts")
            ).scalar_one()
    finally:
        engine.dispose()

    assert count == 0


def test_readonly_role_blocks_writes() -> None:
    engine = create_engine(readonly_database_url())
    try:
        with engine.begin() as connection:
            with pytest.raises(DBAPIError):
                connection.execute(
                    text(
                        """
                        INSERT INTO customers (
                            customer_id,
                            created_at,
                            segment,
                            region,
                            country_code,
                            status
                        )
                        VALUES (
                            '00000000-0000-0000-0000-000000000001',
                            DATE '2026-01-01',
                            'Fixture',
                            'North',
                            'NS',
                            'active'
                        )
                        """
                    )
                )
    finally:
        engine.dispose()


def test_load_small_dataset_and_query_analytics_view(tmp_path) -> None:  # type: ignore[no-untyped-def]
    dataset_dir = tmp_path / "small"
    dataset = generate_dataset("small", 20260806)
    write_dataset(dataset, dataset_dir, overwrite=False)

    loaded = load_dataset(dataset_dir, admin_database_url())
    engine = create_engine(admin_database_url())
    try:
        with engine.connect() as connection:
            order_fact_count = connection.execute(
                text("SELECT count(*) FROM analytics_order_facts")
            ).scalar_one()
            expense_amount = connection.execute(
                text("SELECT COALESCE(SUM(expense_amount), 0) FROM analytics_expense_facts")
            ).scalar_one()
    finally:
        engine.dispose()

    assert loaded["orders"] == dataset.profile.orders
    assert order_fact_count == len(dataset.rows["order_items"])
    assert expense_amount >= 0


def test_semantic_plan_executor_runs_against_readonly_database(tmp_path) -> None:  # type: ignore[no-untyped-def]
    dataset_dir = tmp_path / "small"
    dataset = generate_dataset("small", 20260806)
    write_dataset(dataset, dataset_dir, overwrite=False)
    load_dataset(dataset_dir, admin_database_url())
    catalog = load_catalog(PROJECT_ROOT / "catalog")
    request = SemanticRequestEnvelope.model_validate(
        {
            "schema_version": "1.0",
            "operation": "REPLACE",
            "intent": "ranking",
            "metrics": ["net_revenue"],
            "dimensions": ["region"],
            "filters": [{"field": "year", "operator": "EQ", "value": 2026}],
            "time_grain": "year",
            "sort": [{"field": "net_revenue", "direction": "desc"}],
            "limit": 5,
            "comparison": None,
            "clarifications": [],
            "confidence": "1",
        }
    )
    normalized = normalize_semantic_request(
        request,
        catalog,
        ReferenceContext(date(2026, 8, 1), "UTC"),
    )
    assert normalized.plan is not None

    result = execute_semantic_plan(normalized.plan, catalog, database_url=readonly_database_url())

    assert result.outcome == "ANSWERED"
    assert result.rows
    assert result.compiled_query.sql_sha256.startswith("sha256:")
