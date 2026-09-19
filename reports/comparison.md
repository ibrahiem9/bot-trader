# ETF strategy comparison: build status

Status: **blocked pending verified historical inputs**. Neither strategy is
selected or active. No return estimates have been produced.

| Required evidence | Status |
| --- | --- |
| Authenticated Alpaca historical access | Verified by read-only requests on 2026-09-17 |
| Complete raw minute and daily coverage | Failed sampled coverage checks; details below |
| Audited split/dividend ledger for all seven ETFs | Provider response downloaded privately; not reviewed |
| Reviewed date-effective regulatory fee history | Not available |
| Development, validation and held-out results | Blocked by the above inputs |
| Real paper fills and SNS/email/CloudWatch delivery | Not exercised |

## Observed access and coverage

The IEX feed returned no daily bars for January 4-8, 2016 and no regular-session
minute bars for January 3, 2017 across the seven instruments. Recent IEX daily
bars were accessible. This is a historical-coverage blocker, not an authentication
failure.

SIP historical data was accessible through the existing account without a
subscription change. It returned all five daily bars in the sampled warm-up week.
On January 3, 2017, SPY, IWM, EFA, EEM and GLD each had 390 regular-session minute
bars. IEF had 385 and BIL had 307. BIL also lacked the exact 09:35 execution bar
required by the cash benchmark. These samples fail the frozen data requirement.
The program has not filled gaps or changed execution timing to get a result.

The corporate-action API returned 348 cash-dividend records and one reverse
split for the requested window. The response is saved privately for review;
its existence and record count do not prove historical completeness.

`bot-trader preflight` reproduces the sampled checks and writes a private report.
It now verifies timestamp coverage rather than bar counts alone and reports
per-symbol missing, duplicate and unexpected timestamps. This correction was
checked with synthetic duplicate, shifted, reordered and timezone-equivalent
bars. A fresh authenticated run on 2026-09-17 confirmed the counts above using
the corrected timestamp checks: SIP daily coverage passed for all seven symbols,
while IEF and BIL missed 5 and 83 regular-session minutes respectively. No SIP
sample had duplicate or unexpected timestamps. Preflight exited with status 2,
the expected coverage-failure result.
Account details and credentials are excluded from this public status record.
Resolving data-source coverage and reviewing actions/fees are the next research
steps. No historical strategy returns have been evaluated.

## SIP gap investigation: 2026-09-17

Independent single-symbol REST requests reproduced the January 3, 2017 gaps.
Each bar request used `feed=sip`, `adjustment=raw`, `asof=2026-09-16` and a
100-record page size; all four pages were followed through the final null page
token for each symbol. The resulting timestamps matched single-symbol SDK
requests and the fresh multi-symbol preflight coverage. Extending the request
end through the final fraction of the session's last second recovered no bars.
This provides no evidence of a pagination, multi-symbol or end-boundary defect
for this sample.

Full-session raw SIP trades were also downloaded with explicit pagination:
10,922 IEF trades across 11 pages and 4,325 BIL trades across 5 pages.

| Symbol | Missing minutes | No returned trades | Only odd-lot trades | Exact 09:35 bar |
| --- | ---: | ---: | ---: | --- |
| IEF | 5 | 2 | 3 | Present |
| BIL | 83 | 57 | 26 | Missing; no returned trades |

IEF's missing minutes were 09:32, 10:40, 12:08, 13:30 and 14:53 New York time.
All trades returned inside missing minutes carried condition `I` (odd lot).
[Alpaca's aggregation rules](https://docs.alpaca.markets/us/docs/market-data-faq#how-are-bars-aggregated)
exclude odd lots from bar prices and emit no bar when eligible prices are absent.
The evidence is consistent with sparse eligible trading in the provider's
history. It does not independently prove the completeness of that trade history
or establish what another provider recorded.

The private evidence bundle is under
`~/.local/share/bot-trader/bot-trader-coverage-20260917/`: the preflight report,
raw paginated responses, every missing timestamp and its trade conditions,
diagnostic script, and SHA-256 manifest with package versions. This is a sampled
diagnosis, not an admitted dataset. The script retains its original temporary
output path; change `ROOT` when reproducing into a fresh private directory.

The next coverage step is an independent historical source sample for these exact
gaps, especially BIL at 2017-01-03 09:35 New York time. A candidate must document
whether its bars contain actual eligible trades, quotes or filled prices before
it can be assessed against the frozen protocol. A fuller-looking series alone
is insufficient. No alternate source has yet been verified. If genuine data
cannot satisfy the protocol, a separately approved and versioned research
protocol would be needed; the current experiment remains blocked. No full
multi-year download, gap filling, execution-time change or activation occurred.

Verification: all 12 access-probe tests passed (existing deprecation warnings
remain). Saved responses passed pagination-termination, record-count, timestamp
agreement and gap-accounting cross-checks. Production code was unchanged.

## Independent source access review: 2026-09-17

All eight original evidence hashes were verified. The exact 88 missing intervals
were exported and checked against the raw paginated Alpaca bars, including BIL
at 09:35 New York time. The [sample review and request manifest](../research/independent-source-validation.md)
record the targets, candidate source, request parameters and comparison rules.

Massive documents eligible-trade minute bars and historical trades covering the
sample date under its ten-year Developer plan. The integration is available but
not connected; no independent-provider credentials were found in the inspected
environment or project credential file. FirstRateData's public BIL sample was
downloaded and checked: its 5,352 minute records cover September 2–16, 2026,
with none for the target date. **No independent target-date records have been
retrieved, so none of the 88 gaps is independently verified yet.** Access to an
entitled account or an exact-date vendor sample is still required. No subscription
was purchased. Historical validation remains blocked and trading remains inactive.

As of 2026-09-18, entitled independent-source access is still pending. No paid
sample has been retrieved and no subscription purchase has completed. Account
and payment records are kept outside this public repository.

## Admission review follow-ups

The local review found two additional limits to automated admission checks.
Daily and minute OHLC are validated independently, so inconsistent price scales
can pass. Dividend adjustment changes require ledger entries, but the audit
does not validate the recorded cash amounts against adjustment factors. A
synthetic ledger with an implausibly large dividend and unchanged adjusted
prices passed when accompanied by a matching synthetic review file.

Independent corporate-action review remains required. Before admitting real
inputs, establish the provider's aggregation and adjustment conventions and
add compatible cross-checks for these inconsistencies. These findings do not
alter the frozen strategy rules or justify relaxing coverage requirements.

The implementation provides both experiments, the comparison/report commands,
and an inactive paper-only runner. Offline tests use synthetic data and fake
services to verify behavior. Those tests provide no evidence of profitability.
The first-build performance milestone remains open until genuine data passes
the audit and both experiments produce their comparison.

Run `bot-trader backtest` after configuring the inputs described in the
[README](../README.md). It produces a private comparison even when admission is
blocked, records the reasons and clears any earlier activation qualification.

The protocol fixes SPY, IWM, EFA, EEM, IEF and GLD, with BIL as the cash comparison.
It screens both validation and held-out results after costs and tests 20-basis-point
stress. It also removes the best 63-session excess-return episode to check fragility.
See the [full protocol](../research/strategy-evidence.md) for the frozen rules,
uncertainty measures, evidence and promotion criteria. Finding that neither
candidate qualifies is a valid completed research outcome.
