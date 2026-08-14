"""Shared pytest safeguards."""

from __future__ import annotations

import socket
from collections.abc import Iterator
from typing import Any

import pytest


@pytest.fixture(autouse=True)
def block_network(
    request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch
) -> Iterator[None]:
    """Fail ordinary tests that attempt network access."""

    if request.node.get_closest_marker("allow_network") is not None:
        yield
        return

    def blocked(*_args: Any, **_kwargs: Any) -> None:
        raise AssertionError("Network access is disabled for ordinary tests")

    monkeypatch.setattr(socket, "create_connection", blocked)
    monkeypatch.setattr(socket.socket, "connect", blocked)
    yield
