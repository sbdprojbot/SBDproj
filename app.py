import os
import json
import time
import threading
from datetime import datetime
from queue import Queue

from flask import Flask, request, abort

from linebot import LineBotApi, WebhookHandler
from linebot.exceptions import InvalidSignatureError
from linebot.models import MessageEvent, TextMessage, TextSendMessage

import gspread
from google.oauth2.service_account import Credentials

# =========================
# CONFIG
# =========================

app = Flask(__name__)

LINE_CHANNEL_SECRET = os.getenv("LINE_CHANNEL_SECRET")
LINE_CHANNEL_ACCESS_TOKEN = os.getenv("LINE_CHANNEL_ACCESS_TOKEN")

line_bot_api = LineBotApi(LINE_CHANNEL_ACCESS_TOKEN)
handler = WebhookHandler(LINE_CHANNEL_SECRET)

# =========================
# GOOGLE SHEETS
# =========================

SCOPE = ["https://www.googleapis.com/auth/spreadsheets"]

creds = Credentials.from_service_account_info(
    json.loads(os.getenv("GOOGLE_CREDS_JSON")),
    scopes=SCOPE
)

client = gspread.authorize(creds)

sheet = client.open_by_key(os.getenv("SHEET_ID"))

USER_SHEET = sheet.worksheet("User")
ORDER_SHEET = sheet.worksheet("Order")
LOG_SHEET = sheet.worksheet("Log")

# =========================
# QUEUE (no Redis)
# =========================

job_queue = Queue()

# =========================
# LOG HELPER (idempotent)
# =========================

def write_log(log_type, level, stage, message, parsed=None, missing_fields=None, ai_summary=None, ai_suggestion=None, status="ok"):
    ts = datetime.utcnow().isoformat()

    LOG_SHEET.append_row([
        f"log_{int(time.time()*1000)}",
        ts,
        ts,
        log_type,
        level,
        stage,
        message,
        json.dumps(parsed, ensure_ascii=False),
        json.dumps(missing_fields, ensure_ascii=False),
        ai_summary,
        ai_suggestion,
        status
    ])

# =========================
# PARSER (simple + stable)
# =========================

def parse_text(text):
    text = text.strip()

    if text.startswith("新增") or text.lower().startswith("add"):
        return {"intent": "CREATE", "raw": text}

    if text.startswith("查") or text.lower().startswith("read"):
        return {"intent": "READ", "raw": text}

    if text.startswith("改") or text.lower().startswith("update"):
        return {"intent": "UPDATE", "raw": text}

    if text.startswith("刪") or text.lower().startswith("delete"):
        return {"intent": "DELETE", "raw": text}

    return {"intent": "UNKNOWN", "raw": text}

# =========================
# AI (only fallback helper)
# =========================

def ai_analyze_error(parsed):
    # no external call forced; placeholder safe mode
    return {
        "summary": "parse incomplete or missing fields",
        "suggestion": "check command format (新增/查/改/刪 + product + qty)"
    }

# =========================
# SHEET OPERATIONS
# =========================

def create_order(parsed, user_id, text):
    ORDER_SHEET.append_row([
        f"order_{int(time.time()*1000)}",
        datetime.utcnow().isoformat(),
        datetime.utcnow().isoformat(),
        user_id,
        "",
        "",
        text,
        1,
        0,
        0,
        0,
        "created"
    ])

def read_order(parsed, user_id):
    return ORDER_SHEET.get_all_records()

def update_order(parsed):
    pass

def delete_order(parsed):
    pass

# =========================
# WORKER (background queue)
# =========================

def worker():
    while True:
        job = job_queue.get()

        try:
            parsed = job["parsed"]
            user_id = job["user_id"]
            text = job["text"]

            write_log("order", "info", "queue_start", text, parsed)

            if parsed["intent"] == "CREATE":
                create_order(parsed, user_id, text)

            elif parsed["intent"] == "READ":
                read_order(parsed, user_id)

            elif parsed["intent"] == "UPDATE":
                update_order(parsed)

            elif parsed["intent"] == "DELETE":
                delete_order(parsed)

            else:
                err = ai_analyze_error(parsed)
                write_log(
                    "order",
                    "warn",
                    "ai_fallback",
                    text,
                    parsed,
                    ai_summary=err["summary"],
                    ai_suggestion=err["suggestion"]
                )

            write_log("order", "info", "queue_done", text, parsed)

        except Exception as e:
            write_log("order", "error", "queue_fail", str(e), parsed)

            # DLQ (dead letter queue via log only)
            write_log("order", "error", "DLQ", str(e), parsed)

        finally:
            job_queue.task_done()

# start worker
threading.Thread(target=worker, daemon=True).start()

# =========================
# LINE WEBHOOK
# =========================

@app.route("/callback", methods=["POST"])
def callback():
    signature = request.headers.get("X-Line-Signature")

    body = request.get_data(as_text=True)

    try:
        handler.handle(body, signature)
    except InvalidSignatureError:
        abort(400)

    return "OK"

@handler.add(MessageEvent, message=TextMessage)
def handle_message(event):
    user_text = event.message.text
    user_id = event.source.user_id

    parsed = parse_text(user_text)

    write_log("order", "info", "receive", user_text, parsed)

    job_queue.put({
        "user_id": user_id,
        "text": user_text,
        "parsed": parsed
    })

    line_bot_api.reply_message(
        event.reply_token,
        TextSendMessage(text=f"收到：{parsed['intent']}")
    )

# =========================
# HEALTH CHECK
# =========================

@app.route("/", methods=["GET"])
def health():
    return "OK"

# =========================
# MAIN
# =========================

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=10000)