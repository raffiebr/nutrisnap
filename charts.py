"""Generate progress charts as PNG bytes to send back into the Telegram chat."""
import io
import os

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pandas as pd

SODIUM_LIMIT_MG = float(os.environ.get("DAILY_SODIUM_LIMIT_MG", 2000))
SUGAR_LIMIT_G = float(os.environ.get("DAILY_SUGAR_LIMIT_G", 50))


def progress_chart(df: pd.DataFrame, calorie_goal: int, days: int) -> bytes:
    """2x2 grid: calories vs goal, protein, sugar, sodium — daily totals."""
    daily = (df.groupby(df["date"].dt.date)
               [["calories", "protein_g", "sugar_g", "sodium_mg"]]
               .sum())

    fig, axes = plt.subplots(2, 2, figsize=(11, 7))
    fig.suptitle(f"Nutrition — last {days} days", fontsize=14, weight="bold")

    panels = [
        (axes[0][0], "calories", "Calories (kcal)", "#e74c3c", calorie_goal),
        (axes[0][1], "protein_g", "Protein (g)", "#27ae60", None),
        (axes[1][0], "sugar_g", "Sugar (g)", "#e67e22", SUGAR_LIMIT_G),
        (axes[1][1], "sodium_mg", "Sodium (mg)", "#2980b9", SODIUM_LIMIT_MG),
    ]
    for ax, col, title, color, goal in panels:
        ax.plot(daily.index, daily[col], marker="o", color=color, lw=2)
        if goal:
            ax.axhline(goal, color="gray", ls="--", lw=1,
                       label=f"limit {goal:g}")
            ax.legend(fontsize=8)
        ax.set_title(title, fontsize=11)
        ax.grid(alpha=0.3)
        ax.tick_params(axis="x", rotation=45, labelsize=8)

    plt.tight_layout()
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=120)
    plt.close(fig)
    buf.seek(0)
    return buf.read()
