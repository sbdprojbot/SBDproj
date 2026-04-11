from flask import Flask, request, jsonify
import os, json, re, threading, time, uuid
from datetime import datetime
from queue import Queue, Empty

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

worker_alive = True
last_heartbeat = time.time()

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
# SAFE LOG
# =========================
def log(trace_id, stage, type_, msg):
    row = [
        datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        trace_id,
        stage,
        type_,
        str(msg)[:500]
    ]

    print("LOG:", row)

    try:
        log_ws.append_row(row)
    except Exception as e:
        print("🔥 LOG WRITE FAIL:", e)

# =========================
# LINE REPLY
# =========================
def reply(token, msg):
    try:
        line_bot_api.reply_message(token, TextSendMessage(text=msg))
    except Exception as e:
        print("LINE REPLY FAIL:", e)

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
    except:
        pass
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

    except Exception as e:
        print("AI FAIL:", e)
        return None

# =========================
# WRITE ORDER (HARDENED)
# =========================
def write_order(trace_id, data, user_id):
    oid = "D" + datetime.now().strftime("%Y%m%d%H%M%S")

    for i in range(3):  # retry 3 times
        try:
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
            return

        except Exception as e:
            print(f"WRITE RETRY {i}", e)
            time.sleep(1)

    log(trace_id, "SHEET", "WRITE_FAIL", "RETRY_EXHAUSTED")

# =========================
# WORKER
# =========================
def worker():
    global last_heartbeat

    print("🔥 WORKER STARTED")

    while True:
        try:
            task = task_queue.get(timeout=5)
        except Empty:
            last_heartbeat = time.time()
            continue

        trace_id = task.get("trace_id", "NO_TRACE")

        try:
            log(trace_id, "QUEUE", "RECEIVED", task)

            text = task["text"]
            user_id = task["user_id"]

            data = parse(text)
            if not data:
                data = ai_parse(text)

            if data:
                write_order(trace_id, data, user_id)
            else:
                log(trace_id, "ERROR", "PARSE_EMPTY", text)

        except Exception as e:
            log(trace_id, "WORKER", "CRASH", str(e))

        finally:
            task_queue.task_done()
            last_heartbeat = time.time()

# =========================
# WATCHDOG
# =========================
def watchdog():
    global worker_alive, last_heartbeat

    while True:
        time.sleep(30)

        if time.time() - last_heartbeat > 60:
            print("🔥 WORKER STUCK DETECTED")
            worker_alive = False

# =========================
# START SYSTEM
# =========================
threading.Thread(target=worker, daemon=True).start()
threading.Thread(target=watchdog, daemon=True).start()

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
# HEALTH
# =========================
@app.route("/health")
def health():
    return jsonify({
        "status": "ok",
        "queue": task_queue.qsize(),
        "ai_calls": ai_calls,
        "worker_alive": worker_alive
    })

# =========================
# ROOT
# =========================
@app.route("/")
def home():
    return "v17.7 production hardened running"

# =========================
# RUN
# =========================
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", 10000)))