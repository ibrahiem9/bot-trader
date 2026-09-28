"""Frozen experiment journal, comparison screens and human-readable reports."""
from __future__ import annotations

import hashlib
import json
import math
import os
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import pandas as pd

from .config import COST_CASES_BPS, PERIODS
from .data import END, DataError, audit, digest, external_path, load, write_json


def protocol_hash() -> str:
    # Hash actual executable research rules as well as the frozen prose. An old
    # qualification cannot activate a different local strategy implementation.
    package = Path(__file__).parent
    sources = [package / name for name in ("config.py", "strategy.py", "backtest.py", "data.py", "reporting.py")]
    protocol = package.parents[1] / "research" / "strategy-evidence.md"
    if protocol.exists():
        sources.append(protocol)
    h = hashlib.sha256()
    for source in sources:
        h.update(source.name.encode())
        h.update(source.read_bytes())
    return h.hexdigest()


def _journal(root: Path, event: dict) -> None:
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    with (root / "attempts.jsonl").open("a") as handle:
        handle.write(json.dumps({"at": datetime.now(UTC).isoformat(), **event}, allow_nan=False) + "\n")
        handle.flush()
        os.fsync(handle.fileno())
    (root / "attempts.jsonl").chmod(0o600)


def _fees(path: Path | None) -> tuple[list | None, list[str]]:
    if path is None:
        return None, ["A reviewed date-effective SEC/TAF/CAT fee schedule is missing"]
    try:
        config = json.loads(path.read_text())
        if not isinstance(config, dict):
            return None, ["Fee review must be a JSON object"]
    except (ValueError, OSError) as exc:
        return None, [f"Fee review cannot be read: {exc}"]
    errors = []
    if (config.get("reviewed") is not True or not config.get("reviewer") or not config.get("sources") or
            config.get("coverage_start") != "2017-01-01" or config.get("coverage_end") != END):
        errors.append("Fee schedule needs reviewed evidence covering every evaluation period")
    schedule = config.get("schedule", [])
    required = {"effective_date", "sec_per_dollar", "taf_per_share", "taf_maximum", "cat_per_share"}
    if (not isinstance(schedule, list) or not schedule or
            any(not isinstance(row, dict) or required - set(row) for row in schedule)):
        errors.append("Fee rows must specify effective_date and SEC/TAF/CAT fields, including explicit zero rates")
    else:
        dates = [str(row["effective_date"]) for row in schedule]
        try:
            for date in dates:
                if pd.Timestamp(date).strftime("%Y-%m-%d") != date:
                    raise ValueError("Use YYYY-MM-DD dates")
        except (ValueError, TypeError):
            errors.append("Fee effective_date must be a valid YYYY-MM-DD calendar date")
        if dates != sorted(set(dates)) or dates[0] > "2017-01-01":
            errors.append("Fee effective dates must be unique, increasing and cover the first evaluation session")
        for row in schedule:
            for key in required - {"effective_date"}:
                value = row[key]
                if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
                    errors.append("Fee rates and caps must be finite and nonnegative")
    return schedule, errors


def _returns(result: dict, key: str = "equity") -> pd.Series:
    rows = pd.DataFrame(result["equity"])
    equity = pd.Series(rows[key].to_numpy(dtype=float), index=pd.to_datetime(rows.session))
    # Preserve first-day entry costs by using the original capital as the base.
    return equity / equity.shift(1, fill_value=5000.0) - 1


def relative_metrics(result: dict, cash: dict) -> dict:
    returns = _returns(result)
    excess = returns - _returns(cash).reindex(returns.index)
    if excess.isna().any() or not np.isfinite(excess).all():
        raise DataError("Benchmark and candidate sessions do not align")
    std = float(excess.std(ddof=1))
    sharpe = float(excess.mean() / std * np.sqrt(252)) if std > 0 else None
    total = float(excess.sum())
    best = float(excess.rolling(63, min_periods=63).sum().max()) if len(excess) >= 63 else None
    # Block-bootstrap confidence interval, fixed seed/block length; descriptive
    # only. Neither its bounds nor resamples are used to tune or select rules.
    rng = np.random.default_rng(20260917)
    values = excess.to_numpy()
    boot = []
    if len(values) >= 63:
        for _ in range(1000):
            starts = rng.integers(0, len(values), size=math.ceil(len(values) / 21))
            idx = ((starts[:, None] + np.arange(21)) % len(values)).ravel()[:len(values)]
            boot.append(float(values[idx].mean() * 252))
    return {"excess_annualized_return": result["metrics"]["annualized_return"] - cash["metrics"]["annualized_return"],
            "cash_excess_sharpe": sharpe, "sum_daily_excess": total,
            "best_63_session_excess": best,
            "best_63_session_share": best / total if best is not None and total > 0 else None,
            "excess_without_best_63_sessions": total - best if best is not None else None,
            "bootstrap_annual_mean_excess_95pct": list(np.quantile(boot, [0.025, 0.975])) if boot else None}


def _expense_metrics(result: dict, monthly: float) -> dict:
    rows = pd.DataFrame(result["equity"])
    dates = pd.to_datetime(rows.session)
    # Pro-rate fixed costs by calendar days including weekends since first session.
    elapsed = (dates - pd.Timestamp(result["start"])).dt.days.to_numpy() + 1
    elapsed[-1] = (pd.Timestamp(result["end"]) - pd.Timestamp(result["start"])).days + 1
    equity = rows.equity.to_numpy(dtype=float) - monthly * 12 * elapsed / 365.2425
    years = len(equity) / 252
    annual = float((equity[-1] / 5000) ** (1 / years) - 1) if (equity > 0).all() else None
    peak = np.maximum.accumulate(np.r_[5000.0, equity])[1:]
    return {"monthly_expense": monthly, "ending_equity": float(equity[-1]),
            "annualized_return": annual, "max_drawdown": float(np.max(1 - equity / peak))}


def qualify(results: list[dict], data_ok: bool) -> dict:
    lookup = {(r["period"], r["strategy"], float(r["cost_bps"])): r for r in results}
    screens = {}
    for strategy in ("trend", "rebound"):
        checks = {}
        for period in ("validation", "held_out"):
            base = lookup[(period, strategy, 5.0)]
            passive = lookup[(period, "passive", 5.0)]
            stress = lookup[(period, strategy, 20.0)]
            bm = base["relative"]
            sharpe, passive_sharpe = bm["cash_excess_sharpe"], passive["relative"]["cash_excess_sharpe"]
            numeric = [lookup[(period, strategy, c)]["metrics"]["max_drawdown"] for c in COST_CASES_BPS]
            numeric += [base["metrics"]["turnover"], bm["excess_annualized_return"],
                        passive["metrics"]["max_drawdown"], stress["relative"]["excess_annualized_return"]]
            numeric += [v for v in (sharpe, passive_sharpe, bm["excess_without_best_63_sessions"]) if v is not None]
            finite = all(isinstance(v, (float, int)) and not isinstance(v, bool) and math.isfinite(v)
                         for v in numeric)
            checks[period] = {
                "finite_metrics": finite,
                "beats_cash": bm["excess_annualized_return"] > 0,
                "improves_sharpe": sharpe is not None and passive_sharpe is not None and sharpe > passive_sharpe,
                "reduces_drawdown": base["metrics"]["max_drawdown"] < passive["metrics"]["max_drawdown"],
                "within_drawdown_limit": max(lookup[(period, strategy, c)]["metrics"]["max_drawdown"]
                                             for c in COST_CASES_BPS) <= 0.10,
                "positive_excess_at_20bps": stress["relative"]["excess_annualized_return"] > 0,
                "survives_best_episode_removal": (bm["excess_without_best_63_sessions"] is not None and
                                                   bm["excess_without_best_63_sessions"] > 0),
            }
        screens[strategy] = checks
    eligible = [s for s, p in screens.items() if data_ok and all(v for checks in p.values() for v in checks.values())]
    ranked = sorted(eligible, key=lambda s: (-lookup[("held_out", s, 5.0)]["relative"]["cash_excess_sharpe"],
                                           lookup[("held_out", s, 5.0)]["metrics"]["turnover"]))
    return {"eligible_strategies": eligible, "selected_strategy": ranked[0] if ranked else None, "screens": screens}


def research(dataset: Path, output: Path, fee_path: Path | None = None) -> dict:
    from .backtest import simulate

    output = external_path(output)
    protocol = protocol_hash()
    _journal(output, {"event": "attempt", "protocol_hash": protocol,
                      "variants": ["trend-v1", "rebound-v1"], "cost_bps": list(COST_CASES_BPS)})
    # Invalidate an earlier qualification before any possibly failing recomputation.
    write_json(output / "qualification.json", {"data_audit_passed": False, "eligible_strategies": [],
                                               "selected_strategy": None, "reason": "Evaluation in progress"})
    audit_result = audit(dataset)
    fees, fee_errors = _fees(fee_path)
    blockers = audit_result["errors"] + fee_errors
    result = {"schema": 1, "status": "blocked" if blockers else "complete", "protocol_hash": protocol,
              "created_at": datetime.now(UTC).isoformat(), "blockers": blockers,
              "data_audit": audit_result, "fee_sha256": digest(fee_path) if fee_path and fee_path.is_file() else None,
              "results": [], "qualification": {"eligible_strategies": [], "selected_strategy": None}}
    if not blockers:
        daily, minutes, actions, sessions, manifest = load(dataset)
        actions.attrs["audited"] = True
        frozen_path = output / "held-out-lock.json"
        lock = {"protocol_hash": protocol, "manifest_sha256": audit_result["manifest_sha256"],
                "actions_sha256": audit_result["actions_sha256"], "fee_sha256": result["fee_sha256"]}
        if frozen_path.exists() and json.loads(frozen_path.read_text()) != lock:
            raise DataError("Held-out inputs/rules changed after evaluation; retain the old record and design a new future test")
        write_json(frozen_path, lock)
        for period, (start, end) in PERIODS.items():
            for cost in COST_CASES_BPS:
                batch = []
                for strategy in ("cash", "passive", "trend", "rebound"):
                    _journal(output, {"event": "run", "period": period, "strategy": strategy,
                                      "cost_bps": cost, "protocol_hash": protocol})
                    run = simulate(daily, minutes, actions, sessions, strategy, start, end,
                                   cost_bps=cost, monthly_expense=0.0, fee_schedule=fees)
                    run["period"] = period
                    run["operating_expenses"] = [_expense_metrics(run, v) for v in (15.0, 100.0)]
                    batch.append(run)
                cash = next(r for r in batch if r["strategy"] == "cash")
                for run in batch:
                    run["relative"] = relative_metrics(run, cash)
                result["results"].extend(batch)
        result["qualification"] = qualify(result["results"], data_ok=True)
        result["data_feed"] = manifest["feed"]
    write_json(output / "comparison.json", result)
    (output / "comparison.md").write_text(render(result))
    (output / "comparison.md").chmod(0o600)
    qualification = {**result["qualification"], "data_audit_passed": not blockers,
                     "final_test_end": END, "protocol_hash": protocol,
                     "dataset_hash": audit_result.get("manifest_sha256"),
                     "data_feed": result.get("data_feed"),
                     "comparison_sha256": digest(output / "comparison.json")}
    write_json(output / "qualification.json", qualification)
    _journal(output, {"event": "complete", "status": result["status"], "protocol_hash": protocol,
                      "comparison_sha256": digest(output / "comparison.json")})
    return result


def verify_qualification(path: Path) -> dict:
    q = json.loads(path.read_text())
    comparison_path = path.parent / "comparison.json"
    if (q.get("protocol_hash") != protocol_hash() or q.get("data_audit_passed") is not True or
            q.get("final_test_end") != END or not comparison_path.exists() or
            q.get("comparison_sha256") != digest(comparison_path)):
        raise DataError("Qualification is stale or does not match its comparison report")
    report = json.loads(comparison_path.read_text())
    if (report.get("status") != "complete" or not report.get("data_audit", {}).get("passed") or
            report.get("blockers") or report.get("protocol_hash") != q["protocol_hash"]):
        raise DataError("Research report is incomplete or failed its data audit")
    expected = qualify(report["results"], data_ok=True)
    if (q.get("eligible_strategies") != expected["eligible_strategies"] or
            q.get("selected_strategy") != expected["selected_strategy"] or
            q.get("dataset_hash") != report["data_audit"].get("manifest_sha256") or
            q.get("data_feed") != report.get("data_feed")):
        raise DataError("Qualification does not match independently recomputed report screens")
    return q


def render(report: dict) -> str:
    lines = ["# ETF strategy comparison", "", f"Status: **{report['status']}**.", "",
             "Each candidate and benchmark starts each period with $5,000. Personal taxes are excluded.", ""]
    if report.get("blockers"):
        lines += ["Performance conclusions and ongoing paper activation are blocked.", ""]
        lines += [f"- {reason}" for reason in report["blockers"]]
        lines += ["", "No historical return estimates were produced. Synthetic test fixtures are software checks only.", ""]
        return "\n".join(lines)
    lines += ["Returns include modeled trading costs, dated regulatory fees and distributions.",
              "Sharpe uses daily excess returns over BIL. All periods are independent; annual figures inside a period are linked.", "",
              "| Period | Candidate | Cost/side bps | CAGR | Excess over BIL | Sharpe | Max drawdown | Trades | Turnover | Mean exposure |",
              "| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |"]
    for run in report["results"]:
        m, rel = run["metrics"], run["relative"]
        sharpe = "n/a" if rel["cash_excess_sharpe"] is None else f"{rel['cash_excess_sharpe']:.2f}"
        lines.append(f"| {run['period']} | {run['strategy']} | {run['cost_bps']:g} | {m['annualized_return']:.2%} | "
                     f"{rel['excess_annualized_return']:.2%} | {sharpe} | {m['max_drawdown']:.2%} | "
                     f"{m['trade_count']} | {m['turnover']:.2f} | {m['average_exposure']:.2%} |")
    lines += ["", "## Decision", ""]
    q = report["qualification"]
    lines.append(f"Selected for optional paper observation: **{q['selected_strategy']}**." if q["selected_strategy"]
                 else "Neither candidate passes all screens. Leave the runner inactive.")
    lines += ["", "Passing a screen is not proof of an edge. No automatic activation or live promotion is permitted.", ""]
    for run in report["results"]:
        if run["cost_bps"] != 5:
            continue
        m, rel = run["metrics"], run["relative"]
        lines += [f"## {run['period']}: {run['strategy']} (5 bps)", "",
                  (f"Volatility: {m['volatility']:.2%}. Recovery sessions: {m.get('recovery_sessions')}. "
                   f"Maximum exposure: {m.get('maximum_exposure', m.get('max_exposure'))}."), "",
                  (f"End-of-day drawdown: {m.get('end_of_day_max_drawdown')}; "
                   f"minute-observed drawdown: {m.get('observed_intraday_max_drawdown')}; "
                   f"unrecovered at cutoff: {m.get('unrecovered_at_end')}."), "",
                  "Yearly returns: `" + json.dumps(m.get("yearly_returns", {}), sort_keys=True) + "`.", "",
                  "Per-symbol profit contribution: `" + json.dumps(m.get("symbol_pnl", {}), sort_keys=True) + "`.", "",
                  "Excess return concentration and uncertainty: `" + json.dumps(rel, sort_keys=True) + "`.", "",
                  "Fixed operating expense overlays (do not resize historical trades):", ""]
        for expense in run["operating_expenses"]:
            annual = ("undefined (capital exhausted)" if expense["annualized_return"] is None
                      else f"{expense['annualized_return']:.2%}")
            lines.append(f"- ${expense['monthly_expense']:.0f}/month: ending equity ${expense['ending_equity']:.2f}, "
                         f"CAGR {annual}, drawdown {expense['max_drawdown']:.2%}.")
        if run.get("halt"):
            lines += ["", "Persistent drawdown halt: `" + json.dumps(run["halt"]) + "`."]
        lines += [""]
    lines += [("Uncertainty uses 1,000 circular 21-session block resamples with a fixed seed. "
              "The 95% range describes annualized mean daily excess, not guaranteed future returns. "
              "A 63-session episode-removal check guards against one profitable interval. "
               "The small chosen universe, minute-price proxies and the single held-out period limit inference."), ""]
    return "\n".join(lines)
