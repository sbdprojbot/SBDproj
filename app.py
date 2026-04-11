from flask import Flask, request, jsonify
import os, json, re, uuid
from datetime import datetime, date

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

line_bot_api = LineBotApi(LINE_TOKEN)
openai.api_key = OPENAI_API_KEY

# =========================
# COST CONTROL
# =========================
DAILY_LIMIT = 0.03
cost_map = {}

def today():
    return str(date.today())

def get_cost():
    return cost_map.get(today(), 0)

def add_cost(c=0.001):
    cost_map[today()] = get_cost() + c

def can_use_ai():
    return get_cost() < DAILY_LIMIT

# =========================
# TRACE ID
# =========================
def trace_id():
    return "T" + datetime.now().strftime("%Y%m%d%H%M%S") + "-" + uuid.uuid4().hex[:6]

# =========================
# DEDUP (重點修復)
# =========================
processed_messages = set()

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

def ws(name, cols):
    try:
        w = sheet.worksheet(name)
    except:
        w = sheet.add_worksheet(title=name, rows=5000, cols=len(cols))
        w.append_row(cols)
    return w

user_ws = ws("user", ["user_id","name","phone","address","time"])
product_ws = ws("product", ["product_id","product","price","status","time"])
order_ws = ws("order", ["order_id","user","product","qty","price","total","status","time"])
log_ws = ws("log", ["time","trace","stage","type","msg","ai_diag","cost"])

# =========================
# LOG
# =========================
def log(tr, stage, type_, msg, diag="", cost=0):
    try:
        log_ws.append_row([
            datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            tr,
            stage,
            type_,
            str(msg)[:300],
            diag,
            cost
        ])
    except:
        print("LOG FAIL")

# =========================
# JSON PARSER
# =========================
def extract_json(text):
    try:
        s = text.find("{")
        e = text.rfind("}") + 1
        return json.loads(text[s:e])
    except:
        return None

# =========================
# RULE PARSER
# =========================
def rule_parse(text):

    if "買" in text:
        m = re.match(r"(.+?)買(.+)", text)
        if m:
            user = m.group(1)
            items = re.findall(r"([\u4e00-\u9fa5A-Za-z]+)(\d+)", m.group(2))

            return {
                "action":"order_multi",
                "data":{
                    "user":user,
                    "items":[{"product":i[0],"qty":int(i[1])} for i in items]
                }
            }

    return None

# =========================
# AI PARSE
# =========================
ALLOWED_ACTIONS = {
    "create_user",
    "create_product",
    "order_multi",
    "update",
    "delete"
}

def ai_parse(text, tr):

    if not can_use_ai():
        return None, "AI_BLOCKED"

    try:
        res = openai.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role":"system","content":"""
你是企業級訂單解析器。

只允許 action：
create_user, create_product, order_multi, update, delete

禁止任何其他 action。

輸出 JSON：
{
  "ok": true,
  "action": "",
  "data": {},
  "confidence": 0-1,
  "reason": "",
  "suggestion": ""
}
"""},
                {"role":"user","content":text}
            ],
            temperature=0
        )

        add_cost()

        raw = res.choices[0].message.content.strip()
        log(tr,"AI","RAW",raw,"AI_OK",get_cost())

        data = extract_json(raw)
        return data, "AI_OK"

    except Exception as e:
        return None, f"AI_ERROR:{str(e)}"

# =========================
# HELPERS
# =========================
def gen_id(prefix):
    return prefix + uuid.uuid4().hex[:6]

def get_price(p):
    rows = product_ws.get_all_records()
    for r in rows:
        if r["product"] == p:
            return int(r["price"])
    return 0

# =========================
# HANDLE
# =========================
def handle(data, tr):

    if not data:
        return "⚠️ 無法識別"

    action = data.get("action")
    d = data.get("data", {})

    # 🔴 AI 防火牆（關鍵）
    if action not in ALLOWED_ACTIONS:
        log(tr,"AI","BLOCK_ACTION",data,"INVALID_ACTION")
        return "⚠️ 不支援的操作"

    # CREATE PRODUCT
    if action == "create_product":
        pid = gen_id("p")

        product_ws.append_row([
            pid,
            d.get("product"),
            d.get("price"),
            "active",
            datetime.now().strftime("%H:%M")
        ])

        return f"📦 OK {pid}"

    # CREATE USER
    if action == "create_user":
        uid = gen_id("u")

        user_ws.append_row([
            uid,
            d.get("name"),
            d.get("phone"),
            d.get("address"),
            datetime.now().strftime("%H:%M")
        ])

        return f"👤 OK {uid}"

    # ORDER
    if action == "order_multi":
        oid = gen_id("d")
        total = 0

        for i in d.get("items", []):
            p = get_price(i["product"])
            total += p * i["qty"]

            order_ws.append_row([
                oid,
                d.get("user"),
                i["product"],
                i["qty"],
                p,
                p * i["qty"],
                "pending",
                datetime.now().strftime("%H:%M")
            ])

        return f"🧾 OK {oid}"

    return "⚠️ unknown"

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

        mid = e["message"]["id"]

        # 🔴 去重（防 LINE retry 爆炸）
        if mid in processed_messages:
            return "OK"

        processed_messages.add(mid)

        text = e["message"]["text"]
        tr = trace_id()

        log(tr,"WEBHOOK","RECV",text)

        data = rule_parse(text)
        diag = "RULE"

        if not data:
            data, diag = ai_parse(text, tr)

            if not data:
                result = "⚠️ AI解析失敗"

            elif data.get("ok") is False:
                result = f"⚠️ {data.get('reason','')}\n💡 {data.get('suggestion','')}"

            else:
                result = handle(data, tr)

        else:
            result = handle(data, tr)

        log(tr,"RESULT","DONE",result,diag,get_cost())

        try:
            line_bot_api.reply_message(
                e["replyToken"],
                TextSendMessage(text=result)
            )
        except Exception as ex:
            log(tr,"LINE","FAIL",str(ex))

    return "OK"

# =========================
@app.route("/health")
def health():
    return jsonify({
        "status":"ok",
        "version":"v20.4.2",
        "cost":get_cost(),
        "limit":DAILY_LIMIT
    })

@app.route("/")
def home():
    return "v20.4.2 running"