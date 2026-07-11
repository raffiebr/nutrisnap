"""Best-effort Google Sheets mirror of logged meals. Entirely optional.

Activates only when the service-account JSON file exists (see
GOOGLE_SERVICE_ACCOUNT_FILE). Any failure logs a warning and is swallowed —
the SQLite source of truth (storage.py) never depends on this.
"""
import logging
import os

log = logging.getLogger("nutrisnap.sheets")

_ws = None  # cached worksheet handle; reset to None on any failure


def mirror_row(row: list, headers: list):
    global _ws
    keyfile = os.environ.get("GOOGLE_SERVICE_ACCOUNT_FILE",
                             "service_account.json")
    if not os.path.exists(keyfile):
        return
    try:
        if _ws is None:
            import gspread
            gc = gspread.service_account(filename=keyfile)
            sheet_id = os.environ.get("GSHEET_ID")
            sh = (gc.open_by_key(sheet_id) if sheet_id
                  else gc.open(os.environ.get("GSHEET_NAME", "Nutrition Log")))
            _ws = sh.sheet1
            if _ws.row_values(1) != headers:      # init header row once
                _ws.update("A1", [headers])
        _ws.append_row(row, value_input_option="USER_ENTERED")
    except Exception:
        _ws = None  # drop stale handle (e.g. expired auth); retry next meal
        log.warning("Sheets mirror failed — meal is safe in SQLite, "
                    "will retry on next log", exc_info=True)
