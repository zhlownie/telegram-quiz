import os
import time
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
    # Basic validation for visible questions only: exactly 3 options, answer must match an option
    for i, q in enumerate(data):
        # Visible if is_visible is True, else fallback to legacy display_question, default True
        is_visible = bool(q.get("is_visible", q.get("display_question", True)))
        if not is_visible:
            continue
        # Allow photo-task questions to skip MCQ validation
        if q.get("expect_photo"):
            continue
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
# Hint penalty currently disabled; keep env for future use if needed
HINT_PENALTY_SECS = int(os.environ.get("HINT_PENALTY_SECS", "20"))
HINT_BUTTON_DATA = "__HINT__"
NEXT_BUTTON_DATA = "__NEXT__"
NEXT_BUTTON_LABEL = "Next Question ▶️"
PHOTO_BUTTON_DATA = "__PHOTO__"
PHOTO_BUTTON_LABEL = "📷 Upload Photo"

# --- Active time tracking (pause/resume between questions) ---
def timer_resume(sess: Dict[str, Any]) -> None:
    """Resume active timer if paused."""
    if sess.get("time_segment_started") is None:
        sess["time_segment_started"] = time.time()


def timer_pause(sess: Dict[str, Any]) -> None:
    """Pause active timer and accumulate elapsed into time_accum."""
    ts = sess.get("time_segment_started")
    if ts is not None:
        sess["time_accum"] = float(sess.get("time_accum", 0.0)) + (time.time() - float(ts))
        sess["time_segment_started"] = None


def timer_elapsed(sess: Dict[str, Any]) -> float:
    """Current total active time (seconds), including running segment if any."""
    acc = float(sess.get("time_accum", 0.0))
    ts = sess.get("time_segment_started")
    if ts is not None:
        acc += (time.time() - float(ts))
    return max(0.0, acc)


# --- Hint availability helper ---
def has_hint(q: Dict[str, Any]) -> bool:
    """Return True if question has a non-empty hint or hint_image."""
    ht = q.get("hint")
    hi = q.get("hint_image")
    ht_ok = isinstance(ht, str) and ht.strip() != ""
    hi_ok = isinstance(hi, str) and hi.strip() != ""
    return ht_ok or hi_ok

# Admin notifications: set OWNER_CHAT_ID="123456789" or ADMIN_CHAT_IDS="123,456"
ADMIN_CHAT_IDS: List[int] = []
_env_admins = (os.environ.get("OWNER_CHAT_ID") or os.environ.get("ADMIN_CHAT_IDS") or "").strip()
if _env_admins:
    try:
        ADMIN_CHAT_IDS = [int(x) for x in _env_admins.replace(" ", "").split(",") if x]
    except Exception:
        ADMIN_CHAT_IDS = []


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


def send_chat_action(chat_id: int, action: str = "typing") -> None:
    """Show a chat action (e.g., typing) to make short pauses feel intentional."""
    try:
        requests.post(tg_api("sendChatAction"), json={"chat_id": chat_id, "action": action}, timeout=5)
    except Exception:
        # Non-fatal if this fails
        pass


def to_list(value: Any) -> List[str]:
    """Normalize a value into a list of non-empty strings.
    Accepts list[str] or str; returns [] for None/empty.
    """
    if value is None:
        return []
    if isinstance(value, list):
        return [str(v) for v in value if str(v).strip()]
    if isinstance(value, str):
        v = value.strip()
        return [v] if v else []
    return []


def get_active_questions() -> List[Dict[str, Any]]:
    """Return only questions marked visible (default True). Supports legacy key as fallback."""
    active: List[Dict[str, Any]] = []
    for q in QUESTIONS:
        visible = bool(q.get("is_visible", q.get("display_question", True)))
        if visible:
            active.append(q)
    return active


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


# (Removed) Reply keyboard helpers were previously used to prompt photo uploads.


def notify_admins(text: str) -> None:
    """Send a notification message to configured admin chat IDs, if any."""
    if not ADMIN_CHAT_IDS:
        return
    for admin_id in ADMIN_CHAT_IDS:
        try:
            requests.post(
                tg_api("sendMessage"),
                json={"chat_id": admin_id, "text": text, "parse_mode": "HTML"},
                timeout=10,
            )
        except Exception:
            # Ignore failures to avoid impacting user flow
            pass


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


def send_photo(chat_id: int, photo_url: str, caption: str | None = None) -> None:
    payload: Dict[str, Any] = {
        "chat_id": chat_id,
        "photo": photo_url,
        "parse_mode": "HTML",
    }
    if caption is not None:
        payload["caption"] = caption
    resp = requests.post(tg_api("sendPhoto"), json=payload, timeout=10)
    resp.raise_for_status()


def send_photo_auto(chat_id: int, image_path_or_url: str, caption: str | None = None) -> None:
    """Send a photo by uploading a local file if it exists; otherwise send as URL.
    This avoids Telegram needing to fetch from a public URL during local dev.
    """
    # If already a full URL, just send it
    if image_path_or_url.startswith(("http://", "https://")):
        send_photo(chat_id, image_path_or_url, caption=caption)
        return

    # Resolve local path relative to project root
    here = os.path.dirname(os.path.abspath(__file__))
    local_path = os.path.join(here, image_path_or_url.lstrip("/"))

    if os.path.exists(local_path) and os.path.isfile(local_path):
        url = tg_api("sendPhoto")
        data: Dict[str, Any] = {"chat_id": chat_id}
        if caption is not None:
            data["caption"] = caption
            data["parse_mode"] = "HTML"
        with open(local_path, "rb") as f:
            files = {"photo": f}
            resp = requests.post(url, data=data, files=files, timeout=30)
            resp.raise_for_status()
        return

    # Fallback: build absolute URL and send
    abs_url = make_absolute_image_url(image_path_or_url)
    send_photo(chat_id, abs_url, caption=caption)


def ensure_session(chat_id: int) -> Dict[str, Any]:
    sess = sessions.get(chat_id)
    if not sess:
        sess = {
            "index": 0,
            "score": 0,
            "team_name": None,
            "state": None,  # 'awaiting_team_name' | 'awaiting_ready' | None
            "started_at": None,  # UNIX timestamp when quiz starts (on READY)
            "penalty_secs": 0,
            "hint_used_indices": [],  # indices where hint already used (penalized once)
            "awaiting_next": False,  # require Next before moving on
            "awaiting_photo_for": None,  # when user tapped Upload Photo, expect photo for this index
            "photo_awarded_for": set(),  # indices that have been awarded for photo
            "exp_sent_for": set(),  # indices where explanations already sent
            # Active timer bookkeeping
            "time_accum": 0.0,
            "time_segment_started": None,
        }
        sessions[chat_id] = sess
    else:
        # Backfill new fields for existing sessions
        sess.setdefault("penalty_secs", 0)
        sess.setdefault("hint_used_indices", [])
        sess.setdefault("awaiting_next", False)
        sess.setdefault("awaiting_photo_for", None)
        sess.setdefault("photo_awarded_for", set())
        sess.setdefault("exp_sent_for", set())
        sess.setdefault("time_accum", 0.0)
        sess.setdefault("time_segment_started", None)
    return sess


def build_inline_keyboard(options: List[str], include_hint: bool = False) -> Dict[str, Any]:
    # One button per row for readability
    keyboard = [[{"text": opt, "callback_data": opt}] for opt in options]
    if include_hint:
        # Add a dedicated hint button on its own row
        keyboard.append([[{"text": "💡 Hint", "callback_data": HINT_BUTTON_DATA}]][0])
    return {"inline_keyboard": keyboard}


def build_next_keyboard() -> Dict[str, Any]:
    return {"inline_keyboard": [[{"text": NEXT_BUTTON_LABEL, "callback_data": NEXT_BUTTON_DATA}]]}


def build_photo_keyboard(include_hint: bool = False) -> Dict[str, Any]:
    keyboard = [[{"text": PHOTO_BUTTON_LABEL, "callback_data": PHOTO_BUTTON_DATA}]]
    if include_hint:
        keyboard.append([{"text": "💡 Hint", "callback_data": HINT_BUTTON_DATA}])
    return {"inline_keyboard": keyboard}


def send_next_prompt(chat_id: int) -> None:
    send_message(
        chat_id,
        "If you are ready, press <b>Next Question</b>. Otherwise, you can re-attach another photo.",
        reply_markup=build_next_keyboard(),
    )


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
    sess["awaiting_next"] = False
    sess["awaiting_photo_for"] = None
    # Resume timer for the active question
    timer_resume(sess)
    idx = sess["index"]
    active = get_active_questions()
    if idx >= len(active):
        finalize_quiz(chat_id)
        return
    q = active[idx]

    # 1) Show optional question image first (no buttons)
    q_img = q.get("question_image") or q.get("image_url")
    if q_img:
        try:
            send_photo_auto(chat_id, q_img)
        except Exception as e:
            # Continue even if image fails
            print(f"[present_question] image send failed: {e}", flush=True)

    # 2) Send header + intro + question with answer buttons
    question_text: str = q["question"]
    bold_q = f"<b>{question_text}</b>"
    total = len(active)
    header = f"<b>Question {idx + 1}/{total}</b>"
    intro = q.get("intro")
    if intro:
        lines = str(intro).splitlines()
        if q.get("intro_blue"):
            lines = [f"🔷 {ln}" if ln.strip() else "" for ln in lines]
        intro_block = "<i>" + "\n".join(lines) + "</i>"
        body = f"{header}\n\n{intro_block}\n\n{bold_q}"
    else:
        body = f"{header}\n\n{bold_q}"
    if q.get("expect_photo"):
        reply_markup = build_photo_keyboard(include_hint=has_hint(q))
        send_message(chat_id, body, reply_markup=reply_markup)
        # Clear instruction: accepted anytime; re-uploads allowed
        send_message(
            chat_id,
            "Use the 📎 icon to attach your photo. You can send it anytime in this chat, and you may re-upload more photos before pressing <b>Next</b>."
        )
    else:
        options: List[str] = q["options"]
        reply_markup = build_inline_keyboard(options, include_hint=has_hint(q))
        send_message(chat_id, body, reply_markup=reply_markup)


def _use_hint_and_reprompt(chat_id: int) -> None:
    """Show hint image and/or text with +penalty once per question; do not re-present the question."""
    sess = ensure_session(chat_id)
    idx = sess.get("index", 0)
    active = get_active_questions()
    if idx >= len(active):
        send_message(chat_id, "You're not in an active quiz. Type START to play.")
        return
    q = active[idx]
    hint_text = (q.get("hint") or "").strip()
    hint_image = (q.get("hint_image") or "").strip()
    if not hint_text and not hint_image:
        send_message(chat_id, "No hint available for this question.")
        return

    # Apply penalty once per question
    used_list = sess.get("hint_used_indices", [])
    first_time = idx not in used_list
    if first_time:
        sess["penalty_secs"] = int(sess.get("penalty_secs", 0)) + HINT_PENALTY_SECS
        used_list.append(idx)
        sess["hint_used_indices"] = used_list

    # Send image (with caption) if provided; otherwise send text
    if hint_image:
        caption_base = f"💡 Hint (+{HINT_PENALTY_SECS} secs)" if first_time else "💡 Hint"
        caption = caption_base + (f": {hint_text}" if hint_text else "")
        try:
            send_photo_auto(chat_id, hint_image, caption=caption)
        except Exception as e:
            print(f"[_use_hint_and_reprompt] hint image send failed: {e}", flush=True)
            if hint_text:
                msg_prefix = f"💡 Hint (+{HINT_PENALTY_SECS} secs): " if first_time else "💡 Hint: "
                send_message(chat_id, msg_prefix + hint_text)
    elif hint_text:
        msg_prefix = f"💡 Hint (+{HINT_PENALTY_SECS} secs): " if first_time else "💡 Hint: "
        send_message(chat_id, msg_prefix + hint_text)
    # Do not re-present the question; users can answer from the existing prompt


def handle_answer(chat_id: int, selected: str) -> None:
    sess = ensure_session(chat_id)
    idx = sess["index"]
    active = get_active_questions()
    if idx >= len(active):
        finalize_quiz(chat_id)
        return
    q = active[idx]
    # For photo questions, buttons shouldn't route here
    if q.get("expect_photo"):
        send_message(chat_id, "Please upload a photo for this question using the button.")
        return
    correct = q["answer"]
    is_correct = selected == correct
    if is_correct:
        sess["score"] += 1
        send_message(chat_id, "✅ Correct!")
    else:
        send_message(chat_id, f"❌ Not quite. The correct answer is: <b>{correct}</b>")

    # 3) Support multi-step explanations: images/text arrays with fallback to single values
    img_list = to_list(q.get("explanation_images") or q.get("explanation_image"))
    txt_list = to_list(q.get("explanations") or q.get("explanation"))
    steps = max(len(img_list), len(txt_list))
    for i in range(steps):
        if i < len(img_list):
            try:
                send_photo_auto(chat_id, img_list[i])
            except Exception as e:
                print(f"[handle_answer] explanation image failed: {e}", flush=True)
        if i < len(txt_list):
            send_message(chat_id, f"ℹ️ {txt_list[i]}")

    # Advance behavior: if this is the last question, finish; otherwise require Next button
    if idx + 1 >= len(active):
        sess["awaiting_next"] = False
        finalize_quiz(chat_id)
    else:
        sess["awaiting_next"] = True
        # Pause timer while waiting for Next
        timer_pause(sess)
        send_message(chat_id, "When you’re ready, press <b>Next Question</b>.", reply_markup=build_next_keyboard())


def notify_admins_photo(file_id: str, caption: str | None = None) -> None:
    if not ADMIN_CHAT_IDS:
        return
    for admin_id in ADMIN_CHAT_IDS:
        try:
            payload: Dict[str, Any] = {"chat_id": admin_id, "photo": file_id}
            if caption:
                payload["caption"] = caption
                payload["parse_mode"] = "HTML"
            requests.post(tg_api("sendPhoto"), json=payload, timeout=15)
        except Exception:
            pass


def finalize_quiz(chat_id: int) -> None:
    sess = ensure_session(chat_id)
    total = len(get_active_questions())
    score = sess.get("score", 0)
    # Compute elapsed time if available and format as mins/secs
    def _fmt_dur(sec: Any) -> str:
        try:
            s = int(max(0, int(float(sec))))
        except Exception:
            return "0 secs"
        m, s = divmod(s, 60)
        parts: List[str] = []
        if m > 0:
            parts.append(f"{m} min{'s' if m != 1 else ''}")
        parts.append(f"{s} sec{'s' if s != 1 else ''}")
        return " ".join(parts)

    # Penalties
    penalties_total = int(sess.get("penalty_secs", 0))
    hint_count = len(sess.get("hint_used_indices", []))

    # Compute active elapsed time (paused while waiting on Next)
    duration_line = ""
    base_elapsed = timer_elapsed(sess)
    elapsed_total = base_elapsed + penalties_total
    duration_line = f"\n⏱️ Time: <b>{_fmt_dur(base_elapsed)}</b>"
    if penalties_total > 0:
        duration_line += (
            f"\n⚠️ Penalties: <b>+{_fmt_dur(penalties_total)}</b>"
            f"\n⏱️ Total Time: <b>{_fmt_dur(elapsed_total)}</b>"
        )

    team = sess.get("team_name") or "Adventurers"
    finish = (
        f"🏁 <b>{team}</b> — <b>Hunt complete!</b>\n\n"
        f"Score: <b>{score}</b> / <b>{total}</b>"
        f"{duration_line}"
        + (f"\n🧠 Hints used: <b>{hint_count}</b>  |  Penalty: <b>+{_fmt_dur(penalties_total)}</b>" if hint_count > 0 else "")
        + "\n\n"
        "Type <b>START</b> to play again."
    )
    send_message(chat_id, finish)

    # Notify owner/admins with the same summary but without the replay line
    try:
        admin_finish = (
            f"🏁 <b>{team}</b> — <b>Hunt complete!</b>\n\n"
            f"Score: <b>{score}</b> / <b>{total}</b>"
            f"{duration_line}"
            + (f"\n🧠 Hints used: <b>{hint_count}</b>  |  Penalty: <b>+{_fmt_dur(penalties_total)}</b>" if hint_count > 0 else "")
        )
        notify_admins(admin_finish)
    except Exception:
        pass
    # Reset state but keep session dict
    sess["index"] = 0
    sess["score"] = 0
    sess["started_at"] = None
    sess["awaiting_next"] = False
    sess["awaiting_photo_for"] = None
    sess["photo_awarded_for"] = set()
    sess["exp_sent_for"] = set()
    sess["penalty_secs"] = 0
    sess["hint_used_indices"] = []
    # Reset active timer
    sess["time_accum"] = 0.0
    sess["time_segment_started"] = None


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
            # Intercept special non-answer actions first
            if str(data).upper() == "READY":
                sess = ensure_session(int(chat_id))
                sess["state"] = None  # entering quiz
                sess["index"] = 0
                # Do NOT start timer yet; show intro + Start Timer button
                try:
                    send_photo_auto(int(chat_id), "static/images/introduction_of_themes.png")
                except Exception:
                    pass
                themes_msg = (
                    "<i>“Seek what others overlook. The answers lie where art and memory intertwine.”</i>\n\n"
                    "You will travel through different <b>Art Zones</b>, each representing the four NYGH themes:\n\n"
                    "• <b>Belonging</b>\n"
                    "• <b>Discovering</b>\n"
                    "• <b>Serving</b>\n"
                    "• <b>Leading</b>\n\n"
                    "Each location contains a hidden clue, symbol, or artwork waiting to be discovered."
                )
                send_message(int(chat_id), themes_msg)
                # Show Start Timer button and wait
                sess["state"] = "awaiting_timer"
                timer_kb = build_inline_keyboard(["Start Timer"])
                send_message(int(chat_id), "🕒 <b>When you’re ready, press Start Timer.</b>", reply_markup=timer_kb)
                # Do not present the question yet
            elif str(data).upper() in ("START TIMER", "START_TIMER"):
                sess = ensure_session(int(chat_id))
                # (Re)start timers
                sess["started_at"] = time.time()
                sess["time_accum"] = 0.0
                sess["time_segment_started"] = None
                sess["state"] = None
                present_question(int(chat_id))
            elif str(data).upper() in ("START TIMER", "START_TIMER"):
                sess = ensure_session(int(chat_id))
                # (Re)start timers
                sess["started_at"] = time.time()
                sess["time_accum"] = 0.0
                sess["time_segment_started"] = None
                sess["state"] = None
                present_question(int(chat_id))
            elif str(data) == PHOTO_BUTTON_DATA:
                sess = ensure_session(int(chat_id))
                idx = sess.get("index", 0)
                active = get_active_questions()
                if idx < len(active) and active[idx].get("expect_photo"):
                    sess["awaiting_photo_for"] = idx
                    send_message(
                        int(chat_id),
                        "Please attach a photo now using the 📎 icon (camera or gallery). You can re-upload photos before pressing <b>Next</b>. We’ll forward them to the admins."
                    )
                else:
                    send_message(int(chat_id), "This question expects an option. Please pick one below.")
            elif str(data) == NEXT_BUTTON_DATA:
                sess = ensure_session(int(chat_id))
                active = get_active_questions()
                if not sess.get("awaiting_next"):
                    # Ignore stray NEXT presses
                    try:
                        requests.post(tg_api("answerCallbackQuery"), json={"callback_query_id": cq.get("id")}, timeout=10)
                    except Exception:
                        pass
                    return jsonify({"ok": True})
                sess["awaiting_next"] = False
                sess["index"] += 1
                if sess["index"] < len(active):
                    send_chat_action(int(chat_id), "typing")
                    time.sleep(1)
                    present_question(int(chat_id))
                else:
                    finalize_quiz(int(chat_id))
            elif str(data) == HINT_BUTTON_DATA:
                _use_hint_and_reprompt(int(chat_id))
            else:
                # If awaiting Next, block more answers and nudge
                sess = ensure_session(int(chat_id))
                if sess.get("awaiting_next"):
                    send_message(int(chat_id), "You’ve already answered. Press <b>Next Question</b> to continue.")
                else:
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

        # Handle incoming photo uploads (for photo questions)
        if msg.get("photo"):
            sess = ensure_session(int(chat_id))
            idx = sess.get("index", 0)
            active = get_active_questions()
            photos = msg.get("photo") or []
            # Choose the largest size
            file_id = photos[-1].get("file_id") if photos else None
            team = sess.get("team_name") or "Adventurers"
            if idx < len(active) and active[idx].get("expect_photo") and file_id:
                # Forward to admins
                try:
                    notify_admins_photo(file_id, caption=f"[{team}] — Q{idx+1} photo upload")
                except Exception:
                    pass
                # Award once per question
                if idx not in sess["photo_awarded_for"]:
                    sess["photo_awarded_for"].add(idx)
                    sess["score"] += 1
                    send_message(int(chat_id), "✅ Nice capture! Point awarded.")
                else:
                    send_message(int(chat_id), "📸 Got it — photo received and forwarded.")

                # Send explanations once per question then show Next
                if idx not in sess["exp_sent_for"]:
                    sess["exp_sent_for"].add(idx)
                    q = active[idx]
                    txt_list = to_list(q.get("explanations") or q.get("explanation"))
                    for t in txt_list:
                        send_message(int(chat_id), f"ℹ️ {t}")
                # Next gating or finalize — always prompt Next on every photo upload (unless last)
                if idx + 1 >= len(active):
                    sess["awaiting_next"] = False
                    finalize_quiz(int(chat_id))
                else:
                    sess["awaiting_next"] = True
                    # Pause timer while waiting for Next (idempotent if already paused)
                    timer_pause(sess)
                    send_next_prompt(int(chat_id))
                return jsonify({"ok": True})
            else:
                # Photo sent but not expected; gently nudge
                send_message(int(chat_id), "Thanks! For this question, please select an answer from the options.")
                return jsonify({"ok": True})

        # Normalize commands
        upper = text.upper()
        if upper in ("/START", "START"):
            # Reset and begin pre-start flow
            sessions[int(chat_id)] = {"index": 0, "score": 0, "team_name": None, "state": "awaiting_team_name", "started_at": None}
            send_message(
                int(chat_id),
                (
                    "<b>Welcome to the NYGH Art Scavenger Hunt!</b>\n\n"
                    "Get ready to <b>explore</b>, discover hidden gems, and uncover the beauty of art around you.\n\n"
                    "<b>Before we start, quick tips:</b>\n\n"
                    "• If you run into any issues, message us on Telegram.\n"
                    "• Please don’t share any sensitive information here as this chat may be saved for quality and improvement purposes.\n\n"
                    "<b>What’s your team’s name?</b>\n\n"
                    "<i>Type it below to begin!</i>"
                )
            )
            return jsonify({"ok": True})

        # Team name capture & READY gate take precedence over other text handling
        sess = ensure_session(int(chat_id))
        if sess.get("state") == "awaiting_team_name" and text:
            team_name = text.strip()
            sess["team_name"] = team_name
            sess["state"] = "awaiting_ready"
            # Show Madam Linden image first (upload local if available)
            try:
                send_photo_auto(int(chat_id), "static/images/madam_linden.png")
            except Exception:
                # If the image fails to send, continue gracefully
                pass
            intro = (
                f"<b>Greetings \"{team_name}\", young art adventurers!</b>\n\n"
                "I am Madam Linden, once an artist in these very halls. I’ve collected artworks that captured the heart of NYGH — but only the keenest eyes can uncover the legacies I’ve hidden across time.\n\n"
                "Today, you’ll follow in my footsteps, solving puzzles and revealing the artistic footprints left behind by generations of students and teachers.\n\n"
                "<b>But beware! ⏱️ Your journey will be timed</b> — speed and accuracy will determine your place on the leaderboard.\n\n"
                "<i>Be cautious with your answers — mistakes or requests for help will cost you precious seconds, and even my spirit cannot save you from the penalty of a typo or a wayward auto-correct.</i>\n\n"
                "Now, gather your courage and creativity…\n\n"
                "<b>Your hunt begins when you press READY.</b>"
            )
            # Send intro and show READY button
            send_message(int(chat_id), intro)
            ready_kb = build_inline_keyboard(["READY"])  # single ready button
            send_message(int(chat_id), "▶️ <b>Press READY to begin.</b>", reply_markup=ready_kb)
            return jsonify({"ok": True})

        if sess.get("state") == "awaiting_ready":
            if upper == "READY":
                sess["state"] = None
                sess["index"] = 0
                # Show intro image + themes message and then wait for Start Timer
                try:
                    send_photo_auto(int(chat_id), "static/images/introduction_of_themes.png")
                except Exception:
                    pass
                themes_msg = (
                    "<i>“Seek what others overlook. The answers lie where art and memory intertwine.”</i>\n\n"
                    "You will travel through different <b>Art Zones</b>, each representing the four NYGH themes:\n\n"
                    "• <b>Belonging</b>\n"
                    "• <b>Discovering</b>\n"
                    "• <b>Serving</b>\n"
                    "• <b>Leading</b>\n\n"
                    "Each location contains a hidden clue, symbol, or artwork waiting to be discovered."
                )
                send_message(int(chat_id), themes_msg)
                sess["state"] = "awaiting_timer"
                timer_kb = build_inline_keyboard(["Start Timer"])
                send_message(int(chat_id), "🕒 <b>When you’re ready, press Start Timer.</b>", reply_markup=timer_kb)
                return jsonify({"ok": True})
            # Nudge to press READY
            ready_kb = build_inline_keyboard(["READY"])  # re-show button
            send_message(int(chat_id), "▶️ Please press <b>READY</b> to start the hunt.", reply_markup=ready_kb)
            return jsonify({"ok": True})

        if upper == "HINT":
            _use_hint_and_reprompt(int(chat_id))
            return jsonify({"ok": True})

        # Typed fallback to NEXT when awaiting next
        if sess.get("awaiting_next") and upper in ("NEXT", "NEXT QUESTION", "NEXT_QUESTION"):
            active = get_active_questions()
            sess["awaiting_next"] = False
            sess["index"] += 1
            if sess["index"] < len(active):
                send_chat_action(int(chat_id), "typing")
                time.sleep(1)
                present_question(int(chat_id))
            else:
                finalize_quiz(int(chat_id))
            return jsonify({"ok": True})

        # If waiting for Start Timer and user types it, begin
        if sess.get("state") == "awaiting_timer" and upper in ("START TIMER", "START_TIMER"):
            sess["started_at"] = time.time()
            sess["state"] = None
            present_question(int(chat_id))
            return jsonify({"ok": True})

        # Fallback: if user types an option exactly, accept it (visible questions only)
        if text:
            idx = sess["index"]
            active = get_active_questions()
            if idx < len(active):
                # If already answered and awaiting next, do not accept more answers; nudge
                if sess.get("awaiting_next"):
                    send_message(int(chat_id), "You’ve already answered. Press <b>Next Question</b> to continue.")
                    return jsonify({"ok": True})
                q = active[idx]
                # For photo questions, guide user to upload
                if q.get("expect_photo"):
                    send_message(int(chat_id), "This question needs a photo. Tap <b>Upload Photo</b> or attach one directly.")
                    return jsonify({"ok": True})
                options = q["options"]
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
