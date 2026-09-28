"""Operator commands. Defaults create no orders, subscriptions, or infrastructure."""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from .data import DataError, audit, default_home, external_path, ingest, paper_data


def parser() -> argparse.ArgumentParser:
    cli = argparse.ArgumentParser(description="ETF research and Alpaca paper-only operation")
    cli.add_argument("--home", type=Path, default=default_home(), help="Private runtime directory outside the checkout")
    sub = cli.add_subparsers(dest="command", required=True)
    sub.add_parser("preflight", help="Read-only paper authentication and sample historical coverage checks")
    download = sub.add_parser("ingest", help="Download pinned historical bars with explicit feed")
    download.add_argument("--feed", choices=["iex", "sip"], default="iex")
    download.add_argument("--dataset", type=Path)
    check = sub.add_parser("audit", help="Audit coverage, prices, corporate actions and adjustment behavior")
    check.add_argument("--dataset", type=Path)
    backtest = sub.add_parser("backtest", help="Run all frozen periods/strategies/costs after data admission")
    backtest.add_argument("--dataset", type=Path)
    backtest.add_argument("--fees", type=Path, help="Reviewed date-effective fee schedule JSON")
    backtest.add_argument("--output", type=Path)
    report = sub.add_parser("report", help="Render an existing comparison JSON without rerunning research")
    report.add_argument("--input", type=Path)
    report.add_argument("--output", type=Path)
    paper = sub.add_parser("paper", help="Explicit paper activation or monitoring")
    modes = paper.add_subparsers(dest="paper_command", required=True)
    activate = modes.add_parser("activate")
    activate.add_argument("--qualification", type=Path, required=True)
    activate.add_argument("--strategy", choices=["trend", "rebound"], required=True)
    run = modes.add_parser("run")
    run.add_argument("--interval", type=float, default=5)
    modes.add_parser("once")
    sub.add_parser("status", help="Read local status without contacting broker or email services")
    halt = sub.add_parser("halt", help="Persist a halt, then attempt paper cancellation/liquidation")
    halt.add_argument("--reason", default="Manual halt; review required")
    return cli


def _notifications():
    from .notifications import AwsNotifications, DisabledNotifications
    topic = os.getenv("BOT_TRADER_SNS_TOPIC")
    return AwsNotifications(topic) if topic else DisabledNotifications()


def execute(args) -> int:
    from .reporting import render, research

    home = external_path(args.home)
    if args.command == "preflight":
        from .access import preflight

        output = home / "preflight.json"
        result = preflight(output)
        print(json.dumps({"paper_authenticated": result["paper_authenticated"],
                          "initial_account_eligible": result["initial_account_eligible"],
                          "checks": result["checks"], "private_report": str(output)}, indent=2))
        return 0 if all(row["coverage_passed"] for row in result["checks"]) else 2
    if args.command == "ingest":
        manifest = ingest(args.dataset or home / "data", feed=args.feed)
        print(json.dumps({"feed": manifest["feed"], "partitions": len(manifest["files"]),
                          "next": "Normalize/review actions.csv and run audit"}, indent=2))
    elif args.command == "audit":
        result = audit(args.dataset or home / "data")
        print(json.dumps(result, indent=2))
        return 0 if result["passed"] else 2
    elif args.command == "backtest":
        output = args.output or home / "research"
        result = research(args.dataset or home / "data", output, fee_path=args.fees)
        print(f"Research {result['status']}: {output / 'comparison.md'}")
        return 0 if result["status"] == "complete" else 2
    elif args.command == "report":
        source = external_path(args.input or home / "research" / "comparison.json")
        output = external_path(args.output or home / "research" / "comparison.md")
        report = json.loads(source.read_text())
        output.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        output.write_text(render(report))
        output.chmod(0o600)
        print(output)
    else:
        from .broker import AlpacaBroker
        from .paper import PaperRunner
        from .state import State

        state = State(home / "state.sqlite3")
        try:
            if args.command == "status":
                print(json.dumps(PaperRunner(state, None, None).status(), indent=2))
                return 0
            if args.command == "halt":
                # A credential/network failure must never prevent a local shutdown.
                with state.lock():
                    if not state.get("halt"):
                        state.set("halt", args.reason)
                        state.event("halt", args.reason)
                if not state.get("account_id"):
                    print("Persistent halt saved. No paper account is initialized.")
                    return 0
            runner = PaperRunner(state, AlpacaBroker(), _notifications())
            if args.command == "halt":
                runner.halt(args.reason)
                print(json.dumps(runner.status(), indent=2))
            elif args.paper_command == "activate":
                runner.activate(external_path(args.qualification), args.strategy)
                print(json.dumps(runner.status(), indent=2))
            elif args.paper_command == "once":
                # Single safety tick intentionally does not acquire/execute new signals.
                print(json.dumps(runner.tick(), indent=2))
            else:
                runner.run(lambda: paper_data(state.get("active_data_feed", "iex")), interval=args.interval)
        finally:
            state.close()
    return 0


def main(argv=None) -> int:
    args = parser().parse_args(argv)
    try:
        return execute(args)
    except KeyboardInterrupt:
        print("Monitoring stopped. Persistent state is retained; stopping the process does not flatten positions.",
              file=sys.stderr)
        return 130
    except (DataError, ValueError, OSError, RuntimeError, KeyError) as exc:
        print(f"Blocked: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
