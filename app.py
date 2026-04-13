import os
import json
import uuid
from datetime import datetime

from flask import Flask, request

from linebot import LineBotApi
from linebot.models import TextSendMessage

import gspread
from oauth2client.service_account import ServiceAccountCredentials

# =========================================================
# APP INIT
# =========================================================

app = Flask(__name__)

# =========================================================
# ENV SAFE LOAD (NO CRASH)
# =========================================================

LINE_CHANNEL_ACCESS_TOKEN = os.getenv("LINE_CHANNEL_ACCESS_TOKEN", "")
LINE_CHANNEL_SECRET = os.getenv("LINE_CHANNEL_SECRET", "")
GOOGLE_CREDENTIALS = os.getenv("GOOGLE_CREDENTIALS", "")

line_bot_api = LineBotApi(LINE_CHANNEL_ACCESS_TOKEN)

# =========================================================
# OPS MEMORY (IN-MEMORY INCIDENT BUFFER)
# =========================================================

OPS_LOG_BUFFER = []

def push_incident(stage, message):
    OPS_LOG_BUFFER.append({
        "time": datetime.utcnow().isoformat(),
        "stage": stage,
        "message": message
    })

    if len(OPS_LOG_BUFFER) > 50:
        OPS_LOG_BUFFER.pop(0)

# =========================================================
# LOGGING
# =========================================================

def log_error(stage, message):
    print(f"[{stage}] {message}")
    push_incident(stage, message)

# =========================================================
# GOOGLE SHEET INIT (SAFE)
# =========================================================

client = None
order_sheet = None

try:
    if GOOGLE_CREDENTIALS:

        scope = [
            "https://spreadsheets.google.com/feeds",
            "https://www.googleapis.com/auth/drive"
        ]

        creds_json = json.loads(GOOGLE_CREDENTIALS)

        creds = ServiceAccountCredentials.from_json_keyfile_dict(
            creds_json,
            scope
        )

        client = gspread.authorize(creds)
        sheet = client.open("pos")

        order_sheet = sheet.worksheet("Order")

    else:
        log_error("INIT", "GOOGLE_CREDENTIALS missing")

except Exception as e:
    log_error("SHEET_INIT_ERROR", str(e))

# =========================================================
# SAFE LINE REPLY
# =========================================================

def safe_reply(reply_token, text):

    try:
        line_bot_api.reply_message(
            reply_token,
            TextSendMessage(text=text)
        )

    except Exception as e:
        log_error("LINE_REPLY_FAILED", str(e))

# =========================================================
# NLP ENGINE (LIGHT RULE BASED)
# =========================================================

def normalize_product(text):

    products = ["奶茶", "紅茶", "蛋糕", "雞腿"]

    for p in products:
        if p in text:
            return p

    return "UNKNOWN"


def extract_item(text):

    qty = 1

    if "兩" in text or "2" in text:
        qty = 2

    unit = "unit"

    if "杯" in text:
        unit = "cup"
    elif "份" in text:
        unit = "set"

    return {
        "product": normalize_product(text),
        "qty": qty,
        "unit": unit
    }


def parse_multi_item(text):

    text = text.replace("還有", "+").replace("加上", "+")
    parts = text.split("+")

    return [extract_item(p) for p in parts if p.strip()]

# =========================================================
# ORDER ENGINE
# =========================================================

def process_order(user_id, text):

    items = parse_multi_item(text)

    if not items:
        return "⚠ 無法解析訂單"

    if order_sheet is None:
        return "⚠ Sheet 未初始化"

    order_id = str(uuid.uuid4())
    total = 0

    for item in items:

        if item["product"] == "UNKNOWN":
            return "❗ 未建立商品，請先建立商品"

        price = 50
        subtotal = price * item["qty"]
        total += subtotal

        try:
            order_sheet.append_row([
                order_id,
                datetime.utcnow().isoformat(),
                datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S"),
                user_id,
                "user",
                "p001",
                item["product"],
                item["qty"],
                price,
                subtotal,
                total,
                "SUCCESS"
            ])

        except Exception as e:
            log_error("SHEET_WRITE_FAILED", str(e))

    return f"✔ 訂單成立：{order_id} 總額 {total}"

# =========================================================
# OPS COMMANDS
# =========================================================

def cmd_report():

    return f"""
📊 SYSTEM REPORT
━━━━━━━━━━
🧾 incidents: {len(OPS_LOG_BUFFER)}
🟡 mode: bounded-autonomy
🧠 ops: active
"""


def cmd_incident():

    if not OPS_LOG_BUFFER:
        return "🟢 No incidents"

    msg = "🧠 INCIDENTS\n━━━━━━━━━━\n"

    for i in OPS_LOG_BUFFER[-10:]:
        msg += f"- [{i['stage']}] {i['message']}\n"

    return msg


def cmd_heal():
    return "🧠 Self-healing triggered (bounded mode)"

def ops_router(text):

    if text == "/report":
        return cmd_report()

    if text == "/incident":
        return cmd_incident()

    if text == "/heal":
        return cmd_heal()

    return None

# =========================================================
# WEBHOOK
# =========================================================

@app.route("/callback", methods=["POST"])
def callback():

    try:
        body = request.get_json()

        for event in body.get("events", []):

            if event.get("type") != "message":
                continue

            text = event["message"]["text"]
            user_id = event["source"]["userId"]
            reply_token = event["replyToken"]

            # OPS FIRST
            ops_result = ops_router(text)

            if ops_result:
                safe_reply(reply_token, ops_result)
                return "OK"

            # ORDER FLOW
            result = process_order(user_id, text)

            safe_reply(reply_token, result)

    except Exception as e:
        log_error("CALLBACK_FATAL", str(e))

    return "OK"

# =========================================================
# HEALTH CHECK
# =========================================================

@app.route("/")
def health():
    return "OK"

# =========================================================
# START
# =========================================================

if __name__ == "__main__":
    app.run()