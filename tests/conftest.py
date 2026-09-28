"""Keep tests isolated from real local credentials and application state."""
import socket

import pytest


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def blocked(*args, **kwargs):
        pytest.fail("Network access is prohibited in the offline test suite; use a fake service")

    monkeypatch.setattr(socket.socket, "connect", blocked)
    monkeypatch.setattr(socket.socket, "connect_ex", blocked)
    monkeypatch.setattr(socket, "create_connection", blocked)
    monkeypatch.setattr(socket, "getaddrinfo", blocked)


@pytest.fixture(autouse=True)
def isolated_credentials(monkeypatch, tmp_path):
    monkeypatch.setenv("BOT_TRADER_HOME", str(tmp_path / "bot-trader-home"))
    for name in ("APCA_API_KEY_ID", "APCA_API_SECRET_KEY", "BOT_TRADER_CREDENTIALS_FILE"):
        monkeypatch.delenv(name, raising=False)
