# Implementation baseline

Publication work covers [issue #1](https://github.com/ibrahiem9/bot-trader/issues/1)
and offline CI in [issue #2](https://github.com/ibrahiem9/bot-trader/issues/2).
The baseline includes the frozen protocol, research engine, inactive paper
runner, deployment examples, locked dependencies and synthetic behavior tests.
It establishes software behavior, not historical performance or service readiness.

Published for review in [PR #13](https://github.com/ibrahiem9/bot-trader/pull/13).
Implementation commit `02cfe4253943116255635a5c17365e33ddf878b4` passed a fresh
macOS checkout installation, all 156 tests, Ruff and CLI help. Workflow correction
`dc84a2f` then passed the same checks in
[GitHub Actions on Linux](https://github.com/ibrahiem9/bot-trader/actions/runs/35417538824).
The local suite reported 575 dependency/timestamp deprecation warnings.

## Reproduce the checks

Use a fresh checkout and Python 3.12 with uv 0.12.14:

```sh
git rev-parse HEAD
uv sync --locked --extra dev --python 3.12
uv run --offline --no-sync pytest -q
uv run --offline --no-sync ruff check src tests
uv run --offline --no-sync bot-trader --help
```

Record the printed commit with each later experiment. Dependency installation
downloads packages; the test suite uses fake services and blocks socket
connections. No credentials, market downloads or external notifications are
needed. CI runs these checks on Linux; local verification uses macOS.

## Known follow-ups

- NumPy timedelta deprecations occur in the calendar/pandas integration and in
  timestamp arithmetic in the application and fixtures. The Alpaca SDK also
  imports deprecated `websockets.legacy`. Keep warnings visible. A dependency
  compatibility update must re-run calendar-boundary, signal-timing, ingestion
  and risk-replay tests before changing the lockfile; suppressing warnings is
  not a compatibility fix.
- Historical input admission remains blocked by market-data gaps and unreviewed
  corporate actions and fees. See [comparison status](comparison.md) and issues
  #3–#6. The documented daily/minute scale and dividend-adjustment cross-checks
  need resolution before real data is admitted.
- Staged development/validation review before held-out evaluation is tracked
  in #7. The existing command currently evaluates all periods in one run.
- External paper fills, notification delivery and deployment remain unverified
  (#8–#9); neither strategy is qualified or active.

Private account and purchase notes were excluded from publication. Research
documents retain only the source-access status needed to explain the blocker.
