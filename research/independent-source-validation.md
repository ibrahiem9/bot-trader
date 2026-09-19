# Independent source sample: 2017-01-03

Status on 2026-09-17: **prepared; independent target-date records not retrieved**.
This is a coverage investigation, not dataset admission or strategy evaluation.

The [target CSV](gap-targets-20170103.csv) contains all 5 IEF and 83 BIL missing
minutes from the saved Alpaca investigation. Each interval is start-inclusive,
end-exclusive, with UTC and New York timestamps. BIL's execution interval is
14:35:00–14:36:00 UTC (09:35–09:36 New York). The target set was checked against
the raw paginated bars, and all eight original evidence hashes matched.
`not_retrieved` means unknown, not that the independent source has no bar.

## Candidate and access

Massive's [bar documentation](https://massive.com/docs/rest/stocks/aggregates/custom-bars)
describes eligible-trade aggregates, omits intervals without qualifying trades,
and supports raw prices using `adjusted=false`. Its
[trade endpoint](https://massive.com/docs/rest/stocks/trades-quotes/trades)
provides conditions, correction indicators and SIP/participant timestamps.
It is a candidate for an independent vendor comparison; both vendors can still
share upstream consolidated feeds, so agreement would not prove tape completeness.

[Current pricing](https://massive.com/pricing) lists Stocks Developer at
$79/month with ten years of history and trades. That window includes this
January 2017 sample as of the review date, but excludes the January 2016 warm-up.
Free and Starter history windows are too short. Published coverage must still
be confirmed by authenticated responses for these symbols and dates.

At the initial review, the Massive integration was available but unconnected,
and no independent-provider credentials were configured. No signup or purchase
had occurred at that point. Exact-date entitled access is still needed to
perform the comparison.

As of 2026-09-18, entitled access remains pending and no subscription purchase
has completed. Account setup, payment status and purchase authorization records
remain private. Prepared comparison tooling has not been run against paid
Massive data. After access is available, review that tooling and use the request
manifest below to retrieve the exact sample before drawing coverage conclusions.

The [FirstRateData BIL page](https://firstratedata.com/i/etf/BIL) offers a public
sample. The downloaded ZIP's minute file contains 5,352 records dated
2026-09-02 through 2026-09-16 and zero records for 2017-01-03. Its
[sample check](independent-public-sample-check.json) records the download URL,
hash and date bounds. This sample does not resolve the target session; it says
nothing about availability in the vendor's paid archive. No recent strategy
returns were calculated from it.

## Retrieval and comparison

The [request manifest](independent-source-requests.json) specifies five initial
GET requests: raw one-minute bars and trades for each symbol, plus the provider's
condition dictionary. Fetch the entire date to retain boundary and late-report
context, follow every continuation, and filter bars to [14:30, 21:00) UTC.
Keep credentials and raw responses private; preserve retrieval time, request
parameters, request IDs, pagination termination, and SHA-256 hashes.

1. Confirm API success and historical entitlement before interpreting an empty
   response. Check unique minute-start timestamps and neighboring populated bars.
2. Compare every target against the independent bars, keeping absent bars distinct
   from unreturned or unauthorized data. Report new gaps elsewhere in the session.
3. Inspect underlying prints, conditions and corrections for each target. Retain
   both SIP and participant times and resolve their aggregation convention before
   assigning a boundary discrepancy to a provider defect. Review historical
   condition meanings; a current dictionary alone does not establish 2017 rules.
4. Classify each minute as agreement, a discrepancy requiring trade review, or
   unresolved. Do not substitute quotes, filled prices or adjacent bars. A matching
   omission corroborates this sample only; a present bar needs provenance review.

Historical validation and trading remain blocked/inactive. Access to this sample
would not resolve full-history coverage, corporate actions or fee-history review.
