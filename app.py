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
# APP INIT
# =========================
app = Flask(__name__)

LINE_TOKEN = os.getenv("LINE_CHANNEL_ACCESS_TOKEN")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
SHEET_ID = os.getenv("GOOGLE_SHEET_ID")

openai.api_key = OPENAI_API_KEY
line_bot_api = LineBotApi(LINE_TOKEN)

# =========================
# STATE
# =========================
task_queue = Queue()
worker_alive = True
last_heartbeat = time.time()

ai_calls = 0
ai_cost_estimate = 0.0

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
        w = sheet.add_worksheet(title=name, rows=5000, cols=len(cols))
        w.append_row(cols)
    return w

order_ws = ws("order", ["trace_id","order_id","time","user_id","name","product","qty","status"])
log_ws = ws("log", ["time","trace_id","stage","type","message"])
cost_ws = ws("ai_cost", ["time","trace_id","tokens","cost_est"])

# =========================
# LOG SYSTEM
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
        print("LOG FAIL:", e)

# =========================
# LINE REPLY
# =========================
def reply(token, msg):
    try:
        line_bot_api.reply_message(token, TextSendMessage(text=msg))
    except Exception as e:
        print("LINE FAIL:", e)

# =========================
# SIMPLE PARSER
# =========================
def parse(text):
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

# =========================
# AI PARSER (LIMITED)
# =========================
def ai_parse(text):
    global ai_calls, ai_cost_estimate

    if ai_calls >= 300:
        return None

    try:
        res = openai.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role":"user","content":text}]
        )

        ai_calls += 1

        cost = 0.0002  # rough estimate
        ai_cost_estimate += cost

        data = {
            "raw": res.choices[0].message.content
        }

        return data

    except Exception as e:
        print("AI FAIL:", e)
        return None

# =========================
# WRITE ORDER (SAFE)
# =========================
def write_order(trace_id, data, user_id):
    oid = "D" + datetime.now().strftime("%Y%m%d%H%M%S")

    try:
        for item in data["items"]:
            order_ws.append_row([
                trace_id,
                oid,
                datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                user_id,
                data.get("name",""),
                item.get("product",""),
                item.get("qty",0),
                "confirmed"
            ])

        log(trace_id, "SHEET", "OK", oid)

    except Exception as e:
        log(trace_id, "SHEET", "FAIL", str(e))

# =========================
# WORKER (IMMORTAL LOOP)
# =========================
def worker():
    global last_heartbeat, worker_alive

    print("🔥 WORKER STARTED")

    while True:
        try:
            task = task_queue.get()

            trace_id = task.get("trace_id","NO_TRACE")

            log(trace_id, "QUEUE", "RECEIVED", task)

            text = task["text"]
            user_id = task["user_id"]

            data = parse(text)

            if not data:
                data = ai_parse(text)

            if data:
                write_order(trace_id, data, user_id)
            else:
                log(trace_id, "ERROR", "PARSE_FAIL", text)

            last_heartbeat = time.time()
            worker_alive = True

        except Exception as e:
            print("WORKER CRASH:", e)
            worker_alive = False
            time.sleep(1)

        finally:
            try:
                task_queue.task_done()
            except:
                pass

# =========================
# WATCHDOG (AUTO REVIVE)
# =========================
def watchdog():
    global worker_alive

    while True:
        time.sleep(30)

        if time.time() - last_heartbeat > 60:
            print("🔥 REVIVING WORKER")
            threading.Thread(target=worker, daemon=True).start()
            worker_alive = True

# =========================
# START
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

        text = e["message"]["text"]

        log(trace_id, "WEBHOOK", "RECEIVED", text)

        reply(e["replyToken"], "✅ 已收到，處理中")

        task_queue.put({
            "trace_id": trace_id,
            "text": text,
            "user_id": e["source"]["userId"]
        })

    return "OK"

# =========================
# HEALTH
# =========================
@app.route("/health")
def health():
    return jsonify({
        "status":"ok",
        "queue":task_queue.qsize(),
        "ai_calls":ai_calls,
        "ai_cost_estimate":round(ai_cost_estimate,4),
        "worker_alive":worker_alive
    })

# =========================
# ROOT
# =========================
@app.route("/")
def home():
    return "v18 production stable running"

# =========================
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT",10000)))