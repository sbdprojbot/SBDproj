import os
import json
import uuid
from datetime import datetime

from flask import Flask, request

from linebot import LineBotApi
from linebot.models import TextSendMessage

import gspread
from oauth2client.service_account import ServiceAccountCredentials

# ==================================================
# APP
# ==================================================

app = Flask(__name__)

# ==================================================
# ENV
# ==================================================

LINE_TOKEN = os.getenv("LINE_CHANNEL_ACCESS_TOKEN", "")
GOOGLE_CREDENTIALS = os.getenv("GOOGLE_CREDENTIALS", "")

# ==================================================
# LOG SYSTEM
# ==================================================

EVENT_LOG = []
REPLAY_BUFFER = []

def log(stage, msg):
    EVENT_LOG.append({
        "time": datetime.utcnow().isoformat(),
        "stage": stage,
        "message": str(msg)
    })

    REPLAY_BUFFER.append(EVENT_LOG[-1])

    if len(EVENT_LOG) > 300:
        EVENT_LOG.pop(0)

    if len(REPLAY_BUFFER) > 50:
        REPLAY_BUFFER.pop(0)

# ==================================================
# LINE INIT
# ==================================================

line_bot_api = None

try:
    if LINE_TOKEN:
        line_bot_api = LineBotApi(LINE_TOKEN)
except Exception as e:
    log("LINE_INIT_FAIL", e)

# ==================================================
# SHEET INIT
# ==================================================

client = None
order_sheet = None
sheet_fail_count = 0

def init_sheet():

    global client, order_sheet, sheet_fail_count

    if not GOOGLE_CREDENTIALS:
        log("SHEET_INIT_FAIL", "missing credentials")
        return

    try:
        creds = ServiceAccountCredentials.from_json_keyfile_dict(
            json.loads(GOOGLE_CREDENTIALS),
            [
                "https://spreadsheets.google.com/feeds",
                "https://www.googleapis.com/auth/drive"
            ]
        )

        client = gspread.authorize(creds)

        sheet = client.open("pos")
        order_sheet = sheet.worksheet("Order")

        sheet_fail_count = 0
        log("SHEET_INIT_OK", "connected")

    except Exception as e:
        sheet_fail_count += 1
        log("SHEET_INIT_FAIL", e)
        order_sheet = None


init_sheet()

# ==================================================
# SAFE LINE REPLY
# ==================================================

def reply(token, msg):

    if not line_bot_api:
        log("LINE_DISABLED", msg)
        return

    try:
        line_bot_api.reply_message(
            token,
            TextSendMessage(text=msg)
        )
    except Exception as e:
        log("LINE_REPLY_FAIL", e)

# ==================================================
# NLP PARSER
# ==================================================

def parse(text):

    text = text.replace("還有", "+").replace("加上", "+")

    items = []

    for part in text.split("+"):

        qty = 1

        if "兩" in part or "2" in part:
            qty = 2

        product = "UNKNOWN"

        for p in ["奶茶", "紅茶", "蛋糕", "雞腿"]:
            if p in part:
                product = p

        items.append({
            "product": product,
            "qty": qty
        })

    return items

# ==================================================
# ORDER ENGINE
# ==================================================

def process_order(user_id, text):

    items = parse(text)

    if not order_sheet:
        log("ORDER_FAIL", "sheet offline")
        return "⚠ 系統忙碌，訂單已暫存失敗"

    order_id = str(uuid.uuid4())
    total = 0

    for i in items:

        if i["product"] == "UNKNOWN":
            return "❗ 未建立商品"

        price = 50
        subtotal = price * i["qty"]
        total += subtotal

        try:
            order_sheet.append_row([
                order_id,
                datetime.utcnow().isoformat(),
                user_id,
                i["product"],
                i["qty"],
                subtotal,
                total,
                "OK"
            ])
        except Exception as e:
            log("SHEET_WRITE_FAIL", e)

    return f"✔ 訂單成立 {order_id} / {total}"

# ==================================================
# INCIDENT / OPS
# ==================================================

def replay():

    return "\n".join([
        f"{e['time']} | {e['stage']} | {e['message']}"
        for e in REPLAY_BUFFER[-10:]
    ])

def incident_report():

    return "\n".join([
        f"{e['time']} | {e['stage']} | {e['message']}"
        for e in EVENT_LOG[-20:]
    ])

def heal():

    # bounded self-healing (NO LOOP)
    try:
        init_sheet()
        return "🧠 heal executed (single attempt)"
    except Exception as e:
        log("HEAL_FAIL", e)
        return "⚠ heal failed"

# ==================================================
# WEBHOOK
# ==================================================

@app.route("/callback", methods=["POST"])
def callback():

    try:
        body = request.get_json()

        if not body:
            return "OK"

        for event in body.get("events", []):

            # filter non-text
            if event.get("type") != "message":
                continue

            if event["message"].get("type") != "text":
                continue

            text = event["message"].get("text", "")
            user_id = event["source"].get("userId", "")
            token = event.get("replyToken")

            log("INPUT", text)

            # OPS COMMANDS
            if text == "/report":
                reply(token, incident_report())
                continue

            if text == "/incident":
                reply(token, replay())
                continue

            if text == "/heal":
                reply(token, heal())
                continue

            # ORDER FLOW
            result = process_order(user_id, text)
            reply(token, result)

    except Exception as e:
        log("WEBHOOK_FAIL", e)

    return "OK"

# ==================================================
# HEALTH CHECK
# ==================================================

@app.route("/")
def health():
    return "OK"

# ==================================================
# STARTUP LOG
# ==================================================

log("STARTUP", "system booted")

# ==================================================
# RUN
# ==================================================

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 10000)), debug=False)