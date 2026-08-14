from __future__ import annotations

from semplan.db import (
    ADMIN_DATABASE_URL,
    READONLY_DATABASE_URL,
    admin_database_url,
    readonly_database_url,
)


def test_admin_database_url_uses_default(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.delenv("SEMPLAN_DATABASE_URL", raising=False)

    assert admin_database_url() == ADMIN_DATABASE_URL


def test_admin_database_url_uses_env(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setenv("SEMPLAN_DATABASE_URL", "postgresql+psycopg://local/test")

    assert admin_database_url() == "postgresql+psycopg://local/test"


def test_readonly_database_url_uses_default(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.delenv("SEMPLAN_READONLY_DATABASE_URL", raising=False)

    assert readonly_database_url() == READONLY_DATABASE_URL


def test_readonly_database_url_uses_env(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setenv("SEMPLAN_READONLY_DATABASE_URL", "postgresql+psycopg://readonly/test")

    assert readonly_database_url() == "postgresql+psycopg://readonly/test"
