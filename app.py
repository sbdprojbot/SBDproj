from flask import Flask, request, jsonify
import os, json, re, threading, time
from datetime import datetime
from queue import Queue

import gspread
from oauth2client.service_account import ServiceAccountCredentials

from linebot import LineBotApi
from linebot.models import TextSendMessage

import openai

# =========================
# APP
# =========================
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
# CORE STATE
# =========================
task_queue = Queue()
ai_calls = 0
ai_cache = {}

# =========================
# SHEET INIT
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
# SHEETS SAFE INIT
# =========================
def get_ws(name, cols):
    try:
        ws = sheet.worksheet(name)
    except:
        ws = sheet.add_worksheet(title=name, rows=2000, cols=len(cols))
        ws.append_row(cols)
    return ws

order_ws = get_ws("order", ["order_id","time","user_id","name","product","qty","status"])
log_ws = get_ws("log", ["time","type","message"])

# =========================
# LOGGING
# =========================
def log(type_, msg):
    try:
        log_ws.append_row([
            datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            type_,
            str(msg)[:500]
        ])
    except:
        print("LOG FAIL:", msg)

# =========================
# LINE REPLY
# =========================
def reply(token, msg):
    try:
        line_bot_api.reply_message(token, TextSendMessage(text=msg))
    except Exception as e:
        log("LINE_REPLY_FAIL", e)

# =========================
# SIMPLE PARSER
# =========================
def parse(text):
    try:
        m = re.search(r"(.*)買(.*?)(\d+)", text)
        if m:
            return {
                "name": m.group(1),
                "items": [{
                    "product": m.group(2),
                    "qty": int(m.group(3))
                }]
            }
        return None
    except Exception as e:
        log("PARSE_FAIL", e)
        return None

# =========================
# AI PARSER (LIMITED)
# =========================
def ai_parse(text):
    global ai_calls

    if ai_calls > 300:
        return None

    if text in ai_cache:
        return ai_cache[text]

    try:
        res = openai.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{
                "role": "user",
                "content": f"""
Return JSON only:
{text}
format:
{{"name":"","items":[{{"product":"","qty":1}}]}}
"""
            }],
            temperature=0
        )

        ai_calls += 1

        data = json.loads(
            re.search(r"\{.*\}", res.choices[0].message.content, re.S).group()
        )

        ai_cache[text] = data
        return data

    except Exception as e:
        log("AI_PARSE_FAIL", e)
        return None

# =========================
# WRITE ORDER (SAFE)
# =========================
def write_order(data, user_id):
    try:
        oid = "D" + datetime.now().strftime("%Y%m%d%H%M%S")

        for item in data["items"]:
            order_ws.append_row([
                oid,
                datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                user_id,
                data.get("name",""),
                item["product"],
                item["qty"],
                "confirmed"
            ])

        log("ORDER_WRITE", oid)

    except Exception as e:
        log("SHEET_WRITE_FAIL", e)

# =========================
# WORKER (CRITICAL FIX)
# =========================
def worker():
    log("WORKER", "STARTED")

    while True:
        try:
            task = task_queue.get()

            log("QUEUE", task)

            text = task["text"]
            user_id = task["user_id"]

            data = parse(text)
            if not data:
                data = ai_parse(text)

            if data:
                write_order(data, user_id)
            else:
                log("PARSE_EMPTY", text)

        except Exception as e:
            log("WORKER_FAIL", e)

        finally:
            task_queue.task_done()

# =========================
# START WORKER (FOR RENDER SAFE)
# =========================
def start_worker():
    t = threading.Thread(target=worker)
    t.daemon = False
    t.start()
    log("SYSTEM", "WORKER BOOTED")

start_worker()

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

            reply(e["replyToken"], "✅ 已收到，處理中")

            task_queue.put({
                "text": e["message"]["text"],
                "user_id": e["source"]["userId"]
            })

            log("WEBHOOK", e["message"]["text"])

        except Exception as ex:
            log("WEBHOOK_FAIL", ex)

    return "OK"

# =========================
# HEALTH
# =========================
@app.route("/health")
def health():
    return jsonify({
        "status": "ok",
        "queue": task_queue.qsize(),
        "ai_calls": ai_calls,
        "cache": len(ai_cache)
    })

# =========================
# ROOT
# =========================
@app.route("/")
def home():
    return "LINE BOT OK"

# =========================
# RUN
# =========================
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", 10000)))