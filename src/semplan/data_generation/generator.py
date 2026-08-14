"""Deterministic synthetic data generator for Northstar Commerce."""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal
from random import Random

from semplan.data_generation.determinism import (
    child_seed,
    decimal_cents,
    deterministic_uuid,
    money,
    money_str,
)
from semplan.data_generation.models import (
    END_DATE,
    PROFILES,
    REFERENCE_DATE,
    START_DATE,
    TABLE_ORDER,
    DataProfile,
    GeneratedDataset,
    RowValue,
)

REGIONS = ["North", "South", "East", "West", "Central"]
COUNTRIES = [f"NC{i:02d}" for i in range(1, 13)]
SEGMENTS = ["consumer", "small_business", "mid_market", "enterprise", "education", "public"]
CHANNELS = ["online", "marketplace", "retail", "wholesale", "partner", "mobile"]
PAYMENT_METHODS = ["card", "debit", "wallet", "bank_transfer", "invoice", "voucher"]
PRODUCT_CATEGORIES = [f"category_{i:02d}" for i in range(1, 13)]
EXPENSE_CATEGORIES = [f"expense_{i:02d}" for i in range(1, 13)]
SUPPLIER_CATEGORIES = [f"supplier_category_{i:02d}" for i in range(1, 9)]
RISK_LEVELS = ["low", "medium", "high", "critical"]
MONTH_NAMES = [
    "January",
    "February",
    "March",
    "April",
    "May",
    "June",
    "July",
    "August",
    "September",
    "October",
    "November",
    "December",
]


def generate_dataset(profile_name: str, seed: int) -> GeneratedDataset:
    profile = PROFILES[profile_name]
    child_seeds = {
        name: child_seed(seed, name)
        for name in [
            "calendar",
            "customers",
            "products",
            "departments",
            "suppliers",
            "orders",
            "expenses",
            "budgets",
            "contracts",
        ]
    }

    rows: dict[str, list[dict[str, RowValue]]] = {table: [] for table in TABLE_ORDER}
    rows["calendar"] = _calendar_rows()
    rows["departments"], rows["cost_centers"] = _department_rows(profile)
    rows["customers"] = _customer_rows(profile, Random(child_seeds["customers"]))
    products, product_meta = _product_rows(profile, Random(child_seeds["products"]))
    rows["products"] = products
    suppliers = _supplier_rows(profile, Random(child_seeds["suppliers"]))
    rows["suppliers"] = suppliers

    order_rows, item_rows, payment_rows, order_ledger = _order_rows(
        profile,
        rows["customers"],
        product_meta,
        Random(child_seeds["orders"]),
    )
    rows["orders"] = order_rows
    rows["order_items"] = item_rows
    rows["payments"] = payment_rows

    rows["budgets"] = _budget_rows(profile, rows["cost_centers"], Random(child_seeds["budgets"]))
    expense_rows, expense_ledger = _expense_rows(
        profile,
        rows["departments"],
        rows["cost_centers"],
        Random(child_seeds["expenses"]),
    )
    rows["expenses"] = expense_rows
    contract_rows, contract_ledger = _contract_rows(
        profile,
        suppliers,
        Random(child_seeds["contracts"]),
    )
    rows["contracts"] = contract_rows

    for table in TABLE_ORDER:
        rows[table] = _sort_rows(table, rows[table])

    pattern_manifest: dict[str, object] = {
        "schema_version": "1.0",
        "patterns": [
            {
                "id": "seasonality",
                "description": "Order and expense probabilities vary by calendar month.",
            },
            {
                "id": "channel_differences",
                "description": "Channel mix shifts toward online and mobile after 2025-01-01.",
            },
            {
                "id": "long_tail_concentration",
                "description": "Customer/product/supplier choices use squared random ranks.",
            },
            {
                "id": "controlled_overspend",
                "description": "Q4 approved expenses in expense_01 are deterministically uplifted.",
            },
            {
                "id": "expiring_contracts",
                "description": (
                    "A deterministic subset of contracts expires near the reference date."
                ),
            },
            {
                "id": "structural_shift",
                "description": "Order channel probabilities change from 2025 onward.",
            },
            {
                "id": "labeled_anomalies",
                "description": "Anomaly labels are written only to the private generation ledger.",
            },
        ],
    }
    private_ledger: dict[str, object] = {
        "schema_version": "1.0",
        "not_for_model_input": True,
        "labels": order_ledger + expense_ledger + contract_ledger,
    }

    return GeneratedDataset(
        profile=profile,
        seed=seed,
        child_seeds=child_seeds,
        rows=rows,
        pattern_manifest=pattern_manifest,
        private_ledger=private_ledger,
    )


def _date_range(start: date, end: date) -> list[date]:
    days = (end - start).days
    return [start + timedelta(days=offset) for offset in range(days + 1)]


def _months() -> list[date]:
    months: list[date] = []
    year = START_DATE.year
    month = START_DATE.month
    while date(year, month, 1) <= END_DATE:
        months.append(date(year, month, 1))
        if month == 12:
            year += 1
            month = 1
        else:
            month += 1
    return months


def _calendar_rows() -> list[dict[str, RowValue]]:
    rows: list[dict[str, RowValue]] = []
    for day in _date_range(START_DATE, END_DATE):
        iso_calendar = day.isocalendar()
        rows.append(
            {
                "date": day.isoformat(),
                "year": day.year,
                "quarter": ((day.month - 1) // 3) + 1,
                "month": day.month,
                "month_name": MONTH_NAMES[day.month - 1],
                "iso_week": iso_calendar.week,
                "weekday": iso_calendar.weekday,
                "is_business_day": iso_calendar.weekday <= 5,
            }
        )
    return rows


def _department_rows(
    profile: DataProfile,
) -> tuple[list[dict[str, RowValue]], list[dict[str, RowValue]]]:
    departments: list[dict[str, RowValue]] = []
    cost_centers: list[dict[str, RowValue]] = []
    for index in range(profile.departments):
        department_id = deterministic_uuid("department", f"{index:03d}")
        departments.append({"department_id": department_id, "name": f"department_{index + 1:02d}"})

    for index in range(profile.cost_centers):
        department = departments[index % len(departments)]
        cost_centers.append(
            {
                "cost_center_id": deterministic_uuid("cost_center", f"{index:04d}"),
                "department_id": department["department_id"],
                "code": f"cc_{index + 1:03d}",
                "name": f"cost_center_{index + 1:03d}",
            }
        )
    return departments, cost_centers


def _customer_rows(profile: DataProfile, rng: Random) -> list[dict[str, RowValue]]:
    dates = _date_range(START_DATE, REFERENCE_DATE)
    rows: list[dict[str, RowValue]] = []
    for index in range(profile.customers):
        rows.append(
            {
                "customer_id": deterministic_uuid("customer", f"{index:08d}"),
                "created_at": dates[rng.randrange(len(dates))].isoformat(),
                "segment": SEGMENTS[index % len(SEGMENTS)],
                "region": REGIONS[index % len(REGIONS)],
                "country_code": COUNTRIES[index % len(COUNTRIES)],
                "status": _choice(rng, ["active", "inactive", "prospect"], [85, 10, 5]),
            }
        )
    return rows


def _product_rows(
    profile: DataProfile, rng: Random
) -> tuple[list[dict[str, RowValue]], list[dict[str, RowValue]]]:
    rows: list[dict[str, RowValue]] = []
    meta: list[dict[str, RowValue]] = []
    for index in range(profile.products):
        category = PRODUCT_CATEGORIES[index % len(PRODUCT_CATEGORIES)]
        unit_cost = money(decimal_cents(rng.randint(250, 18_000)))
        markup = Decimal(rng.randint(125, 260)) / Decimal(100)
        list_price = money(unit_cost * markup)
        product_id = deterministic_uuid("product", f"{index:06d}")
        row: dict[str, RowValue] = {
            "product_id": product_id,
            "category": category,
            "subcategory": f"{category}_sub_{(index % 5) + 1:02d}",
            "brand": f"synthetic_brand_{(index % 20) + 1:02d}",
            "unit_cost": money_str(unit_cost),
            "list_price": money_str(list_price),
            "active": index % 19 != 0,
        }
        rows.append(row)
        meta.append(row)
    return rows, meta


def _supplier_rows(profile: DataProfile, rng: Random) -> list[dict[str, RowValue]]:
    rows: list[dict[str, RowValue]] = []
    for index in range(profile.suppliers):
        rows.append(
            {
                "supplier_id": deterministic_uuid("supplier", f"{index:07d}"),
                "region": REGIONS[index % len(REGIONS)],
                "category": SUPPLIER_CATEGORIES[index % len(SUPPLIER_CATEGORIES)],
                "risk_level": _choice(rng, RISK_LEVELS, [65, 22, 10, 3]),
                "status": _choice(rng, ["active", "inactive", "suspended"], [86, 11, 3]),
            }
        )
    return rows


def _order_rows(
    profile: DataProfile,
    customers: list[dict[str, RowValue]],
    products: list[dict[str, RowValue]],
    rng: Random,
) -> tuple[
    list[dict[str, RowValue]],
    list[dict[str, RowValue]],
    list[dict[str, RowValue]],
    list[dict[str, object]],
]:
    order_rows: list[dict[str, RowValue]] = []
    item_rows: list[dict[str, RowValue]] = []
    payment_rows: list[dict[str, RowValue]] = []
    ledger: list[dict[str, object]] = []
    dates = _weighted_dates()

    for order_index in range(profile.orders):
        customer = _long_tail_pick(rng, customers)
        order_date = dates[rng.randrange(len(dates))]
        channel = _channel_for_date(order_date, rng)
        status = _choice(
            rng, ["placed", "paid", "shipped", "cancelled", "refunded"], [5, 45, 44, 4, 2]
        )
        order_id = deterministic_uuid("order", f"{profile.name}:{order_index:09d}")
        item_count = rng.randint(profile.min_items_per_order, profile.max_items_per_order)
        gross_total = Decimal("0.00")

        for item_number in range(item_count):
            product = _long_tail_pick(rng, products)
            quantity = rng.randint(1, 5)
            unit_price = money(Decimal(str(product["list_price"])))
            unit_cost = money(Decimal(str(product["unit_cost"])))
            gross_total += money(unit_price * quantity)
            item_rows.append(
                {
                    "order_item_id": deterministic_uuid(
                        "order_item", f"{profile.name}:{order_index:09d}:{item_number:02d}"
                    ),
                    "order_id": order_id,
                    "product_id": product["product_id"],
                    "quantity": quantity,
                    "unit_price": money_str(unit_price),
                    "unit_cost": money_str(unit_cost),
                }
            )

        gross_total = money(gross_total)
        discount_rate = Decimal(_choice(rng, [0, 3, 5, 8, 12, 18], [35, 25, 18, 12, 7, 3]))
        discount_amount = money(gross_total * discount_rate / Decimal(100))
        paid_amount = money(max(gross_total - discount_amount, Decimal("0.00")))
        fee_rate = (
            Decimal("0.021") if channel in {"online", "mobile", "marketplace"} else Decimal("0.011")
        )
        fee_amount = money(paid_amount * fee_rate)
        refunded_amount = Decimal("0.00")
        if status == "refunded" or (status != "cancelled" and rng.randrange(100) < 3):
            refunded_amount = money(paid_amount * Decimal(rng.randint(5, 35)) / Decimal(100))

        order_rows.append(
            {
                "order_id": order_id,
                "customer_id": customer["customer_id"],
                "order_date": order_date.isoformat(),
                "channel": channel,
                "status": status,
                "gross_amount": money_str(gross_total),
                "discount_amount": money_str(discount_amount),
            }
        )
        payment_rows.append(
            {
                "payment_id": deterministic_uuid("payment", f"{profile.name}:{order_index:09d}"),
                "order_id": order_id,
                "payment_date": min(
                    order_date + timedelta(days=rng.randint(0, 5)), END_DATE
                ).isoformat(),
                "method": _choice(rng, PAYMENT_METHODS, [35, 20, 15, 14, 10, 6]),
                "fee_amount": money_str(fee_amount if status != "cancelled" else Decimal("0.00")),
                "refunded_amount": money_str(
                    refunded_amount if status != "cancelled" else Decimal("0.00")
                ),
                "status": "failed" if status == "cancelled" else "confirmed",
            }
        )
        if order_index % 997 == 0:
            ledger.append(
                {
                    "entity": "order",
                    "id": order_id,
                    "label": "labeled_order_anomaly",
                    "pattern": "labeled_anomalies",
                }
            )

    return order_rows, item_rows, payment_rows, ledger


def _budget_rows(
    profile: DataProfile,
    cost_centers: list[dict[str, RowValue]],
    rng: Random,
) -> list[dict[str, RowValue]]:
    rows: list[dict[str, RowValue]] = []
    categories = EXPENSE_CATEGORIES[: profile.budget_categories]
    for month in _months():
        seasonal_multiplier = Decimal("1.18") if month.month in {11, 12} else Decimal("1.00")
        for cost_center in cost_centers:
            for category in categories:
                base = decimal_cents(rng.randint(40_000, 220_000))
                amount = money(base * seasonal_multiplier)
                rows.append(
                    {
                        "budget_id": deterministic_uuid(
                            "budget",
                            f"{month.isoformat()}:{cost_center['cost_center_id']}:{category}",
                        ),
                        "month": month.isoformat(),
                        "cost_center_id": cost_center["cost_center_id"],
                        "category": category,
                        "budget_amount": money_str(amount),
                    }
                )
    return rows


def _expense_rows(
    profile: DataProfile,
    departments: list[dict[str, RowValue]],
    cost_centers: list[dict[str, RowValue]],
    rng: Random,
) -> tuple[list[dict[str, RowValue]], list[dict[str, object]]]:
    rows: list[dict[str, RowValue]] = []
    ledger: list[dict[str, object]] = []
    dates = _weighted_dates()
    department_by_id = {department["department_id"]: department for department in departments}
    for index in range(profile.expenses):
        cost_center = cost_centers[rng.randrange(len(cost_centers))]
        department = department_by_id[cost_center["department_id"]]
        expense_date = dates[rng.randrange(len(dates))]
        category = EXPENSE_CATEGORIES[index % len(EXPENSE_CATEGORIES)]
        amount = decimal_cents(rng.randint(1_500, 80_000))
        if expense_date.month in {10, 11, 12} and category == "expense_01":
            amount = money(amount * Decimal("1.55"))
        status = _choice(rng, ["draft", "approved", "rejected"], [5, 90, 5])
        expense_id = deterministic_uuid("expense", f"{profile.name}:{index:09d}")
        rows.append(
            {
                "expense_id": expense_id,
                "expense_date": expense_date.isoformat(),
                "department_id": department["department_id"],
                "cost_center_id": cost_center["cost_center_id"],
                "category": category,
                "amount": money_str(amount),
                "status": status,
            }
        )
        if index % 733 == 0:
            ledger.append(
                {
                    "entity": "expense",
                    "id": expense_id,
                    "label": "controlled_overspend_sample",
                    "pattern": "controlled_overspend",
                }
            )
    return rows, ledger


def _contract_rows(
    profile: DataProfile,
    suppliers: list[dict[str, RowValue]],
    rng: Random,
) -> tuple[list[dict[str, RowValue]], list[dict[str, object]]]:
    rows: list[dict[str, RowValue]] = []
    ledger: list[dict[str, object]] = []
    dates = _date_range(START_DATE, REFERENCE_DATE)
    for index in range(profile.contracts):
        supplier = _long_tail_pick(rng, suppliers)
        start_date = dates[rng.randrange(len(dates))]
        duration_days = rng.randint(180, 1_460)
        end_date_value: date | None = start_date + timedelta(days=duration_days)
        if index % 17 == 0:
            end_date_value = REFERENCE_DATE + timedelta(days=rng.randint(1, 60))
        if index % 29 == 0:
            end_date_value = None
        annual_value = money(decimal_cents(rng.randint(25_000, 2_500_000)))
        status = _contract_status(start_date, end_date_value, rng)
        contract_id = deterministic_uuid("contract", f"{profile.name}:{index:09d}")
        rows.append(
            {
                "contract_id": contract_id,
                "supplier_id": supplier["supplier_id"],
                "start_date": start_date.isoformat(),
                "end_date": end_date_value.isoformat() if end_date_value is not None else None,
                "annual_value": money_str(annual_value),
                "status": status,
                "risk_level": _choice(rng, RISK_LEVELS, [60, 25, 12, 3]),
            }
        )
        if end_date_value is not None and 0 <= (end_date_value - REFERENCE_DATE).days <= 60:
            ledger.append(
                {
                    "entity": "contract",
                    "id": contract_id,
                    "label": "expiring_contract",
                    "pattern": "expiring_contracts",
                }
            )
    return rows, ledger


def _weighted_dates() -> list[date]:
    weighted: list[date] = []
    month_weights = {
        1: 8,
        2: 8,
        3: 9,
        4: 10,
        5: 10,
        6: 11,
        7: 12,
        8: 12,
        9: 13,
        10: 16,
        11: 20,
        12: 22,
    }
    for day in _date_range(START_DATE, END_DATE):
        weighted.extend([day] * month_weights[day.month])
    return weighted


def _channel_for_date(order_date: date, rng: Random) -> str:
    if order_date >= date(2025, 1, 1):
        return _choice(rng, CHANNELS, [30, 24, 17, 8, 8, 13])
    return _choice(rng, CHANNELS, [20, 16, 31, 12, 11, 10])


def _contract_status(start_date: date, end_date: date | None, rng: Random) -> str:
    if start_date > REFERENCE_DATE:
        return "draft"
    if end_date is not None and end_date < REFERENCE_DATE:
        return "expired"
    if rng.randrange(100) < 4:
        return "terminated"
    return "active"


def _choice[T](rng: Random, values: list[T], weights: list[int]) -> T:
    total = sum(weights)
    draw = rng.randrange(total)
    running = 0
    for value, weight in zip(values, weights, strict=True):
        running += weight
        if draw < running:
            return value
    return values[-1]


def _long_tail_pick(rng: Random, rows: list[dict[str, RowValue]]) -> dict[str, RowValue]:
    index = min(int((rng.random() ** 2) * len(rows)), len(rows) - 1)
    return rows[index]


def _sort_rows(table: str, rows: list[dict[str, RowValue]]) -> list[dict[str, RowValue]]:
    key_map = {
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
    key = key_map[table]
    return sorted(rows, key=lambda row: str(row[key]))
