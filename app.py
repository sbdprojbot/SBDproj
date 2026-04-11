from flask import Flask, request, jsonify
import os, json, re, time, threading
from datetime import datetime, date
from queue import Queue

import gspread
from oauth2client.service_account import ServiceAccountCredentials

from linebot import LineBotApi
from linebot.models import TextSendMessage

import openai

app = Flask(__name__)

# =========================
# ENV
# =========================
LINE_TOKEN = os.getenv("LINE_CHANNEL_ACCESS_TOKEN")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
SHEET_ID = os.getenv("GOOGLE_SHEET_ID")

openai.api_key = OPENAI_API_KEY
line_bot_api = LineBotApi(LINE_TOKEN)

# =========================
# COST CONTROL
# =========================
MAX_AI_CALLS = 300
ai_calls = 0

# =========================
# QUEUE (ASYNC CORE)
# =========================
task_queue = Queue()

# =========================
# CACHE
# =========================
ai_cache = {}
event_cache = set()

# =========================
# SHEET
# =========================
scope = [
    "https://spreadsheets.google.com/feeds",
    "https://www.googleapis.com/auth/drive"
]

creds = ServiceAccountCredentials.from_json_keyfile_dict(
    json.loads(os.getenv("GOOGLE_CREDS_JSON")),
    scope
)

gs = gspread.authorize(creds)
sheet = gs.open_by_key(SHEET_ID)

# =========================
# TIME
# =========================
def now():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")

# =========================
# FAST REPLY
# =========================
def safe_reply(token, text):
    try:
        line_bot_api.reply_message(token, TextSendMessage(text=text))
    except:
        pass

# =========================
# EVENT DEDUP
# =========================
def is_duplicate(event_id):
    if not event_id:
        return False
    if event_id in event_cache:
        return True
    event_cache.add(event_id)
    return False

# =========================
# AI PARSER
# =========================
def ai_parse(text):
    global ai_calls

    if ai_calls >= MAX_AI_CALLS:
        return None

    if text in ai_cache:
        return ai_cache[text]

    try:
        res = openai.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{
                "role": "user",
                "content": f"""
JSON only:
{text}
format:
{{"name":"","items":[{{"product":"","qty":1}}]}}
"""
            }],
            temperature=0
        )

        ai_calls += 1

        data = json.loads(re.search(r"\{.*\}", res.choices[0].message.content, re.S).group())

        ai_cache[text] = data
        return data

    except:
        return None

# =========================
# WORKER (ASYNC BACKGROUND)
# =========================
def worker():
    while True:
        task = task_queue.get()
        if task is None:
            continue

        try:
            user_id = task["user_id"]
            text = task["text"]

            data = ai_parse(text)

            if data:
                # write sheet
                ws = sheet.worksheet("order") if "order" in [w.title for w in sheet.worksheets()] else sheet.add_worksheet("order", 1000, 7)

                oid = "D" + datetime.now().strftime("%Y%m%d%H%M%S")

                for item in data["items"]:
                    ws.append_row([
                        oid,
                        now(),
                        user_id,
                        data.get("name","unknown"),
                        item["product"],
                        item["qty"],
                        "confirmed"
                    ])

        except Exception as e:
            pass

        task_queue.task_done()

# start worker
threading.Thread(target=worker, daemon=True).start()

# =========================
# WEBHOOK
# =========================
@app.route("/callback", methods=["POST"])
def callback():

    body = request.get_json()
    events = body.get("events", [])

    for e in events:

        try:
            if e["type"] != "message":
                continue

            event_id = e.get("webhookEventId")
            if is_duplicate(event_id):
                continue

            user_id = e["source"]["userId"]
            text = e["message"]["text"]

            # =========================
            # FAST REPLY (IMPORTANT)
            # =========================
            safe_reply(e["replyToken"], "✅ 已收到，處理中")

            # =========================
            # PUSH TO QUEUE
            # =========================
            task_queue.put({
                "user_id": user_id,
                "text": text
            })

        except:
            pass

    return "OK"

# =========================
# HEALTH
# =========================
@app.route("/health")
def health():
    return jsonify({
        "status": "ok",
        "queue_size": task_queue.qsize(),
        "ai_calls": ai_calls,
        "cache_size": len(ai_cache),
        "time": now()
    })

# =========================
# RUN
# =========================
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", 10000)))