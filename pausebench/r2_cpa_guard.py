"""R2: a daily "pause if cost per conversion > target" rule, with the post's 500-impression guard.

Per keyword per day: impressions ~ Poisson(mean), clicks ~ Binomial(impr, CTR), conversions ~ Binomial(clicks, CVR),
cost = clicks x CPC. CPC is fixed so a keyword with the base CVR has CPA exactly at target; a keyword's true CPA ratio
r = CPA / target comes from its own CVR = base CVR / r. Pausing is absorbing within the 30-day horizon.
"""

import json

import numpy as np
from scipy import optimize, special

TRAIN_SEEDS = range(0, 10)
TEST_SEEDS = range(100, 160)
DAYS = 30
TARGET = 50.0
GUARD = 500


def simulate(rng, mean_impr, r, ctr=0.05, cvr=0.05):
    k = len(mean_impr)
    impr = rng.poisson(mean_impr[:, None], (k, DAYS))
    clicks = rng.binomial(impr, ctr)
    conv = rng.binomial(clicks, np.minimum(cvr / r, 1.0)[:, None])
    cost = clicks * (TARGET * cvr)
    return impr, clicks, conv, cost


def first_fire(fire):
    """Day index of the first True per row, or DAYS if none."""
    any_ = fire.any(axis=1)
    return np.where(any_, fire.argmax(axis=1), DAYS)


def daily_rule(impr, conv, cost, guard, zero_fires=True):
    over = np.where(conv > 0, cost / np.maximum(conv, 1) > TARGET, zero_fires & (cost > 0))
    return first_fire(over & (impr >= guard))


def lookback_rule(impr, conv, cost, guard, window=30):
    ci, cc, cv = (np.cumsum(x, axis=1) for x in (impr, cost, conv))

    def win(c):
        lag = np.zeros_like(c)
        lag[:, window:] = c[:, :-window]
        return c - lag

    wi, wc, wv = win(ci), win(cc), win(cv)
    over = np.where(wv > 0, wc / np.maximum(wv, 1) > TARGET, wc > 0)
    return first_fire(over & (wi >= guard))


def fit_prior(conv_list, cost_list):
    n = np.concatenate([c.sum(axis=1) for c in conv_list]).astype(float)
    s = np.concatenate([c.sum(axis=1) for c in cost_list]) / TARGET
    keep = s > 0
    n, s = n[keep], s[keep]

    def nll(theta):
        a, b = np.exp(theta)
        ll = (special.gammaln(a + n) - special.gammaln(a) - special.gammaln(n + 1)
              + a * np.log(b / (b + s)) + n * np.log(s / (b + s)))
        return -ll.sum()

    a, b = np.exp(optimize.minimize(nll, [0.0, 0.0], method="Nelder-Mead").x)
    return float(a), float(b)


def posterior_rule(conv, cost, a0, b0, q):
    """Pause when P(CPA > target | all data so far) > q. Rate is conversions per target-CPA of spend."""
    n = np.cumsum(conv, axis=1)
    s = np.cumsum(cost, axis=1) / TARGET
    p_bad = special.gammainc(a0 + n, b0 + s)  # P(rate < 1) under Gamma(a0 + n, rate b0 + s)
    return first_fire(p_bad > q)


def portfolio(seed, median_impr=60.0, sigma=1.5, ctr=0.05):
    rng = np.random.default_rng(seed)
    k = 3000
    mean_impr = rng.lognormal(np.log(median_impr), sigma, k)
    r = rng.lognormal(0.0, 0.5, k)
    return mean_impr, r, simulate(rng, mean_impr, r, ctr=ctr)


def policies(a0, b0, q):
    return {
        "daily_no_guard": lambda i, v, c: daily_rule(i, v, c, 0),
        "daily_guard500": lambda i, v, c: daily_rule(i, v, c, GUARD),
        "daily_guard500_zero_never_fires": lambda i, v, c: daily_rule(i, v, c, GUARD, zero_fires=False),
        "lookback30_guard500": lambda i, v, c: lookback_rule(i, v, c, GUARD),
        "posterior": lambda i, v, c: posterior_rule(v, c, a0, b0, q),
    }


def portfolio_scores(fire_day, r, cost):
    paused = fire_day < DAYS
    ok, bad = r <= 0.8, r >= 2.0  # PREREG: good is CPA <= 0.8 x target, bad is >= 2x
    spend_rate = cost.mean(axis=1)
    return {
        "false_pauses": int((paused & ok).sum()),
        "false_pause_spend": float(spend_rate[paused & ok].sum()),
        "ok_spend": float(spend_rate[ok].sum()),
        "ok": int(ok.sum()),
        "bad_caught": int((paused & bad).sum()),
        "bad": int(bad.sum()),
        "bad_spend_caught": float(spend_rate[paused & bad].sum()),
        "bad_spend": float(spend_rate[bad].sum()),
    }


def run(median_impr=60.0, ctr=0.05):
    train = [portfolio(s, median_impr, ctr=ctr) for s in TRAIN_SEEDS]
    a0, b0 = fit_prior([t[2][2] for t in train], [t[2][3] for t in train])

    def bad_catch(fn):
        caught = total = 0
        for _, r, (i, _c, v, cost) in train:
            d = fn(i, v, cost)
            caught += ((d < DAYS) & (r >= 2.0)).sum()
            total += (r >= 2.0).sum()
        return caught / total

    target = bad_catch(policies(a0, b0, 0.5)["lookback30_guard500"])
    q_star = None
    for q in np.round(np.arange(0.995, 0.5, -0.005), 3):
        if bad_catch(lambda i, v, c: posterior_rule(v, c, a0, b0, q)) >= target:
            q_star = float(q)
            break

    pol = policies(a0, b0, q_star)
    totals = {name: {} for name in pol}
    for seed in TEST_SEEDS:
        _, r, (i, _c, v, cost) = portfolio(seed, median_impr, ctr=ctr)
        for name, fn in pol.items():
            sc = portfolio_scores(fn(i, v, cost), r, cost)
            totals[name] = {k: totals[name].get(k, 0) + x for k, x in sc.items()}

    # single keyword curves: P(paused within 7 and 30 days) by daily impressions and true CPA ratio
    curves = []
    rng = np.random.default_rng(999)
    for m in [50, 200, 500, 1000, 3000]:
        for ratio in [0.8, 1.0, 2.0]:
            k = 4000
            i, _c, v, cost = simulate(rng, np.full(k, float(m)), np.full(k, ratio), ctr=ctr)
            row = {"mean_impr": m, "r": ratio}
            for name, fn in pol.items():
                d = fn(i, v, cost)
                row[name] = {"p7": float((d < 7).mean()), "p30": float((d < DAYS).mean())}
            curves.append(row)

    # frontier (added after prereg, descriptive only): posterior rule over a range of q on the test seeds
    frontier = []
    test = [portfolio(seed, median_impr, ctr=ctr) for seed in TEST_SEEDS[:20]]
    for q in [0.6, 0.7, 0.8, 0.9, 0.95, 0.98, 0.99]:
        acc = {}
        for _, r, (i, _c, v, cost) in test:
            sc = portfolio_scores(posterior_rule(v, cost, a0, b0, q), r, cost)
            acc = {k: acc.get(k, 0) + x for k, x in sc.items()}
        frontier.append({"q": q, **acc})

    return {"median_impr": median_impr, "ctr": ctr, "prior": [a0, b0], "q_star": q_star,
            "matched_bad_catch": float(target), "portfolio": totals, "curves": curves, "frontier": frontier}


SHORT = {"daily_no_guard": "daily", "daily_guard500": "daily+500", "daily_guard500_zero_never_fires": "daily+500(z)",
         "lookback30_guard500": "30d+500", "posterior": "posterior"}


def summarize(res):
    p = res["portfolio"]
    out = [f"median_impr={res['median_impr']} ctr={res['ctr']} q*={res['q_star']} matched bad catch={res['matched_bad_catch']:.1%}"]
    base = p["daily_no_guard"]
    for name, t in p.items():
        out.append(
            f"  {name:32s} false pauses={t['false_pauses'] / t['ok']:.1%} of ok keywords"
            f" ({t['false_pause_spend'] / t['ok_spend']:.1%} of ok spend)"
            f"  bad caught={t['bad_caught'] / t['bad']:.1%} ({t['bad_spend_caught'] / t['bad_spend']:.1%} of bad spend)"
            f"  blocked vs no guard: count {1 - t['false_pauses'] / base['false_pauses']:.0%},"
            f" spend {1 - t['false_pause_spend'] / base['false_pause_spend']:.0%}"
        )
    out.append("  posterior frontier (q: false pause share of good, bad caught):")
    for f in res["frontier"]:
        out.append(f"    q={f['q']:.2f}  good paused={f['false_pauses'] / f['ok']:.1%} (spend {f['false_pause_spend'] / f['ok_spend']:.1%})"
                   f"  bad caught={f['bad_caught'] / f['bad']:.1%} (spend {f['bad_spend_caught'] / f['bad_spend']:.1%})")
    out.append("  single keyword, P(paused within 7 days):")
    for row in res["curves"]:
        cells = "  ".join(f"{SHORT[n]}={row[n]['p7']:.2f}" for n in p)
        out.append(f"    impr/day={row['mean_impr']:5d} r={row['r']:.1f}  {cells}")
    return "\n".join(out)


if __name__ == "__main__":
    import sys

    grid = [(60.0, 0.05), (20.0, 0.05), (200.0, 0.05), (60.0, 0.02), (60.0, 0.08)] if "--all" in sys.argv else [(60.0, 0.05)]
    res = [run(m, c) for m, c in grid]
    for r in res:
        print(summarize(r))
    with open("results/r2.json", "w") as f:
        json.dump(res, f, indent=1)
