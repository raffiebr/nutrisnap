"""SQLite storage — source of truth. One row per logged meal.

Each successfully logged meal is also mirrored (best-effort) to Google Sheets
when configured; see sheets_mirror.py. The local DB never depends on it.
"""
import os
import sqlite3
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pandas as pd

import sheets_mirror

TZ = ZoneInfo("Asia/Singapore")

# Column order is a contract shared with the Google Sheets mirror — appending
# columns is OK, reordering breaks existing sheets.
HEADERS = ["timestamp", "date", "meal_name", "calories", "protein_g",
           "carbs_g", "fat_g", "sodium_mg", "sugar_g", "confidence",
           "source", "items_detail", "user_id"]

_SCHEMA = """
CREATE TABLE IF NOT EXISTS meals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL,
    date TEXT NOT NULL,
    meal_name TEXT,
    calories REAL,
    protein_g REAL,
    carbs_g REAL,
    fat_g REAL,
    sodium_mg REAL,
    sugar_g REAL,
    confidence TEXT,
    source TEXT,
    items_detail TEXT,
    user_id INTEGER
);
CREATE INDEX IF NOT EXISTS idx_meals_date ON meals(date);
"""

NUMERIC_COLS = ["calories", "protein_g", "carbs_g", "fat_g",
                "sodium_mg", "sugar_g"]


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(os.environ.get("NUTRISNAP_DB", "nutrisnap.db"))
    conn.executescript(_SCHEMA)
    return conn


def log_meal(analysis: dict, user_id: int, when: datetime | None = None):
    when = when or datetime.now(TZ)
    t = analysis["total"]
    items = "; ".join(
        f"{i['name']} ({i['portion']}, {i['calories']} kcal)"
        for i in analysis.get("items", []))
    row = [
        when.isoformat(), when.strftime("%Y-%m-%d"),
        analysis.get("meal_name", "Unknown"),
        t.get("calories", 0), t.get("protein_g", 0), t.get("carbs_g", 0),
        t.get("fat_g", 0), t.get("sodium_mg", 0), t.get("sugar_g", 0),
        analysis.get("confidence", ""), analysis.get("source", ""),
        items, user_id,
    ]
    conn = _connect()
    try:
        with conn:
            conn.execute(
                f"INSERT INTO meals ({', '.join(HEADERS)}) "
                f"VALUES ({', '.join('?' * len(HEADERS))})", row)
    finally:
        conn.close()
    sheets_mirror.mirror_row(row, HEADERS)


def fetch_history(days: int = 30, user_id: int | None = None) -> pd.DataFrame:
    """Rows from the last `days` days as a DataFrame, filtered to one user."""
    cutoff = (datetime.now(TZ).date() - timedelta(days=days)).strftime("%Y-%m-%d")
    query, params = "SELECT * FROM meals WHERE date >= ?", [cutoff]
    if user_id is not None:
        query += " AND user_id = ?"
        params.append(user_id)
    conn = _connect()
    try:
        df = pd.read_sql_query(query + " ORDER BY timestamp",
                               conn, params=params)
    finally:
        conn.close()
    if df.empty:
        return df
    df["date"] = pd.to_datetime(df["date"])
    for col in NUMERIC_COLS:
        df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0)
    return df


def today_summary(user_id: int | None = None) -> dict:
    today = datetime.now(TZ).strftime("%Y-%m-%d")
    query, params = "SELECT * FROM meals WHERE date = ?", [today]
    if user_id is not None:
        query += " AND user_id = ?"
        params.append(user_id)
    conn = _connect()
    try:
        df = pd.read_sql_query(query, conn, params=params)
    finally:
        conn.close()
    if df.empty:
        return {"meals": 0}
    for col in NUMERIC_COLS:
        df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0)
    return {
        "meals": len(df),
        "calories": int(df["calories"].sum()),
        "protein_g": round(df["protein_g"].sum(), 1),
        "carbs_g": round(df["carbs_g"].sum(), 1),
        "fat_g": round(df["fat_g"].sum(), 1),
        "sodium_mg": int(df["sodium_mg"].sum()),
        "sugar_g": round(df["sugar_g"].sum(), 1),
        "meal_names": df["meal_name"].tolist(),
    }


def delete_last(user_id: int) -> str | None:
    """Delete the user's most recently logged meal; return its name.

    Note: only removes the SQLite row — an already-mirrored Google Sheets row
    must be deleted by hand in the sheet.
    """
    conn = _connect()
    try:
        with conn:
            row = conn.execute(
                "SELECT id, meal_name FROM meals WHERE user_id = ? "
                "ORDER BY timestamp DESC LIMIT 1", (user_id,)).fetchone()
            if row is None:
                return None
            conn.execute("DELETE FROM meals WHERE id = ?", (row[0],))
            return row[1]
    finally:
        conn.close()
