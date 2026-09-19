# ETF research protocol v1

Frozen on 2026-09-17, before evaluating market returns. This is a test of two
hypotheses. Neither strategy has earned a recommendation or permission to trade.
The six ETFs below are research instruments. Synthetic fixtures test software;
they provide no evidence of profitability.

## Question and evidence

Can a simple ETF strategy beat a Treasury-bill alternative after costs while
taking less risk than a passive ETF basket?

[Moskowitz, Ooi and Pedersen, *Time Series Momentum*](https://w4.stern.nyu.edu/facdir/lpederse/papers/TimeSeriesMomentum.pdf)
studies momentum across futures markets. Its instruments, sizing and signals
differ from this experiment. It motivates testing trends but does not validate
a 200-day average rule on six ETFs.

[De Groot, Huij and Zhou, *Another Look at Trading Costs and Short-Term Reversal Profits*](https://pure.eur.nl/ws/files/47020617/AnotherLook_2011.pdf)
studies stock reversal strategies and the importance of turnover and trading
costs. It does not test the RSI strategy below. No cited paper establishes that
these parameters are optimal or profitable.

## Frozen rules

Both candidates use SPY, IWM, EFA, EEM, IEF and GLD. Keep this universe fixed,
including losing instruments. This is a small, deliberately selected universe;
its selection limits generalization and may introduce selection bias.

| Rule | Trend | Swing rebound |
| --- | --- | --- |
| Evaluation | Last completed exchange session of each ISO week | Every completed exchange session |
| Enter | Close strictly above its 200-session simple moving average | Close strictly above its 200-session average and Wilder RSI(2) strictly below 10 |
| Exit | Close at or below the average | RSI(2) strictly above 50, or five completed held sessions |
| Reentry | Only on a later signal session | Only on a later signal session |

The average includes the signal day's close. Use split-adjusted closes for
signals. Seed Wilder's average gain and loss with the first two price changes,
then update each with weight 1/2. If both averages are zero, RSI is 50. If only
loss is zero, RSI is 100; if only gain is zero, RSI is zero. At least 200 completed
sessions are required. A rebound position does not exit merely because its
close crosses below the average. Count the entry session as the first held
session after its close; the time exit executes the session after the fifth
completed held session.

Generate orders from completed sessions only. Execute at the next exchange
session's 09:35 America/New_York minute open, with transaction costs. Exchange
holidays and early closes come from the exchange calendar. A holiday Friday
makes Thursday the week's signal day. Never substitute a daily open, later
minute, or next available day for missing scheduled execution data. A minute
open is a historical execution proxy; it is not a guaranteed executable quote.

Each candidate starts each evaluation period independently with $5,000 cash.
Use fractional long shares. Each new position receives at most one-twelfth of
current equity. Aggregate positions and reserved pending buys must stay at or
below 50% of equity when submitting an entry. Reserve cash for costs using the
shared 0.5% sizing buffer. No borrowing, shorts, pyramiding, leverage or options.
Process exits before entries and entries in the fixed universe order. Ordinary
cash earns zero. Hold overnight and across weekends. Exposure may drift above
50% after submission because prices change; this is not an ongoing rebalance
rule.

During regular market hours, observe equity against its persisted account peak.
At a drawdown of at least 10%, cancel pending entries and liquidate at the next
available minute open in replay. Paper operation attempts cancellation and
liquidation through the broker. Persist the halt and require manual review.
Include receivables and trading costs in simulated equity. Never reset a halt
inside an evaluation period. Overnight gaps, execution delays and failures can
cause a loss beyond 10%. Minute observations also miss movement within a bar.

## Periods and benchmarks

| Purpose | Sessions |
| --- | --- |
| Indicator warm-up | 2016 |
| Development | 2017-01-01 through 2021-12-31 |
| Validation | 2022-01-01 through 2023-12-31 |
| Final held-out test | 2024-01-01 through 2026-09-16 |

Use only exchange sessions inside these inclusive dates. Earlier data may warm
indicators for each period. Starting capital, positions and peak reset between
periods so results describe independent allocations. Report this convention;
these are not slices of one continuous portfolio. The final period must stay
unread for strategy selection until implementation and validation checks pass.
Once inspected, it cannot become a fresh test set after a rule change.

The passive comparison budgets one-twelfth of initial equity per ETF, including
costs, at its first 09:35 execution. It keeps the remaining cash, holds shares
without rebalancing and permits weights to drift. The cash alternative buys
BIL with available initial cash after a cost buffer. BIL tracks short-dated
Treasury bills but remains a traded fund with costs and price risk, as described
by its [issuer](https://www.ssga.com/us/en/intermediary/etfs/state-street-spdr-bloomberg-1-3-month-t-bill-etf-bil).
Benchmark distributions become cash when paid; there is no automatic dividend
reinvestment. Neither passive benchmark applies the candidates' drawdown halt.
Use the same execution prices, corporate-action accounting and fee assumptions.
Fund expenses are already reflected in traded prices and distributions; do not
deduct the stated expense ratio again.

## Data admission checks

[Alpaca's data overview](https://docs.alpaca.markets/us/docs/about-market-data-api)
lists history beginning in 2016. Its free Basic plan lists IEX real-time coverage
and 200 historical requests per minute. This does not establish that every ETF
has complete minute history since 2016. At protocol freeze, account entitlement
had not been verified. Later authenticated samples are recorded in the
[comparison status](../reports/comparison.md); they show data gaps on both tested
feeds. Authentication is required for stock data.

Start with the explicitly selected free IEX feed. A missing feed or missing
history blocks the experiment. Do not replace missing IEX bars with SIP bars or
forward-filled prices. A switch to another entitled feed requires a new recorded
dataset and consistent history; do not purchase a subscription automatically.
The plan's $100 monthly budget includes all research and operation costs.

Set feed and adjustment explicitly. The
[historical bars API](https://docs.alpaca.markets/us/reference/stockbars)
documents separate raw, split and dividend adjustments. Its page limit applies
across symbols, so consume every continuation token. Record feed, requested
dates, retrieval time, package versions and hashes with each dataset. Keep raw
OHLCV bars for positions and execution. Store split-adjusted close separately
for signals. Also retrieve fully adjusted daily bars as an audit reference.
Bind the corporate-action review JSON to hashes of raw, split-adjusted and fully
adjusted inputs, and include primary-source evidence for the manual review.
Do not add dividends to a dividend-adjusted price return.

Compare every symbol against the full calendar. Require completed daily bars
for warm-up and evaluation, the exact 09:35 bar for normal execution, and regular
session minute coverage for equity monitoring. Validate timestamps, daylight
saving changes, early closes, duplicate records, positive finite OHLC values,
nonnegative volume and OHLC ordering. Reject unexplained gaps or price jumps.
Sparse IEX trading can fail strict coverage checks; that is a documented data
limitation, not evidence against either strategy. Record the exact gaps.

Audit splits and all cash distributions for the six ETFs and BIL from 2016 to
the cutoff. Reconcile distributions and dates against issuer records or other
independent primary evidence. Check unusual actions, capital-gains payouts and
symbol changes. Unsupported action types block conclusions until handled.
[Alpaca's corporate-actions endpoint](https://docs.alpaca.markets/us/reference/corporateactions-1)
does not guarantee immediate availability of announced actions. Its documentation
does not establish complete coverage back to 2016. An empty response is not
evidence that no action occurred. A successful download alone cannot mark the
history audited.

Apply splits before ex-date trading. Multiply held shares by the new/old ratio
and preserve economic value. Record dividend entitlement from pre-ex-date
holdings, expressed on the post-split share basis where necessary. Recognize a
receivable on the ex-date and cash on the payment session. Include receivables
in equity but never spend them early. Normalize multiple actions per symbol
and session. Preserve payment dates after the evaluation cutoff for outstanding
receivables. Independently verify raw/split-adjusted relationships around every
split. Full-history split scaling may use later ratios only as unit conversion;
it must not change historical decisions or introduce future price information.

Failed data checks block performance conclusions and paper qualification.
Private downloaded data and review evidence stay outside the public repository.

## Costs and fee completeness

Run 5 basis points per buy or sell, then stress at 10 and 20 basis points per
side. These are combined spread/slippage assumptions, not observed execution
estimates. Add applicable regulatory fees separately. Inspect actual paper
execution differences later; simulated fills still cannot identify live costs.

The [Alpaca brokerage fee schedule](https://files.alpaca.markets/disclosures/library/BrokFeeSched.pdf),
revised September 1, 2026, lists equity SEC charges on sales at 0.0000206 times
trade value. It lists TAF sales fees of $0.000195 per share, capped at $9.79 per
trade, and CAT charges on both sides of $0.000003 per NMS share. It uses exact
fractional quantities. Each fee type is totaled separately per account per day,
then rounded up to cents. These current rates cannot represent the whole test.

Supply a reviewed, date-effective schedule covering every evaluation session.
Its review JSON must include sourced, dated SEC, TAF and CAT rows.
Capture SEC, TAF and CAT applicability, rate changes, caps, rounding and source
links. Rates of zero need evidence too. The
[regulatory-fee overview](https://docs.alpaca.markets/us/docs/regulatory-fees)
omits SEC from its equity summary while referring readers to the fee schedule;
the dated schedule takes precedence. Missing intervals or unresolved historical
rounding rules block qualification. A provisional fee model may exercise code
but its report must say why it cannot support a performance conclusion.

Report trading results after variable costs first. Separately show the effect
of fixed monthly operating expenses, including a $15/month infrastructure
scenario and the full $100/month budget ceiling. Accrue these expense overlays
as monthly cost times 12 times elapsed calendar days divided by 365.2425. They
reduce reported wealth but do not resize the underlying trading path. Learning
costs belong in a separate ledger. Personal taxes are outside v1. Fees and dividends absent from
paper broker equity must be explained when comparing paper and research P&L.

## Metrics and decision

Report annualized compounded return, its difference from BIL annualized return,
annualized daily volatility, Sharpe against BIL daily returns, maximum observed
drawdown, peak recovery time, turnover, average and maximum exposure, trade
count, and each calendar year's return. Use 252 exchange sessions per year for
daily annualization and sample standard deviation for volatility. Define
turnover as gross traded notional divided by average equity, without halving or
annualizing it. Exposure is invested market value divided by equity at each
session close. Recovery duration is the longest run of underwater session
closes, including an ongoing run. State whether a peak is still unrecovered at the cutoff.
Report both end-of-day drawdown and the minute-observed drawdown used for the
stop. Do not describe end-of-day measurements as continuous risk monitoring.

A candidate must pass all conditions in both validation and the final test:

1. At 5 basis points, annualized return exceeds BIL after fees. Sharpe exceeds
   the passive basket's Sharpe and drawdown is lower than the passive basket's.
2. Observed drawdown is no greater than 10%, including execution after a halt.
3. At 20 basis points, excess return over BIL stays positive and the drawdown
   condition still holds. Show the 10-basis-point result too.
4. The audited dataset, fee coverage and timing checks pass. No future bars,
   future distributions, or final-period parameter tuning influenced signals.
5. The exceptional-episode check below passes. An undefined metric cannot pass.

To make the episode condition reproducible, subtract BIL's simple daily return
from the candidate's. Find the contiguous 63-session window with the largest
sum of excess returns. The sum outside that window must remain positive in each
period. Report that window's share of total excess return, annual concentration,
and contributions by ETF. This is a predeclared fragility screen; removing
returns is a diagnostic and does not simulate an alternate trading path.

Report sampling uncertainty and limited observations plainly. The two-year
validation window spans few independent market regimes. Correlated trades and
overnight gaps limit what a Sharpe estimate can show. A favorable result is
evidence to observe a strategy in paper, never proof of an edge. If both pass,
choose higher held-out Sharpe, then lower turnover to break a tie. If neither
passes, publish that finding and leave both inactive. Missing data produces
"blocked", not "failed" and not a strategy selection.

## Experiment register and operations

| Experiment | Rule set | Status at protocol freeze |
| --- | --- | --- |
| trend-v1 | Weekly 200-session SMA; shared sizing and stop | Awaiting audited historical dataset |
| rebound-v1 | Daily Wilder RSI(2), 10/50 thresholds, five-session exit | Awaiting audited historical dataset |
| passive-v1 | Initial 50% six-ETF basket | Benchmark, awaiting data |
| cash-v1 | Initial BIL allocation | Benchmark, awaiting data |

The three cost levels and fixed-expense scenarios are predeclared sensitivities.
Record each run's dataset hash, protocol revision, code revision, parameter set,
fee schedule, start/end dates and results. Record failed runs and rejected
variants as well. Any strategy change creates a new experiment entry with its
reason and the periods already inspected. Do not erase inconvenient results.

Paper execution requires a qualifying report and explicit local activation.
Use a dedicated account with initial equity no greater than $5,000 and cash
funding. Persist intent before submission, reconcile positions and order states,
and suspend entries on stale data or ambiguity. Check active, tradable and
fractionable asset flags. Use regular-session fractional DAY market orders;
the [fractional-order documentation](https://docs.alpaca.markets/us/docs/fractional-trading)
requires one of quantity or notional, with at most nine decimal places.

[Alpaca paper simulation](https://docs.alpaca.markets/us/docs/paper-trading)
omits dividends and regulatory fees. It also omits several execution effects,
including latency slippage and market impact. Its fills use NBBO even when a
paper-only account sees IEX data. Broker P&L alone is therefore incomplete.
Tests must cover partial fills, ambiguous retries, rejections, restart recovery,
stale quotes, persistent halts and failure of both notification channels.

The deployment target is one Linux Lightsail instance in us-east-1, with
systemd, SNS email and an external CloudWatch missing-heartbeat alarm. The
[Lightsail price page](https://aws.amazon.com/lightsail/pricing/) lists a 1 GB
Linux public-IPv4 bundle at $7/month as checked on 2026-09-17. The infrastructure
target is below $15/month, including monitoring, email and any storage charges.
No infrastructure or paid subscription is activated by this build. After an
authorized deployment, review daily summaries and weekly performance without
automatic promotion to live trading.
