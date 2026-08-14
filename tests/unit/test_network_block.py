from __future__ import annotations

import socket

import pytest


def test_ordinary_tests_block_network_access() -> None:
    with pytest.raises(AssertionError, match="Network access is disabled"):
        socket.create_connection(("example.com", 80))
