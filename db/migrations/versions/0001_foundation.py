"""Create Northstar Commerce schema and governed views.

Revision ID: 0001_foundation
Revises:
Create Date: 2026-08-06
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0001_foundation"
down_revision = None
branch_labels = None
depends_on = None

UUID = postgresql.UUID(as_uuid=True)
MONEY = sa.Numeric(18, 2)


def upgrade() -> None:
    op.create_table(
        "customers",
        sa.Column("customer_id", UUID, primary_key=True),
        sa.Column("created_at", sa.Date(), nullable=False),
        sa.Column("segment", sa.Text(), nullable=False),
        sa.Column("region", sa.Text(), nullable=False),
        sa.Column("country_code", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.CheckConstraint("created_at BETWEEN DATE '2022-01-01' AND DATE '2026-12-31'"),
        sa.CheckConstraint("region IN ('North', 'South', 'East', 'West', 'Central')"),
        sa.CheckConstraint("status IN ('active', 'inactive', 'prospect')"),
    )
    op.create_index("ix_customers_region", "customers", ["region"])
    op.create_index("ix_customers_segment", "customers", ["segment"])

    op.create_table(
        "products",
        sa.Column("product_id", UUID, primary_key=True),
        sa.Column("category", sa.Text(), nullable=False),
        sa.Column("subcategory", sa.Text(), nullable=False),
        sa.Column("brand", sa.Text(), nullable=False),
        sa.Column("unit_cost", MONEY, nullable=False),
        sa.Column("list_price", MONEY, nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.CheckConstraint("unit_cost >= 0"),
        sa.CheckConstraint("list_price >= 0"),
        sa.CheckConstraint("list_price >= unit_cost"),
    )
    op.create_index("ix_products_category", "products", ["category"])
    op.create_index("ix_products_brand", "products", ["brand"])

    op.create_table(
        "orders",
        sa.Column("order_id", UUID, primary_key=True),
        sa.Column("customer_id", UUID, sa.ForeignKey("customers.customer_id"), nullable=False),
        sa.Column("order_date", sa.Date(), nullable=False),
        sa.Column("channel", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("gross_amount", MONEY, nullable=False),
        sa.Column("discount_amount", MONEY, nullable=False),
        sa.CheckConstraint("order_date BETWEEN DATE '2022-01-01' AND DATE '2026-12-31'"),
        sa.CheckConstraint("status IN ('placed', 'paid', 'shipped', 'cancelled', 'refunded')"),
        sa.CheckConstraint("gross_amount >= 0"),
        sa.CheckConstraint("discount_amount >= 0"),
        sa.CheckConstraint("discount_amount <= gross_amount"),
    )
    op.create_index("ix_orders_customer_id", "orders", ["customer_id"])
    op.create_index("ix_orders_order_date", "orders", ["order_date"])
    op.create_index("ix_orders_status", "orders", ["status"])
    op.create_index("ix_orders_channel", "orders", ["channel"])

    op.create_table(
        "order_items",
        sa.Column("order_item_id", UUID, primary_key=True),
        sa.Column("order_id", UUID, sa.ForeignKey("orders.order_id"), nullable=False),
        sa.Column("product_id", UUID, sa.ForeignKey("products.product_id"), nullable=False),
        sa.Column("quantity", sa.Integer(), nullable=False),
        sa.Column("unit_price", MONEY, nullable=False),
        sa.Column("unit_cost", MONEY, nullable=False),
        sa.UniqueConstraint("order_id", "product_id", "order_item_id"),
        sa.CheckConstraint("quantity > 0"),
        sa.CheckConstraint("unit_price >= 0"),
        sa.CheckConstraint("unit_cost >= 0"),
    )
    op.create_index("ix_order_items_order_id", "order_items", ["order_id"])
    op.create_index("ix_order_items_product_id", "order_items", ["product_id"])

    op.create_table(
        "payments",
        sa.Column("payment_id", UUID, primary_key=True),
        sa.Column("order_id", UUID, sa.ForeignKey("orders.order_id"), nullable=False),
        sa.Column("payment_date", sa.Date(), nullable=False),
        sa.Column("method", sa.Text(), nullable=False),
        sa.Column("fee_amount", MONEY, nullable=False),
        sa.Column("refunded_amount", MONEY, nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.CheckConstraint("payment_date BETWEEN DATE '2022-01-01' AND DATE '2026-12-31'"),
        sa.CheckConstraint("fee_amount >= 0"),
        sa.CheckConstraint("refunded_amount >= 0"),
        sa.CheckConstraint("status IN ('pending', 'confirmed', 'failed')"),
    )
    op.create_index("ix_payments_order_id", "payments", ["order_id"])
    op.create_index("ix_payments_payment_date", "payments", ["payment_date"])
    op.create_index("ix_payments_status", "payments", ["status"])

    op.create_table(
        "departments",
        sa.Column("department_id", UUID, primary_key=True),
        sa.Column("name", sa.Text(), nullable=False, unique=True),
    )

    op.create_table(
        "cost_centers",
        sa.Column("cost_center_id", UUID, primary_key=True),
        sa.Column(
            "department_id",
            UUID,
            sa.ForeignKey("departments.department_id"),
            nullable=False,
        ),
        sa.Column("code", sa.Text(), nullable=False, unique=True),
        sa.Column("name", sa.Text(), nullable=False),
    )
    op.create_index("ix_cost_centers_department_id", "cost_centers", ["department_id"])

    op.create_table(
        "expenses",
        sa.Column("expense_id", UUID, primary_key=True),
        sa.Column("expense_date", sa.Date(), nullable=False),
        sa.Column(
            "department_id", UUID, sa.ForeignKey("departments.department_id"), nullable=False
        ),
        sa.Column(
            "cost_center_id", UUID, sa.ForeignKey("cost_centers.cost_center_id"), nullable=False
        ),
        sa.Column("category", sa.Text(), nullable=False),
        sa.Column("amount", MONEY, nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.CheckConstraint("expense_date BETWEEN DATE '2022-01-01' AND DATE '2026-12-31'"),
        sa.CheckConstraint("amount >= 0"),
        sa.CheckConstraint("status IN ('draft', 'approved', 'rejected')"),
    )
    op.create_index("ix_expenses_expense_date", "expenses", ["expense_date"])
    op.create_index("ix_expenses_department_id", "expenses", ["department_id"])
    op.create_index("ix_expenses_cost_center_id", "expenses", ["cost_center_id"])
    op.create_index("ix_expenses_category", "expenses", ["category"])

    op.create_table(
        "budgets",
        sa.Column("budget_id", UUID, primary_key=True),
        sa.Column("month", sa.Date(), nullable=False),
        sa.Column(
            "cost_center_id", UUID, sa.ForeignKey("cost_centers.cost_center_id"), nullable=False
        ),
        sa.Column("category", sa.Text(), nullable=False),
        sa.Column("budget_amount", MONEY, nullable=False),
        sa.UniqueConstraint("month", "cost_center_id", "category"),
        sa.CheckConstraint("month = date_trunc('month', month)::date"),
        sa.CheckConstraint("month BETWEEN DATE '2022-01-01' AND DATE '2026-12-31'"),
        sa.CheckConstraint("budget_amount >= 0"),
    )
    op.create_index("ix_budgets_month", "budgets", ["month"])
    op.create_index("ix_budgets_cost_center_id", "budgets", ["cost_center_id"])
    op.create_index("ix_budgets_category", "budgets", ["category"])

    op.create_table(
        "suppliers",
        sa.Column("supplier_id", UUID, primary_key=True),
        sa.Column("region", sa.Text(), nullable=False),
        sa.Column("category", sa.Text(), nullable=False),
        sa.Column("risk_level", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.CheckConstraint("region IN ('North', 'South', 'East', 'West', 'Central')"),
        sa.CheckConstraint("risk_level IN ('low', 'medium', 'high', 'critical')"),
        sa.CheckConstraint("status IN ('active', 'inactive', 'suspended')"),
    )
    op.create_index("ix_suppliers_region", "suppliers", ["region"])
    op.create_index("ix_suppliers_risk_level", "suppliers", ["risk_level"])

    op.create_table(
        "contracts",
        sa.Column("contract_id", UUID, primary_key=True),
        sa.Column("supplier_id", UUID, sa.ForeignKey("suppliers.supplier_id"), nullable=False),
        sa.Column("start_date", sa.Date(), nullable=False),
        sa.Column("end_date", sa.Date(), nullable=True),
        sa.Column("annual_value", MONEY, nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("risk_level", sa.Text(), nullable=False),
        sa.CheckConstraint("start_date BETWEEN DATE '2022-01-01' AND DATE '2026-12-31'"),
        sa.CheckConstraint("end_date IS NULL OR end_date >= start_date"),
        sa.CheckConstraint("annual_value >= 0"),
        sa.CheckConstraint("status IN ('draft', 'active', 'terminated', 'expired')"),
        sa.CheckConstraint("risk_level IN ('low', 'medium', 'high', 'critical')"),
    )
    op.create_index("ix_contracts_supplier_id", "contracts", ["supplier_id"])
    op.create_index("ix_contracts_start_date", "contracts", ["start_date"])
    op.create_index("ix_contracts_end_date", "contracts", ["end_date"])
    op.create_index("ix_contracts_status", "contracts", ["status"])

    op.create_table(
        "calendar",
        sa.Column("date", sa.Date(), primary_key=True),
        sa.Column("year", sa.Integer(), nullable=False),
        sa.Column("quarter", sa.Integer(), nullable=False),
        sa.Column("month", sa.Integer(), nullable=False),
        sa.Column("month_name", sa.Text(), nullable=False),
        sa.Column("iso_week", sa.Integer(), nullable=False),
        sa.Column("weekday", sa.Integer(), nullable=False),
        sa.Column("is_business_day", sa.Boolean(), nullable=False),
        sa.CheckConstraint("date BETWEEN DATE '2022-01-01' AND DATE '2026-12-31'"),
        sa.CheckConstraint("quarter BETWEEN 1 AND 4"),
        sa.CheckConstraint("month BETWEEN 1 AND 12"),
        sa.CheckConstraint("iso_week BETWEEN 1 AND 53"),
        sa.CheckConstraint("weekday BETWEEN 1 AND 7"),
    )
    op.create_index("ix_calendar_year_month", "calendar", ["year", "month"])

    _create_views()
    _create_readonly_role()


def downgrade() -> None:
    _drop_readonly_role()
    for view_name in [
        "analytics_budget_facts",
        "analytics_contract_facts",
        "analytics_expense_facts",
        "analytics_order_facts",
        "dim_suppliers",
        "dim_cost_centers",
        "dim_departments",
        "dim_products",
        "dim_customers",
        "dim_calendar",
    ]:
        op.execute(f"DROP VIEW IF EXISTS {view_name}")

    for table_name in [
        "calendar",
        "contracts",
        "suppliers",
        "budgets",
        "expenses",
        "cost_centers",
        "departments",
        "payments",
        "order_items",
        "orders",
        "products",
        "customers",
    ]:
        op.drop_table(table_name)


def _create_views() -> None:
    op.execute(
        """
        CREATE VIEW dim_calendar AS
        SELECT date, year, quarter, month, month_name, iso_week, weekday, is_business_day
        FROM calendar
        """
    )
    op.execute(
        """
        CREATE VIEW dim_customers AS
        SELECT customer_id, created_at, segment, region, country_code, status
        FROM customers
        """
    )
    op.execute(
        """
        CREATE VIEW dim_products AS
        SELECT product_id, category, subcategory, brand, unit_cost, list_price, active
        FROM products
        """
    )
    op.execute(
        """
        CREATE VIEW dim_departments AS
        SELECT department_id, name
        FROM departments
        """
    )
    op.execute(
        """
        CREATE VIEW dim_cost_centers AS
        SELECT cost_center_id, department_id, code, name
        FROM cost_centers
        """
    )
    op.execute(
        """
        CREATE VIEW dim_suppliers AS
        SELECT supplier_id, region, category, risk_level, status
        FROM suppliers
        """
    )
    op.execute(
        """
        CREATE VIEW analytics_order_facts AS
        WITH payment_agg AS (
            SELECT
                order_id,
                MIN(method) FILTER (WHERE status = 'confirmed') AS payment_method,
                SUM(CASE WHEN status = 'confirmed' THEN fee_amount ELSE 0 END) AS fee_amount,
                SUM(
                    CASE WHEN status = 'confirmed' THEN refunded_amount ELSE 0 END
                ) AS refunded_amount
            FROM payments
            GROUP BY order_id
        ),
        line_items AS (
            SELECT
                o.order_id,
                oi.order_item_id,
                o.customer_id,
                oi.product_id,
                o.order_date,
                o.channel,
                o.status AS order_status,
                c.region,
                c.country_code,
                c.segment AS customer_segment,
                p.category,
                p.subcategory,
                p.brand,
                COALESCE(pa.payment_method, 'unknown') AS payment_method,
                (oi.quantity * oi.unit_price)::numeric(18, 2) AS line_gross,
                (oi.quantity * oi.unit_cost)::numeric(18, 2) AS product_cost,
                o.gross_amount,
                o.discount_amount,
                COALESCE(pa.fee_amount, 0)::numeric(18, 2) AS fee_amount,
                COALESCE(pa.refunded_amount, 0)::numeric(18, 2) AS refunded_amount
            FROM order_items oi
            JOIN orders o ON o.order_id = oi.order_id
            JOIN customers c ON c.customer_id = o.customer_id
            JOIN products p ON p.product_id = oi.product_id
            LEFT JOIN payment_agg pa ON pa.order_id = o.order_id
        )
        SELECT
            order_id,
            order_item_id,
            customer_id,
            product_id,
            order_date AS date,
            order_date,
            EXTRACT(YEAR FROM order_date)::integer AS year,
            EXTRACT(QUARTER FROM order_date)::integer AS quarter,
            EXTRACT(MONTH FROM order_date)::integer AS month,
            EXTRACT(WEEK FROM order_date)::integer AS week,
            region,
            country_code,
            customer_segment,
            channel,
            category,
            subcategory,
            brand,
            payment_method,
            CASE
                WHEN order_status = 'cancelled' THEN 0
                ELSE line_gross
            END::numeric(18, 2) AS gross_revenue,
            CASE
                WHEN order_status = 'cancelled' THEN 0
                ELSE (
                    line_gross
                    - COALESCE(discount_amount * line_gross / NULLIF(gross_amount, 0), 0)
                    - COALESCE(refunded_amount * line_gross / NULLIF(gross_amount, 0), 0)
                )
            END::numeric(18, 2) AS net_revenue,
            CASE
                WHEN order_status = 'cancelled' THEN 0
                ELSE (
                    line_gross
                    - COALESCE(discount_amount * line_gross / NULLIF(gross_amount, 0), 0)
                    - COALESCE(refunded_amount * line_gross / NULLIF(gross_amount, 0), 0)
                    - product_cost
                    - COALESCE(fee_amount * line_gross / NULLIF(gross_amount, 0), 0)
                )
            END::numeric(18, 2) AS contribution_margin,
            CASE
                WHEN order_status = 'cancelled' THEN NULL
                ELSE customer_id
            END AS active_customer_id
        FROM line_items
        """
    )
    op.execute(
        """
        CREATE VIEW analytics_expense_facts AS
        SELECT
            e.expense_id,
            e.expense_date AS date,
            e.expense_date,
            EXTRACT(YEAR FROM e.expense_date)::integer AS year,
            EXTRACT(QUARTER FROM e.expense_date)::integer AS quarter,
            EXTRACT(MONTH FROM e.expense_date)::integer AS month,
            EXTRACT(WEEK FROM e.expense_date)::integer AS week,
            e.department_id,
            d.name AS department,
            e.cost_center_id,
            cc.code AS cost_center,
            e.category AS expense_category,
            CASE
                WHEN e.status = 'approved' THEN e.amount
                ELSE 0
            END::numeric(18, 2) AS expense_amount,
            e.status
        FROM expenses e
        JOIN departments d ON d.department_id = e.department_id
        JOIN cost_centers cc ON cc.cost_center_id = e.cost_center_id
        """
    )
    op.execute(
        """
        CREATE VIEW analytics_budget_facts AS
        WITH expense_months AS (
            SELECT
                date_trunc('month', expense_date)::date AS month,
                cost_center_id,
                category,
                SUM(
                    CASE WHEN status = 'approved' THEN amount ELSE 0 END
                )::numeric(18, 2) AS expense_amount
            FROM expenses
            GROUP BY 1, 2, 3
        )
        SELECT
            b.budget_id,
            b.month AS date,
            EXTRACT(YEAR FROM b.month)::integer AS year,
            EXTRACT(QUARTER FROM b.month)::integer AS quarter,
            EXTRACT(MONTH FROM b.month)::integer AS month,
            b.cost_center_id,
            cc.code AS cost_center,
            cc.department_id,
            d.name AS department,
            b.category AS expense_category,
            b.budget_amount::numeric(18, 2) AS budget_amount,
            COALESCE(em.expense_amount, 0)::numeric(18, 2) AS expense_amount,
            (COALESCE(em.expense_amount, 0) - b.budget_amount)::numeric(18, 2) AS budget_variance,
            CASE
                WHEN b.budget_amount = 0 THEN NULL
                ELSE (
                    (COALESCE(em.expense_amount, 0) - b.budget_amount)
                    / b.budget_amount
                )::numeric(18, 6)
            END AS budget_variance_pct
        FROM budgets b
        JOIN cost_centers cc ON cc.cost_center_id = b.cost_center_id
        JOIN departments d ON d.department_id = cc.department_id
        LEFT JOIN expense_months em
            ON em.month = b.month
            AND em.cost_center_id = b.cost_center_id
            AND em.category = b.category
        """
    )
    op.execute(
        """
        CREATE VIEW analytics_contract_facts AS
        SELECT
            c.contract_id,
            c.supplier_id,
            c.start_date AS date,
            c.start_date,
            c.end_date,
            EXTRACT(YEAR FROM c.start_date)::integer AS year,
            EXTRACT(QUARTER FROM c.start_date)::integer AS quarter,
            EXTRACT(MONTH FROM c.start_date)::integer AS month,
            s.region,
            s.category AS supplier_category,
            c.risk_level AS contract_risk,
            c.status,
            CASE
                WHEN c.status = 'terminated' THEN 0
                ELSE c.annual_value
            END::numeric(18, 2) AS active_contract_value
        FROM contracts c
        JOIN suppliers s ON s.supplier_id = c.supplier_id
        """
    )


def _create_readonly_role() -> None:
    op.execute(
        """
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'semplan_readonly') THEN
                CREATE ROLE semplan_readonly LOGIN PASSWORD 'semplan_readonly_password';
            ELSE
                ALTER ROLE semplan_readonly LOGIN PASSWORD 'semplan_readonly_password';
            END IF;
        END
        $$;
        """
    )
    op.execute("ALTER ROLE semplan_readonly SET default_transaction_read_only = on")
    op.execute("GRANT USAGE ON SCHEMA public TO semplan_readonly")
    op.execute(
        """
        GRANT SELECT ON
            analytics_order_facts,
            analytics_expense_facts,
            analytics_budget_facts,
            analytics_contract_facts,
            dim_calendar,
            dim_customers,
            dim_products,
            dim_departments,
            dim_cost_centers,
            dim_suppliers
        TO semplan_readonly
        """
    )


def _drop_readonly_role() -> None:
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'semplan_readonly') THEN
                REVOKE ALL PRIVILEGES ON SCHEMA public FROM semplan_readonly;
                REVOKE ALL PRIVILEGES ON ALL TABLES IN SCHEMA public FROM semplan_readonly;
                DROP ROLE semplan_readonly;
            END IF;
        END
        $$;
        """
    )
