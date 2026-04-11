from flask import Flask, request, jsonify
import os, json, re, threading, time, uuid
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
# STATE
# =========================
task_queue = Queue()
trace_store = {}

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

def ws(name, cols):
    try:
        w = sheet.worksheet(name)
    except:
        w = sheet.add_worksheet(title=name, rows=2000, cols=len(cols))
        w.append_row(cols)
    return w

order_ws = ws("order", ["trace_id","order_id","time","user_id","name","product","qty","status"])
log_ws = ws("log", ["time","trace_id","stage","type","message"])

# =========================
# TRACE LOG
# =========================
def log(trace_id, stage, type_, msg):
    entry = {
        "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "trace_id": trace_id,
        "stage": stage,
        "type": type_,
        "msg": str(msg)[:500]
    }

    trace_store.setdefault(trace_id, []).append(entry)

    try:
        log_ws.append_row([
            entry["time"],
            trace_id,
            stage,
            type_,
            entry["msg"]
        ])
    except:
        print("LOG FAIL", entry)

# =========================
# REPLY
# =========================
def reply(token, msg):
    try:
        line_bot_api.reply_message(token, TextSendMessage(text=msg))
    except Exception as e:
        print("REPLY FAIL", e)

# =========================
# PARSER
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
    except:
        return None

# =========================
# AI PARSER
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
                "content": f"JSON only:\n{text}"
            }]
        )

        ai_calls += 1

        data = json.loads(
            re.search(r"\{.*\}", res.choices[0].message.content, re.S).group()
        )

        ai_cache[text] = data
        return data

    except:
        return None

# =========================
# WRITE ORDER
# =========================
def write_order(trace_id, data, user_id):
    try:
        oid = "D" + datetime.now().strftime("%Y%m%d%H%M%S")

        log(trace_id, "SHEET", "WRITE_START", oid)

        for item in data["items"]:
            row = [
                trace_id,
                oid,
                datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                user_id,
                data.get("name",""),
                item["product"],
                item["qty"],
                "confirmed"
            ]

            order_ws.append_row(row)

        log(trace_id, "SHEET", "WRITE_OK", oid)

    except Exception as e:
        log(trace_id, "SHEET", "WRITE_FAIL", e)

# =========================
# WORKER
# =========================
def worker():
    print("🔥 WORKER STARTED")

    while True:
        task = task_queue.get()

        trace_id = task["trace_id"]

        try:
            log(trace_id, "QUEUE", "RECEIVED", task)

            text = task["text"]
            user_id = task["user_id"]

            log(trace_id, "PARSER", "START", text)

            data = parse(text)

            if data:
                log(trace_id, "PARSER", "OK", data)
            else:
                log(trace_id, "PARSER", "FALLBACK_AI", text)
                data = ai_parse(text)

            if data:
                write_order(trace_id, data, user_id)
            else:
                log(trace_id, "ERROR", "PARSE_EMPTY", text)

        except Exception as e:
            log(trace_id, "WORKER", "CRASH", e)

        finally:
            task_queue.task_done()

# =========================
# START WORKER
# =========================
def start_worker():
    t = threading.Thread(target=worker)
    t.daemon = False
    t.start()

start_worker()

# =========================
# WEBHOOK
# =========================
@app.route("/callback", methods=["POST"])
def callback():

    body = request.get_json()
    events = body.get("events", [])

    for e in events:

        if e["type"] != "message":
            continue

        trace_id = "T" + datetime.now().strftime("%Y%m%d%H%M%S") + "-" + str(uuid.uuid4())[:6]

        log(trace_id, "WEBHOOK", "RECEIVED", e["message"]["text"])

        reply(e["replyToken"], "✅ 已收到，處理中")

        task_queue.put({
            "trace_id": trace_id,
            "text": e["message"]["text"],
            "user_id": e["source"]["userId"]
        })

    return "OK"

# =========================
# DEBUG VIEW
# =========================
@app.route("/debug/<trace_id>")
def debug(trace_id):
    return jsonify(trace_store.get(trace_id, []))

# =========================
# HEALTH
# =========================
@app.route("/health")
def health():
    return jsonify({
        "status": "ok",
        "queue": task_queue.qsize(),
        "ai_calls": ai_calls
    })

# =========================
# ROOT
# =========================
@app.route("/")
def home():
    return "v17.6 debug console running"

# =========================
# RUN
# =========================
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", 10000)))