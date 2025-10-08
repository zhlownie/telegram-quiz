import os
import json
from typing import Dict, Any, List

import requests
from flask import Flask, request, jsonify


# Flask app
app = Flask(__name__, static_folder="static", static_url_path="/static")


# Load questions from local JSON (must be present in this project folder)
def load_questions() -> List[Dict[str, Any]]:
    here = os.path.dirname(os.path.abspath(__file__))
    path = os.path.join(here, "questions.json")
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    # Basic validation: exactly 3 options, answer must match an option
    for i, q in enumerate(data):
        opts = q.get("options", [])
        if len(opts) != 3:
            raise ValueError(f"Question {i+1} must have exactly 3 options, got {len(opts)}")
        ans = q.get("answer")
        if ans not in opts:
            raise ValueError(f"Question {i+1} answer must match one of the options")
    return data


QUESTIONS: List[Dict[str, Any]] = load_questions()


# In-memory session store keyed by Telegram chat_id
sessions: Dict[int, Dict[str, Any]] = {}


# Env
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
RENDER_EXTERNAL_URL = os.environ.get("RENDER_EXTERNAL_URL")


def get_base_url() -> str:
    # Prefer explicit env from Render; fallback to request.url_root when available
    if RENDER_EXTERNAL_URL:
        return RENDER_EXTERNAL_URL.rstrip("/")
    try:
        # Works only inside a request context
        return request.url_root.rstrip("/")
    except Exception:
        return "http://localhost:3000"


def tg_api(method: str) -> str:
    if not TELEGRAM_BOT_TOKEN:
        raise RuntimeError("Missing TELEGRAM_BOT_TOKEN env var")
    return f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/{method}"


def send_message(chat_id: int, text: str, reply_markup: Dict[str, Any] | None = None) -> None:
    payload: Dict[str, Any] = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "HTML",
        # Disable link previews for cleaner UI unless we send photos
        "disable_web_page_preview": True,
    }
    if reply_markup:
        payload["reply_markup"] = reply_markup
    resp = requests.post(tg_api("sendMessage"), json=payload, timeout=10)
    resp.raise_for_status()


def send_photo_with_buttons(chat_id: int, photo_url: str, caption: str, reply_markup: Dict[str, Any]) -> None:
    payload: Dict[str, Any] = {
        "chat_id": chat_id,
        "photo": photo_url,
        "caption": caption,
        "parse_mode": "HTML",
        "reply_markup": reply_markup,
    }
    resp = requests.post(tg_api("sendPhoto"), json=payload, timeout=10)
    resp.raise_for_status()


def ensure_session(chat_id: int) -> Dict[str, Any]:
    sess = sessions.get(chat_id)
    if not sess:
        sess = {
            "index": 0,
            "score": 0,
        }
        sessions[chat_id] = sess
    return sess


def build_inline_keyboard(options: List[str]) -> Dict[str, Any]:
    # One button per row for readability
    keyboard = [[{"text": opt, "callback_data": opt}] for opt in options]
    return {"inline_keyboard": keyboard}


def make_absolute_image_url(image_url: str) -> str:
    if image_url.startswith("http://") or image_url.startswith("https://"):
        return image_url
    base = get_base_url()
    # Normalize leading slash
    if image_url.startswith("/"):
        return f"{base}{image_url}"
    return f"{base}/{image_url}"


def present_question(chat_id: int) -> None:
    sess = ensure_session(chat_id)
    idx = sess["index"]
    if idx >= len(QUESTIONS):
        finalize_quiz(chat_id)
        return
    q = QUESTIONS[idx]
    question_text: str = q["question"]
    options: List[str] = q["options"]
    reply_markup = build_inline_keyboard(options)
    image_url = q.get("image_url")
    if image_url:
        abs_url = make_absolute_image_url(image_url)
        send_photo_with_buttons(chat_id, abs_url, question_text, reply_markup)
    else:
        send_message(chat_id, question_text, reply_markup=reply_markup)


def handle_answer(chat_id: int, selected: str) -> None:
    sess = ensure_session(chat_id)
    idx = sess["index"]
    if idx >= len(QUESTIONS):
        finalize_quiz(chat_id)
        return
    q = QUESTIONS[idx]
    correct = q["answer"]
    is_correct = selected == correct
    if is_correct:
        sess["score"] += 1
        send_message(chat_id, "✅ Correct!")
    else:
        send_message(chat_id, f"❌ Not quite. The correct answer is: <b>{correct}</b>")

    # Optional explanation
    explanation = q.get("explanation")
    if explanation:
        send_message(chat_id, f"ℹ️ {explanation}")

    # Next question or finish
    sess["index"] += 1
    if sess["index"] < len(QUESTIONS):
        present_question(chat_id)
    else:
        finalize_quiz(chat_id)


def finalize_quiz(chat_id: int) -> None:
    sess = ensure_session(chat_id)
    total = len(QUESTIONS)
    score = sess.get("score", 0)
    send_message(chat_id, f"🏁 Quiz complete! You scored <b>{score}</b> out of <b>{total}</b>.")
    # Friendly prompt to restart
    send_message(chat_id, "Type START to play again.")
    # Reset state but keep session dict
    sess["index"] = 0
    sess["score"] = 0


@app.get("/")
def health() -> Any:
    return {"ok": True, "service": "telegram-quiz"}


@app.post("/telegram")
def telegram_webhook() -> Any:
    update = request.get_json(force=True, silent=True) or {}

    # Handle callback_query (button taps)
    if "callback_query" in update:
        cq = update["callback_query"]
        data = cq.get("data")
        message = cq.get("message", {})
        chat = message.get("chat", {})
        chat_id = chat.get("id")
        if chat_id is not None and data:
            handle_answer(int(chat_id), str(data))
        # Always answer callback to remove loading state
        try:
            requests.post(tg_api("answerCallbackQuery"), json={"callback_query_id": cq.get("id")}, timeout=10)
        except Exception:
            pass
        return jsonify({"ok": True})

    # Handle regular messages
    if "message" in update:
        msg = update["message"]
        chat = msg.get("chat", {})
        chat_id = chat.get("id")
        text = (msg.get("text") or "").strip()
        if chat_id is None:
            return jsonify({"ok": True})

        # Normalize commands
        upper = text.upper()
        if upper in ("/START", "START"):
            sessions[int(chat_id)] = {"index": 0, "score": 0}
            send_message(int(chat_id), "🎉 Welcome to the Singapore Quiz! Tap a button to answer.")
            present_question(int(chat_id))
            return jsonify({"ok": True})

        if upper == "HINT":
            sess = ensure_session(int(chat_id))
            idx = sess["index"]
            if idx < len(QUESTIONS):
                hint = QUESTIONS[idx].get("hint")
                if hint:
                    send_message(int(chat_id), f"💡 Hint: {hint}")
                else:
                    send_message(int(chat_id), "No hint available for this question.")
            else:
                send_message(int(chat_id), "You're not in an active quiz. Type START to play.")
            return jsonify({"ok": True})

        # Fallback: if user types an option exactly, accept it
        if text:
            sess = ensure_session(int(chat_id))
            idx = sess["index"]
            if idx < len(QUESTIONS):
                options = QUESTIONS[idx]["options"]
                if text in options:
                    handle_answer(int(chat_id), text)
                    return jsonify({"ok": True})
                else:
                    # Reprompt with buttons
                    send_message(int(chat_id), "Please tap one of the options below.")
                    present_question(int(chat_id))
                    return jsonify({"ok": True})
            else:
                send_message(int(chat_id), "Type START to begin the quiz.")
                return jsonify({"ok": True})

    return jsonify({"ok": True})


@app.post("/set-webhook")
def set_webhook() -> Any:
    if not TELEGRAM_BOT_TOKEN:
        return jsonify({"ok": False, "error": "Missing TELEGRAM_BOT_TOKEN"}), 400
    url = f"{get_base_url()}/telegram"
    resp = requests.post(
        tg_api("setWebhook"),
        json={
            "url": url,
            "allowed_updates": ["message", "callback_query"],
        },
        timeout=10,
    )
    try:
        data = resp.json()
    except Exception:
        data = {"status_code": resp.status_code, "text": resp.text[:300]}
    return jsonify(data), resp.status_code


@app.post("/delete-webhook")
def delete_webhook() -> Any:
    if not TELEGRAM_BOT_TOKEN:
        return jsonify({"ok": False, "error": "Missing TELEGRAM_BOT_TOKEN"}), 400
    resp = requests.post(tg_api("deleteWebhook"), timeout=10)
    try:
        data = resp.json()
    except Exception:
        data = {"status_code": resp.status_code, "text": resp.text[:300]}
    return jsonify(data), resp.status_code


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 3000))
    app.run(host="0.0.0.0", port=port)
