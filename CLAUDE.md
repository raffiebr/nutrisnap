# CLAUDE.md — NutriSnap project context

Personal nutrition-tracking Telegram bot for 1-2 whitelisted users. Not a
public product — never add multi-tenant features, onboarding flows, or
anything that assumes unknown users.

`sandbox/` holds the original prototype from a claude.ai chat — reference
only, never edit or run it.

## Architecture (keep this shape)

- `bot.py` — Telegram handlers, inline keyboards, meters, all UX. Polling mode.
- `llm.py` — the ONLY file that talks to an LLM. Provider-agnostic
  (Gemini via the new `google-genai` SDK / Claude via `LLM_PROVIDER` env var).
  Both providers share one prompt and must return the same JSON schema.
- `storage.py` — the ONLY file that talks to SQLite. Source of truth, one row
  per meal. Calls the mirror after each successful insert.
- `sheets_mirror.py` — optional best-effort Google Sheets copy. Failures are
  logged and swallowed; nothing may ever depend on the mirror succeeding.
- `charts.py` — matplotlib PNGs returned as bytes, sent into chat.

Keep this separation. New integrations get their own module.

## Hard constraints — do not change without asking

1. **Polling, not webhooks.** The whole deployment story (no public URL, no
   ports, runs on a Pi/VPS/laptop) depends on polling. Never convert to
   webhooks.
2. **Silent auth rejection.** Unauthorized users get NO reply, just a log
   line. Don't "improve" this with an error message.
3. **Nothing is logged without explicit user confirmation** (the ✅ Log
   button). Analysis cards are held in `context.user_data["pending"]` until
   confirmed or discarded.
4. **The JSON schema in `llm.py`'s SYSTEM_PROMPT is a contract.**
   `fmt_analysis`, `storage.log_meal`, and `scale` all depend on it. If you
   change the schema, update all three.
5. Tracked nutrients: calories, protein_g, carbs_g, fat_g, sodium_mg, sugar_g.
   Column order is fixed in `storage.HEADERS` (shared with the Sheets
   mirror) — appending columns is OK, reordering breaks existing sheets.

## Conventions

- Timezone: Asia/Singapore (`storage.TZ`). All timestamps and "today" logic
  use it.
- **Everything is per-user**: `today_summary`/`fetch_history`/`delete_last`
  filter by `user_id`; handlers pass `update.effective_user.id`. Never show
  one user another user's data.
- Daily 21:00 SGT summary push via PTB `JobQueue`
  (`python-telegram-bot[job-queue]` extra); time set by `DAILY_SUMMARY_TIME`.
- `/undo` deletes the last SQLite row only — a mirrored Google Sheets row
  stays (documented, accepted).
- User context: Singaporean food (hawker dishes) is common — the LLM prompt
  is deliberately primed for this. Keep that priming.
- LLM responses are parsed defensively via `_extract_json` (strips fences,
  finds outermost `{}`). Don't assume clean JSON from the model.
- LLM-produced strings pass through `bot.sanitize` before display — Telegram
  MARKDOWN parse errors come from stray `*`/`_` in meal names.
- Daily limits (WHO defaults): sodium 2000 mg, sugar 50 g — env-overridable
  (`DAILY_SODIUM_LIMIT_MG`, `DAILY_SUGAR_LIMIT_G`), read by bot.py and
  charts.py.
- `python-telegram-bot` v21+ async API. All handlers are `async def`.
- Secrets live in `.env` (see `.env.example`) + `service_account.json`.
  Both are gitignored — never commit or print them.

## State model (deliberately simple)

- Pending (unconfirmed) analyses: in-memory `context.user_data["pending"]`,
  keyed by short uuid. Lost on restart — acceptable, user just resends.
- Calorie goal: `context.user_data["goal"]`, also in-memory. If persistence
  is ever needed, store it in the SQLite DB, not another cloud dependency.
- Source of truth for logged meals: SQLite (`nutrisnap.db`). The Google
  Sheet is a convenience mirror, never read back.

## Testing locally

```bash
source venv/bin/activate && python bot.py
```
Then message the bot on Telegram. There is no test suite yet; if adding one,
mock `llm.analyze_food` and point `NUTRISNAP_DB` at a temp file — never hit
real APIs in tests.

## Known gaps / v2 backlog (in rough priority order)

1. `/weight <kg>` logging + weight trend chart
2. EXIF date extraction when photo sent as uncompressed file/document
3. Voice note logging (Telegram voice → transcription → existing text path)
4. Weekly rollups

## Gotchas already discovered

- Telegram strips EXIF from compressed photos; only file/document uploads
  keep it. Default to message timestamp.
- The gspread worksheet handle is cached in `sheets_mirror._ws`; it is reset
  to None on any failure so the next log retries with a fresh session.
- Markdown parse errors in Telegram: meal names from the LLM may contain
  `*`/`_` chars — handled by `bot.sanitize`; keep new LLM strings going
  through it.
