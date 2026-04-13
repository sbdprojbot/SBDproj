import os
import json
import uuid
from datetime import datetime

from flask import Flask, request

from linebot import LineBotApi, WebhookHandler
from linebot.models import TextSendMessage

import gspread
from oauth2client.service_account import ServiceAccountCredentials

# =========================
# APP INIT
# =========================

app = Flask(__name__)

# =========================
# ENV SAFE LOAD
# =========================

LINE_CHANNEL_ACCESS_TOKEN = os.getenv("LINE_CHANNEL_ACCESS_TOKEN", "")
LINE_CHANNEL_SECRET = os.getenv("LINE_CHANNEL_SECRET", "")

line_bot_api = LineBotApi(LINE_CHANNEL_ACCESS_TOKEN)
handler = WebhookHandler(LINE_CHANNEL_SECRET)

# =========================
# GOOGLE SHEET AUTH (RENDER SAFE)
# =========================

scope = [
    "https://spreadsheets.google.com/feeds",
    "https://www.googleapis.com/auth/drive"
]

creds_json = json.loads(os.environ["GOOGLE_CREDENTIALS"])

creds = ServiceAccountCredentials.from_json_keyfile_dict(
    creds_json,
    scope
)

client = gspread.authorize(creds)

product_sheet = client.open("pos").worksheet("Product")
user_sheet = client.open("pos").worksheet("User")
order_sheet = client.open("pos").worksheet("Order")
log_sheet = client.open("pos").worksheet("Log")
replay_sheet = client.open("pos").worksheet("Replay")

# =========================
# LOG SYSTEM
# =========================

def log_error(stage, message):
    try:
        log_sheet.append_row([
            str(uuid.uuid4()),
            datetime.utcnow().isoformat(),
            stage,
            "ERROR",
            message,
            ""
        ])
    except:
        pass

# =========================
# SAFE REPLY (CRITICAL FIX)
# =========================

def safe_reply(reply_token, text):

    try:
        line_bot_api.reply_message(
            reply_token,
            TextSendMessage(text=text)
        )

    except Exception as e:
        log_error("LINE_REPLY_FAILED", str(e))

        # store replay event
        try:
            replay_sheet.append_row([
                str(uuid.uuid4()),
                datetime.utcnow().isoformat(),
                json.dumps({"reply_token": reply_token, "text": text}),
                "LINE_REPLY",
                "PENDING",
                0,
                str(e),
                datetime.utcnow().isoformat()
            ])
        except:
            pass

# =========================
# NLP / ORDER ENGINE
# =========================

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

# =========================
# ORDER PROCESSOR
# =========================

def process_order(user_id, text):

    items = parse_multi_item(text)

    if not items:
        return "⚠ 無法解析訂單"

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

# =========================
# OPS COMMANDS
# =========================

def ops(text):

    if text == "/status":
        return "🟢 SYSTEM OK"

    if text == "/help":
        return "/status /help"

    return None

# =========================
# WEBHOOK
# =========================

@app.route("/callback", methods=["POST"])
def callback():

    try:
        body = request.get_json()

        for event in body["events"]:

            if event["type"] != "message":
                continue

            text = event["message"]["text"]
            user_id = event["source"]["userId"]
            reply_token = event["replyToken"]

            ops_result = ops(text)

            if ops_result:
                safe_reply(reply_token, ops_result)
                return "OK"

            result = process_order(user_id, text)

            safe_reply(reply_token, result)

    except Exception as e:
        log_error("CALLBACK_FATAL", str(e))

    return "OK"

# =========================
# HEALTH CHECK
# =========================

@app.route("/")
def health():
    return "OK"

# =========================
# RUN
# =========================

if __name__ == "__main__":
    app.run()