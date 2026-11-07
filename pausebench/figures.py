"""Draws docs/r2-frontier.png and docs/r1-curve.png from results/*.json."""

import json

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt

BLUE, VERMILLION, INK, MUTED, GRID = "#0072B2", "#D55E00", "#1F1F1F", "#6B6B6B", "#E6E6E3"
plt.rcParams.update({"font.family": "Helvetica Neue", "font.size": 11, "axes.edgecolor": MUTED,
                     "axes.labelcolor": INK, "xtick.color": MUTED, "ytick.color": MUTED,
                     "axes.spines.top": False, "axes.spines.right": False, "figure.facecolor": "#FCFCFB",
                     "axes.facecolor": "#FCFCFB"})


def r2():
    res = json.load(open("results/r2.json"))[0]
    fr = res["frontier"]
    x = [100 * f["false_pause_spend"] / f["ok_spend"] for f in fr]
    y = [100 * f["bad_spend_caught"] / f["bad_spend"] for f in fr]
    fig, ax = plt.subplots(figsize=(7.2, 4.6), dpi=200)
    ax.grid(color=GRID, lw=0.8)
    ax.plot(x, y, color=BLUE, lw=2, marker="o", ms=6, mec="#FCFCFB", mew=1.5, zorder=3)
    for f, xi, yi in zip(fr, x, y):
        if f["q"] in (0.6, 0.8, 0.95):
            off, ha = ((4, -16), "left") if f["q"] == 0.6 else ((8, -14), "left")
            ax.annotate(f"posterior, q={f['q']:.2f}", (xi, yi), xytext=off, textcoords="offset points",
                        ha=ha, color=INK, fontsize=9)
    pts = {"daily rule + 500-impression guard": "daily_guard500", "30-day lookback + guard": "lookback30_guard500"}
    for label, key in pts.items():
        t = res["portfolio"][key]
        xi, yi = 100 * t["false_pause_spend"] / t["ok_spend"], 100 * t["bad_spend_caught"] / t["bad_spend"]
        ax.plot([xi], [yi], marker="s", ms=9, color=VERMILLION, mec="#FCFCFB", mew=1.5, zorder=4, ls="none")
        dy = -4 if key.startswith("lookback") else 10
        ax.annotate(label, (xi, yi), xytext=(10, dy), textcoords="offset points", ha="left", color=INK, fontsize=9)
    ax.set_xlabel("Spend on good keywords that got paused (%)")
    ax.set_ylabel("Spend on bad keywords that got paused (%)")
    ax.set_xlim(-1, 60)
    ax.set_ylim(50, 101)
    ax.set_title("Pausing keywords: the blog's rules vs a posterior rule", loc="left", color=INK, fontsize=12)
    ax.text(0, -0.2, "Squares: published rules. Circles: one posterior rule at different confidence levels. "
            "Up and left is better.", transform=ax.transAxes, color=MUTED, fontsize=8.5)
    fig.tight_layout()
    fig.savefig("docs/r2-frontier.png")


def r1():
    res = json.load(open("results/r1.json"))[1]
    c = [p[0] for p in res["curve_closed_form_r1"]]
    fig, ax = plt.subplots(figsize=(7.2, 4.2), dpi=200)
    ax.grid(color=GRID, lw=0.8)
    ax.plot(c, [100 * p[1] for p in res["curve_closed_form_r1"]], color=VERMILLION, lw=2, marker="s", ms=7,
            mec="#FCFCFB", mew=1.5, label="2x CPA with zero conversions (blog rule)")
    ax.plot(c, [100 * p[1] for p in res["curve_challenger"]], color=BLUE, lw=2, marker="o", ms=7,
            mec="#FCFCFB", mew=1.5, label="posterior rule, tuned to the same catch rate")
    ax.axvline(1.0, color=MUTED, lw=1, ls=(0, (3, 3)))
    ax.annotate("13.5% of queries at exactly\naverage CPA get negated", (1.0, 13.5), xytext=(1.25, 4),
                color=INK, fontsize=9, arrowprops={"arrowstyle": "-", "color": MUTED})
    ax.annotate("63% of queries at 2x average\nCPA are never negated", (2.0, 36.8), xytext=(2.3, 22),
                color=INK, fontsize=9, arrowprops={"arrowstyle": "-", "color": MUTED})
    ax.set_xlabel("True CPA of the query, in multiples of the account's average CPA")
    ax.set_ylabel("Chance the query is negated (%)")
    ax.set_title("Negative keywords: who gets cut", loc="left", color=INK, fontsize=12)
    ax.legend(frameon=False, fontsize=9, loc="upper left")
    fig.tight_layout()
    fig.savefig("docs/r1-curve.png")


if __name__ == "__main__":
    r1()
    r2()
