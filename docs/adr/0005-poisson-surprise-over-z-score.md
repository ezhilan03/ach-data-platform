# ADR-0005: Poisson surprise, not a z-score on daily rates

**Status:** accepted · 2026-09-11 · supersedes the initial detector design

## Context

The detector flags originator-days whose return behaviour departs from that
originator's own recent history. The obvious first implementation: compute a
daily return rate, compare it to a trailing 14-day mean and standard deviation,
flag when the z-score exceeds 3 or when the rate exceeds the NACHA network
threshold.

That is what was built first. The evaluation harness measured it against the
labeled incident plan and reported:

```
day-level   P=0.097  R=0.862  F1=0.174   (tp=25, fp=233, fn=4)
cluster precision 0.064  (10 of 156 clusters overlapped a real incident)
```

An analyst handed 156 clusters to investigate, 146 of which are noise, stops
opening them by the end of the first week.

## Decision

Two changes, both measured.

**1. Measure settlement lag in banking days.** `date_diff('day', …)` made every
Friday entry (settling Monday) look like a 3-day lag against a 1-day baseline.
160 of the 233 false positives were Fridays. A closed-form
`banking_days_between` dbt macro replaces it, pinned by a test that
brute-forces it against a day-by-day count for every weekday and every lag up
to 30 days.

**2. Replace the z-score on rates with a Poisson tail test on counts.** Flag
when `P(X ≥ k | λ = n·p₀) < α`, where *k* is today's return count, *n* today's
entry count, and *p₀* the pooled trailing rate. α = 10⁻³, with a floor of 3
events and Bayesian pseudo-counts (0.5 returns / 500 entries) so a quiet
baseline cannot drive λ to zero.

## Why the z-score was the wrong statistic

A 200-entry day has zero, one, or two unauthorized returns. The daily "rate" is
a count divided by a small denominator, so it takes three values, not a
continuous distribution. Against a trailing baseline that is often exactly
zero — giving a standard deviation near zero — a single extra return is a
5σ event. The z-score was not mis-tuned; it was measuring the wrong thing.

Applying the NACHA thresholds per day compounded it: 0.5% unauthorized is
defined over a rolling **60-day** window, and a single return on a 200-entry
day is 0.5%. That daily comparison now exists only as a dashboard panel;
`fct_return_rate_60d` computes the network-defined version.

The Poisson formulation asks the question that actually matters — *how
surprising is this many returns, given this originator's normal rate and
today's volume?* — and answers with a p-value that can be thresholded honestly.
Pooling the trailing window into counts rather than averaging fourteen noisy
daily rates is the same argument one level down.

## Measured result

Same seed, same ground truth, after both changes:

```
day-level   P=0.895  R=0.607  F1=0.723   (tp=17, fp=2, fn=11)
incident recall 9/10   cluster precision 0.818   mean detection delay 0.2 days
```

Precision went from 0.10 to 0.89. Day-level recall fell from 0.86 to 0.61, and
that trade is deliberate: the detector flags the *sharp* days of an incident
rather than every day of it, and incident-level recall — did we catch the
incident at all — stayed at 9 of 10. Loosening α would buy day-level recall
back at the cost of precision, and an alert nobody reads has a true recall of
zero.

## A third finding, from the same run

Two of the ten labeled incidents were undetectable by construction: the
generator had assigned an NSF spike and an unauthorized spike to a payroll
originator, which is 97% credits. Credits do not NSF. The incident planner is
now debit-aware.

This is worth recording because it is the failure mode of synthetic
benchmarks in general — the harness was correctly reporting a miss, and the
bug was in the ground truth rather than in the thing being measured. A number
that is "too low" is a hypothesis about the system *or* about the benchmark.

## Consequences

- The detector needs pooled count columns in `fct_originator_daily`
  (`baseline_*_sum`), alongside the mean-of-rates columns the dashboards use.
- α, the event floor and the pseudo-counts are `DetectorConfig` fields,
  serialised into every `anomalies.json` so any result can be traced to the
  configuration that produced it.
- `eval/thresholds.json` holds the floors CI enforces. Loosening one to make a
  build pass is a visible, reviewable diff.
