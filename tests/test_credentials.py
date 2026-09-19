from pathlib import Path
from types import SimpleNamespace

import pytest

from bot_trader.data import DataError, credentials, default_home


def credential_file(path, text="APCA_API_KEY_ID=file-key\nAPCA_API_SECRET_KEY=file-secret\n", mode=0o600):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    path.chmod(mode)
    return path


def test_default_file_fallback():
    credential_file(default_home() / "credentials.env")
    assert credentials() == ("file-key", "file-secret")


def test_explicit_file_fallback(tmp_path, monkeypatch):
    path = credential_file(tmp_path / "custom.env")
    monkeypatch.setenv("BOT_TRADER_CREDENTIALS_FILE", str(path))
    assert credentials() == ("file-key", "file-secret")


def test_complete_environment_takes_precedence(monkeypatch):
    credential_file(default_home() / "credentials.env", "malformed", mode=0o644)
    monkeypatch.setenv("APCA_API_KEY_ID", "environment-key")
    monkeypatch.setenv("APCA_API_SECRET_KEY", "environment-secret")
    assert credentials() == ("environment-key", "environment-secret")


@pytest.mark.parametrize("key,secret", [("key", None), (None, "secret"), ("", ""), ("key", ""), (" ", "secret")])
def test_incomplete_environment_never_uses_file(monkeypatch, key, secret):
    credential_file(default_home() / "credentials.env")
    for name, value in (("APCA_API_KEY_ID", key), ("APCA_API_SECRET_KEY", secret)):
        if value is not None:
            monkeypatch.setenv(name, value)
    with pytest.raises(DataError, match="Set both"):
        credentials()


@pytest.mark.parametrize("mode", [0o640, 0o604, 0o601, 0o610, 0o666])
def test_group_or_world_access_rejected(mode):
    credential_file(default_home() / "credentials.env", mode=mode)
    with pytest.raises(DataError, match="permissions"):
        credentials()


@pytest.mark.parametrize("contents", [
    "APCA_API_KEY_ID=private-value\n",
    "APCA_API_KEY_ID=private-value\nAPCA_API_SECRET_KEY=\n",
    "APCA_API_KEY_ID=private-value\nAPCA_API_SECRET_KEY=' '\n",
    "APCA_API_KEY_ID=private-value\nAPCA_API_KEY_ID=duplicate\nAPCA_API_SECRET_KEY=x\n",
    "export APCA_API_KEY_ID=private-value\nAPCA_API_SECRET_KEY=x\n",
    "APCA_API_KEY_ID=private-value\ninvalid-line\n",
    "APCA_API_KEY_ID=private-value\nAPCA_API_SECRET_KEY='unterminated\n",
])
def test_malformed_file_rejected_without_disclosing_values(contents):
    credential_file(default_home() / "credentials.env", contents)
    with pytest.raises(DataError) as exc:
        credentials()
    assert "private-value" not in str(exc.value)


def test_values_are_literal_without_interpolation(tmp_path, monkeypatch):
    marker = tmp_path / "must-not-exist"
    monkeypatch.setenv("CREDENTIAL_TEST_VALUE", "expanded")
    literal = f"$(touch {marker})`touch {marker}`"
    credential_file(default_home() / "credentials.env", (
        "# Credentials remain inert\n\n"
        "APCA_API_KEY_ID='${CREDENTIAL_TEST_VALUE}'\n"
        f'APCA_API_SECRET_KEY="{literal}"\n'
    ))
    assert credentials() == ("${CREDENTIAL_TEST_VALUE}", literal)
    assert not marker.exists()


def test_checkout_file_rejected_before_reading(monkeypatch):
    monkeypatch.setenv("BOT_TRADER_CREDENTIALS_FILE", str(Path(__file__).resolve()))
    with pytest.raises(DataError, match="outside the repository"):
        credentials()


def test_missing_file_has_actionable_error():
    with pytest.raises(DataError, match="APCA_API_KEY_ID"):
        credentials()


def test_broker_uses_file_and_ignores_live_endpoint(monkeypatch):
    from alpaca.trading import client

    from bot_trader.broker import AlpacaBroker

    credential_file(default_home() / "credentials.env", (
        "APCA_API_KEY_ID=file-key\nAPCA_API_SECRET_KEY=file-secret\n"
        "APCA_API_BASE_URL=https://api.alpaca.markets\n"
    ))
    captured = {}

    def trading_client(*args, **kwargs):
        captured.update(args=args, kwargs=kwargs)
        return SimpleNamespace(_session=SimpleNamespace(request=lambda: None))

    monkeypatch.setattr(client, "TradingClient", trading_client)
    AlpacaBroker()
    assert captured == {"args": ("file-key", "file-secret"), "kwargs": {"paper": True}}
