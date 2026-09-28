"""Offline public CLI behavior; no broker calls and no historical claims."""
import json

from bot_trader.cli import main


def test_backtest_defaults_to_development_validation_stage():
    from bot_trader.cli import parser

    args = parser().parse_args(["backtest"])
    assert args.stage == "development-validation"
    assert args.review_checkpoint is None


def test_backtest_forwards_stage_and_review_and_accepts_awaiting_review(tmp_path, capsys, monkeypatch):
    from bot_trader import reporting

    seen = {}

    def fake_research(dataset, output, fee_path=None, stage=None, review_checkpoint=None):
        seen.update(dataset=dataset, output=output, fee_path=fee_path, stage=stage,
                    review_checkpoint=review_checkpoint)
        return {"status": "awaiting_review"}

    monkeypatch.setattr(reporting, "research", fake_research)
    dataset = tmp_path / "dataset"
    fees = tmp_path / "fees.json"
    output = tmp_path / "out"
    checkpoint = tmp_path / "checkpoint.json"
    assert main(["--home", str(tmp_path), "backtest", "--dataset", str(dataset),
                 "--fees", str(fees), "--output", str(output), "--stage", "held-out",
                 "--review-checkpoint", str(checkpoint)]) == 0
    capsys.readouterr()
    assert seen == {"dataset": dataset, "output": output, "fee_path": fees,
                    "stage": "held-out", "review_checkpoint": checkpoint}


def test_status_needs_no_credentials_and_does_not_activate(tmp_path, capsys, monkeypatch):
    monkeypatch.delenv("APCA_API_KEY_ID", raising=False)
    monkeypatch.delenv("APCA_API_SECRET_KEY", raising=False)
    assert main(["--home", str(tmp_path), "status"]) == 0
    state = json.loads(capsys.readouterr().out)
    assert state["active_strategy"] is None
    assert state["account_id"] is None


def test_manual_halt_persists_without_credentials(tmp_path, capsys, monkeypatch):
    monkeypatch.delenv("APCA_API_KEY_ID", raising=False)
    assert main(["--home", str(tmp_path), "halt", "--reason", "test review"]) == 0
    capsys.readouterr()
    assert main(["--home", str(tmp_path), "status"]) == 0
    assert json.loads(capsys.readouterr().out)["halt"] == "test review"


def test_missing_data_and_fee_file_write_blocked_report_and_cannot_activate(tmp_path, capsys):
    assert main(["--home", str(tmp_path), "backtest", "--fees", str(tmp_path / "missing-fees.json")]) == 2
    result = json.loads((tmp_path / "research" / "comparison.json").read_text())
    assert result["status"] == "blocked"
    assert result["stage"] == "development-validation"
    assert result["results"] == []
    assert not json.loads((tmp_path / "research" / "qualification.json").read_text())["eligible_strategies"]
    assert main(["--home", str(tmp_path), "report"]) == 0
    assert "No historical return estimates" in (tmp_path / "research" / "comparison.md").read_text()


def test_ingest_without_credentials_is_clear_and_does_not_create_data(tmp_path, capsys, monkeypatch):
    monkeypatch.delenv("APCA_API_KEY_ID", raising=False)
    assert main(["--home", str(tmp_path), "ingest"]) == 2
    assert "APCA_API_KEY_ID" in capsys.readouterr().err
    assert not (tmp_path / "data").exists()


def test_runtime_paths_inside_checkout_are_rejected(capsys):
    from pathlib import Path
    repo = Path(__file__).resolve().parents[1]
    assert main(["--home", str(repo / "private-runtime"), "status"]) == 2
    assert "outside the repository" in capsys.readouterr().err
