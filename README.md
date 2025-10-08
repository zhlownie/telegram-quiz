# Telegram Quiz Game

Lightweight Telegram quiz about Singapore. No database; perfect for demos and small tests.

Two simple interactions:
- Inline buttons: users tap one of 3 options
- Text fallback: users can type the exact option text

## Tech Stack
- Language: Python 3.10+
- Web Framework: Flask
- Telegram Bot API: via HTTPS calls (no SDK needed)
- Hosting: Render.com (free tier)
- Session Management: In-memory dict keyed by chat_id (resets on restart)
- Dependencies: Flask, requests

## Files
- `app.py`: Flask app with `/telegram` webhook and helpers
- `questions.json`: Quiz content (editable by non-developers)
- `static/images/`: Local assets referenced by questions via `image_url`
- `requirements.txt`: Flask + requests

## Data Structure (questions.json)
Each question object includes:
- `question`: string
- `options`: list of 3 strings (clean text, no "A)" prefixes)
- `answer`: string (must exactly equal one of the options)
- `hint`: optional string (shown on HINT)
- `explanation`: optional string (shown after answering)
- `image_url`: optional string path to a local static asset (e.g., `static/images/merlion.jpg`)

Example:
```json
{
  "question": "What is the national flower of Singapore?",
  "options": ["Vanda Miss Joaquim", "Hibiscus", "Lotus"],
  "answer": "Vanda Miss Joaquim",
  "hint": "It's a type of orchid.",
  "explanation": "Chosen in 1981 for its resilience..."
}
```

## Run locally
1) Create a virtualenv and install deps
2) Set env vars
- `TELEGRAM_BOT_TOKEN`: your bot token from @BotFather
- Optional: `PORT` (default 3000)
- Optional: `RENDER_EXTERNAL_URL` (used to build absolute image URLs)
3) Start server: `python app.py`
4) Use a tunneling tool (e.g., ngrok) to expose `http://localhost:3000/telegram`
5) Set webhook: `POST http(s)://<your-host>/set-webhook`

To remove webhook: `POST http(s)://<your-host>/delete-webhook`

## Deploy to Render
- Build Command: `pip install -r requirements.txt`
- Start Command: `python app.py`
- Port: `3000` (or environment `PORT`)

After deploy, call: `POST https://<your-app>.onrender.com/set-webhook`

## Commands
- Type `START` or `/start` to begin
- Type `HINT` during a question to get the hint (if available)

## Notes
- Options are clean text; the correct answer must exactly match one option
- We send an image + inline buttons when `image_url` is present
- State is in-memory and resets on app restart (OK for demo)

## Troubleshooting
- Webhook not set: call `/set-webhook` and inspect JSON response
- 400 from Telegram API: check `TELEGRAM_BOT_TOKEN` and request body
- Images not showing: ensure `RENDER_EXTERNAL_URL` is correct and `image_url` points to `/static/...`