"""Frozen v1 research parameters, shared by simulation and paper execution."""

UNIVERSE = ("SPY", "IWM", "EFA", "EEM", "IEF", "GLD")
CASH_SYMBOL = "BIL"
INITIAL_EQUITY = 5000.0
ENTRY_FRACTION = 1 / 12
MAX_EXPOSURE = 0.5
DRAWDOWN_LIMIT = 0.1
COST_CASES_BPS = (5.0, 10.0, 20.0)
PERIODS = {
    "development": ("2017-01-01", "2021-12-31"),
    "validation": ("2022-01-01", "2023-12-31"),
    "held_out": ("2024-01-01", "2026-09-16"),
}
