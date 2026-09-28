"""Launch the installed official MCP server using private paper credentials."""
from __future__ import annotations

import os
from pathlib import Path

from bot_trader.data import credentials


def launch() -> None:
    # This installation deliberately uses the already-saved paper credential file.
    # No key is copied into Codex configuration or passed on the command line.
    for name in ("APCA_API_KEY_ID", "APCA_API_SECRET_KEY"):
        os.environ.pop(name, None)
    os.environ["BOT_TRADER_CREDENTIALS_FILE"] = str(
        Path.home() / ".local/share/bot-trader/credentials.env"
    )
    key, secret = credentials()
    environment = os.environ.copy()
    environment.update(
        ALPACA_API_KEY=key,
        ALPACA_SECRET_KEY=secret,
        ALPACA_PAPER_TRADE="true",
        ALPACA_TOOLSETS="assets,stock-data,corporate-actions,news",
        DATA_API_URL="https://data.alpaca.markets",
    )
    executable = str(Path.home() / ".local/bin/alpaca-mcp-server")
    os.execve(executable, [executable, "--transport", "stdio"], environment)


if __name__ == "__main__":
    launch()
