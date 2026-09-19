# ETF trading research and paper bot

Status: **Proposal**. Opened 2026-09-17.

Determine whether ETF trend following or swing rebounds earn worthwhile returns
for their risk. The first implementation supports reproducible tests and paper
operation. Research approval depends on verified historical evidence. Finding
that neither candidate merits an allocation is a successful research outcome.

## Constraints and decisions

The eventual capital ceiling is $5,000 with no borrowing. Stop after observing
a 10% decline from the account peak, then attempt to close positions. Execution
can carry losses beyond that threshold. Overnight and weekend holdings are
allowed. Research and operation may cost up to $100/month, tracked separately
from trading returns. There is no deadline to trade real money.

Compare SPY, IWM, EFA, EEM, IEF and GLD under two frozen hypotheses: weekly
200-day trend following and daily RSI(2) rebounds above the 200-day average.
Each entry uses at most one-twelfth of equity; total exposure is capped at 50%
when an entry is submitted. Signals use completed sessions and execute the next
session at 09:35 Eastern. See the
[frozen protocol](../research/strategy-evidence.md) for exact rules and gates.

Use Python, Alpaca's paper endpoints, pandas/NumPy, Parquet and SQLite. Keep
credentials, account settings, downloaded bars and trading logs outside this
public repository. Begin with free data and audit its limits. Neither a paid
data subscription nor a deployment is part of the first local build.

## Measurable results

The research milestone is a reproducible comparison across development
(2017-2021), validation (2022-2023), and held-out sessions (2024-2026-09-16).
Use 2016 for warm-up. Include a passive 50% ETF basket and BIL benchmark with
consistent distributions and costs. Publish risk, turnover, annual performance,
profit concentration and uncertainty. Show 5, 10 and 20 basis-point costs per
side, plus applicable fees and separate fixed-expense scenarios.

The operational milestone is a tested runner that cannot contact live trading
endpoints. It must survive partial fills, retries and restarts without duplicate
orders. It must block entries on stale data or unresolved broker differences.
Its drawdown halt must survive restart. Verify alert and external heartbeat
failure behavior without sending messages during local tests.

First-build research completion requires both reproducible experiments and the
comparison report using admitted market data. Software checks alone cannot
complete that milestone. No historical performance or paper qualification is
claimed at proposal creation.

## Phases

| Phase | Deliverable | Exit condition |
| --- | --- | --- |
| Protocol and implementation | Frozen rules, tests, CLI, research records | Local behavior checks pass; assumptions are explicit |
| Data admission | Raw and split-adjusted bars, action ledger, fee schedule | Coverage, timing, corporate actions and historical fees reviewed |
| Historical comparison | Both candidates, both benchmarks, cost stresses | Reproducible report with pass/fail/blocked reasons |
| Paper observation | One qualifying strategy, daily email, weekly review | Explicit activation after research qualification and operational checks |
| Later live-pilot decision | Separate capital, tax and execution review | New decision; never automatic promotion |

## Current evidence and blockers

The repository began with a README and no strategy implementation. The first
build adds local research and operational tools. No authenticated historical
dataset has been evaluated at protocol creation. Paper authentication was
subsequently verified on 2026-09-17 using a private local credential file.
Credentials and personal account details remain outside tracked files.

Alpaca advertises history since 2016, but that does not prove complete minute
coverage for this universe on a free account. Corporate-action coverage back
to 2016 and a date-effective historical fee schedule need independent review.
Missing evidence blocks conclusions. Mock broker tests and synthetic prices
exercise the implementation only. Authenticated samples now confirm missing
early IEX history. SIP samples include old daily data but miss some regular-session
minutes, including BIL's required 09:35 bar on January 3, 2017. The provider's
corporate-action response was saved for review. No subscriptions were changed.

The local implementation now includes ingestion/audits, both backtests and
benchmarks, all cost scenarios, report qualification, and the inactive paper
runner. The offline suite covers accounting, signal timing, data gaps, report
screens, restarts, duplicate orders, partial fills, notifications and persistent
halts. Deployment templates are prepared for review. The
[comparison status](../reports/comparison.md) records the open evidence gates.
Read-only broker connectivity passed. Real fills, email delivery and AWS deployment remain untested.

The planned infrastructure is one Lightsail server in us-east-1, systemd, SNS
email and a CloudWatch missing-heartbeat alarm. Target infrastructure cost is
below $15/month. No server, subscription, ongoing strategy or live order has
been activated by the proposal. No funds have been spent. Daily summaries and
weekly reviews begin only after paper activation. Individual stocks and minute-scale strategies remain
future work.

## Decision record

| Date | Decision | Reason |
| --- | --- | --- |
| 2026-09-17 | Keep initiative at proposal until evidence supports progression | No tested profitability claim exists |
| 2026-09-17 | Freeze both initial rules before evaluating returns | Limit parameter selection against the final test |
| 2026-09-17 | Use BIL as the cash alternative | Compare opportunity cost using an explicit traded benchmark |
| 2026-09-17 | Treat action and fee completeness as admission gates | Missing dividends or fees can reverse the conclusion |
| 2026-09-17 | Require report qualification and local activation before paper entries | A passing software suite is insufficient research evidence |
| 2026-09-17 | Persist halts and exclude live execution from this implementation | Keep the agreed account-risk boundary explicit |

Related work: [research evidence](../research/strategy-evidence.md),
[implementation interfaces](../research/implementation-contract.md), and the
[repository guide](../README.md). Future live allocation, tax treatment and any
stock-data subscription need a later decision.
