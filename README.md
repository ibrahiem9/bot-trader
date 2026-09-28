# ETF research and Alpaca paper bot

This project tests two frozen ETF strategies against a passive basket and BIL.
It includes reproducible simulation, a data audit, comparison reports and a
paper-only runner. **No strategy is active. Authentication has been verified;
historical results remain blocked by data gaps and unreviewed action/fee history.**

Read the [comparison status](reports/comparison.md),
[frozen research protocol](research/strategy-evidence.md), and
[proposal initiative](initiatives/2026-09-17-trading-research.md).
Neither candidate is a recommendation to invest.

## What is implemented

Trend following evaluates the six ETFs weekly against their 200-session average.
Swing rebound evaluates daily, entering above that average with Wilder RSI(2)
below 10 and exiting above RSI 50 or after five completed held sessions.
The shared strategy code sizes each entry at at most one-twelfth of equity with
a 50% total submission exposure cap. Each experiment starts with $5,000 and
never borrows. Holdings may remain overnight and over weekends.

Signals use completed split-adjusted daily closes. Simulation executes against
the following exchange session's raw 09:35 Eastern minute open. Raw accounting
handles splits and dividend receivables, paying dividends into cash on their
payment dates. Risk replay observes every regular-session minute close. A 10%
drawdown from observed peak causes a persistent halt and next-minute liquidation;
execution gaps can exceed 10%. Benchmarks do not inherit that stop.

The runner hardcodes Alpaca paper operation. SQLite preserves order intentions
before submission, fills, account peak and halts. Ambiguous submissions are
reconciled by deterministic client IDs and never blindly retried. Unresolved
orders, stale signals, position discrepancies and notification failures block
entries. A qualified comparison plus explicit local activation is required.
There is no live-trading switch or automatic resume.

## Install and verify

Use Python 3.12 for the verified development and CI environment. Package metadata
allows Python 3.11 or newer, but other versions are not covered by CI.
`uv.lock` pins the tested dependencies; CI uses uv 0.12.14.

```sh
uv sync --locked --extra dev --python 3.12
uv run --no-sync pytest -q
uv run --no-sync ruff check src tests
uv run --no-sync bot-trader --help
```

Tests use synthetic prices and fake brokers. An automatic fixture blocks socket
connections during tests, and each test uses an isolated credential directory.
The [GitHub Actions workflow](.github/workflows/checks.yml) runs the suite, Ruff,
and CLI help on pull requests and updates to `main` without broker or AWS secrets.
Only dependency installation needs network access; the checks use `uv --offline`.
These checks do not measure trading performance or prove external services work.
Known NumPy timedelta and `websockets.legacy` deprecation warnings remain visible;
see the [implementation baseline](reports/implementation-baseline.md) for follow-up.

Work is tracked in the [GitHub roadmap](https://github.com/ibrahiem9/bot-trader/issues/12).

## Private runtime files

The default runtime directory is `~/.local/share/bot-trader`; override it with
`--home` or `BOT_TRADER_HOME`. The CLI rejects runtime paths inside this checkout.
Keep credentials, account settings, downloads, reports with account information,
and SQLite state outside this public repository. The examples below use an
external home directory. No command installs an AWS service or buys market data.

The bot reads a complete `APCA_API_KEY_ID` / `APCA_API_SECRET_KEY` environment
pair first. Otherwise, it reads `credentials.env` under `BOT_TRADER_HOME`
(default `~/.local/share/bot-trader`). Override that file location with
`BOT_TRADER_CREDENTIALS_FILE`. The file must be outside this checkout with no
group/world permissions, normally mode 0600. Its parent should be mode 0700.
An incomplete environment pair is an error; credentials from different sources
are never combined. Parsing does not execute shell commands or expand variables.
Alpaca execution still uses a hardcoded paper endpoint.

The file contains `APCA_API_KEY_ID=...` and `APCA_API_SECRET_KEY=...` on separate
lines. `--home` controls command outputs/state; `BOT_TRADER_HOME` also controls
the default credential-file location. Keep real values out of this repository.
See [deployment configuration](deploy/environment.example) for variable names.

## Acquire and admit data

```sh
uv run --no-sync bot-trader preflight
uv run --no-sync bot-trader ingest --feed iex
uv run --no-sync bot-trader audit
```

`preflight` makes read-only authentication and sample coverage checks before a
large download. It stores account details and results privately in `preflight.json`.
Coverage checks compare expected timestamps for each symbol; duplicate or
out-of-window bars cannot substitute for missing sessions or minutes. The report
includes missing, duplicate and unexpected counts, up to five missing/unexpected
timestamp examples per symbol, and exact 09:35 availability for the minute sample.
A failed sample proves complete coverage is absent; successful samples cannot
certify a full dataset. It also saves unreviewed provider corporate actions.
The recorded samples already fail this protocol; see the comparison status.

Ingestion requests 2016-01-01 through 2026-09-16 for SPY, IWM, EFA, EEM, IEF,
GLD and BIL. It stores raw, split-adjusted and fully adjusted daily bars plus raw
regular-session minute bars in Parquet. The SDK paginates requests. Year/symbol
partitions allow resume after interruption. Feed and date changes require a
separate `--dataset` directory. `sip` is available only if the account is already
entitled; the CLI never changes subscriptions or mixes feeds.

The free IEX feed may lack much of the required history or minute coverage.
Missing bars fail admission. The program does not forward-fill them or replace
them with another feed. Research can require several GB of RAM and disk; run
the full backtest on a suitable workstation, not the small paper server.

Ingestion saves the provider's corporate-action response as an unreviewed aid.
It cannot prove complete coverage. Prepare `<home>/data/actions.csv` with these
columns and one normalized row per ex-date and symbol:

```csv
session,symbol,split_ratio,dividend,pay_session
```

`split_ratio` is new shares divided by old shares (1 for dividend-only events).
`dividend` is raw cash per post-split share (0 for split-only events).
`pay_session` is the payment date, including dates after the research cutoff.
No row means no action only after independent coverage review. Verify distributions,
splits and unusual actions against issuer evidence; unsupported actions are a blocker.

Add `actions-review.json` alongside it. Supply factual values after review:

```json
{
  "approved": false,
  "reviewer": "",
  "evidence": [],
  "coverage_start": "2016-01-01",
  "coverage_end": "2026-09-16",
  "symbols": ["SPY", "IWM", "EFA", "EEM", "IEF", "GLD", "BIL"],
  "manifest_sha256": "",
  "actions_sha256": ""
}
```

Hashes are SHA-256 of `manifest.json` and `actions.csv`, respectively. For example,
`shasum -a 256 ~/.local/share/bot-trader/data/manifest.json` displays one hash.
The manifest hashes every bar file, so the review binds to the exact downloads.
Set `approved` to true only after the evidence establishes coverage. The audit
checks hashes, calendar/minute coverage, duplicates, prices and adjustment changes.
It writes a failed audit even when a review file is malformed.

## Run the frozen comparison

Create a private fee-schedule JSON with reviewed historical SEC, TAF and CAT
rates. Current rates cannot be applied to all years. Its structure is:

```json
{
  "reviewed": false,
  "reviewer": "",
  "sources": [],
  "coverage_start": "2017-01-01",
  "coverage_end": "2026-09-16",
  "schedule": []
}
```

Each schedule row needs `effective_date`, `sec_per_dollar`, `taf_per_share`,
`taf_maximum`, and `cat_per_share`. Include explicit zero rates where applicable,
all rate changes, and evidence for the entire period. Optional
`commission_per_order` defaults to zero. The simulator caps TAF per trade and
rounds each daily fee-type total upward to a cent.

```sh
uv run --no-sync bot-trader backtest --fees ~/.local/share/bot-trader/fees.json
uv run --no-sync bot-trader report
```

The backtest logs every attempt before execution. It evaluates development
(2017–2021), validation (2022–2023) and held-out (2024–2026-09-16) periods at
5, 10 and 20 basis points per side. All candidates and benchmarks use independent
$5,000 starts per period. The report includes returns, excess over cash, cash-excess
Sharpe, volatility, drawdown/recovery, turnover, exposure, trades, yearly results,
profit concentration, uncertainty and separate $15/$100 monthly expense overlays.
Personal taxes remain outside v1.

Outputs are `<home>/research/comparison.json`, `comparison.md`, `qualification.json`,
`attempts.jsonl`, and a held-out input/rule lock. Changed rules or data cannot reuse
that locked evaluation directory. Keep earlier attempts and design a new future
test if rules change after looking at held-out results. Do not delete history to
make a previously inspected interval appear unseen.

Admission failure produces a blocked report and empty qualification, with exit
code 2. Neither a missing credential nor a failed candidate is a reason to invent
returns. A passing screen still does not prove an edge.

## Paper operation

Use a dedicated, empty paper account with at most $5,000. Configure a confirmed
SNS email subscription and external CloudWatch heartbeat alarm before activation;
see [deployment instructions](deploy/README.md). These connections have not been
exercised by offline tests.

```sh
uv run --no-sync bot-trader status
uv run --no-sync bot-trader paper activate \
  --qualification ~/.local/share/bot-trader/research/qualification.json \
  --strategy trend
uv run --no-sync bot-trader paper run
uv run --no-sync bot-trader halt --reason "Manual review"
```

Use the candidate actually selected by the report. Activation recomputes its
screens and checks hashes against the installed research code. `paper once`
performs one risk/reconciliation tick without opening new signal positions.
The runner also blocks new signals if its research code changes after activation.
`status` reads local state only. `halt` persists shutdown even when credentials
are missing; broker cancellation/liquidation still requires connectivity.

The daemon monitors equity every five seconds by default, subject to network
latency. It sends daily summaries and weekly review reminders. Failed delivery
stays in a durable outbox. Closing the process is not a liquidation command.
Paper results omit some real-market effects, including dividends and regulatory
fees; see [Alpaca's limitations](https://docs.alpaca.markets/us/docs/paper-trading).
There is no automatic promotion to real money.

## Alpaca MCP

[Alpaca's official Trading MCP server](https://docs.alpaca.markets/us/docs/alpaca-mcp-server)
supports interactive data research and account operations. It uses
`ALPACA_API_KEY` and `ALPACA_SECRET_KEY`, with `ALPACA_PAPER_TRADE=true` for paper
operation. Research-only toolsets can be selected with
`ALPACA_TOOLSETS=stock-data,corporate-actions,assets,news`.
The SDK remains the reproducible backtest/runner interface. MCP uses the same
underlying data API and cannot establish missing historical coverage.

The local Codex installation now has an `alpaca` MCP entry using official server
version 2.3.2, installed with `uv tool install`. It launches through
[`deploy/alpaca_mcp.py`](deploy/alpaca_mcp.py), which reads the existing private
credential file and forces paper mode. Keys are absent from Codex configuration.
The entry allows 22 read-only market-data, corporate-action, asset, calendar and
documentation tools. Order execution and account changes are unavailable through
this entry. MCP initialization and an authenticated `get_clock` call passed.
Restart the Codex client to load the new configuration.
