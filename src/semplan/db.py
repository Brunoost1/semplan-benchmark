"""Database connection helpers for local validation commands and tests."""

from __future__ import annotations

import os

ADMIN_DATABASE_URL = (
    "postgresql+psycopg://semplan_admin:semplan_local_password@localhost:55432/semplan"
)
READONLY_DATABASE_URL = (
    "postgresql+psycopg://semplan_readonly:semplan_readonly_password@localhost:55432/semplan"
)


def admin_database_url() -> str:
    return os.environ.get("SEMPLAN_DATABASE_URL", ADMIN_DATABASE_URL)


def readonly_database_url() -> str:
    return os.environ.get("SEMPLAN_READONLY_DATABASE_URL", READONLY_DATABASE_URL)
