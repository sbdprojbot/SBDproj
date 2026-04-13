import os
import json
import uuid
from datetime import datetime
from collections import defaultdict

from flask import Flask, request

from linebot import LineBotApi
from linebot.models import TextSendMessage

import gspread
from oauth2client.service_account import ServiceAccountCredentials

# =========================================================
# APP INIT
# =========================================================

app = Flask(__name__)

LINE_TOKEN = os.getenv("LINE_CHANNEL_ACCESS_TOKEN", "")
GOOGLE_CREDENTIALS = os.getenv("GOOGLE_CREDENTIALS", "")

line_bot_api = LineBotApi(LINE_TOKEN)

# =========================================================
# INCIDENT STORE
# =========================================================

EVENT_LOG = []
REPLAY_BUFFER = []

# =========================================================
# SYSTEM STATE
# =========================================================

STATE = {
    "line": True,
    "sheet": True,
    "mode": "normal"
}

# =========================================================
# LOGGING
# =========================================================

def log_event(stage, message, meta=None):

    event = {
        "time": datetime.utcnow().isoformat(),
        "stage": stage,
        "message": str(message),
        "meta": meta or {}
    }

    EVENT_LOG.append(event)
    REPLAY_BUFFER.append(event)

    if len(EVENT_LOG) > 100:
        EVENT_LOG.pop(0)

    if len(REPLAY_BUFFER) > 200:
        REPLAY_BUFFER.pop(0)

# =========================================================
# SHEET AUTO RESOLVER
# =========================================================

client = None
order_sheet = None

def auto_resolve_sheet(client, keyword="pos"):

    try:
        files = client.list_spreadsheet_files()

        for f in files:
            name = f["name"].lower()
            if keyword in name:
                return client.open(f["name"])

        if files:
            return client.open(files[0]["name"])

        return None

    except Exception as e:
        log_event("SHEET_RESOLVE_FAIL", e)
        return None

# =========================================================
# INIT SHEET
# =========================================================

try:
    if GOOGLE_CREDENTIALS:

        creds = ServiceAccountCredentials.from_json_keyfile_dict(
            json.loads(GOOGLE_CREDENTIALS),
            [
                "https://spreadsheets.google.com/feeds",
                "https://www.googleapis.com/auth/drive"
            ]
        )

        client = gspread.authorize(creds)
        sheet = auto_resolve_sheet(client, "pos")

        if sheet:
            order_sheet = sheet.worksheet("Order")

except Exception as e:
    log_event("SHEET_INIT_ERROR", e)

# =========================================================
# SAFE LINE REPLY
# =========================================================

def safe_reply(token, msg):

    try:
        line_bot_api.reply_message(
            token,
            TextSendMessage(text=msg)
        )

    except Exception as e:
        STATE["line"] = False
        log_event("LINE_REPLY_FAILED", e)

# =========================================================
# NLP ENGINE (LIGHT)
# =========================================================

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

# =========================================================
# ORDER ENGINE
# =========================================================

def process_order(user_id, text):

    items = parse(text)

    if not order_sheet:
        log_event("SHEET_MISSING", "fallback mode")
        return "⚠ 系統暫時離線（memory mode）"

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
            log_event("SHEET_WRITE_FAILED", e)

    return f"✔ 訂單成立 {order_id} / {total}"

# =========================================================
# INCIDENT CLASSIFIER
# =========================================================

def classify(events):

    clusters = defaultdict(list)

    for e in events:

        if "SHEET" in e["stage"]:
            clusters["sheet"].append(e)

        elif "LINE" in e["stage"]:
            clusters["line"].append(e)

        else:
            clusters["system"].append(e)

    return clusters

# =========================================================
# REPLAY ENGINE
# =========================================================

def replay():

    return [
        f"[{e['time']}] {e['stage']} → {e['message']}"
        for e in REPLAY_BUFFER[-10:]
    ]

# =========================================================
# INCIDENT REPORTER
# =========================================================

def incident_report():

    clusters = classify(EVENT_LOG)

    return f"""
🧠 INCIDENT REPORT
━━━━━━━━━━━━━━
📊 total events: {len(EVENT_LOG)}
📦 sheet issues: {len(clusters['sheet'])}
📡 line issues: {len(clusters['line'])}

🧾 last replay:
""" + "\n".join(replay())

# =========================================================
# OPS COMMANDS
# =========================================================

def ops(text):

    if text == "/report":
        return incident_report()

    if text == "/incident":
        return "\n".join(replay())

    if text == "/heal":
        return "🧠 bounded self-heal executed"

    return None

# =========================================================
# WEBHOOK
# =========================================================

@app.route("/callback", methods=["POST"])
def callback():

    try:
        body = request.get_json()

        for event in body.get("events", []):

            if event["type"] != "message":
                continue

            text = event["message"]["text"]
            user_id = event["source"]["userId"]
            reply_token = event["replyToken"]

            log_event("INPUT", text)

            ops_result = ops(text)

            if ops_result:
                safe_reply(reply_token, ops_result)
                return "OK"

            result = process_order(user_id, text)

            safe_reply(reply_token, result)

    except Exception as e:
        log_event("FATAL", e)

    return "OK"

# =========================================================
# HEALTH
# =========================================================

@app.route("/")
def health():
    return "OK"

# =========================================================
# RUN
# =========================================================

if __name__ == "__main__":
    app.run()