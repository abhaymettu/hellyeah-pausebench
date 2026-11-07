# Preregistration

Written and committed 2026-11-06 20:32 CT (commit cf2034f), before any rule was scored. Changes after this commit are listed at the
bottom with the reason.

## Question

Hellyeah's blog publishes concrete decision rules for pausing, negating and flagging ads. Three of them, all under
Jay Ma's byline, are checkable on simulated data where the truth is known:

| id | Source | Rule as published |
|---|---|---|
| R1 | "Google Ads Optimization Checklist", 2026-11-02 | "Any query that has spent 2x your average CPA with zero conversions gets added to the negative keyword list immediately." |
| R2 | "Google Ads Automated Rules That Actually Work", 2026-10-14 | A CPA pause rule ("if cost per conversion is greater than $50") with the post's fix: "require at least 500 impressions for CPA-based rules before any action triggers", which the post says "blocks 70-80% of false-positive rule fires". The post also says accounts under 100 conversions a month should use 14 to 30 day lookbacks. |
| R3 | "Ad Creative Fatigue", 2026-10-19 | "When Hellyeah's AIMA engine detects that a creative's CTR has fallen 20% or more from its launch baseline for five consecutive days while frequency is rising above 3.0, it flags the creative." The baseline is the first 72 hours. |

These are the rules as written in public posts. Nothing here claims they are what the production system runs.

For each rule: how often does it act on something that is fine (false action), how often does it miss something that is
bad, and how much does that cost? Then: does a rule that accounts for sample size do better at equal power?

## Units and ground truth

- R1 works in spend measured in units of the account's average CPA. A query with true CPA `c` (in those units)
  converts as a Poisson process with rate `1/c` per unit of spend. "Good" means `c <= 1.0` (at or better than average).
  "Bad" means `c >= 2.0`.
- R2 works per keyword per day: impressions, clicks (binomial on CTR), conversions (binomial on CVR), cost = clicks x CPC.
  "At target" means true CPA equals the rule's CPA threshold. "Good" means true CPA <= 0.8 x threshold.
- R3 works per creative per day. True CTR decays with average frequency; daily clicks are binomial on a day-level CTR
  that also carries multiplicative day-to-day noise (overdispersion). "Fatigued" on day t means true CTR on day t is at or
  below 0.8 x the creative's true launch CTR, the post's own 20% definition. A flag is a **false alarm** if, on the day it
  fires, the true CTR is above 0.9 x launch CTR (less than 10% real decline).

## Metrics (fixed now)

- R1: P(negated) as a function of true `c`; over a portfolio, share of negations that hit good queries, catch rate of bad
  queries, spend wasted on bad queries before negation, and expected conversions lost from good queries negated.
- R2: per-keyword false pause rate (at-target and good keywords) per day and within 7 days, as a function of daily
  impressions; share of false fires the 500-impression guard blocks, by count and by spend, over a keyword portfolio.
- R3: false-alarm rate per creative over a 30-day horizon; detection delay (flag day minus true crossing day) for creatives
  that truly cross; miss rate within the horizon. Reported per daily-impression level.

## Challengers (fixed now)

- R1 challenger: Gamma-Poisson posterior on the query's conversion rate per unit spend, prior Gamma(a0, b0) set from the
  account's spread of query CPAs (empirical Bayes, fit on training seeds only). Negate as soon as
  P(c > 1.5 | data) > q, checked continuously as spend accrues. `q` is tuned on training seeds 0 to 9 so the challenger
  catches bad queries (c >= 2) at least as often as R1, then frozen and scored on held-out seeds 100 to 199.
- R2 challenger: the same posterior idea on the keyword's cumulative data (no daily reset), pause when
  P(CPA > threshold | data) > q, q tuned on training seeds to match R2's catch rate of bad keywords (CPA >= 2x threshold),
  scored on held-out seeds.
- R3 challenger: baseline shrunk toward the cohort launch CTR (empirical Bayes), compared with a pooled 5-day window;
  flag when the log ratio is below log(0.8) on its point estimate and its one-sided upper bound (z, including an
  overdispersion term estimated from the creative's own launch days) is below log(0.9). z tuned on training seeds so the
  pooled false-alarm rate is at or below R3's, then frozen and scored on held-out seeds for delay and misses.

## Scenarios (fixed now)

- R1 portfolio: 2,000 queries per seed; true `c` lognormal with median 1.0 and sigma 0.6 (sweep 0.3, 0.6, 0.9);
  potential spend per query lognormal with median 0.5 average-CPA units and sigma 1.5.
- R2: CTR 5%, CVR 5%, CPC set so the at-target keyword's CPA equals the threshold (sweep CTR 2%, 5%, 8%). Daily impressions
  per keyword lognormal (median 60, sigma 1.5; sweep median 20, 60, 200). 30-day horizon, rule evaluated each day on
  yesterday's data, as in the post's example. Zero conversions with nonzero cost counts as CPA above threshold, as in the
  post's example; the variant where zero-conversion days never fire is also reported.
- R3: launch CTR 1% (lognormal across creatives, sigma 0.2); daily impressions in {1k, 3k, 10k, 30k, 100k}; audience size
  log-uniform between 3 and 60 days of impressions; decay rate per creative a mix of 30% near-evergreen and 70% fatiguing;
  day-level overdispersion sigma 0.10 (sweep 0, 0.10, 0.20). Two arms: a single creative, and a "best of 4 at 72 hours"
  arm where four creatives launch and only the highest 72-hour CTR keeps running (selection on a noisy launch, which is
  how creative testing at volume works). Frequency gate as in the post: 7-day frequency above 3.0.

## What would count against the finding

- If R1, R2 and R3 each act falsely at rates under 5% across realistic volumes, the headline is "the published rules hold
  up" and that is what gets reported.
- If the challengers do not beat the rules at equal power on held-out seeds, that is reported as is.

## Changes after this commit

Each change below was made after the first scoring run, and says why and which way it cuts.

1. **R2 "good" definition (code bug, fixed before any number was reported).** The first R2 run scored good keywords as
   CPA <= target. This file says CPA <= 0.8 x target. The code now matches this file.
2. **R2 challenger tuning target.** "Match R2's catch rate" was ambiguous between the daily rule and the post's 30-day
   lookback advice. The challenger is matched to the 30-day lookback with the 500-impression guard, the stronger of the
   two, so the comparison is against the post's best version. A descriptive frontier over q (not tuned) is added.
3. **R3 exposure model and audience range.** The first generator used random exposure and audiences of 3 to 60 days of
   impressions. Under that model 7-day frequency can never pass about 2.6, so the rule's "frequency above 3.0" gate could
   never open and R3 caught nothing. That is a flaw in the generator, not a property of the rule. The generator now uses
   an NBD exposure model (shape 1: delivery concentrated on fewer people, as platforms do) and audiences of 1 to 60 days
   of impressions. The change can only help R3 fire.
4. **R3 overdispersion estimate.** Pooled across the cohort's launch days instead of per creative (three days per
   creative is too few to estimate a variance).
5. **R3 metric added.** Detection delay is only defined for creatives a policy catches, so it favors a policy that
   catches only the easy ones. "Fatigued days left unflagged" (flag day minus crossing day, censored at day 30, over all
   creatives that truly crossed) is added and is the main R3 comparison.
6. **R1 tuning range.** The q search stopped at 0.5; at c_sigma 0.3 no q in that range matched R1's catch rate. The
   search now runs down to 0.01. At c_sigma 0.3 the tuned q did not transfer to the held-out seeds (catch 3.6% vs R1's
   7.4%, about 20 bad queries per seed); that is reported as is.
