# Project: Telegram Quiz Game (NYGH Art Scavenger Hunt)

This repo hosts a lightweight Telegram quiz bot built with Flask. Content is data-driven via questions.json. No database; sessions are in-memory, suitable for demos and small events.

## Runtime and Hosting
- Language: Python 3.10+
- Framework: Flask
- Bot API: direct HTTPS (requests)
- Hosting: Render.com
- Sessions: in-memory dict keyed by chat_id (reset on app restart)

## Environment variables
- TELEGRAM_BOT_TOKEN: Required Telegram bot token.
- RENDER_EXTERNAL_URL: Public base URL for building absolute image links and webhook setup (e.g., https://<service>.onrender.com).
- PORT: Optional; defaults to 3000.
- OWNER_CHAT_ID or ADMIN_CHAT_IDS: Optional; send completion DM to owner/admin(s). Use a single numeric chat_id or comma-separated list.
 - HINT_PENALTY_SECS: Optional; seconds added once per question when hint is used (default 20).
 - RESULTS_WEBHOOK_URL: Optional; if set, POST quiz results to this URL on finish (Zapier/Make/webhook.site).
 - AIRTABLE_API_KEY / AIRTABLE_BASE_ID / AIRTABLE_TABLE: Optional; if set (and RESULTS_WEBHOOK_URL empty), append results to Airtable.

## Repository layout
- app.py: Flask app with /telegram webhook, start flow, question presentation, answers, hints, next-question gating, timer, admin notifications.
- questions.json: All quiz content (do not hardcode questions in app.py).
- static/images/: Local assets referenced by questions.json.
- requirements.txt: Flask + requests.
- README.md: Setup and deployment guide.
- .github/copilot-instructions.md: This file (guidance for AI assistants).

## Current user flow
1) START or /start
   - Ask for team name (stores `team_name` in session).
   - Sends Madam Linden image (static/images/madam_linden.png), then styled intro.
   - Shows “READY” button.

2) READY
   - Sends themes intro image (introduction_of_themes.png) and message.
   - Shows “Start Timer” button.

3) Start Timer
   - Starts the active timer (see Timer model below).
   - Presents Question 1.

4) Per-question presentation
   - Header: “Question N/Total” (bold).
   - Optional question_image sent first.
   - Intro (multiline italics), then bold question text.
   - Inline answer buttons (3 options) plus a “💡 Hint” button only if either `hint` or `hint_image` is non-empty.

5) Hint
   - Supports optional `hint_image`. Shows hint (image/text) only; the question is not re-shown. User can answer from existing buttons.
   - Applies a +HINT_PENALTY_SECS once per question (displayed as +20 secs by default).

6) Answer
   - Immediate feedback (correct/incorrect).
   - Sequential explanations:
     - Supports arrays: image[0] → text[0] → image[1] → text[1] → … (falls back to single fields if arrays not provided).

7) Next Question
   - Requires explicit “Next Question ▶️” button (or user types NEXT). No auto-advance.
   - Sends “typing” indicator and pauses ~1s before showing the next question.
   - On final question, no “Next Question” button is shown; quiz finalizes.
   - Timer model: timer is PAUSED while waiting for Next; RESUMES when the next question is presented.

8) Finish
   - Shows Score and Time (mins/secs). Time counts only while a question is active (excludes waiting-for-Next pauses).
   - Notifies owner/admins: “[Team] — Hunt complete! Score X/Y; Time NN mins MM secs” (if OWNER_CHAT_ID/ADMIN_CHAT_IDS is set).
   - If hints were used, shows Penalties and Total Time (Time + penalties).
   - Optionally POSTs results to RESULTS_WEBHOOK_URL (or Airtable if configured).
   - Resets session for replay.

## Data model (questions.json)
Each question is a JSON object. Current fields:
- id: number (supports decimals like 5.1).
- is_visible: boolean (true/false). Invisible questions are skipped in all flows.
- question_image: optional string (relative path under static/images).
- intro: optional string; supports multiline with \n (rendered in italics).
- question: string; rendered bold in prompt.
- options: array of strings (prefer exactly 3 clean options).
- answer: string; must exactly equal one of the options.
- hint: optional string; shown via the Hint button or typed “HINT”.
 - hint_image: optional string; if present, hint is sent as an image (with optional caption from `hint`).
- explanation_images: array of strings (optional; paths under static/images).
- explanations: array of strings (optional). Sent after answer in sequence with images.
 - expect_photo: optional boolean. If true, this is a photo-upload task (no MCQ options/answer validation).

Notes:
- You can add more questions or draft entries with `"is_visible": false` until they’re ready.
- If a question has no images or explanations, omit those fields or use empty arrays.

## Image handling
- Bot auto-uploads local files via multipart when paths are relative (e.g., static/images/foo.jpg). This works offline and on Render; no public URL required.
- If an item is a URL, it’s sent directly. If local file is missing, the bot falls back to building an absolute URL using RENDER_EXTERNAL_URL or request.url_root.

## Webhook endpoints
- POST /telegram: Telegram webhook handler.
- POST /set-webhook: Registers the webhook to {base_url}/telegram (base from RENDER_EXTERNAL_URL or request headers).
- POST /delete-webhook: Removes the webhook.

## Guardrails for AI changes
- Do NOT hardcode question content in app.py. Always edit questions.json.
- Keep “answer” equal to one of the “options” exactly.
- Maintain HTML formatting in messages (bold/italic), but avoid Markdown special sequences inside HTML captions.
- Preserve the flow flags and session keys:
   - team_name, state (awaiting_team_name | awaiting_ready | awaiting_timer), started_at, index, score, awaiting_next, hint_used_indices, penalty_secs.
- Respect is_visible filtering across presentation, answering, and scoring.
- Keep 1s pause and typing indicator before moving to the next question.
- Keep Hint behavior: show hint only; do not re-present question.
- Maintain admin notifications on finalize (OWNER_CHAT_ID/ADMIN_CHAT_IDS).
 - Show Hint button only when `hint` or `hint_image` is non-empty.
 - Timer must pause when awaiting Next and resume on present_question.
 - For expect_photo questions:
    - Show inline “📷 Upload Photo” button (sends attach instructions; images can be attached anytime).
    - Accept re-uploads; award once; forward all photos to admins.
    - Send explanations once.
    - After every photo upload (not just first), re-prompt: “If you are ready, press Next Question. Otherwise, you can re-attach another photo.” with Next button.
    - On last question, do not show Next; finalize immediately after explanations.

## Common edit recipes
- Add/modify a question:
  - Update questions.json: set fields, ensure is_visible=true when ready.
  - Place images in static/images and reference by relative path.
- Hide a question:
  - Set `"is_visible": false` in questions.json.
- Add multi-step explanations:
  - Fill arrays: explanation_images and explanations with matching lengths (unequal sizes are okay; both sequences are sent).
   - If you prefer a single message, place one string in `explanations` containing embedded \n\n paragraph breaks.
- Style intros:
  - Provide multiline text via \n. HTML italics are applied automatically.

## Local run
- pip install -r requirements.txt
- python app.py
- Use ngrok (optional) or set RENDER_EXTERNAL_URL for testing external image URLs.
- Send /start to your bot (webhook must be set on Render).

## Deployment on Render
- Build: pip install -r requirements.txt
- Start: python app.py
- Env: TELEGRAM_BOT_TOKEN, RENDER_EXTERNAL_URL, OWNER_CHAT_ID (optional)
- Call POST https://<service>.onrender.com/set-webhook to register.

### Optional result logging (no database required)
- Webhook: set RESULTS_WEBHOOK_URL to any HTTPS endpoint (e.g., Zapier/Make). The bot POSTs a JSON payload on finish.
- Airtable: set AIRTABLE_API_KEY, AIRTABLE_BASE_ID, AIRTABLE_TABLE. The bot appends a row on finish via Airtable REST API.
- Google Sheets (alternative): deploy an Apps Script Web App (script.google.com/macros/.../exec) and set SHEETS_WEBAPP_URL; Sheets editor URLs (docs.google.com/...) won’t work as webhooks.

## Future enhancements
- Persist scores and timings (DB).
- Leaderboard and analytics.
- Multi-language support.
- Per-zone tracking.
 - Native Google Sheets/AppScript integration helper.
