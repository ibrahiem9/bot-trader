# Optional paper deployment

These templates are reviewable examples. This build provisions nothing, starts
no service, creates no subscription, and sends no email. A qualified experiment
and explicit local activation are required before entries are possible.

The intended host is one Linux Lightsail instance in `us-east-1`. Start with the
documented $7/month 1 GB public-IPv4 bundle and keep a $15/month infrastructure
budget alert. Verify current regional prices, storage, transfer, monitoring,
and taxes before provisioning. Historical minute data can exceed the server's
disk or RAM; run research elsewhere and keep only the required live signal cache
on the server. Research expenses belong to the separate $100/month allowance.

Install the project under `/opt/bot-trader`, use an unprivileged `bot-trader`
service user, and keep its private runtime directory at `/var/lib/bot-trader`.
Use the locked Python dependencies. Adapt `bot-trader.service` to the selected
paths. Store `environment.example` values in `/etc/bot-trader.env` with mode 0600.
Keep the machine clock synchronized and install security updates. SQLite and its
WAL must be backed up consistently; losing the ledger requires manual broker
reconciliation, never silently starting a fresh database against open positions.

Review and apply `alarms.yaml` only when deployment is separately authorized.
Confirm the SNS email subscription, then configure its ARN for the runner.
Replace the placeholder resource in `iam-policy.json`; limit the service's AWS
credentials to these actions. Lightsail needs a separately managed AWS credential
source because it does not supply EC2 instance profiles. Do not copy personal
administrator credentials to the server. The CloudWatch alarm runs externally and
treats missing data as failure after two five-minute periods, including weekends.
The runner publishes the dimensionless `BotTrader/Heartbeat` metric and checks
the confirmed SNS subscription once per 60 seconds after success, independently
of its five-second trading monitor cadence. Failed checks or metric writes retry
on the next invocation; failures do not advance the throttle. Subscription loss
may therefore take up to one minute to detect. Keep the default namespace with
the supplied alarm and IAM policy, or update all three together.

Before enabling a service, test that SNS email arrives, deliberately stop the
runner and verify the external alarm, then start the runner and verify recovery.
The SDK's successful SNS publish confirms acceptance, not inbox delivery. Monitor
the email account and AWS billing separately. Code tests mock AWS and the broker;
they do not prove credentials, subscriptions, IAM, network, or email delivery.

Use a new, dedicated Alpaca **paper** account with no orders or positions and
positive equity at most $5,000. Do not manually trade, deposit, withdraw, reset,
or share this account while the runner owns it. Research qualification and local
activation are separate from installing the service. The service never selects
or activates a candidate. There is no live endpoint setting or live switch.

The daemon checks equity at a configured interval (five seconds by default),
subject to bounded network latency. Signal data acquisition runs in a background
thread, once starting at 09:25 Eastern per trading day (or on restart through
09:35); a failure or data arriving after 09:35 misses that day's signal window.
The wall clock is checked again immediately before each ordinary transmission;
blocking broker calls cannot carry an unsent order into the next minute.
It only enters using the immediately preceding
completed exchange session. Orders are submitted sequentially as prior fills
reconcile. Some planned orders may miss the minute window; paper observations
must include these differences from the backtest. An unresolved submission,
partial fill, rejection, missing email subscription, heartbeat failure, or
unexplained position difference blocks new entries. Splits or manual broker
changes that disagree with the persisted fill ledger also require review.

The 10% threshold uses broker-reported paper equity and a persisted observed peak.
Paper accounting can omit dividends and fees; this equity series is not the
research total-return series. Price gaps, network outages, market closures, and
failed fills can exceed the threshold. A halt cancels known outstanding orders,
waits for their final state, and sells reconciled positions when the market is
open. Rejected liquidations retry after at least 60 seconds. Ambiguous submissions
remain suspended, even during a halt: inspect their client IDs at Alpaca rather
than retrying manually. An unknown broker order or position prevents automated
liquidation; intervene in the paper account after reviewing the ledger.

There is deliberately no automatic reset or resume command. After a halt or
ambiguous state, stop the service, preserve a copy of its database, compare the
broker order/fill history with every intention, and investigate the cause. Do not
delete the ledger to bypass a halt. A new activation requires a fresh, empty paper
account and a separately reviewed state file. Daily summaries are generated after
16:10 Eastern on exchange sessions; weekly review reminders follow the week's
last session. The exchange calendar is loaded independently of signal downloads,
so restarting after 09:35 or a failed download does not disable these summaries.
Review costs, missed orders, fills, and drawdowns yourself. Neither
paper profitability nor a running service authorizes live trading.
