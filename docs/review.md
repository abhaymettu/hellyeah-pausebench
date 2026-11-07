# pausebench code review (2026-11-06)

Repo: hellyeah-pausebench at cd99f66. Read-only review; nothing edited. Probes were run with `uv run python -c` against the repo modules; outputs quoted below.

## Short answer

The math is right. The R1 closed form matches the sim, the Gamma-Poisson posteriors and gammainc/gammaincinv calls are parameterized correctly, the rules' windows and consecutive-day logic are right, pauses are absorbing, and no test seed leaks into tuning. Three findings would change a headline number or what it is credited to:

1. **R3 (severity 7):** the challenger's whole catch advantage comes from dropping the post's frequency gate. The shrinkage and pooling add nothing to it. With the gate added back, the challenger catches the same ~45% R3 does.
2. **R1 (severity 6):** the "average CPA" unit is the median query CPA, not the account's average CPA. As coded, R1 negates at 2.4x the real average (sigma 0.6) and 3x (sigma 0.9), not the 2x the post says.
3. **R2 (severity 5):** two "blocked vs no guard" shares are measured against the wrong baseline, and those shares are the numbers that test the post's "blocks 70-80%" claim.

## Findings

### F1. R3: the challenger's win comes from dropping the frequency gate (severity 7)

`pausebench/r3_fatigue.py:61-72` applies `f7 > 3.0` to R3. `challenger` (lines 75-90) has no frequency condition at all. PREREG's challenger spec leaves out the gate too, so this is disclosed in the letter of the spec. Still, the headline comparison "challenger vs R3 at matched false alarms" mixes two changes: (a) the statistics and (b) removing the gate.

In this generator the gate is the binding constraint. With NBD K=1, `nbd_reach(I, A) = A*I/(A+I)`, so `f7 = 1 + i7/A` exactly. The gate opens only if the audience is under 3.5 days of impressions, and audiences are log-uniform on 1 to 60 days. A probe (10k/day, od 0.10, seed 100, 4,000 creatives) found that only **45.2% of true crossers ever have f7 > 3**. That is why R3's catch rate sits flat at 40 to 48% at every volume in results/r3.txt.

Probe, test seeds 100 to 109, od 0.10, z = 2.5, same prior and od_hat as the repo. `ch_gate` is the repo challenger with `& f7 > 3` added:

| arm, impr/day | r3 | r3_no_gate | challenger | challenger + gate |
|---|---|---|---|---|
| single, 1k | FA 1.4%, caught 45%, unflagged 13.7d | FA 7.2%, 76%, 10.2d | FA 0.5%, 61%, 13.1d | FA 0.1%, 42%, 14.9d |
| single, 10k | FA 0.1%, 46%, 12.5d | FA 0.9%, 86%, 7.8d | FA 0.3%, 85%, 7.7d | FA 0.1%, 46%, 12.2d |
| single, 100k | FA 0.0%, 47%, 12.1d | FA 0.1%, 88%, 7.1d | FA 0.2%, 90%, 6.2d | FA 0.0%, 47%, 11.7d |
| best_of_4, 1k | FA 2.2%, 43%, 12.6d | FA 14.8%, 85%, 7.2d | FA 0.7%, 61%, 12.7d | FA 0.1%, 39%, 14.6d |
| best_of_4, 10k | FA 0.5%, 41%, 12.5d | FA 1.8%, 88%, 7.0d | FA 0.4%, 84%, 7.2d | FA 0.1%, 40%, 12.4d |

What the sim actually supports: the published gate halves R3's reach here, and that comes from a generator choice (K=1 and the 1 to 60 day audience range, PREREG change 3). The statistical part of the challenger cuts false alarms at low volume (1k/day: 0.5% vs 7.2% ungated, 0.1% vs 1.4% gated). At high volume it only matches the ungated rule, with about 1 day less unflagged. The fix is to report challenger vs r3_no_freq_gate as the test of the statistics, report the gated challenger vs R3 as a second comparison, and state that R3's catch ceiling is set by the audience distribution.

### F2. R1: the CPA unit is the median query CPA, not the account average (severity 6)

`pausebench/r1_negate.py:22` draws `c ~ lognormal(0, sigma)`, so the unit is the median query CPA. Spend is independent of c, so the account's average CPA (total spend / total conversions) comes out near exp(-sigma^2/2), not 1. Probe over seeds 100 to 119, realized sum(spend)/sum(conversions):

- sigma 0.3: 0.964 (theory 0.956)
- sigma 0.6: 0.853 (0.835)
- sigma 0.9: 0.689 (0.667)

So `rule_r1` at spend 2.0 (line 33) fires at 2.3x to 3.0x the account's average CPA, while the post says 2x. "Good = c <= 1.0, at or better than average" (line 16, PREREG line 25) is likewise above the account average. Re-running R1 with the threshold at 2x the true account CPA (seeds 100 to 139, good kept at c <= 1):

| sigma | threshold | good share of negations | bad caught | good conversions lost |
|---|---|---|---|---|
| 0.6 | 2.00, as coded | 15.1% | 8.5% | 2.27% |
| 0.6 | 1.67, 2x true average | 19.5% | 11.5% | 3.65% |
| 0.9 | 2.00, as coded | 9.7% | 9.5% | 1.22% |
| 0.9 | 1.33, 2x true average | 16.6% | 16.8% | 3.33% |

The rule as written is harsher on good queries than the results report. The challenger would be re-tuned to the new catch rate, so how the head-to-head moves is not measured. The fix is to rescale c so its spend-weighted harmonic mean is 1, or set the R1 threshold to 2 x the realized account CPA from training seeds, and define good relative to that same average.

### F3. R2: "blocked vs no guard" uses the wrong baseline for two rows (severity 5)

`pausebench/r2_cpa_guard.py:172-180` divides every policy's false pauses by `daily_no_guard`, which has zero-conversion days firing.

- **daily_guard500_zero_never_fires:** the right baseline is the no-guard rule that also never fires on zero conversions. Probe, seeds 100 to 119, median 60/day: `daily_noguard_z` pauses 13.2% of good keywords and `daily500_z` pauses 7.5%. The guard therefore blocks **43%** of false pauses by count, not the reported 92%.
- **lookback30_guard500:** its 68% share mixes the change from daily to a 30-day lookback with the guard itself. Against a 30-day lookback with no guard (73.5% of good keywords paused), the guard blocks **57%**.

These rows are the direct test of the post's "blocks 70-80% of false-positive rule fires" claim, so the verdict on that claim depends on which baseline is used. The fix is to give each guarded policy its own unguarded twin.

### F4. R2: spend-weighted metrics ignore when the pause happens (severity 2)

`portfolio_scores` (lines 95-108) weights each paused keyword by its full 30-day mean cost, whether the pause came on day 0 or day 29. I checked a timing-aware version (seeds 100 to 119): the share of good-keyword spend lost after the pause is 25.2% for lookback30+500 and 14.6% for the posterior. For bad keywords, spend before the pause is 11.1% vs 11.7%, with median pause day 5 for both. The ordering holds, and the challenger's equal 30-day catch rate does not hide slower detection. Worth reporting instead of, or next to, the current spend share.

### F5. Minor, under 5, code can stay

- `r2_cpa_guard.py`: PREREG asks for a per-day false pause rate. Only within-7 and within-30 days are computed (severity 2).
- `r2_cpa_guard.py:152-159`: the frontier runs on test seeds 100 to 119. It is fine as descriptive output, but no q should ever be picked from it (severity 1).
- `r3_fatigue.py:113`: a crosser that got a false alarm earlier is counted as never flagged (unflagged runs to day 30). This hits the ungated rule hardest because it has the highest false-alarm rate (severity 2).
- `r3_fatigue.py:131`: od_hat absorbs the real CTR decay during baseline days 1 and 2 for small audiences (0.045 when the true od is 0). This widens the challenger's bound and so works against the challenger (severity 1).
- `r3_fatigue.py:97-99`: truth uses the true day-0 CTR, while both policies use the observed 72-hour average, which already includes some decay for audiences of 1 to 3 days. This applies equally to both policies (severity 2).
- `r3_fatigue.py:71`: "frequency rising above 3.0" is checked only on the fire day, not over the run. This is lenient to R3 (severity 1).
- `r1_negate.py`: R1's "bad caught" denominator includes bad queries that never reach spend 2 (about 82% of them under the spend distribution). That is why the headline is 7 to 9%. Among bad queries that reach 2x, R1 catches 47 to 54% (seeds 100 to 119). The repo computes `bad_eligible` but never prints it. Reporting both would stop readers misreading 8.5% (severity 3).

## Checked and correct

- **R1 rule:** negates at spend 2.0 if spend reached 2.0 and the first conversion came at or after 2.0. That is exactly "spent 2x with zero conversions".
- **R1 closed form:** checked with 200,000 queries per c at S = 10. c=0.5: 0.0187 vs exp(-4) = 0.0183. c=1: 0.1343 vs 0.1353. c=2: 0.3677 vs 0.3679. c=5: 0.6691 vs 0.6703. The repo's own curve (2,500 draws) agrees within sampling error.
- **R1 challenger:** posterior on the rate is Gamma(a0+k, b0+x), and P(c > 1.5) = gammainc(a0+k, (b0+x)/1.5). The fire spend `1.5*gammaincinv(a0+k, q) - b0` is right, as is the segment logic between conversions. The firing point can only move right after a conversion, so a missed boundary case cannot happen. The negative-binomial marginal likelihood is right.
- **R2:** CPC = 50 x 0.05 gives a true CPA of exactly 50r. `posterior_rule`'s gammainc(a0+n, b0+s) = P(rate < 1) = P(CPA > target) is right, with a rate of 1/r per target-CPA of spend. The 500-impression guard applies to the day's impressions for the daily rule and to the window's for the lookback. Pauses are absorbing via the first fire.
- **R3:** baseline = days 0 to 2. The consecutive-run counter is right. The earliest fire for both policies is day 7, using days 3 to 7, and the challenger's window never overlaps the baseline. The prior moment-matching removes binomial variance. `best_of_four` picks by observed 72-hour CTR.
- **Leakage:** priors and tuning use seeds 0 to 9 only. Scoring uses 100 to 199 (R1), 100 to 159 (R2) and 100 to 139 (R3). The curves use separate RNG streams.

## What was changed after this review

- F1 (R3 gate attribution): README now credits the ungated rule for the catch gain and the challenger for holding the
  false-alarm rate at the gated level. The generator's gate condition (audience under 3.5 days of impressions) is stated.
- F2 (R1 unit): `portfolio()` rescales true CPAs so the unit is the account's average CPA (total spend over expected
  conversions). All R1 numbers were rerun.
- F3 (R2 blocked column): each guarded variant is now compared with its own unguarded twin
  (`daily_no_guard_zero_never_fires` added). Under the zero-never-fires reading the guard blocks 44% by count.
- F4 (timing): kept as is, the reviewer's timing-aware number is quoted in the README.
- F5: R1 now prints the catch rate among bad queries that reach 2x spend; R3 stops the unflagged clock at any flag,
  false alarm included (a flag pulls the creative). Other F5 items left as is.
