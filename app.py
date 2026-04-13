import os
import json
import time
import threading
from datetime import datetime

from flask import Flask, request

from linebot import LineBotApi, WebhookHandler
from linebot.exceptions import InvalidSignatureError
from linebot.models import MessageEvent, TextMessage, TextSendMessage

import gspread
from oauth2client.service_account import ServiceAccountCredentials

# =========================
# APP INIT
# =========================
app = Flask(__name__)

LINE_TOKEN = os.getenv("LINE_CHANNEL_ACCESS_TOKEN")
LINE_SECRET = os.getenv("LINE_CHANNEL_SECRET")
GOOGLE_CREDS = os.getenv("GOOGLE_CREDS_JSON")

line_bot_api = LineBotApi(LINE_TOKEN)
handler = WebhookHandler(LINE_SECRET)

sheet = None

QUEUE_FILE = "queue.json"
MAX_RETRY = 5
WORKER_INTERVAL = 5


# =========================
# LOG (incident base)
# =========================
def log(event, data=None):
    print(json.dumps({
        "time": datetime.utcnow().isoformat(),
        "event": event,
        "data": data
    }, ensure_ascii=False))


# =========================
# SHEET RESOLVER (FINAL)
# =========================
def init_sheet():
    global sheet

    try:
        creds = json.loads(GOOGLE_CREDS)

        scope = [
            "https://spreadsheets.google.com/feeds",
            "https://www.googleapis.com/auth/drive"
        ]

        auth = ServiceAccountCredentials.from_json_keyfile_dict(creds, scope)
        client = gspread.authorize(auth)

        sheets = client.openall()

        if not sheets:
            raise Exception("no sheets found")

        # 🎯 deterministic priority matching
        priority = ["pos", "order", "sheet1", "data"]

        chosen = None

        for p in priority:
            for s in sheets:
                if p in s.title.lower():
                    chosen = s
                    break
            if chosen:
                break

        if not chosen:
            chosen = sheets[0]

        sheet = chosen.sheet1

        log("SHEET_SELECTED", chosen.title)

    except Exception as e:
        sheet = None
        log("SHEET_INIT_FAIL", str(e))


# =========================
# SAFE WRITE
# =========================
def write_sheet_safe(row):
    global sheet

    try:
        if not sheet:
            init_sheet()

        if not sheet:
            raise Exception("sheet unavailable")

        sheet.append_row(row)

        log("SHEET_WRITE_OK", row)
        return True

    except Exception as e:
        log("SHEET_WRITE_FAIL", str(e))
        enqueue(row)
        return False


# =========================
# QUEUE SYSTEM (PERSISTENT)
# =========================
def load_queue():
    if not os.path.exists(QUEUE_FILE):
        return []
    try:
        return json.load(open(QUEUE_FILE))
    except:
        return []


def save_queue(q):
    tmp = QUEUE_FILE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(q, f)
    os.replace(tmp, QUEUE_FILE)


def enqueue(data):
    q = load_queue()
    q.append({
        "data": data,
        "retry": 0,
        "ts": datetime.utcnow().isoformat()
    })
    save_queue(q)

    log("QUEUE_ADD", data)


# =========================
# QUEUE WORKER (SELF-HEAL)
# =========================
def retry_queue():
    q = load_queue()
    if not q:
        return

    new_q = []

    for item in q:
        try:
            ok = write_sheet_safe(item["data"])

            if ok:
                log("QUEUE_DONE", item["data"])
            else:
                raise Exception("write failed")

        except Exception as e:
            item["retry"] += 1

            log("QUEUE_RETRY_FAIL", {
                "data": item["data"],
                "retry": item["retry"],
                "err": str(e)
            })

            if item["retry"] < MAX_RETRY:
                new_q.append(item)
            else:
                log("QUEUE_DEAD_LETTER", item)

    save_queue(new_q)


def worker():
    log("WORKER_START", "v3 self-healing online")

    while True:
        try:
            retry_queue()
        except Exception as e:
            log("WORKER_ERROR", str(e))

        time.sleep(WORKER_INTERVAL)


def start_worker():
    t = threading.Thread(target=worker, daemon=True)
    t.start()


# =========================
# LINE WEBHOOK
# =========================
@app.route("/callback", methods=["POST"])
def callback():
    body = request.get_data(as_text=True)
    signature = request.headers.get("X-Line-Signature")

    log("WEBHOOK_IN", body)

    try:
        handler.handle(body, signature)
    except InvalidSignatureError:
        log("SIGNATURE_FAIL")
        return "invalid signature", 400

    return "OK"


# =========================
# MESSAGE HANDLER
# =========================
@handler.add(MessageEvent, message=TextMessage)
def handle(event):
    text = event.message.text.strip()

    log("INPUT", text)

    # ------------------
    # /heal
    # ------------------
    if text == "/heal":
        init_sheet()
        retry_queue()

        status = {
            "sheet": "OK" if sheet else "FAIL",
            "queue": len(load_queue())
        }

        line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage(text=json.dumps(status, ensure_ascii=False))
        )
        return

    # ------------------
    # /report
    # ------------------
    if text == "/report":
        q = load_queue()

        line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage(text=json.dumps(q[:10], ensure_ascii=False))
        )
        return

    # ------------------
    # normal flow
    # ------------------
    row = [
        datetime.utcnow().isoformat(),
        text
    ]

    write_sheet_safe(row)

    line_bot_api.reply_message(
        event.reply_token,
        TextSendMessage(text="OK")
    )


# =========================
# ROOT
# =========================
@app.route("/", methods=["GET"])
def home():
    return "OK"


# =========================
# BOOT
# =========================
init_sheet()
start_worker()