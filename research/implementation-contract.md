# Internal implementation contract

This file records interfaces agreed before parallel implementation. The frozen
experiment specification in strategy-evidence.md governs research decisions.

- `UNIVERSE = ('SPY','IWM','EFA','EEM','IEF','GLD')`; cash benchmark `BIL`.
- `config.py` and `strategy.py` owned by research worker. Expose `UNIVERSE`,
  `CASH_SYMBOL`, `INITIAL_EQUITY=5000.0`, `ENTRY_FRACTION=1/12`,
  `MAX_EXPOSURE=0.5`, `DRAWDOWN_LIMIT=0.1` in config.
- `strategy.decide(strategy, closes, held_sessions, weekly)` returns dict
  symbol -> 'buy' or 'sell'. `closes`: split-adjusted completed daily closes,
  ascending DatetimeIndex (naive midnight session labels); `held_sessions`:
  dict symbol -> number of completed held sessions. `weekly` is True when
  the completed session is the last exchange session of its ISO week.
  No pyramiding or same-session re-entry. Signals use 200-session SMA and
  genuinely Wilder-seeded RSI(2).
- `strategy.entry_notional(equity, cash, exposure, reserved=0.0)` returns
  nonnegative dollar budget capped at equity/12 and 50% exposure and cash,
  with 0.5% cash buffer; budget applies only at submission.
- Research inputs: `daily` DataFrame columns `session` (naive midnight),
  `symbol`, `open`, `high`, `low`, `close`, `volume`, `signal_close`
  (split-adjusted close); `minutes` columns `timestamp` (UTC aware),
  `symbol`, `open`, `high`, `low`, `close`, `volume`.
- `actions` DataFrame columns `session`, `symbol`, `split_ratio` (new/old,
  default 1), `dividend` (raw cash/share, default 0), `pay_session`.
  Splits apply before ex-date trading; dividends become receivables based on
  pre-ex-date positions (post-split basis) and cash on payment date. Missing
  or unaudited action history blocks conclusions. Multiple actions must be
  normalized into one row per session/symbol before simulation.
- `sessions` DatetimeIndex: full exchange calendar over input range.
  Exact 09:35 Eastern minute open for normal execution. Full regular-session
  minute bars support account-equity risk replay. Halt at observed 10% peak
  drawdown and execute liquidation on next available minute open; gaps can
  exceed the threshold. Normal orders only on next session after signal.
- `backtest.simulate(daily, minutes, actions, sessions, strategy, start, end,
  cost_bps=5.0, monthly_expense=0.0, fee_schedule=None)` returns a dict
  containing JSON-compatible metrics plus equity/trades (worker documents).
  Strategies include trend, rebound, passive, cash. Each period starts with
  independent $5,000; all prior data may warm indicators. No parameter tuning.
- Paper worker owns `paper.py`, `broker.py`, `state.py`, `notifications.py`,
  their tests, and `deploy/`. Uses shared strategy/config; provides practical
  public entry points for CLI (coordinate with lead). No real requests during
  development tests. Require qualified research artifact AND explicit local
  paper activation. Dedicated account initially <= $5,000. Persist peak and
  halt; no automatic resume. Use hardcoded paper=True, no endpoint override.
- Lead owns `data.py`, `cli.py`, packaging, README, research records, report
  renderer, integration tests. Data commands store under explicitly chosen
  external directory (default platform data directory); private runtime files
  must not be tracked. No deployment or notifications are activated by build.
