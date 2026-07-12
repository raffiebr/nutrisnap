# NutriSnap — personal nutrition-tracking Telegram bot

Send a food photo (meal or nutrition label) or a text description; get back an
interactive nutrition card; confirm to log it; view summaries, charts, and
sodium/sugar **% of daily limit meters** in-chat.

## Architecture

```
Telegram photo/text
      │  (polling — no public URL needed)
      ▼
   bot.py ──► llm.py ──► Gemini 3.1 Flash-Lite / Claude Haiku (vision → JSON)
      │
      ├──► inline buttons: ✅ Log · ✏️ Fix · ½ / ×2 · 🗑 Discard
      │
      ├──► storage.py ──► SQLite (nutrisnap.db, source of truth)
      │         └──► sheets_mirror.py ──► Google Sheet (optional, best-effort)
      │
      └──► charts.py ──► matplotlib PNG back into the chat
```

## Setup

### 1. Telegram bot (~2 min, free)
1. Message **@BotFather** → `/newbot` → name it (e.g. "NutriSnap") → copy the token.
2. Message **@userinfobot** to get your numeric user ID.

### 2. Gemini API key (~2 min, free tier is fine)
- Personal Google account works: https://aistudio.google.com → **Get API key**.
- Default model is `gemini-3.1-flash-lite` (Google's cheapest); change via
  `GEMINI_MODEL` in `.env`.
- Alternative: Anthropic — https://console.anthropic.com → API key, set
  `LLM_PROVIDER=anthropic`.

### 3. Run locally
```bash
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # fill in token, user ID, API key
python bot.py
```
Send your bot a photo of your dinner. That's the whole test.

### 4. Google Sheets mirror
Every logged meal is mirrored to a Google Sheet for browsing on any device.
1. https://console.cloud.google.com → your project → enable **Google Sheets
   API** + **Google Drive API**.
2. IAM → Service Accounts → create one → Keys → add JSON key → save as
   `service_account.json` in this folder.
3. Create a Google Sheet, **share it with the service account email** (the
   `client_email` inside the JSON) as Editor, and put the sheet's ID (from
   its URL) in `GSHEET_ID` in `.env`.

The mirror activates automatically once `service_account.json` exists.
Mirror failures never block logging — meals are always safe in SQLite.

## Commands

- `/today` — today's totals + sodium/sugar limit meters
- `/week` — 7-day summary: per-day calories with 🧂/🍬 over-limit flags,
  daily averages, and days-over-limit counts
- `/chart [days]` — progress chart (default 30)
- `/goal <kcal>` — set daily calorie goal
- `/limits` — sodium & sugar consumed today as % of WHO daily limits
- `/undo` — delete your last logged meal (SQLite only; an already-mirrored
  sheet row must be removed by hand)
- `/info` — how the % meters are computed, limit values + sources (WHO),
  and the full feature list

A nightly summary (totals + meters) is pushed to every whitelisted user at
`DAILY_SUMMARY_TIME` (default 21:00 SGT).

## Multiple users

Add each person's numeric Telegram ID to `ALLOWED_USER_IDS`
(comma-separated). Every logged row is tagged with the logger's `user_id`;
summaries, charts, undo, and the nightly push are all per-user. The Google
Sheet mirror is shared — filter the `user_id` column to see one person.

## Deployment

Runs 24/7 as a systemd service on a GCP Compute Engine **e2-micro**
(always-free tier) — VM `nutrisnap`, zone `us-central1-c`. Polling mode
needs no open ports or domain. **Only one instance may poll at a time** —
stop the VM service before running the bot locally, and vice versa.

Ops cheatsheet (gcloud, personal account):

```bash
gcloud compute ssh nutrisnap --zone=us-central1-c    # shell on the VM
# on the VM:
sudo journalctl -u nutrisnap -f                      # live logs
sudo systemctl restart nutrisnap                     # restart the bot

# ship code changes (run from this repo on the Mac):
tar czf /tmp/deploy.tar.gz --exclude=venv --exclude=.git \
    --exclude=__pycache__ --exclude=nutrisnap.db --exclude=.DS_Store .
gcloud compute scp /tmp/deploy.tar.gz nutrisnap:~/ --zone=us-central1-c
gcloud compute ssh nutrisnap --zone=us-central1-c --command \
    "tar xzf ~/deploy.tar.gz -C ~/nutrisnap && rm ~/deploy.tar.gz && sudo systemctl restart nutrisnap"
```

Secrets (`.env`, `service_account.json`) and the SQLite DB live only on the
VM (with a copy on the Mac) — they are never in git.

## Ideas for v2
- Scheduled daily 9pm summary push (`JobQueue` in python-telegram-bot)
- Weight logging (`/weight 79.8`) + weight trend chart
- EXIF date extraction when photos are sent as *files* (uncompressed)
- Voice note logging (Telegram voice → transcription → same text path)
- Weekly rollups
