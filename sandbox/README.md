# CalBot — personal nutrition-tracking Telegram bot

Send a food photo (meal or nutrition label) or a text description; get back an
interactive nutrition card; confirm to log it to Google Sheets; view summaries
and charts in-chat.

## Architecture

```
Telegram photo/text
      │  (polling — no public URL needed)
      ▼
   bot.py ──► llm.py ──► Gemini Flash / Claude Haiku (vision → JSON)
      │
      ├──► inline buttons: ✅ Log · ✏️ Fix · ½ / ×2 · 🗑 Discard
      │
      ├──► sheets.py ──► Google Sheet (one row per meal)
      │
      └──► charts.py ──► matplotlib PNG back into the chat
```

## Setup

### 1. Telegram bot
1. Message **@BotFather** → `/newbot` → get your token.
2. Message **@userinfobot** to get your numeric user ID (and your user's).
3. Put both in `.env` (copy from `.env.example`).

### 2. LLM key
- **Gemini** (recommended, cheapest): https://aistudio.google.com → get API key.
- **Anthropic**: https://console.anthropic.com → API key, set
  `LLM_PROVIDER=anthropic`.

### 3. Google Sheets
1. https://console.cloud.google.com → new project → enable **Google Sheets
   API** + **Google Drive API**.
2. IAM → Service Accounts → create one → Keys → add JSON key → save as
   `service_account.json` in this folder.
3. Create a Google Sheet named `Nutrition Log` (or your GSHEET_NAME) and
   **share it with the service account email** (the `client_email` inside the
   JSON) as Editor.

### 4. Run locally (MacBook)
```bash
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # fill it in
python bot.py
```
Send your bot a photo of your dinner. That's the whole test.

## Deploying later

Polling mode means **any machine that runs Python 24/7 works** — no domain,
no open ports, no reverse proxy.

**Option A — small VPS (simplest, ~US$4-6/mo)** or **Oracle Cloud free tier**:
```bash
# on the server
git clone <your repo> && cd calbot
python3 -m venv venv && venv/bin/pip install -r requirements.txt
# copy .env and service_account.json over (scp), then:
sudo tee /etc/systemd/system/calbot.service <<'EOF'
[Unit]
Description=CalBot
After=network.target

[Service]
WorkingDirectory=/home/ubuntu/calbot
ExecStart=/home/ubuntu/calbot/venv/bin/python bot.py
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF
sudo systemctl enable --now calbot
```

**Option B — Docker anywhere (Pi, VPS, old laptop):**
```dockerfile
FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
CMD ["python", "bot.py"]
```
```bash
docker build -t calbot . && docker run -d --restart=always --env-file .env \
  -v ./service_account.json:/app/service_account.json calbot
```

## Ideas for v2
- Scheduled daily 9pm summary push (`JobQueue` in python-telegram-bot)
- Weight logging (`/weight 79.8`) + weight trend chart like the apps you saw
- EXIF date extraction when photos are sent as *files* (uncompressed)
- Weekly Sheet tab with per-week rollups
- Voice note logging (Telegram voice → transcription → same text path)
