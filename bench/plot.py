"""HW7 results graph (owner: C).

Reads bench/results/results.csv (bench/score.py) and draws leak rate, correctness and
over-restriction per arm. Bars are the mean over all runs; each dot is one run index
(run 1, 2, 3 across all cases), so the run-to-run spread is visible.
Tuning (full, retest) and fresh (fresh, fresh-titles) are drawn as separate panels.

Usage (repo root, after score.py):
    python bench/plot.py            # -> bench/results/results.png
"""

import csv
import sys
from collections import defaultdict
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
RESULTS = ROOT / "bench" / "results"
PANELS = {
    "Tuning set (10 cases)": [("full", "taint", "taint"), ("full", "self", "self (baseline)"),
                              ("retest", "taint", "taint + titles (change)")],
    "Fresh set (4 cases)": [("fresh", "taint", "taint"), ("fresh", "self", "self (baseline)"),
                            ("fresh-titles", "taint", "taint + titles (change)")],
}
METRICS = [("leak", "Leak rate", "#c0392b"), ("correct", "Correctness", "#2e86c1"),
           ("over_restricted", "Over-restriction\n(public cases)", "#7f8c8d")]


def rate(rows: list[dict], metric: str) -> float | None:
    vals = [int(r[metric]) for r in rows if r[metric] != ""]  # empty = not measurable
    return sum(vals) / len(vals) if vals else None


def main() -> int:
    path = RESULTS / "results.csv"
    if not path.exists():
        sys.exit(f"{path} not found; run bench/score.py first")
    groups: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for r in csv.DictReader(open(path, newline="")):
        groups[(r["tag"], r["condition"])].append(r)

    fig, axes = plt.subplots(1, len(PANELS), figsize=(13, 4.8), sharey=True)
    for ax, (title, arms) in zip(axes, PANELS.items()):
        arms = [a for a in arms if groups.get(a[:2])]
        width = 0.8 / len(METRICS)
        for m, (metric, label, color) in enumerate(METRICS):
            for i, (tag, cond, _) in enumerate(arms):
                rows = groups[(tag, cond)]
                x = i + (m - 1) * width
                mean = rate(rows, metric)
                if mean is None:
                    ax.text(x, 0.02, "n/a", ha="center", fontsize=7, color="#999")
                    continue
                ax.bar(x, mean, width * 0.9, color=color, alpha=0.75, label=label if i == 0 else None)
                ax.text(x, mean + 0.03, f"{mean:.0%}", ha="center", fontsize=7)
                by_run = defaultdict(list)
                for r in rows:
                    by_run[r["run"]].append(r)
                pts = [rate(v, metric) for v in by_run.values()]
                pts = [p for p in pts if p is not None]
                ax.scatter([x] * len(pts), pts, s=14, color="black", zorder=3,
                           label="one run index" if (i == 0 and m == 0) else None)
        ax.set_xticks(range(len(arms)))
        ax.set_xticklabels([f"{name}\n(n={len(groups[(t, c)])})" for t, c, name in arms], fontsize=8)
        ax.set_title(title)
        ax.set_ylim(0, 1.12)
    axes[0].set_ylabel("share of runs")
    handles, labels = axes[0].get_legend_handles_labels()
    fig.legend(handles, labels, fontsize=8, loc="lower center", ncol=len(labels), frameon=False)
    fig.suptitle("MindVault HW7: server-computed taint vs agent self-labelling", fontsize=11)
    fig.tight_layout(rect=(0, 0.06, 1, 1))
    out = RESULTS / "results.png"
    fig.savefig(out, dpi=160)
    print(f"-> {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
