"""R1: "Any query that has spent 2x your average CPA with zero conversions gets added to the negative keyword list."

Spend is measured in units of the account's average CPA. A query with true CPA c converts as a Poisson process with
rate 1/c per unit of spend, until its potential spend S runs out.
"""

import json
import sys

import numpy as np
from scipy import optimize, special

TRAIN_SEEDS = range(0, 10)
TEST_SEEDS = range(100, 200)
N_QUERIES = 2000
GOOD, BAD = 1.0, 2.0  # c <= GOOD is good, c >= BAD is bad
CHALLENGER_LINE = 1.5  # challenger negates when P(c > 1.5 | data) > q


def portfolio(seed, c_sigma=0.6):
    rng = np.random.default_rng(seed)
    c = rng.lognormal(0.0, c_sigma, N_QUERIES)
    spend = rng.lognormal(np.log(0.5), 1.5, N_QUERIES)
    n_conv = rng.poisson(spend / c)
    times = [np.sort(rng.uniform(0, s, k)) for s, k in zip(spend, n_conv)]
    return c, spend, times


def rule_r1(spend, times):
    """Negation spend per query (np.inf if never negated)."""
    out = np.full(len(spend), np.inf)
    for i, (s, t) in enumerate(zip(spend, times)):
        if s >= 2.0 and (len(t) == 0 or t[0] >= 2.0):
            out[i] = 2.0
    return out


def fit_prior(spend_list, times_list):
    """Empirical Bayes: Gamma(a, b) prior on the rate, fit by negative-binomial marginal likelihood."""
    s = np.concatenate(spend_list)
    n = np.concatenate([[len(t) for t in ts] for ts in times_list])

    def nll(theta):
        a, b = np.exp(theta)
        ll = (special.gammaln(a + n) - special.gammaln(a) - special.gammaln(n + 1)
              + a * np.log(b / (b + s)) + n * np.log(s / (b + s)))
        return -ll.sum()

    res = optimize.minimize(nll, x0=[0.0, 0.0], method="Nelder-Mead")
    a, b = np.exp(res.x)
    return float(a), float(b)


def challenger(spend, times, a0, b0, q):
    """Negate at the first spend where P(rate < 1/LINE | data) > q, checked continuously."""
    out = np.full(len(spend), np.inf)
    for i, (s, t) in enumerate(zip(spend, times)):
        edges = np.concatenate([[0.0], t, [s]])
        for k in range(len(t) + 1):
            # P(rate < 1/LINE) = gammainc(a0 + k, (b0 + x) / LINE) rises with spend x inside segment k
            x_star = CHALLENGER_LINE * special.gammaincinv(a0 + k, q) - b0
            x_fire = max(x_star, edges[k])
            if x_fire < edges[k + 1]:
                out[i] = x_fire
                break
    return out


def score(c, spend, neg):
    negated = np.isfinite(neg)
    good, bad = c <= GOOD, c >= BAD
    stop = np.minimum(neg, spend)
    return {
        "negations": int(negated.sum()),
        "negations_good": int((negated & good).sum()),
        "bad": int(bad.sum()),
        "bad_caught": int((negated & bad).sum()),
        "bad_eligible": int((bad & (spend >= 2.0)).sum()),
        "bad_caught_eligible": int((negated & bad & (spend >= 2.0)).sum()),
        "wasted_on_bad": float(stop[bad].sum()),
        "lost_good_conversions": float(((spend - stop) / c)[negated & good].sum()),
        "good_conversions_total": float((spend / c)[good].sum()),
    }


def add(a, b):
    return {k: a.get(k, 0) + v for k, v in b.items()}


def curve(neg_fn, seeds, c_grid):
    """P(negated | c) for queries with enough spend to reach the 2x check (S = 10)."""
    rows = []
    for c0 in c_grid:
        hits = tot = 0
        for seed in seeds:
            rng = np.random.default_rng(10_000 + seed)
            m = 500
            spend = np.full(m, 10.0)
            n = rng.poisson(spend / c0)
            times = [np.sort(rng.uniform(0, 10.0, k)) for k in n]
            hits += np.isfinite(neg_fn(spend, times)).sum()
            tot += m
        rows.append((c0, hits / tot))
    return rows


def run(c_sigma=0.6):
    train = [portfolio(s, c_sigma) for s in TRAIN_SEEDS]
    a0, b0 = fit_prior([p[1] for p in train], [p[2] for p in train])

    r1_train = {}
    for c, spend, times in train:
        r1_train = add(r1_train, score(c, spend, rule_r1(spend, times)))
    target = r1_train["bad_caught"] / r1_train["bad"]

    q_star = None
    for q in np.round(np.arange(0.99, 0.0, -0.01), 2):
        acc = {}
        for c, spend, times in train:
            acc = add(acc, score(c, spend, challenger(spend, times, a0, b0, q)))
        if acc["bad_caught"] / acc["bad"] >= target:
            q_star = float(q)
            break

    r1, ch = {}, {}
    for seed in TEST_SEEDS:
        c, spend, times = portfolio(seed, c_sigma)
        r1 = add(r1, score(c, spend, rule_r1(spend, times)))
        ch = add(ch, score(c, spend, challenger(spend, times, a0, b0, q_star)))

    c_grid = [0.5, 0.75, 1.0, 1.5, 2.0, 3.0, 5.0]
    return {
        "c_sigma": c_sigma,
        "prior": {"a0": a0, "b0": b0},
        "q_star": q_star,
        "train_r1_bad_catch": target,
        "test": {"r1": r1, "challenger": ch},
        "curve_r1": curve(rule_r1, range(5), c_grid),
        "curve_challenger": curve(lambda s, t: challenger(s, t, a0, b0, q_star), range(5), c_grid),
        "curve_closed_form_r1": [(c0, float(np.exp(-2.0 / c0))) for c0 in c_grid],
    }


def summarize(res):
    lines = [f"c_sigma={res['c_sigma']}  prior a0={res['prior']['a0']:.2f} b0={res['prior']['b0']:.2f}  q*={res['q_star']}"]
    for name in ("r1", "challenger"):
        t = res["test"][name]
        lines.append(
            f"  {name:10s} negations={t['negations']:6d}  good share={t['negations_good'] / max(t['negations'], 1):.1%}"
            f"  bad caught={t['bad_caught'] / t['bad']:.1%}  wasted on bad={t['wasted_on_bad']:.0f}"
            f"  good conv lost={t['lost_good_conversions']:.0f} ({t['lost_good_conversions'] / t['good_conversions_total']:.2%})"
        )
    lines.append("  P(negated | true CPA c), S=10:")
    for (c0, p1), (_, p2), (_, pc) in zip(res["curve_r1"], res["curve_challenger"], res["curve_closed_form_r1"]):
        lines.append(f"    c={c0:4.2f}  R1 sim={p1:.3f} closed form={pc:.3f}  challenger={p2:.3f}")
    return "\n".join(lines)


if __name__ == "__main__":
    sigmas = [float(x) for x in sys.argv[1:]] or [0.3, 0.6, 0.9]
    out = [run(s) for s in sigmas]
    for r in out:
        print(summarize(r))
    with open("results/r1.json", "w") as f:
        json.dump(out, f, indent=1)
