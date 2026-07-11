# CLAUDE.md — CalBot project context

Personal nutrition-tracking Telegram bot for 1-2 whitelisted users. Not a
public product — never add multi-tenant features, onboarding flows, or
anything that assumes unknown users.

## Architecture (keep this shape)

- `bot.py` — Telegram handlers, inline keyboards, all UX. Polling mode.
- `llm.py` — the ONLY file that talks to an LLM. Provider-agnostic
  (Gemini Flash / Claude Haiku via `LLM_PROVIDER` env var). Both providers
  share one prompt and must return the same JSON schema.
- `sheets.py` — the ONLY file that talks to Google Sheets. One row per meal.
- `charts.py` — matplotlib PNGs returned as bytes, sent into chat.

Keep this separation. New integrations get their own module.

## Hard constraints — do not change without asking

1. **Polling, not webhooks.** The whole deployment story (no public URL, no
   ports, runs on a Pi/VPS/laptop) depends on polling. Never convert to
   webhooks.
2. **Silent auth rejection.** Unauthorized users get NO reply, just a log
   line. Don't "improve" this with an error message.
3. **Nothing is logged to the sheet without explicit user confirmation**
   (the ✅ Log button). Analysis cards are held in `context.user_data["pending"]`
   until confirmed or discarded.
4. **The JSON schema in `llm.py`'s SYSTEM_PROMPT is a contract.** `fmt_analysis`,
   `sheets.log_meal`, and `scale` all depend on it. If you change the schema,
   update all three.
5. Tracked nutrients: calories, protein_g, carbs_g, fat_g, sodium_mg, sugar_g.
   Sheet column order is fixed in `sheets.HEADERS` — appending columns is OK,
   reordering breaks the existing sheet.

## Conventions

- Timezone: Asia/Singapore (`sheets.TZ`). All timestamps and "today" logic
  use it.
- User context: Singaporean food (hawker dishes) is common — the LLM prompt
  is deliberately primed for this. Keep that priming.
- LLM responses are parsed defensively via `_extract_json` (strips fences,
  finds outermost `{}`). Don't assume clean JSON from the model.
- `python-telegram-bot` v21+ async API. All handlers are `async def`.
- Secrets live in `.env` (see `.env.example`) + `service_account.json`.
  Both are gitignored — never commit or print them.

## State model (deliberately simple)

- Pending (unconfirmed) analyses: in-memory `context.user_data["pending"]`,
  keyed by short uuid. Lost on restart — acceptable, user just resends.
- Calorie goal: `context.user_data["goal"]`, also in-memory. If persistence
  is ever needed, use a small local JSON file, not another cloud dependency.
- Source of truth for logged meals: the Google Sheet. No local DB.

## Testing locally

```bash
source venv/bin/activate && python bot.py
```
Then message the bot on Telegram. There is no test suite yet; if adding one,
mock `llm.analyze_food` and `sheets._worksheet` — never hit real APIs in tests.

## Known gaps / v2 backlog (in rough priority order)

1. Scheduled daily ~9pm summary push (use PTB `JobQueue`)
2. `/weight <kg>` logging + weight trend chart (second sheet tab)
3. EXIF date extraction when photo sent as uncompressed file/document
4. Voice note logging (Telegram voice → transcription → existing text path)
5. Weekly rollup tab in the sheet

## Gotchas already discovered

- Telegram strips EXIF from compressed photos; only file/document uploads
  keep it. Default to message timestamp.
- `gspread` worksheet handle is cached in `sheets._ws`; if auth/session
  errors appear after long uptime, add a retry that resets `_ws` to None.
- Markdown parse errors in Telegram: meal names from the LLM may contain
  `*`/`_` chars. If `parse_mode=MARKDOWN` throws, escape or strip them.
