"""Generate progress charts as PNG bytes to send back into the Telegram chat.

Phone-first design: 4 stacked panels (one per nutrient), daily-total bars in a
validated categorical palette, hairline limit lines, no chart junk. Days over
a limit are re-inked in the reserved status tone — the bar visibly crossing
the drawn limit line is the non-color backup for that state.
"""
import io
import os
from datetime import timedelta

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pandas as pd

SODIUM_LIMIT_MG = float(os.environ.get("DAILY_SODIUM_LIMIT_MG", 2000))
SUGAR_LIMIT_G = float(os.environ.get("DAILY_SUGAR_LIMIT_G", 50))

# dataviz reference palette (light mode) — swap here if rebranding
SURFACE = "#fcfcfb"
INK = "#0b0b0b"
INK_2 = "#52514e"
MUTED = "#898781"
BASELINE = "#c3c2b7"
GRID = "#e1e0d9"
SERIOUS = "#ec835a"          # status: day over the limit — never a series color
SLOT = {"calories": "#2a78d6", "protein_g": "#1baf7a",
        "sugar_g": "#eda100", "sodium_mg": "#008300"}

plt.rcParams.update({
    "font.family": "sans-serif",
    "font.sans-serif": ["Helvetica Neue", "Arial", "DejaVu Sans"],
    "axes.edgecolor": BASELINE,
    "text.color": INK,
})


def _fmt(v: float) -> str:
    return f"{v:,.1f}" if 0 < v < 10 else f"{v:,.0f}"


def progress_chart(df: pd.DataFrame, calorie_goal: int, days: int) -> bytes:
    """Stacked daily-total bar panels: calories, protein, sugar, sodium."""
    daily = (df.groupby(df["date"].dt.date)
               [["calories", "protein_g", "sugar_g", "sodium_mg"]]
               .sum())
    # even time axis: every day from first logged day to the last, gaps stay empty
    full = pd.date_range(min(daily.index), max(daily.index), freq="D").date
    daily = daily.reindex(full)

    panels = [
        # (column, title, unit, limit line, limit word, y-scale floor)
        ("calories", "Calories", "kcal", calorie_goal, "goal", calorie_goal),
        ("protein_g", "Protein", "g", None, "", 60),
        ("sugar_g", "Sugar", "g", SUGAR_LIMIT_G, "limit", SUGAR_LIMIT_G),
        ("sodium_mg", "Sodium", "mg", SODIUM_LIMIT_MG, "limit",
         SODIUM_LIMIT_MG),
    ]

    fig, axes = plt.subplots(4, 1, figsize=(7, 9.8), dpi=150,
                             facecolor=SURFACE)
    fig.subplots_adjust(top=0.90, bottom=0.05, left=0.10, right=0.97,
                        hspace=0.75)
    n = len(daily)
    title_days = f"{n} day" + ("s" if n > 1 else "")
    fig.text(0.10, 0.965, "Your nutrition", fontsize=15, weight="bold",
             color=INK)
    fig.text(0.10, 0.945,
             f"daily totals · {title_days} · "
             f"{full[0].strftime('%-d %b')} – {full[-1].strftime('%-d %b')}",
             fontsize=9.5, color=MUTED)

    x = range(n)
    step = max(1, round(n / 6))
    ticks = list(range(0, n, step)) or [0]
    if (n - 1) - ticks[-1] <= step / 2:  # avoid crowding the final tick
        ticks[-1] = n - 1
    elif ticks[-1] != n - 1:
        ticks.append(n - 1)

    pad = max(0.6, (8 - n) / 2)   # few-day charts: keep bars slim
    for ax, (col, title, unit, limit, limit_word, floor) in zip(axes, panels):
        vals = daily[col].fillna(0)
        avg = vals[vals > 0].mean() if (vals > 0).any() else 0
        colors = [SERIOUS if (limit and v > limit) else SLOT[col]
                  for v in vals]

        ax.set_facecolor(SURFACE)
        ax.grid(axis="y", color=GRID, lw=0.75, zorder=0)
        ax.bar(x, vals, width=0.72, color=colors, zorder=3)

        top = max(vals.max(), floor) * 1.18 or 1
        ax.set_ylim(0, top)
        ax.set_xlim(-pad, n - 1 + pad)
        if limit:
            ax.axhline(limit, color=MUTED, lw=1.1, zorder=2)

        # direct label on the most recent day only
        last = vals.iloc[-1]
        if last > 0:
            ax.text(n - 1, last + top * 0.03, _fmt(last), ha="center",
                    va="bottom", fontsize=9, weight="bold", color=INK_2)

        ax.set_title(title, loc="left", fontsize=12, weight="bold",
                     color=INK, pad=10)
        header = f"avg {_fmt(avg)} {unit}/day"
        if limit:
            header += f" · {limit_word} {_fmt(limit)}"
        ax.text(1.0, 1.06, header, transform=ax.transAxes, ha="right",
                fontsize=9, color=MUTED)

        for side in ("top", "right", "left"):
            ax.spines[side].set_visible(False)
        ax.spines["bottom"].set_color(BASELINE)
        ax.tick_params(axis="y", length=0, labelsize=8.5, labelcolor=MUTED)
        ax.tick_params(axis="x", length=0, labelsize=8.5, labelcolor=MUTED)
        ax.set_xticks(ticks)
        ax.set_xticklabels([full[i].strftime("%-d %b") for i in ticks])
        ax.margins(y=0)

    buf = io.BytesIO()
    fig.savefig(buf, format="png", facecolor=SURFACE)
    plt.close(fig)
    buf.seek(0)
    return buf.read()
