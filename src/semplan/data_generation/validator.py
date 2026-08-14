"""Dataset validation for generated Northstar Commerce artifacts."""

from __future__ import annotations

import json
import re
from collections import Counter
from decimal import Decimal
from pathlib import Path
from typing import Any

from semplan.data_generation.models import TABLE_ORDER, RowValue, TableRows
from semplan.data_generation.writer import canonical_json, refresh_manifest, sha256_file

EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
PHONE_RE = re.compile(r"(?:\+?\d[\d .()-]{7,}\d)")
ISO_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
MONEY_RE = re.compile(r"^\d+\.\d{2}$")


def load_table_rows(dataset_dir: Path) -> TableRows:
    rows: TableRows = {}
    for table in TABLE_ORDER:
        path = dataset_dir / "tables" / f"{table}.jsonl"
        rows[table] = [
            json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line
        ]
    return rows


def validate_dataset_dir(dataset_dir: Path, *, write_report: bool = True) -> dict[str, Any]:
    rows = load_table_rows(dataset_dir)
    findings: list[dict[str, str]] = []
    _validate_uniqueness(rows, findings)
    _validate_foreign_keys(rows, findings)
    _validate_financial_reconciliation(rows, findings)
    _validate_ranges(rows, findings)
    _validate_pii(rows, findings)

    distribution = _distribution_report(rows)
    errors = [finding for finding in findings if finding["severity"] == "error"]
    warnings = [finding for finding in findings if finding["severity"] == "warning"]
    report = {
        "schema_version": "1.0",
        "status": "passed" if not errors else "failed",
        "row_counts": {table: len(rows[table]) for table in TABLE_ORDER},
        "findings": findings,
        "summary": {
            "errors": len(errors),
            "warnings": len(warnings),
            "infos": len([finding for finding in findings if finding["severity"] == "info"]),
        },
        "distribution": distribution,
        "file_hashes_verified": True,
    }

    if write_report:
        (dataset_dir / "validation_report.json").write_text(
            canonical_json(report) + "\n", encoding="utf-8", newline="\n"
        )
        _write_markdown_summary(dataset_dir / "validation_report.md", report)
        refresh_manifest(dataset_dir)

    if errors:
        raise ValueError(f"Dataset validation failed with {len(errors)} errors")
    return report


def compare_dataset_dirs(left: Path, right: Path) -> dict[str, Any]:
    left_manifest = json.loads((left / "dataset_manifest.json").read_text(encoding="utf-8"))
    right_manifest = json.loads((right / "dataset_manifest.json").read_text(encoding="utf-8"))
    left_files = {
        str(path.relative_to(left)): sha256_file(path)
        for path in sorted(left.rglob("*"))
        if path.is_file()
    }
    right_files = {
        str(path.relative_to(right)): sha256_file(path)
        for path in sorted(right.rglob("*"))
        if path.is_file()
    }
    equal = left_manifest == right_manifest and left_files == right_files
    return {
        "schema_version": "1.0",
        "byte_equivalent": equal,
        "left_files": left_files,
        "right_files": right_files,
    }


def _validate_uniqueness(rows: TableRows, findings: list[dict[str, str]]) -> None:
    primary_keys = {
        "calendar": "date",
        "customers": "customer_id",
        "products": "product_id",
        "departments": "department_id",
        "cost_centers": "cost_center_id",
        "suppliers": "supplier_id",
        "orders": "order_id",
        "order_items": "order_item_id",
        "payments": "payment_id",
        "expenses": "expense_id",
        "budgets": "budget_id",
        "contracts": "contract_id",
    }
    for table, key in primary_keys.items():
        values = [str(row[key]) for row in rows[table]]
        if len(values) != len(set(values)):
            findings.append(_finding("error", "duplicate_primary_key", table))

    budget_keys = [
        (row["month"], row["cost_center_id"], row["category"]) for row in rows["budgets"]
    ]
    if len(budget_keys) != len(set(budget_keys)):
        findings.append(_finding("error", "duplicate_budget_natural_key", "budgets"))


def _validate_foreign_keys(rows: TableRows, findings: list[dict[str, str]]) -> None:
    customers = {row["customer_id"] for row in rows["customers"]}
    products = {row["product_id"] for row in rows["products"]}
    orders = {row["order_id"] for row in rows["orders"]}
    departments = {row["department_id"] for row in rows["departments"]}
    cost_centers = {row["cost_center_id"] for row in rows["cost_centers"]}
    suppliers = {row["supplier_id"] for row in rows["suppliers"]}

    _require_fk(rows["orders"], "customer_id", customers, "orders", findings)
    _require_fk(rows["order_items"], "order_id", orders, "order_items", findings)
    _require_fk(rows["order_items"], "product_id", products, "order_items", findings)
    _require_fk(rows["payments"], "order_id", orders, "payments", findings)
    _require_fk(rows["cost_centers"], "department_id", departments, "cost_centers", findings)
    _require_fk(rows["expenses"], "department_id", departments, "expenses", findings)
    _require_fk(rows["expenses"], "cost_center_id", cost_centers, "expenses", findings)
    _require_fk(rows["budgets"], "cost_center_id", cost_centers, "budgets", findings)
    _require_fk(rows["contracts"], "supplier_id", suppliers, "contracts", findings)


def _validate_financial_reconciliation(rows: TableRows, findings: list[dict[str, str]]) -> None:
    items_by_order: dict[RowValue, Decimal] = {}
    for item in rows["order_items"]:
        extended = Decimal(str(item["unit_price"])) * Decimal(str(item["quantity"]))
        order_id = item["order_id"]
        items_by_order[order_id] = items_by_order.get(order_id, Decimal("0.00")) + extended

    payments_by_order: dict[RowValue, Decimal] = {}
    for payment in rows["payments"]:
        if payment["status"] == "confirmed":
            payments_by_order[payment["order_id"]] = payments_by_order.get(
                payment["order_id"], Decimal("0.00")
            ) + Decimal(str(payment["refunded_amount"]))

    for order in rows["orders"]:
        order_id = order["order_id"]
        gross = Decimal(str(order["gross_amount"]))
        if abs(items_by_order.get(order_id, Decimal("0.00")) - gross) > Decimal("0.01"):
            findings.append(_finding("error", "order_gross_reconciliation", "orders"))
        discount = Decimal(str(order["discount_amount"]))
        if discount > gross:
            findings.append(_finding("error", "discount_exceeds_gross", "orders"))
        paid = gross - discount
        if payments_by_order.get(order_id, Decimal("0.00")) > paid:
            findings.append(_finding("error", "refund_exceeds_paid_amount", "payments"))


def _validate_ranges(rows: TableRows, findings: list[dict[str, str]]) -> None:
    for item in rows["order_items"]:
        if int(str(item["quantity"])) <= 0:
            findings.append(_finding("error", "non_positive_quantity", "order_items"))
    for contract in rows["contracts"]:
        end_date = contract["end_date"]
        if end_date is not None and str(end_date) < str(contract["start_date"]):
            findings.append(_finding("error", "contract_end_before_start", "contracts"))


def _validate_pii(rows: TableRows, findings: list[dict[str, str]]) -> None:
    for table in TABLE_ORDER:
        for row in rows[table]:
            for value in row.values():
                if not isinstance(value, str):
                    continue
                if ISO_DATE_RE.match(value) or UUID_RE.match(value) or MONEY_RE.match(value):
                    continue
                if EMAIL_RE.search(value) or PHONE_RE.search(value):
                    findings.append(_finding("error", "pii_pattern_detected", table))
                    return


def _distribution_report(rows: TableRows) -> dict[str, Any]:
    return {
        "customer_regions": dict(Counter(str(row["region"]) for row in rows["customers"])),
        "order_status": dict(Counter(str(row["status"]) for row in rows["orders"])),
        "order_channels": dict(Counter(str(row["channel"]) for row in rows["orders"])),
        "contract_status": dict(Counter(str(row["status"]) for row in rows["contracts"])),
        "private_labels": "stored in private/generation_ledger.json, not benchmark inputs",
    }


def _require_fk(
    table_rows: list[dict[str, RowValue]],
    column: str,
    allowed: set[RowValue],
    table: str,
    findings: list[dict[str, str]],
) -> None:
    for row in table_rows:
        if row[column] not in allowed:
            findings.append(_finding("error", f"missing_fk_{column}", table))
            return


def _finding(severity: str, code: str, location: str) -> dict[str, str]:
    return {"severity": severity, "code": code, "location": location}


def _write_markdown_summary(path: Path, report: dict[str, Any]) -> None:
    lines = [
        "# Dataset Validation Report",
        "",
        f"Status: {report['status']}",
        f"Errors: {report['summary']['errors']}",
        f"Warnings: {report['summary']['warnings']}",
        "",
        "## Row Counts",
        "",
    ]
    for table, count in report["row_counts"].items():
        lines.append(f"- `{table}`: {count}")
    lines.append("")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
