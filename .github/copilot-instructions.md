Project: Telegram Quiz Game

Overview:
Lightweight Telegram quiz about Singapore. Works with inline buttons and optional images.

Backend: Minimal Python Flask app deployed on Render.com. No DB; ideal for demos and small tests.

Core Behavior:
- User sends START (or /start) to begin
- Bot asks questions sequentially with inline keyboard buttons (3 options)
- Immediate feedback and optional explanation after each answer
- Final score and friendly message at the end; user can START again to replay
- Users can also type the exact option text as a fallback

Data Structure (questions.json):
- All quiz content lives in questions.json (NOT hardcoded)
- Each question object includes:
  - "question": string
  - "options": list of 3 strings (clean text, no "A)" prefixes)
  - "answer": string (must exactly equal one of the options)
  - "hint": optional string (shown on HINT)
  - "explanation": optional string (shown after answering)
  - "image_url": optional string path to a local static asset (e.g., "static/images/merlion.jpg")

Technical Stack:
- Language: Python 3.10+
- Web Framework: Flask
- Telegram Bot API: direct HTTPS (no SDK dependency)
- Hosting: Render.com (free tier)
- Session Management: In-memory dict keyed by chat_id (resets on app restart)
- Dependencies: Flask, requests

File Structure:
- app.py: Flask app with /telegram webhook; set/delete webhook endpoints; image + buttons support
- questions.json: Quiz content (editable by non-developers)
- static/images/: Local images referenced by questions via image_url
- requirements.txt: Flask + requests
- README.md: Setup, deployment, and usage
- .github/copilot-instructions.md: This file (for AI guidance)

Deployment:
- Push to GitHub
- Create Web Service on Render.com
  - Build Command: pip install -r requirements.txt
  - Start Command: python app.py
  - Port: 3000 (or environment PORT)
- Set webhook URL by calling POST https://<your-app>.onrender.com/set-webhook

Important Notes for AI Assistance:
- Do NOT hardcode questions in app.py—always load from questions.json
- Keep options as clean text (3 items) and ensure "answer" exactly matches one option
- For images (image_url), send a photo with inline buttons (sendPhoto) if present; otherwise send text + inline buttons (sendMessage)
- Build absolute image URLs using RENDER_EXTERNAL_URL (Render) or request.url_root as fallback when in request context
- Only standard libs + Flask + requests—avoid adding dependencies unless requested
- State is in-memory and resets on restart (acceptable for demo)
- No letter shortcuts (A/B/C). Users tap the option text or type it exactly.

Future ideas: score persistence, richer content, analytics, l10n.