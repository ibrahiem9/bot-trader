"""Invented returns verify reports and gates; they are not historical results."""

import copy
import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from bot_trader import reporting
from bot_trader.data import DataError, digest, write_json


@pytest.fixture
def candidates():
    runs = []
    for period in ("validation", "held_out"):
        for cost in (5., 10., 20.):
            for strategy in ("cash", "passive", "trend", "rebound"):
                runs.append({
                    "period": period, "strategy": strategy, "cost_bps": cost,
                    "start": reporting.PERIODS[period][0], "end": reporting.PERIODS[period][1],
                    "metrics": {"max_drawdown": .08 if strategy == "passive" else .04,
                                "turnover": 2. if strategy == "trend" else 3.},
                    "relative": {"cash_excess_sharpe": .5 if strategy == "passive" else 1.,
                                 "excess_annualized_return": .03,
                                 "excess_without_best_63_sessions": .01},
                })
    return runs


def run_at(runs, strategy="trend", period="validation", cost=5.):
    return next(r for r in runs if (r["strategy"], r["period"], r["cost_bps"]) == (strategy, period, cost))


def test_both_pass_select_highest_heldout_sharpe_then_lower_turnover(candidates):
    qualification = reporting.qualify(candidates, data_ok=True)
    assert qualification["eligible_strategies"] == ["trend", "rebound"]
    assert qualification["selected_strategy"] == "trend"
    run_at(candidates, "rebound", "held_out")["relative"]["cash_excess_sharpe"] = 1.01
    assert reporting.qualify(candidates, True)["selected_strategy"] == "rebound"


@pytest.mark.parametrize("field,container,value,cost,screen", [
    ("excess_annualized_return", "relative", 0., 5., "beats_cash"),
    ("cash_excess_sharpe", "relative", .5, 5., "improves_sharpe"),
    ("cash_excess_sharpe", "relative", None, 5., "improves_sharpe"),
    ("max_drawdown", "metrics", .08, 5., "reduces_drawdown"),
    ("max_drawdown", "metrics", .101, 20., "within_drawdown_limit"),
    ("max_drawdown", "metrics", .101, 10., "within_drawdown_limit"),
    ("excess_annualized_return", "relative", 0., 20., "positive_excess_at_20bps"),
    ("excess_without_best_63_sessions", "relative", 0., 5., "survives_best_episode_removal"),
    ("excess_without_best_63_sessions", "relative", None, 5., "survives_best_episode_removal"),
])
@pytest.mark.parametrize("period", ["validation", "held_out"])
def test_each_period_and_criterion_blocks_candidate(candidates, field, container, value, cost, screen, period):
    run_at(candidates, period=period, cost=cost)[container][field] = value
    qualification = reporting.qualify(candidates, data_ok=True)
    assert qualification["eligible_strategies"] == ["rebound"]
    assert not qualification["screens"]["trend"][period][screen]


def test_neither_passes_or_bad_data_leaves_no_selection(candidates):
    assert reporting.qualify(candidates, False)["selected_strategy"] is None
    for strategy in ("trend", "rebound"):
        run_at(candidates, strategy)["relative"]["excess_annualized_return"] = -.01
    assert reporting.qualify(candidates, True)["eligible_strategies"] == []


def return_run(returns, start="2024-01-02"):
    equity = 5000. * np.cumprod(1. + np.asarray(returns))
    return {"equity": [{"session": str(day.date()), "equity": float(value)}
                       for day, value in zip(pd.bdate_range(start, periods=len(equity)), equity)],
            "metrics": {"annualized_return": float((equity[-1] / 5000.) ** (252 / len(equity)) - 1)}}


def test_relative_metrics_preserve_entry_costs_and_find_best_episode():
    returns = np.r_[-.002, np.full(63, .003), np.full(64, .0001)]
    candidate, cash = return_run(returns), return_run(np.zeros(len(returns)))
    relative = reporting.relative_metrics(candidate, cash)
    assert relative["sum_daily_excess"] == pytest.approx(returns.sum())
    assert relative["best_63_session_excess"] == pytest.approx(63 * .003)
    assert relative["excess_without_best_63_sessions"] == pytest.approx(-.002 + 64 * .0001)
    assert relative == reporting.relative_metrics(candidate, cash)
    cash["equity"] = cash["equity"][1:]
    with pytest.raises(DataError, match="sessions"):
        reporting.relative_metrics(candidate, cash)


def test_short_series_has_no_episode_or_uncertainty_estimate():
    relative = reporting.relative_metrics(return_run([.001, -.001]), return_run([0., 0.]))
    assert relative["excess_without_best_63_sessions"] is None
    assert relative["bootstrap_annual_mean_excess_95pct"] is None


@pytest.fixture
def fee_file(tmp_path):
    path = tmp_path / "synthetic-fees.json"
    write_json(path, {"reviewed": True, "reviewer": "test fixture",
                     "sources": ["Synthetic zero rates for software checks only"],
                     "coverage_start": "2017-01-01", "coverage_end": reporting.END,
                     "schedule": [{"effective_date": "2017-01-01", "sec_per_dollar": 0.,
                                   "taf_per_share": 0., "taf_maximum": 0., "cat_per_share": 0.}]})
    return path


@pytest.mark.parametrize("field,value", [("sec_per_dollar", -1.), ("taf_per_share", float("nan")),
                                          ("cat_per_share", float("inf")), ("taf_maximum", "zero")])
def test_fee_schedule_rejects_invalid_rates(fee_file, field, value):
    config = json.loads(fee_file.read_text())
    config["schedule"][0][field] = value
    fee_file.write_text(json.dumps(config))
    assert reporting._fees(fee_file)[1]


def test_fee_schedule_requires_evidence_fields_and_coverage(fee_file):
    assert reporting._fees(None)[1]
    assert reporting._fees(fee_file)[1] == []
    original = json.loads(fee_file.read_text())
    for mutation in ({"reviewed": False}, {"sources": []}, {"coverage_end": "2025-12-31"},
                     {"schedule": []}, {"schedule": [{"effective_date": "2017-01-01"}]}):
        write_json(fee_file, {**original, **mutation})
        assert reporting._fees(fee_file)[1]
    late = copy.deepcopy(original)
    late["schedule"][0]["effective_date"] = "2018-01-01"
    write_json(fee_file, late)
    assert reporting._fees(fee_file)[1]


def test_missing_data_writes_blocked_comparison_and_journal(tmp_path):
    output = tmp_path / "research"
    result = reporting.research(tmp_path / "missing-dataset", output)
    assert result["status"] == "blocked"
    assert result["results"] == []
    assert result["qualification"]["selected_strategy"] is None
    rendered = (output / "comparison.md").read_text()
    assert "No historical return estimates were produced" in rendered
    assert "blocked" in rendered
    entries = [json.loads(line) for line in (output / "attempts.jsonl").read_text().splitlines()]
    assert [entry["event"] for entry in entries] == ["attempt", "complete"]
    assert entries[-1]["status"] == "blocked"
    assert not json.loads((output / "qualification.json").read_text())["data_audit_passed"]


@pytest.fixture
def qualified_files(tmp_path, candidates):
    protocol = reporting.protocol_hash()
    development = []
    for run in candidates:
        if run["period"] != "validation":
            continue
        copied = copy.deepcopy(run)
        copied["period"] = "development"
        copied["start"], copied["end"] = reporting.PERIODS["development"]
        development.append(copied)
    all_results = development + candidates
    metadata = {"protocol_hash": protocol, "manifest_sha256": "synthetic-hash",
                "actions_sha256": "synthetic-actions", "fee_sha256": "synthetic-fees",
                "data_feed": "iex"}
    staged = {"schema": reporting.REPORT_SCHEMA, "stage": reporting.DEVELOPMENT_VALIDATION_STAGE,
              "status": "awaiting_review", **metadata, "data_feed": "iex",
              "periods": list(reporting.STAGED_PERIODS), "results": development + candidates[:12]}
    staged_path = tmp_path / reporting.STAGED_ARTIFACT
    write_json(staged_path, staged)
    staged_hash = digest(staged_path)
    review = {"schema": reporting.REPORT_SCHEMA, "stage": reporting.HELD_OUT_STAGE,
              "approved": True, "reviewer": "test reviewer", "reviewed_at": "2026-09-20T00:00:00Z",
              "decision": "continue_to_held_out", **metadata,
              "staged_artifact_sha256": staged_hash, "periods": list(reporting.STAGED_PERIODS),
              "variants": [list(item) for item in reporting._expected_variants(reporting.STAGED_PERIODS)]}
    review_path = tmp_path / "review-checkpoint.json"
    write_json(review_path, review)
    review_raw = review_path.read_bytes()
    (tmp_path / reporting.REVIEW_COPY).write_bytes(review_raw)
    (tmp_path / reporting.REVIEW_COPY).chmod(0o600)
    review_hash = reporting._bytes_digest(review_raw)
    lock = reporting._lock_for(metadata, staged_hash, review_hash)
    lock_path = tmp_path / reporting.HELD_OUT_LOCK
    write_json(lock_path, lock)
    lock_hash = digest(lock_path)
    report = {"schema": reporting.REPORT_SCHEMA, "stage": reporting.HELD_OUT_STAGE,
              "status": "complete", "protocol_hash": protocol, "blockers": [],
              "results": all_results, "data_feed": "iex", "fee_sha256": metadata["fee_sha256"],
              "staged_artifact_sha256": staged_hash, "review_checkpoint_sha256": review_hash,
              "held_out_lock_sha256": lock_hash,
              "data_audit": {"passed": True, "manifest_sha256": metadata["manifest_sha256"],
                             "actions_sha256": metadata["actions_sha256"]}}
    report_path = tmp_path / "comparison.json"
    write_json(report_path, report)
    q = {**reporting.qualify(all_results, True), "protocol_hash": protocol, "dataset_hash": "synthetic-hash",
         "data_feed": "iex", "comparison_sha256": digest(report_path), "final_test_end": reporting.END,
         "data_audit_passed": True, "stage": reporting.HELD_OUT_STAGE,
         "staged_artifact_sha256": staged_hash, "review_checkpoint_sha256": review_hash,
         "held_out_lock_sha256": lock_hash}
    path = tmp_path / "qualification.json"
    write_json(path, q)
    return path, report_path


@pytest.mark.parametrize("key,value", [("protocol_hash", "stale"), ("comparison_sha256", "tampered"),
                                       ("selected_strategy", "rebound"), ("eligible_strategies", ["trend"]),
                                       ("dataset_hash", "wrong"), ("data_feed", "sip")])
def test_tampered_or_stale_qualification_is_rejected(qualified_files, key, value):
    path, _ = qualified_files
    assert reporting.verify_qualification(path)["selected_strategy"] == "trend"
    q = json.loads(path.read_text())
    q[key] = value
    write_json(path, q)
    with pytest.raises(DataError):
        reporting.verify_qualification(path)


def test_changed_report_cannot_reuse_qualification(qualified_files):
    path, report_path = qualified_files
    report = json.loads(report_path.read_text())
    report["results"][0]["metrics"]["max_drawdown"] = .01
    write_json(report_path, report)
    with pytest.raises(DataError, match="stale"):
        reporting.verify_qualification(path)


def test_recomputed_report_hash_cannot_change_staged_metrics(qualified_files):
    path, report_path = qualified_files
    report = json.loads(report_path.read_text())
    report["results"][0]["metrics"]["max_drawdown"] = .25
    write_json(report_path, report)
    qualification = json.loads(path.read_text())
    qualification["comparison_sha256"] = digest(report_path)
    write_json(path, qualification)
    with pytest.raises(DataError, match="development-validation results"):
        reporting.verify_qualification(path)


def test_legacy_ungated_qualification_is_rejected(qualified_files):
    path, _ = qualified_files
    qualification = json.loads(path.read_text())
    qualification.pop("stage")
    write_json(path, qualification)
    with pytest.raises(DataError):
        reporting.verify_qualification(path)


@pytest.mark.parametrize("name", [reporting.REVIEW_COPY, reporting.STAGED_ARTIFACT, reporting.HELD_OUT_LOCK])
def test_persisted_provenance_tampering_is_rejected(qualified_files, name):
    path, _ = qualified_files
    target = path.parent / name
    raw = target.read_bytes()
    target.write_bytes(raw + b" ")
    with pytest.raises(DataError):
        reporting.verify_qualification(path)


@pytest.fixture
def staged_pipeline(tmp_path, fee_file, monkeypatch):
    from bot_trader import backtest

    calls = []
    monkeypatch.setattr(reporting, "audit", lambda _: {"passed": True, "errors": [],
                       "manifest_sha256": "synthetic-bars", "actions_sha256": "synthetic-actions"})
    monkeypatch.setattr(reporting, "load", lambda _: (None, None, pd.DataFrame(), None, {"feed": "iex"}))

    def simulate(daily, minutes, actions, sessions, strategy, start, end, **kwargs):
        calls.append((strategy, start, end, kwargs["cost_bps"]))
        assert actions.attrs["audited"]
        returns = .0001 + np.sin(np.arange(128)) * .00002
        if strategy != "cash":
            returns += .0002
        run = return_run(returns, start=start)
        run.update(strategy=strategy, cost_bps=kwargs["cost_bps"], trades=[], halt=None, start=start, end=end)
        run["metrics"].update(max_drawdown=.01, turnover=1., trade_count=2, average_exposure=.3,
                               volatility=.02, recovery_sessions=2, max_exposure=.4,
                               yearly_returns={"2024": .01}, symbol_pnl={"SPY": 20.})
        return run

    monkeypatch.setattr(backtest, "simulate", simulate)
    output = tmp_path / "pipeline-fixture"
    dataset = tmp_path / "synthetic"
    result = reporting.research(dataset, output, fee_file)
    assert result["status"] == "awaiting_review"
    return {"output": output, "dataset": dataset, "fees": fee_file, "calls": calls,
            "simulate": simulate, "monkeypatch": monkeypatch}


def _approved_checkpoint(output: Path):
    checkpoint = json.loads((output / reporting.REVIEW_EXAMPLE).read_text())
    checkpoint.update(approved=True, reviewer="test reviewer", reviewed_at="2026-09-20T00:00:00Z")
    path = output / "review-checkpoint.json"
    write_json(path, checkpoint)
    return path


@pytest.mark.parametrize("mutation", [
    "missing", "false", "blank-reviewer", "naive-time", "future-time", "bad-time",
    "protocol_hash", "manifest_sha256", "actions_sha256", "fee_sha256", "staged_artifact_sha256",
    "json-list", "json-null", "wrong-types",
])
def test_checkpoint_gate_blocks_before_held_out_simulation(staged_pipeline, mutation):
    output = staged_pipeline["output"]
    dataset, fees, calls = staged_pipeline["dataset"], staged_pipeline["fees"], staged_pipeline["calls"]
    checkpoint = output / "review-checkpoint.json"
    if mutation == "missing":
        pass
    elif mutation == "json-list":
        checkpoint.write_text("[]")
    elif mutation == "json-null":
        checkpoint.write_text("null")
    else:
        value = json.loads((output / reporting.REVIEW_EXAMPLE).read_text())
        if mutation == "false":
            pass
        elif mutation == "blank-reviewer":
            value.update(approved=True, reviewed_at="2026-09-20T00:00:00Z")
        elif mutation == "naive-time":
            value.update(approved=True, reviewer="test reviewer", reviewed_at="2026-09-20T00:00:00")
        elif mutation == "future-time":
            value.update(approved=True, reviewer="test reviewer", reviewed_at="2999-09-20T00:00:00Z")
        elif mutation == "bad-time":
            value.update(approved=True, reviewer="test reviewer", reviewed_at="not-a-time")
        elif mutation == "wrong-types":
            value.update(approved=True, reviewer="test reviewer", reviewed_at="2026-09-20T00:00:00Z",
                         variants="not-a-list")
        else:
            value.update(approved=True, reviewer="test reviewer", reviewed_at="2026-09-20T00:00:00Z",
                         **{mutation: "mismatch"})
        write_json(checkpoint, value)
    result = reporting.research(dataset, output, fees, stage=reporting.HELD_OUT_STAGE,
                                review_checkpoint=checkpoint)
    assert result["status"] == "blocked"
    assert len(calls) == 24
    assert not (output / reporting.HELD_OUT_LOCK).exists()


@pytest.mark.parametrize("variant_change", ["duplicate", "missing", "extra"])
def test_staged_variant_set_is_checked_before_held_out(staged_pipeline, variant_change):
    output = staged_pipeline["output"]
    staged_path = output / reporting.STAGED_ARTIFACT
    staged = json.loads(staged_path.read_text())
    if variant_change == "duplicate":
        staged["results"][-1] = copy.deepcopy(staged["results"][0])
    elif variant_change == "missing":
        staged["results"].pop()
    else:
        staged["results"].append(copy.deepcopy(staged["results"][0]))
    write_json(staged_path, staged)
    checkpoint = json.loads((output / reporting.REVIEW_EXAMPLE).read_text())
    checkpoint.update(approved=True, reviewer="test reviewer", reviewed_at="2026-09-20T00:00:00Z",
                      staged_artifact_sha256=digest(staged_path))
    checkpoint_path = output / "review-checkpoint.json"
    write_json(checkpoint_path, checkpoint)
    result = reporting.research(staged_pipeline["dataset"], output, staged_pipeline["fees"],
                                stage=reporting.HELD_OUT_STAGE, review_checkpoint=checkpoint_path)
    assert result["status"] == "blocked"
    assert len(staged_pipeline["calls"]) == 24
    assert not (output / reporting.HELD_OUT_LOCK).exists()


def test_changed_inputs_block_held_out_before_simulation(staged_pipeline, monkeypatch):
    checkpoint = _approved_checkpoint(staged_pipeline["output"])
    monkeypatch.setattr(reporting, "audit", lambda _: {"passed": True, "errors": [],
                       "manifest_sha256": "changed-bars", "actions_sha256": "synthetic-actions"})
    result = reporting.research(staged_pipeline["dataset"], staged_pipeline["output"], staged_pipeline["fees"],
                                stage=reporting.HELD_OUT_STAGE, review_checkpoint=checkpoint)
    assert result["status"] == "blocked"
    assert len(staged_pipeline["calls"]) == 24


def test_audit_passed_flag_is_required_even_without_errors(staged_pipeline, monkeypatch):
    monkeypatch.setattr(reporting, "audit", lambda _: {"passed": False, "errors": [],
                       "manifest_sha256": "synthetic-bars", "actions_sha256": "synthetic-actions"})
    result = reporting.research(staged_pipeline["dataset"], staged_pipeline["output"], staged_pipeline["fees"])
    assert result["status"] == "blocked"
    assert "audit did not pass" in result["blockers"][0].lower()
    assert len(staged_pipeline["calls"]) == 24


def test_failed_held_out_simulation_invalidates_then_exact_retry_reuses_lock(staged_pipeline, monkeypatch):
    from bot_trader import backtest

    checkpoint = _approved_checkpoint(staged_pipeline["output"])
    original = staged_pipeline["simulate"]
    failed = {"done": False}

    def flaky(*args, **kwargs):
        if not failed["done"] and args[5] == reporting.PERIODS["held_out"][0]:
            failed["done"] = True
            staged_pipeline["calls"].append((args[4], args[5], args[6], kwargs["cost_bps"]))
            raise RuntimeError("synthetic held-out failure")
        return original(*args, **kwargs)

    monkeypatch.setattr(backtest, "simulate", flaky)
    first = reporting.research(staged_pipeline["dataset"], staged_pipeline["output"], staged_pipeline["fees"],
                               stage=reporting.HELD_OUT_STAGE, review_checkpoint=checkpoint)
    assert first["status"] == "blocked"
    assert not json.loads((staged_pipeline["output"] / "qualification.json").read_text())["data_audit_passed"]
    lock_path = staged_pipeline["output"] / reporting.HELD_OUT_LOCK
    lock_bytes = lock_path.read_bytes()
    monkeypatch.setattr(backtest, "simulate", original)
    second = reporting.research(staged_pipeline["dataset"], staged_pipeline["output"], staged_pipeline["fees"],
                                stage=reporting.HELD_OUT_STAGE, review_checkpoint=checkpoint)
    assert second["status"] == "complete"
    assert lock_path.read_bytes() == lock_bytes
    assert len(staged_pipeline["calls"]) == 37


def test_research_pipeline_requires_review_before_held_out(tmp_path, fee_file, monkeypatch):
    from bot_trader import backtest

    calls = []
    monkeypatch.setattr(reporting, "audit", lambda _: {"passed": True, "errors": [],
                       "manifest_sha256": "synthetic-bars", "actions_sha256": "synthetic-actions"})
    monkeypatch.setattr(reporting, "load", lambda _: (None, None, pd.DataFrame(), None, {"feed": "iex"}))

    def simulate(daily, minutes, actions, sessions, strategy, start, end, **kwargs):
        calls.append((strategy, start, end, kwargs["cost_bps"]))
        assert actions.attrs["audited"]
        returns = .0001 + np.sin(np.arange(128)) * .00002
        if strategy != "cash":
            returns += .0002
        run = return_run(returns, start=start)
        run.update(strategy=strategy, cost_bps=kwargs["cost_bps"], trades=[], halt=None, start=start, end=end)
        run["metrics"].update(max_drawdown=.01, turnover=1., trade_count=2, average_exposure=.3,
                               volatility=.02, recovery_sessions=2, max_exposure=.4,
                               yearly_returns={"2024": .01}, symbol_pnl={"SPY": 20.})
        return run

    monkeypatch.setattr(backtest, "simulate", simulate)
    output = tmp_path / "pipeline"
    result = reporting.research(tmp_path / "synthetic", output, fee_file)
    assert result["status"] == "awaiting_review"
    assert len(calls) == 2 * 3 * 4
    assert len(result["results"]) == len(calls)
    assert {run["period"] for run in result["results"]} == set(reporting.STAGED_PERIODS)
    assert not (output / reporting.HELD_OUT_LOCK).exists()
    assert (output / reporting.STAGED_ARTIFACT).exists()
    assert (output / reporting.REVIEW_EXAMPLE).exists()
    rendered = (output / "comparison.md").read_text()
    assert "| development |" in rendered and "| validation |" in rendered
    assert "Held-out evaluation awaits" in rendered
    assert "Neither candidate passes" not in rendered
    staged_bytes = (output / reporting.STAGED_ARTIFACT).read_bytes()
    rerun = reporting.research(tmp_path / "synthetic", output, fee_file)
    assert rerun["status"] == "blocked"
    assert "artifact exists" in rerun["blockers"][0]
    assert len(calls) == 2 * 3 * 4
    assert (output / reporting.STAGED_ARTIFACT).read_bytes() == staged_bytes
    preserved_report = json.loads(staged_bytes)
    assert "| development |" in reporting.render(preserved_report)
    assert "| validation |" in reporting.render(preserved_report)
    review = json.loads((output / reporting.REVIEW_EXAMPLE).read_text())
    review.update(approved=True, reviewer="test reviewer", reviewed_at="2026-09-20T00:00:00Z")
    review_path = output / "review-checkpoint.json"
    review_path.write_text("[]")
    blocked = reporting.research(tmp_path / "synthetic", output, fee_file,
                                 stage=reporting.HELD_OUT_STAGE,
                                 review_checkpoint=review_path)
    assert blocked["status"] == "blocked"
    assert len(calls) == 2 * 3 * 4
    write_json(review_path, review)
    result = reporting.research(tmp_path / "synthetic", output, fee_file,
                                stage=reporting.HELD_OUT_STAGE,
                                review_checkpoint=review_path)
    assert result["status"] == "complete"
    assert len(calls) == 3 * 3 * 4
    assert len(result["results"]) == len(calls)
    assert {run["period"] for run in result["results"]} == set(reporting.PERIODS)
    assert all([e["monthly_expense"] for e in run["operating_expenses"]] == [15., 100.]
               for run in result["results"])
    assert (output / reporting.HELD_OUT_LOCK).exists()
    rendered = (output / "comparison.md").read_text()
    assert "Neither candidate passes" in rendered
    assert "$15/month" in rendered and "$100/month" in rendered
    assert "Per-symbol profit contribution" in rendered and "Uncertainty" in rendered
    assert reporting.verify_qualification(output / "qualification.json")["eligible_strategies"] == []
    journal = [json.loads(line) for line in (output / "attempts.jsonl").read_text().splitlines()]
    assert len([entry for entry in journal if entry["event"] == "run"]) == 36
    assert {entry["stage"] for entry in journal if entry["event"] == "run"} == {
        reporting.DEVELOPMENT_VALIDATION_STAGE, reporting.HELD_OUT_STAGE}
    original_review = review_path.read_bytes()
    review["reviewer"] = "changed reviewer"
    write_json(review_path, review)
    blocked = reporting.research(tmp_path / "synthetic", output, fee_file,
                                 stage=reporting.HELD_OUT_STAGE,
                                 review_checkpoint=review_path)
    assert blocked["status"] == "blocked"
    assert len(calls) == 36
    review_path.write_bytes(original_review)
    review_path.chmod(0o600)
    monkeypatch.setattr(reporting, "protocol_hash", lambda: "changed-protocol")
    result = reporting.research(tmp_path / "synthetic", output, fee_file,
                                stage=reporting.HELD_OUT_STAGE,
                                review_checkpoint=output / "review-checkpoint.json")
    assert result["status"] == "blocked"
    assert not json.loads((output / "qualification.json").read_text())["data_audit_passed"]


def test_nonfinite_drawdown_cannot_qualify(candidates):
    run_at(candidates, cost=20.)["metrics"]["max_drawdown"] = float("nan")
    assert "trend" not in reporting.qualify(candidates, True)["eligible_strategies"]


def test_fee_effective_date_must_be_real_calendar_date(fee_file):
    config = json.loads(fee_file.read_text())
    config["schedule"][0]["effective_date"] = "2016-99-99"
    write_json(fee_file, config)
    assert reporting._fees(fee_file)[1]


def test_invalid_fee_json_still_records_blocked_report(tmp_path, fee_file):
    fee_file.write_text("{")
    output = tmp_path / "blocked-fees"
    result = reporting.research(tmp_path / "missing", output, fee_file)
    assert result["status"] == "blocked"
    assert (output / "comparison.md").exists()
    journal = [json.loads(line) for line in (output / "attempts.jsonl").read_text().splitlines()]
    assert journal[-1]["status"] == "blocked"
