from flask import Flask, request, abort
import re
import time
from datetime import datetime

app = Flask(__name__)

# =========================
# 🧠 STATE
# =========================

processed_events = set()

# =========================
# 🧠 UNIT ENGINE
# =========================

NUM_MAP = {
    "一":1,"壹":1,"1":1,
    "兩":2,"二":2,"2":2,
    "三":3,"3":3,
    "四":4,"4":4,
    "五":5,"5":5,
    "六":6,"6":6,
    "七":7,"7":7,
    "八":8,"8":8,
    "九":9,"9":9,
    "十":10,"10":10
}

UNIT_MAP = {
    "杯":"cup",
    "瓶":"bottle",
    "個":"piece",
    "份":"set"
}

NOISE = ["我要","幫我","請","來","買","給我"]

def clean(text):
    if not text:
        return ""
    for w in NOISE:
        text = text.replace(w,"")
    return text.strip()

def parse_unit(text):

    qty = 1
    unit = "piece"

    for k,v in NUM_MAP.items():
        if k in text:
            qty = v

    for k,v in UNIT_MAP.items():
        if k in text:
            unit = v

    m = re.search(r"\d+", text)
    if m:
        qty = int(m.group())

    return qty, unit

def unit_engine(text):

    text = clean(text)

    qty, unit = parse_unit(text)

    product = text

    for k in list(NUM_MAP.keys()) + list(UNIT_MAP.keys()):
        product = product.replace(k,"")

    product = product.strip()

    return {
        "product": product if product else "unknown",
        "qty": qty,
        "unit": unit
    }

# =========================
# 🧠 DEDUP
# =========================

def is_duplicate(event_id):
    if not event_id:
        return False
    if event_id in processed_events:
        return True
    processed_events.add(event_id)
    return False

# =========================
# 🧠 TIME
# =========================

def now():
    return int(time.time()), datetime.now().strftime("%Y-%m-%d %H:%M:%S")

# =========================
# 🧠 LOG (SAFE)
# =========================

def log_event(data):
    print("LOG:", data)

# =========================
# 🧠 CORE ENGINE
# =========================

def handle_text(text):

    parsed = unit_engine(text)

    return f"✔ 訂單成立：{parsed['product']} x{parsed['qty']} ({parsed['unit']})"

# =========================
# 📡 LINE CALLBACK
# =========================

@app.route("/callback", methods=["POST"])
def callback():

    try:
        body = request.get_json()

        print("RAW EVENT:", body)

        if not body:
            return "no body", 200

        events = body.get("events", [])

        if not events:
            return "no events", 200

        event = events[0]

        event_id = event.get("webhookEventId") or event.get("replyToken")

        if is_duplicate(event_id):
            return "duplicate", 200

        message = event.get("message", {})
        text = message.get("text", "")

        reply_token = event.get("replyToken")

        result = handle_text(text)

        log_event({
            "event_id": event_id,
            "text": text,
            "result": result
        })

        # 👉 LINE reply (safe fallback: 不會 crash)
        try:
            from linebot import LineBotApi
            from linebot.models import TextSendMessage

            CHANNEL_ACCESS_TOKEN = "YOUR_TOKEN_HERE"

            line_bot_api = LineBotApi(CHANNEL_ACCESS_TOKEN)

            line_bot_api.reply_message(
                reply_token,
                TextSendMessage(text=result)
            )

        except Exception as e:
            print("LINE REPLY ERROR:", e)

        return "OK", 200

    except Exception as e:
        print("FATAL ERROR:", e)
        return "error", 200

# =========================
# 🧪 HEALTH CHECK
# =========================

@app.route("/", methods=["GET"])
def home():
    return "LINE POS SYSTEM RUNNING", 200

# =========================
# 🚀 RUN
# =========================

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=10000)