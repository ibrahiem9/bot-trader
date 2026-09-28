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

REPORT_SCHEMA = 2
DEVELOPMENT_VALIDATION_STAGE = "development-validation"
HELD_OUT_STAGE = "held-out"
STAGED_PERIODS = ("development", "validation")
ALL_PERIODS = (*STAGED_PERIODS, "held_out")
STRATEGIES = ("cash", "passive", "trend", "rebound")
STAGED_ARTIFACT = "development-validation.json"
REVIEW_EXAMPLE = "review-checkpoint.example.json"
REVIEW_COPY = "held-out-review.json"
HELD_OUT_LOCK = "held-out-lock.json"


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


def _empty_qualification(reason: str) -> dict:
    return {"eligible_strategies": [], "selected_strategy": None, "reason": reason}


def _read_object(path: Path, label: str) -> dict:
    try:
        value = json.loads(path.read_text())
    except (OSError, UnicodeError, TypeError, ValueError) as exc:
        raise DataError(f"{label} cannot be read as JSON: {exc}") from None
    if not isinstance(value, dict):
        raise DataError(f"{label} must be a JSON object")
    return value


def _bytes_digest(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _expected_variants(periods) -> list[tuple[str, str, float]]:
    return [(period, strategy, float(cost))
            for period in periods for cost in COST_CASES_BPS for strategy in STRATEGIES]


def _validate_results(results, periods, label: str) -> None:
    if not isinstance(results, list):
        raise DataError(f"{label} results must be a list")
    expected = _expected_variants(periods)
    actual = []
    for run in results:
        if not isinstance(run, dict):
            raise DataError(f"{label} contains a non-object result")
        try:
            period, strategy = run["period"], run["strategy"]
            raw_cost = run["cost_bps"]
            if isinstance(raw_cost, bool) or not isinstance(raw_cost, (int, float)):
                raise TypeError
            cost = float(raw_cost)
        except (KeyError, TypeError, ValueError):
            raise DataError(f"{label} contains a result with incomplete metadata") from None
        if (not isinstance(period, str) or period not in periods or
                not isinstance(strategy, str) or strategy not in STRATEGIES or
                not math.isfinite(cost)):
            raise DataError(f"{label} contains an unknown or invalid variant")
        actual.append((period, strategy, cost))
        if (run.get("start"), run.get("end")) != PERIODS[period]:
            raise DataError(f"{label} result date range does not match the frozen period")
    if len(actual) != len(set(actual)) or sorted(actual) != sorted(expected):
        raise DataError(f"{label} must contain the complete unique frozen variant set")


def _timestamp(value: object, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise DataError(f"{label} must be a nonempty timezone-aware timestamp")
    try:
        parsed = pd.Timestamp(value)
    except (OverflowError, TypeError, ValueError):
        raise DataError(f"{label} must be a valid timezone-aware timestamp") from None
    if parsed.tzinfo is None:
        raise DataError(f"{label} must include a timezone")
    if parsed.tz_convert("UTC") > pd.Timestamp.now(tz="UTC"):
        raise DataError(f"{label} cannot be in the future")
    return value


def _validate_review(review: dict, staged: dict, staged_hash: str, current: dict, label: str) -> None:
    if review.get("schema") != REPORT_SCHEMA or review.get("stage") != HELD_OUT_STAGE:
        raise DataError(f"{label} has an unsupported schema or stage")
    if review.get("approved") is not True or not isinstance(review.get("reviewer"), str) \
            or not review["reviewer"].strip():
        raise DataError(f"{label} requires an approved review and nonempty reviewer")
    if review.get("decision") != "continue_to_held_out":
        raise DataError(f"{label} has no held-out continuation decision")
    _timestamp(review.get("reviewed_at"), f"{label} reviewed_at")
    required = {
        "protocol_hash": current["protocol_hash"],
        "manifest_sha256": current["manifest_sha256"],
        "actions_sha256": current["actions_sha256"],
        "fee_sha256": current["fee_sha256"],
        "staged_artifact_sha256": staged_hash,
    }
    if any(review.get(key) != value for key, value in required.items()):
        raise DataError(f"{label} hashes do not match the current staged inputs")
    if "data_feed" in current and review.get("data_feed") != current["data_feed"]:
        raise DataError(f"{label} feed does not match the staged inputs")
    if review.get("periods") != list(STAGED_PERIODS):
        raise DataError(f"{label} does not name development and validation")
    if review.get("variants") != [list(item) for item in _expected_variants(STAGED_PERIODS)]:
        raise DataError(f"{label} does not bind the complete staged variant set")
    if staged.get("protocol_hash") != current["protocol_hash"]:
        raise DataError("Staged artifact protocol hash changed")


def _validate_staged(staged: dict, current: dict) -> None:
    if staged.get("schema") != REPORT_SCHEMA or staged.get("stage") != DEVELOPMENT_VALIDATION_STAGE:
        raise DataError("Development-validation artifact has an unsupported schema or stage")
    if staged.get("status") != "awaiting_review":
        raise DataError("Development-validation artifact is not awaiting review")
    for key, value in current.items():
        if staged.get(key) != value:
            raise DataError(f"Development-validation artifact {key} does not match current inputs")
    if staged.get("periods") != list(STAGED_PERIODS):
        raise DataError("Development-validation artifact has unexpected periods")
    _validate_results(staged.get("results"), STAGED_PERIODS, "Development-validation artifact")


def _write_report(output: Path, report: dict) -> None:
    write_json(output / "comparison.json", report)
    (output / "comparison.md").write_text(render(report))
    (output / "comparison.md").chmod(0o600)


def _write_terminal(output: Path, report: dict, reason: str) -> dict:
    report["qualification"] = _empty_qualification(reason)
    _write_report(output, report)
    qualification = {**report["qualification"], "data_audit_passed": False,
                     "final_test_end": END, "protocol_hash": report["protocol_hash"],
                     "stage": report.get("stage"), "comparison_sha256": digest(output / "comparison.json")}
    write_json(output / "qualification.json", qualification)
    _journal(output, {"event": "complete", "status": report["status"],
                      "stage": report.get("stage"), "protocol_hash": report["protocol_hash"],
                      "comparison_sha256": digest(output / "comparison.json")})
    return report


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


def _base_report(stage: str, protocol: str, audit_result: dict, fee_sha256: str | None,
                 blockers: list[str]) -> dict:
    return {"schema": REPORT_SCHEMA, "stage": stage,
            "status": "blocked" if blockers else "complete",
            "protocol_hash": protocol, "created_at": datetime.now(UTC).isoformat(),
            "blockers": blockers, "data_audit": audit_result, "fee_sha256": fee_sha256,
            "results": [], "qualification": _empty_qualification("Evaluation incomplete")}


def _current_metadata(protocol: str, audit_result: dict, fee_sha256: str | None) -> dict:
    try:
        return {"protocol_hash": protocol,
                "manifest_sha256": audit_result["manifest_sha256"],
                "actions_sha256": audit_result["actions_sha256"],
                "fee_sha256": fee_sha256}
    except (KeyError, TypeError):
        raise DataError("Data audit did not provide the required input hashes") from None


def _run_periods(output: Path, daily, minutes, actions, sessions, fees, protocol: str,
                 periods: tuple[str, ...]) -> list[dict]:
    from .backtest import simulate

    results = []
    for period in periods:
        start, end = PERIODS[period]
        for cost in COST_CASES_BPS:
            batch = []
            for strategy in STRATEGIES:
                stage = DEVELOPMENT_VALIDATION_STAGE if period in STAGED_PERIODS else HELD_OUT_STAGE
                _journal(output, {"event": "run", "stage": stage, "period": period,
                                  "strategy": strategy, "cost_bps": cost, "protocol_hash": protocol})
                run = simulate(daily, minutes, actions, sessions, strategy, start, end,
                               cost_bps=cost, monthly_expense=0.0, fee_schedule=fees)
                run["period"] = period
                run["operating_expenses"] = [_expense_metrics(run, value) for value in (15.0, 100.0)]
                batch.append(run)
            cash = next(item for item in batch if item["strategy"] == "cash")
            for run in batch:
                run["relative"] = relative_metrics(run, cash)
            results.extend(batch)
    _validate_results(results, periods, "Generated")
    return results


def _write_review_example(output: Path, metadata: dict, staged_hash: str) -> None:
    example = {"schema": REPORT_SCHEMA, "stage": HELD_OUT_STAGE, "approved": False,
               "reviewer": "", "reviewed_at": "", "decision": "continue_to_held_out",
               **metadata, "staged_artifact_sha256": staged_hash,
               "periods": list(STAGED_PERIODS),
               "variants": [list(item) for item in _expected_variants(STAGED_PERIODS)]}
    write_json(output / REVIEW_EXAMPLE, example)


def _load_staged(output: Path, metadata: dict) -> tuple[dict, str]:
    path = output / STAGED_ARTIFACT
    if not path.is_file():
        raise DataError("Development-validation artifact is missing")
    staged_hash = digest(path)
    staged = _read_object(path, "Development-validation artifact")
    _validate_staged(staged, metadata)
    return staged, staged_hash


def _review_bytes(path: Path, output: Path, staged: dict, staged_hash: str, metadata: dict,
                  existing_lock: dict | None) -> tuple[dict, str]:
    try:
        raw = path.read_bytes()
    except (OSError, UnicodeError) as exc:
        raise DataError(f"Review checkpoint cannot be read: {exc}") from None
    review = _read_object_from_bytes(raw, "Review checkpoint")
    review_hash = _bytes_digest(raw)
    _validate_review(review, staged, staged_hash, metadata, "Review checkpoint")
    copy_path = output / REVIEW_COPY
    if existing_lock is None:
        copy_path.write_bytes(raw)
        copy_path.chmod(0o600)
    elif not copy_path.is_file() or digest(copy_path) != existing_lock.get("review_checkpoint_sha256"):
        raise DataError("Persisted held-out review does not match its lock")
    return review, review_hash


def _read_object_from_bytes(raw: bytes, label: str) -> dict:
    try:
        value = json.loads(raw)
    except (UnicodeError, TypeError, ValueError) as exc:
        raise DataError(f"{label} cannot be read as JSON: {exc}") from None
    if not isinstance(value, dict):
        raise DataError(f"{label} must be a JSON object")
    return value


def _lock_for(metadata: dict, staged_hash: str, review_hash: str) -> dict:
    return {"schema": REPORT_SCHEMA, "stage": HELD_OUT_STAGE, **metadata,
            "staged_artifact_sha256": staged_hash, "review_checkpoint_sha256": review_hash}


def _load_or_write_lock(output: Path, lock: dict) -> str:
    path = output / HELD_OUT_LOCK
    if path.exists():
        existing = _read_object(path, "Held-out lock")
        if existing != lock:
            raise DataError("Held-out inputs, staged artifact, or review changed; retain the old record")
    else:
        write_json(path, lock)
    return digest(path)


def research(dataset: Path, output: Path, fee_path: Path | None = None,
             stage: str = DEVELOPMENT_VALIDATION_STAGE,
             review_checkpoint: Path | None = None) -> dict:
    output = external_path(output)
    if stage not in {DEVELOPMENT_VALIDATION_STAGE, HELD_OUT_STAGE}:
        raise DataError("Research stage must be development-validation or held-out")
    protocol = protocol_hash()
    _journal(output, {"event": "attempt", "stage": stage, "protocol_hash": protocol,
                      "variants": ["trend-v1", "rebound-v1"], "cost_bps": list(COST_CASES_BPS)})
    write_json(output / "qualification.json", {"data_audit_passed": False, "eligible_strategies": [],
                                               "selected_strategy": None, "stage": stage,
                                               "reason": "Evaluation in progress"})
    audit_result = {"passed": False, "errors": ["Audit did not complete"], "warnings": []}
    fee_sha256 = None
    try:
        audit_result = audit(dataset)
        fee_sha256 = digest(fee_path) if fee_path and fee_path.is_file() else None
        fees, fee_errors = _fees(fee_path)
        if not isinstance(audit_result, dict):
            raise DataError("Data audit did not return an object")
        audit_errors = audit_result.get("errors", [])
        if not isinstance(audit_errors, list):
            raise DataError("Data audit errors must be a list")
        blockers = audit_errors + fee_errors
        if audit_result.get("passed") is not True:
            blockers.append("Data audit did not pass")
        metadata = _current_metadata(protocol, audit_result, fee_sha256) if not blockers else {
            "protocol_hash": protocol, "manifest_sha256": audit_result.get("manifest_sha256"),
            "actions_sha256": audit_result.get("actions_sha256"), "fee_sha256": fee_sha256}
        result = _base_report(stage, protocol, audit_result, fee_sha256, blockers)
        if blockers:
            return _write_terminal(output, result, "Data admission or fee review blocked evaluation")
        daily, minutes, actions, sessions, manifest = load(dataset)
        actions.attrs["audited"] = True
        metadata["data_feed"] = manifest["feed"]
        result["data_feed"] = manifest["feed"]
        if stage == DEVELOPMENT_VALIDATION_STAGE:
            if (output / STAGED_ARTIFACT).exists():
                raise DataError("Development-validation artifact exists; use a new output directory")
            if (output / HELD_OUT_LOCK).exists():
                raise DataError("Held-out lock exists; staged artifact is immutable")
            result["results"] = _run_periods(output, daily, minutes, actions, sessions, fees,
                                              protocol, STAGED_PERIODS)
            result["status"] = "awaiting_review"
            result["qualification"] = _empty_qualification("Held-out test awaits explicit review")
            result["periods"] = list(STAGED_PERIODS)
            # Retain an independently renderable report even if a later blocked
            # attempt replaces comparison.json. Its own digest lives elsewhere.
            artifact = {**result, **metadata}
            write_json(output / STAGED_ARTIFACT, artifact)
            staged_hash = digest(output / STAGED_ARTIFACT)
            result["staged_artifact_sha256"] = staged_hash
            _write_review_example(output, metadata, staged_hash)
            _write_report(output, result)
            write_json(output / "qualification.json", {**result["qualification"],
                       "data_audit_passed": False, "final_test_end": END,
                       "protocol_hash": protocol, "dataset_hash": metadata["manifest_sha256"],
                       "data_feed": manifest["feed"], "stage": stage,
                       "comparison_sha256": digest(output / "comparison.json")})
            _journal(output, {"event": "complete", "stage": stage, "status": result["status"],
                              "protocol_hash": protocol, "comparison_sha256": digest(output / "comparison.json")})
            return result
        staged, staged_hash = _load_staged(output, metadata)
        checkpoint_path = external_path(review_checkpoint or output / "review-checkpoint.json")
        existing_lock = _read_object(output / HELD_OUT_LOCK, "Held-out lock") if (output / HELD_OUT_LOCK).exists() else None
        _review, review_hash = _review_bytes(checkpoint_path, output, staged, staged_hash, metadata, existing_lock)
        lock = _lock_for(metadata, staged_hash, review_hash)
        lock_hash = _load_or_write_lock(output, lock)
        held_out = _run_periods(output, daily, minutes, actions, sessions, fees, protocol, ("held_out",))
        _validate_results(staged["results"] + held_out, ALL_PERIODS, "Final")
        result["results"] = staged["results"] + held_out
        result["periods"] = list(ALL_PERIODS)
        result["staged_artifact_sha256"] = staged_hash
        result["review_checkpoint_sha256"] = review_hash
        result["held_out_lock_sha256"] = lock_hash
        result["qualification"] = qualify(result["results"], data_ok=True)
        result["status"] = "complete"
        _write_report(output, result)
        qualification = {**result["qualification"], "data_audit_passed": True,
                         "final_test_end": END, "protocol_hash": protocol,
                         "dataset_hash": metadata["manifest_sha256"], "data_feed": manifest["feed"],
                         "stage": stage, "staged_artifact_sha256": staged_hash,
                         "review_checkpoint_sha256": review_hash, "held_out_lock_sha256": lock_hash,
                         "comparison_sha256": digest(output / "comparison.json")}
        write_json(output / "qualification.json", qualification)
        _journal(output, {"event": "complete", "stage": stage, "status": result["status"],
                          "protocol_hash": protocol, "comparison_sha256": digest(output / "comparison.json")})
        return result
    except Exception as exc:  # noqa: BLE001 - every failed stage must persist a blocked result
        result = _base_report(stage, protocol, audit_result, fee_sha256, [str(exc)])
        return _write_terminal(output, result, str(exc))


def verify_qualification(path: Path) -> dict:
    q = _read_object(path, "Qualification")
    comparison_path = path.parent / "comparison.json"
    if (q.get("protocol_hash") != protocol_hash() or q.get("data_audit_passed") is not True or
            q.get("final_test_end") != END or q.get("stage") != HELD_OUT_STAGE or
            not comparison_path.exists() or q.get("comparison_sha256") != digest(comparison_path)):
        raise DataError("Qualification is stale or does not match its comparison report")
    report = _read_object(comparison_path, "Comparison report")
    data_audit = report.get("data_audit")
    if not isinstance(data_audit, dict):
        raise DataError("Research report has malformed data-audit metadata")
    if (report.get("status") != "complete" or report.get("stage") != HELD_OUT_STAGE or
            data_audit.get("passed") is not True or report.get("blockers") or
            report.get("protocol_hash") != q["protocol_hash"]):
        raise DataError("Research report is incomplete or failed its data audit")
    metadata = {"protocol_hash": protocol_hash(),
                "manifest_sha256": data_audit.get("manifest_sha256"),
                "actions_sha256": data_audit.get("actions_sha256"),
                "fee_sha256": report.get("fee_sha256"), "data_feed": report.get("data_feed")}
    staged_path = path.parent / STAGED_ARTIFACT
    staged_hash = digest(staged_path) if staged_path.is_file() else None
    staged = _read_object(staged_path, "Development-validation artifact")
    _validate_staged(staged, metadata)
    if staged_hash != q.get("staged_artifact_sha256") or staged_hash != report.get("staged_artifact_sha256"):
        raise DataError("Qualification does not match the persisted staged artifact")
    review_path = path.parent / REVIEW_COPY
    try:
        review_raw = review_path.read_bytes()
    except OSError:
        raise DataError("Persisted held-out review is missing") from None
    review_hash = _bytes_digest(review_raw)
    review = _read_object_from_bytes(review_raw, "Persisted held-out review")
    _validate_review(review, staged, staged_hash, metadata, "Persisted held-out review")
    if review_hash != q.get("review_checkpoint_sha256") or review_hash != report.get("review_checkpoint_sha256"):
        raise DataError("Qualification does not match the persisted held-out review")
    lock_path = path.parent / HELD_OUT_LOCK
    lock = _read_object(lock_path, "Held-out lock")
    lock_hash = digest(lock_path)
    expected_lock = _lock_for(metadata, staged_hash, review_hash)
    if lock != expected_lock or lock_hash != q.get("held_out_lock_sha256") \
            or lock_hash != report.get("held_out_lock_sha256"):
        raise DataError("Qualification does not match the persisted held-out lock")
    _validate_results(report.get("results"), ALL_PERIODS, "Final report")
    staged_rows = [run for run in report["results"] if run["period"] in STAGED_PERIODS]
    if staged_rows != staged["results"]:
        raise DataError("Final report development-validation results do not match the persisted artifact")
    try:
        expected = qualify(report["results"], data_ok=True)
    except (KeyError, TypeError, ValueError) as exc:
        raise DataError(f"Final report qualification data is malformed: {exc}") from None
    if (q.get("eligible_strategies") != expected["eligible_strategies"] or
            q.get("selected_strategy") != expected["selected_strategy"] or
            q.get("dataset_hash") != metadata["manifest_sha256"] or
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
    if report.get("status") == "awaiting_review":
        lines.append("Development and validation are complete. Held-out evaluation awaits an explicit hash-bound review checkpoint.")
    else:
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
