"""Google Sheets storage. One row per logged meal.

Setup (one-time):
1. Google Cloud Console -> create project -> enable "Google Sheets API" and
   "Google Drive API".
2. Create a Service Account, download its JSON key as service_account.json.
3. Create a Google Sheet named per GSHEET_NAME, share it with the service
   account's email (found inside the JSON) as Editor.
"""
import os
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import gspread
import pandas as pd

TZ = ZoneInfo("Asia/Singapore")
HEADERS = ["timestamp", "date", "meal_name", "calories", "protein_g",
           "carbs_g", "fat_g", "sodium_mg", "sugar_g", "confidence",
           "source", "items_detail", "user_id"]

_ws = None  # cached worksheet


def _worksheet():
    global _ws
    if _ws is None:
        gc = gspread.service_account(
            filename=os.environ.get("GOOGLE_SERVICE_ACCOUNT_FILE",
                                    "service_account.json"))
        sh = gc.open(os.environ.get("GSHEET_NAME", "Nutrition Log"))
        _ws = sh.sheet1
        if _ws.row_values(1) != HEADERS:      # init header row once
            _ws.update("A1", [HEADERS])
    return _ws


def log_meal(analysis: dict, user_id: int, when: datetime | None = None):
    when = when or datetime.now(TZ)
    t = analysis["total"]
    items = "; ".join(
        f"{i['name']} ({i['portion']}, {i['calories']} kcal)"
        for i in analysis.get("items", []))
    _worksheet().append_row([
        when.isoformat(), when.strftime("%Y-%m-%d"),
        analysis.get("meal_name", "Unknown"),
        t.get("calories", 0), t.get("protein_g", 0), t.get("carbs_g", 0),
        t.get("fat_g", 0), t.get("sodium_mg", 0), t.get("sugar_g", 0),
        analysis.get("confidence", ""), analysis.get("source", ""),
        items, user_id,
    ], value_input_option="USER_ENTERED")


def fetch_history(days: int = 30) -> pd.DataFrame:
    """All rows from the last `days` days as a DataFrame."""
    rows = _worksheet().get_all_records()
    df = pd.DataFrame(rows)
    if df.empty:
        return df
    df["date"] = pd.to_datetime(df["date"])
    cutoff = datetime.now(TZ).date() - timedelta(days=days)
    df = df[df["date"].dt.date >= cutoff]
    for col in ["calories", "protein_g", "carbs_g", "fat_g",
                "sodium_mg", "sugar_g"]:
        df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0)
    return df


def today_summary() -> dict:
    df = fetch_history(days=1)
    today = datetime.now(TZ).strftime("%Y-%m-%d")
    if df.empty:
        return {"meals": 0}
    d = df[df["date"].dt.strftime("%Y-%m-%d") == today]
    if d.empty:
        return {"meals": 0}
    return {
        "meals": len(d),
        "calories": int(d["calories"].sum()),
        "protein_g": round(d["protein_g"].sum(), 1),
        "carbs_g": round(d["carbs_g"].sum(), 1),
        "fat_g": round(d["fat_g"].sum(), 1),
        "sodium_mg": int(d["sodium_mg"].sum()),
        "sugar_g": round(d["sugar_g"].sum(), 1),
        "meal_names": d["meal_name"].tolist(),
    }
