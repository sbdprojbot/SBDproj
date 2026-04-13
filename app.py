import os
import json
import time
import uuid
import threading
from datetime import datetime
from flask import Flask, request, abort

import gspread
from oauth2client.service_account import ServiceAccountCredentials

from linebot import LineBotApi, WebhookHandler
from linebot.exceptions import InvalidSignatureError
from linebot.models import MessageEvent, TextMessage, TextSendMessage

import re

# =========================
# CONFIG
# =========================

GOOGLE_SHEET_ID = os.getenv("GOOGLE_SHEET_ID")
LINE_CHANNEL_ACCESS_TOKEN = os.getenv("LINE_CHANNEL_ACCESS_TOKEN")
LINE_CHANNEL_SECRET = os.getenv("LINE_CHANNEL_SECRET")

if not GOOGLE_SHEET_ID:
    raise RuntimeError("GOOGLE_SHEET_ID missing")

app = Flask(__name__)

line_bot_api = LineBotApi(LINE_CHANNEL_ACCESS_TOKEN)
handler = WebhookHandler(LINE_CHANNEL_SECRET)

# =========================
# GOOGLE SHEET INIT
# =========================

def init_sheet():
    try:
        scope = [
            "https://spreadsheets.google.com/feeds",
            "https://www.googleapis.com/auth/drive"
        ]

        creds_json = os.getenv("GOOGLE_CREDENTIALS_JSON")

        if not creds_json:
            raise RuntimeError("GOOGLE_CREDENTIALS_JSON missing")

        creds = ServiceAccountCredentials.from_json_keyfile_dict(
            json.loads(creds_json),
            scope
        )

        client = gspread.authorize(creds)
        sheet = client.open_by_key(GOOGLE_SHEET_ID)

        return sheet

    except Exception as e:
        print("[GSHEET INIT ERROR]", str(e))
        return None


SHEET = init_sheet()

# =========================
# IN-MEMORY QUEUE (v4)
# =========================

QUEUE = []
DLQ = []
LOCK = threading.Lock()

def enqueue(task):
    with LOCK:
        QUEUE.append({
            "task": task,
            "retry": 0
        })


def worker():
    while True:
        if not QUEUE:
            time.sleep(0.5)
            continue

        with LOCK:
            job = QUEUE.pop(0)

        try:
            process_task(job["task"])

        except Exception as e:
            job["retry"] += 1

            if job["retry"] <= 3:
                time.sleep(2 ** job["retry"])
                enqueue(job["task"])
            else:
                DLQ.append(job)
                log("error", "dlq", str(e), job["task"])

        time.sleep(0.1)


threading.Thread(target=worker, daemon=True).start()

# =========================
# LOG SYSTEM
# =========================

def log(level, stage, message, parsed=None):
    try:
        ws = SHEET.worksheet("Log")

        ws.append_row([
            str(uuid.uuid4()),
            str(datetime.utcnow()),
            datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "system",
            level,
            stage,
            message,
            json.dumps(parsed, ensure_ascii=False) if parsed else "",
            "",
            "",
            "",
            "ok"
        ])
    except Exception as e:
        print("[LOG ERROR]", e)

# =========================
# PARSER (AI-lite engine)
# =========================

def parse_message(text):

    text = text.strip()

    # ORDER PATTERN
    order_match = re.search(r"(\d+)\s*(個|件|pcs|x)?\s*(.*)", text)

    if "買" in text or "order" in text.lower():
        return {
            "type": "order",
            "product": text,
            "qty": 1
        }

    # PRODUCT QUERY
    if "庫存" in text or "product" in text.lower():
        return {
            "type": "product_query",
            "query": text
        }

    # USER QUERY
    if "user" in text.lower():
        return {
            "type": "user_query",
            "query": text
        }

    # math fallback
    if any(op in text for op in ["+", "-", "*", "x", "÷"]):
        return {
            "type": "math",
            "expr": text
        }

    return {
        "type": "unknown",
        "raw": text
    }

# =========================
# CORE EXECUTION
# =========================

def process_task(task):

    ttype = task.get("type")

    if ttype == "order":
        log("info", "order", "processing", task)

    elif ttype == "product_query":
        log("info", "product", "query", task)

    elif ttype == "user_query":
        log("info", "user", "query", task)

    elif ttype == "math":
        log("info", "math", "compute", task)

    else:
        log("warn", "unknown", "cannot parse", task)

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

    parsed = parse_message(user_text)

    enqueue(parsed)

    reply = f"已收到：{parsed['type']}"

    line_bot_api.reply_message(
        event.reply_token,
        TextSendMessage(text=reply)
    )

# =========================
# ROOT
# =========================

@app.route("/", methods=["GET"])
def home():
    return "SBDPROJ_SYSTEM_BD v4 ONLINE"

# =========================
# MAIN
# =========================

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=10000)