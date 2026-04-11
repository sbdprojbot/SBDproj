from flask import Flask, request, jsonify
import os, json, re, uuid
from datetime import datetime, date

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

line_bot_api = LineBotApi(LINE_TOKEN)
openai.api_key = OPENAI_API_KEY

# =========================
# COST CONTROL
# =========================
DAILY_LIMIT = 0.03
TEST_LIMIT = 0.07
COST_MODE = os.getenv("COST_MODE", "prod")

cost_usage = {}

def today():
    return str(date.today())

def limit():
    return TEST_LIMIT if COST_MODE == "test" else DAILY_LIMIT

def add_cost(v):
    k = today()
    cost_usage[k] = cost_usage.get(k, 0) + v

def can_use_ai():
    return cost_usage.get(today(), 0) < limit()

# =========================
# CACHE (safe in-memory)
# =========================
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
        w = sheet.add_worksheet(title=name, rows=5000, cols=len(cols))
        w.append_row(cols)
    return w

order_ws = ws("order", ["trace_id","order_id","time","user_id","name","product","qty","status"])
log_ws = ws("log", ["time","trace_id","stage","type","message","ai_analysis"])

# =========================
# LOG
# =========================
def log(trace_id, stage, type_, msg, ai_analysis=""):
    row = [
        datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        trace_id,
        stage,
        type_,
        str(msg)[:500],
        ai_analysis
    ]
    print("LOG:", row)

    try:
        log_ws.append_row(row)
    except:
        pass

# =========================
# LINE REPLY SAFE
# =========================
def reply(token, msg):
    try:
        line_bot_api.reply_message(token, TextSendMessage(text=msg))
    except Exception as e:
        print("LINE FAIL:", e)

# =========================
# RULE PARSER (FAST PATH)
# =========================
def rule_parse(text):
    try:
        m = re.search(r"(.*)買(.*?)(\d+)", text)
        if not m:
            return None

        return {
            "name": m.group(1).strip(),
            "items": [{
                "product": m.group(2).strip(),
                "qty": int(m.group(3))
            }],
            "source": "rule"
        }
    except:
        return None

# =========================
# AI PARSER (HARDENED)
# =========================
def ai_parse(text):
    if not can_use_ai():
        return None

    # cache hit
    if text in ai_cache:
        return ai_cache[text]

    try:
        prompt = f"""
你是訂單解析系統，只能輸出 JSON。

格式：
{{
  "name": "string",
  "items": [
    {{"product": "string", "qty": number}}
  ]
}}

規則：
- 只能 JSON
- 不要 markdown
- 不要解釋

輸入：
{text}
"""

        res = openai.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role":"user","content":prompt}],
            temperature=0
        )

        add_cost(0.001)

        content = res.choices[0].message.content.strip()

        # safe json extract
        match = re.search(r"\{.*\}", content, re.S)
        if not match:
            raise ValueError("No JSON found")

        data = json.loads(match.group())

        # validation guard
        if "items" not in data:
            raise ValueError("Invalid schema")

        data["source"] = "ai"

        ai_cache[text] = data
        return data

    except Exception as e:
        log("NO_TRACE", "AI", "FAIL", str(e))
        return None

# =========================
# PARSER ENTRY
# =========================
def parse(text):
    data = rule_parse(text)
    if data:
        return data

    return ai_parse(text)

# =========================
# WRITE ORDER
# =========================
def write_order(trace_id, data, user_id):
    order_id = "D" + datetime.now().strftime("%Y%m%d%H%M%S")

    try:
        for item in data["items"]:
            order_ws.append_row([
                trace_id,
                order_id,
                datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                user_id,
                data.get("name",""),
                item.get("product",""),
                item.get("qty",0),
                "confirmed"
            ])

        log(trace_id, "SHEET", "OK", order_id)

    except Exception as e:
        log(trace_id, "SHEET", "FAIL", str(e))

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

        text = e["message"]["text"]
        user_id = e["source"]["userId"]

        trace_id = "T" + datetime.now().strftime("%Y%m%d%H%M%S") + "-" + str(uuid.uuid4())[:6]

        log(trace_id, "WEBHOOK", "RECEIVED", text)

        reply(e["replyToken"], "✅ 已收到，處理中")

        data = parse(text)

        if data:
            write_order(trace_id, data, user_id)
        else:
            log(trace_id, "ERROR", "PARSE_FAIL", text)

    return "OK"

# =========================
# HEALTH
# =========================
@app.route("/health")
def health():
    return jsonify({
        "status":"ok",
        "version":"v18.4",
        "cost_mode": COST_MODE,
        "today_cost": cost_usage.get(today(),0),
        "limit": limit(),
        "cache_size": len(ai_cache)
    })

# =========================
@app.route("/")
def home():
    return "v18.4 engineered stable AI system"

# =========================
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT",10000)))