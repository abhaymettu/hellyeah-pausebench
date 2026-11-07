"""R3: "CTR has fallen 20% or more from its launch baseline for five consecutive days while frequency is rising above
3.0" (baseline = first 72 hours), as the Oct 19 post describes AIMA's fatigue flag.

Per creative: launch CTR, daily impressions ~ Poisson(N), an audience of A people, true CTR decaying with average
frequency at a creative-specific rate, and day-level multiplicative noise on CTR (overdispersion).
"""

import json
import sys

import numpy as np

TRAIN_SEEDS = range(0, 10)
TEST_SEEDS = range(100, 140)
DAYS = 30
BASE_DAYS = 3
WINDOW = 5
LEVELS = [1_000, 3_000, 10_000, 30_000, 100_000]
CTR_MEDIAN, CTR_SIGMA = 0.01, 0.2
FLOOR = 0.3


K = 1.0


def nbd_reach(impressions, audience):
    return audience * (1 - (1 + impressions / (audience * K)) ** -K)


def creatives(rng, m, n_impr, od_sigma):
    ctr0 = CTR_MEDIAN * rng.lognormal(0.0, CTR_SIGMA, m)
    audience = n_impr * np.exp(rng.uniform(np.log(1), np.log(60), m))
    evergreen = rng.random(m) < 0.3
    decay = np.where(evergreen, rng.uniform(0.0, 0.02, m), rng.uniform(0.05, 0.4, m))
    impr = rng.poisson(n_impr, (m, DAYS)).astype(float)
    cum_before = np.cumsum(impr, axis=1) - impr
    # NBD exposure (shape K): platforms concentrate delivery, so reach grows slower than random exposure
    reach = nbd_reach(cum_before, audience[:, None])
    freq = np.where(reach > 0, cum_before / np.maximum(reach, 1e-9), 0.0)
    g = FLOOR + (1 - FLOOR) * np.exp(-decay[:, None] * np.maximum(freq - 1.0, 0.0))
    true_ctr = ctr0[:, None] * g
    noise = rng.normal(-od_sigma**2 / 2, od_sigma, (m, DAYS)) if od_sigma > 0 else 0.0
    clicks = rng.binomial(impr.astype(int), np.clip(true_ctr * np.exp(noise), 0, 1)).astype(float)
    # 7-day frequency on each day: impressions in the trailing 7 days over people reached in them
    c = np.cumsum(impr, axis=1)
    lag = np.zeros_like(c)
    lag[:, 7:] = c[:, :-7]
    i7 = c - lag
    f7 = i7 / nbd_reach(i7, audience[:, None])
    return {"ctr0": ctr0, "true_ctr": true_ctr, "impr": impr, "clicks": clicks, "f7": f7}


def best_of_four(rng, m, n_impr, od_sigma):
    """Launch four candidates per slot, keep the one with the highest observed 72-hour CTR."""
    pool = creatives(rng, 4 * m, n_impr, od_sigma)
    base = pool["clicks"][:, :BASE_DAYS].sum(1) / pool["impr"][:, :BASE_DAYS].sum(1)
    pick = base.reshape(m, 4).argmax(1) + 4 * np.arange(m)
    return {k: v[pick] for k, v in pool.items()}


def rule_r3(c, freq_gate=True):
    base = c["clicks"][:, :BASE_DAYS].sum(1) / c["impr"][:, :BASE_DAYS].sum(1)
    daily = c["clicks"] / np.maximum(c["impr"], 1)
    low = daily <= 0.8 * base[:, None]
    low[:, :BASE_DAYS] = False
    run = np.zeros_like(low, dtype=int)
    for t in range(DAYS):
        run[:, t] = np.where(low[:, t], (run[:, t - 1] if t else 0) + 1, 0)
    fire = run >= WINDOW
    if freq_gate:
        fire &= c["f7"] > 3.0
    return first(fire)


def challenger(c, prior, od_sigma_hat, z):
    """Shrunk baseline vs a pooled 5-day window; flag when the drop is >= 20% (point) and > 10% (one-sided bound)."""
    a, b = prior  # Gamma prior on launch CTR (shape a, rate b in impressions)
    bc = c["clicks"][:, :BASE_DAYS].sum(1)
    bi = c["impr"][:, :BASE_DAYS].sum(1)
    base = (a + bc) / (b + bi)
    var_base = 1.0 / (a + bc) + od_sigma_hat**2 / BASE_DAYS
    cc, ci = np.cumsum(c["clicks"], 1), np.cumsum(c["impr"], 1)
    lag_c, lag_i = np.zeros_like(cc), np.zeros_like(ci)
    lag_c[:, WINDOW:], lag_i[:, WINDOW:] = cc[:, :-WINDOW], ci[:, :-WINDOW]
    wc, wi = cc - lag_c, ci - lag_i
    log_r = np.log((wc + 0.5) / wi) - np.log(base)[:, None]
    se = np.sqrt(1.0 / (wc + 0.5) + var_base[:, None] + od_sigma_hat**2 / WINDOW)
    fire = (log_r < np.log(0.8)) & (log_r + z * se < np.log(0.9))
    fire[:, : BASE_DAYS + WINDOW - 1] = False  # first full window after the baseline
    return first(fire)


def first(fire):
    return np.where(fire.any(1), fire.argmax(1), DAYS)


def truth(c):
    ratio = c["true_ctr"] / c["ctr0"][:, None]
    cross = first(ratio <= 0.8)
    return ratio, cross


def score(c, flag_day):
    ratio, cross = truth(c)
    m = len(flag_day)
    flagged = flag_day < DAYS
    at_flag = np.where(flagged, ratio[np.arange(m), np.minimum(flag_day, DAYS - 1)], 1.0)
    false_alarm = flagged & (at_flag > 0.9)
    crossers = cross <= DAYS - 1 - WINDOW
    caught = crossers & flagged & ~false_alarm
    delay = (flag_day - cross)[caught]
    # days a truly fatigued creative keeps running unflagged, censored at the horizon (no selection on being caught)
    unflagged = np.clip(np.where(false_alarm, DAYS, flag_day) - cross, 0, None)[crossers]
    return {"n": m, "false_alarms": int(false_alarm.sum()), "crossers": int(crossers.sum()),
            "fatigued_days_unflagged": float(unflagged.sum()),
            "caught": int(caught.sum()), "missed": int((crossers & ~flagged).sum()),
            "delay_sum": float(delay.sum()), "delays": delay.tolist()}


def fit(seeds, od_sigma):
    """Cohort prior on launch CTR and a pooled overdispersion estimate, from launch days only."""
    ctrs, binvar, excess = [], [], []
    for seed in seeds:
        rng = np.random.default_rng(seed)
        for n in LEVELS:
            c = creatives(rng, 400, n, od_sigma)
            k, i = c["clicks"][:, :BASE_DAYS], c["impr"][:, :BASE_DAYS]
            ctrs.append(k.sum(1) / i.sum(1))
            binvar.append(ctrs[-1] / i.sum(1))
            lc = np.log((k + 0.5) / i)
            excess.append(lc.var(1, ddof=1) - (1.0 / (k + 0.5)).mean(1))
    ctrs = np.concatenate(ctrs)
    mean, var = ctrs.mean(), ctrs.var()
    od_hat = float(np.sqrt(max(np.concatenate(excess).mean(), 0.0)))
    var_true = max(var - np.concatenate(binvar).mean(), 1e-12)  # remove binomial noise from the spread
    a = mean**2 / var_true
    return (float(a), float(a / mean)), od_hat


def run_arm(arm, seeds, od_sigma, policies):
    gen = creatives if arm == "single" else best_of_four
    out = {n: {p: None for p in policies} for n in LEVELS}
    for seed in seeds:
        rng = np.random.default_rng(seed)
        for n in LEVELS:
            c = gen(rng, 400, n, od_sigma)
            for name, fn in policies.items():
                s = score(c, fn(c))
                prev = out[n][name]
                out[n][name] = s if prev is None else {k: prev[k] + s[k] for k in s}
    return out


def run(od_sigma=0.10):
    prior, od_hat = fit(TRAIN_SEEDS, od_sigma)
    rule = {"r3": lambda c: rule_r3(c, True), "r3_no_freq_gate": lambda c: rule_r3(c, False)}

    # tune z on training seeds, pooled over both arms and all levels: smallest z whose false-alarm rate <= R3's
    def fa(policy):
        tot_fa = tot_n = 0
        for arm in ("single", "best_of_4"):
            res = run_arm(arm, TRAIN_SEEDS, od_sigma, {"p": policy})
            tot_fa += sum(res[n]["p"]["false_alarms"] for n in LEVELS)
            tot_n += sum(res[n]["p"]["n"] for n in LEVELS)
        return tot_fa / tot_n

    target = fa(rule["r3"])
    z_star = None
    for z in np.round(np.arange(0.0, 4.01, 0.25), 2):
        if fa(lambda c: challenger(c, prior, od_hat, z)) <= target:
            z_star = float(z)
            break

    policies = {**rule, "challenger": lambda c: challenger(c, prior, od_hat, z_star)}
    arms = {arm: run_arm(arm, TEST_SEEDS, od_sigma, policies) for arm in ("single", "best_of_4")}
    return {"od_sigma": od_sigma, "prior": prior, "od_hat": od_hat, "z_star": z_star,
            "train_r3_false_alarm": target, "arms": arms}


def summarize(res):
    out = [f"od_sigma={res['od_sigma']} od_hat={res['od_hat']:.3f} z*={res['z_star']} train R3 FA={res['train_r3_false_alarm']:.2%}"]
    for arm, by_level in res["arms"].items():
        out.append(f"  arm={arm}")
        for n, pols in by_level.items():
            cells = []
            for name, s in pols.items():
                d = np.array(s["delays"])
                med = float(np.median(d)) if len(d) else float("nan")
                cells.append(f"{name}: FA={s['false_alarms'] / s['n']:.1%} caught={s['caught'] / max(s['crossers'], 1):.0%}"
                             f" delay={med:.0f}d unflagged={s['fatigued_days_unflagged'] / max(s['crossers'], 1):.1f}d")
            out.append(f"    {n:>6}/day  crossers={pols['r3']['crossers'] / pols['r3']['n']:.0%}  " + " | ".join(cells))
    return "\n".join(out)


if __name__ == "__main__":
    sigmas = [float(x) for x in sys.argv[1:]] or [0.0, 0.10, 0.20]
    res = [run(s) for s in sigmas]
    for r in res:
        print(summarize(r))
    for r in res:
        for arm in r["arms"].values():
            for pols in arm.values():
                for s in pols.values():
                    s.pop("delays")
    with open("results/r3.json", "w") as f:
        json.dump(res, f, indent=1)
