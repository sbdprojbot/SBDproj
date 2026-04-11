from flask import Flask, request, jsonify
import os, json, re
from datetime import datetime, date
import uuid

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
    if get_cost() >= DAILY_LIMIT:
        return False
    return True

# =========================
# TRACE SYSTEM
# =========================
def trace_id():
    return "T" + datetime.now().strftime("%Y%m%d%H%M%S") + "-" + uuid.uuid4().hex[:6]

# =========================
# GOOGLE SHEET
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
# DUP PREVENT
# =========================
seen = set()

def dedupe(mid):
    if mid in seen:
        return True
    seen.add(mid)
    return False

# =========================
# SHEET FIND SAFE
# =========================
def find_order_row(order_id):
    values = order_ws.get_all_values()
    for i, row in enumerate(values):
        if i == 0:
            continue
        if row[0] == order_id:
            return i+1, row
    return None, None

# =========================
# PRICE
# =========================
def price(product):
    rows = product_ws.get_all_records()
    for r in rows:
        if r["product"] == product:
            return int(r.get("price",0))
    return 0

# =========================
# MULTI ORDER PARSER
# =========================
def parse_order(text):
    m = re.match(r"(.+?)買(.+)", text)
    if not m:
        return None

    user = m.group(1)
    items = re.findall(r"([\u4e00-\u9fa5A-Za-z]+)(\d+)", m.group(2))

    if not items:
        return None

    return {
        "action":"order_multi",
        "user":user,
        "items":[{"p":i[0],"q":int(i[1])} for i in items]
    }

# =========================
# RULE PARSER
# =========================
def rule_parse(text):

    r = parse_order(text)
    if r:
        return r

    if "改" in text:
        m = re.search(r"(d\d+).*(\d+)", text)
        if m:
            return {"action":"update","id":m.group(1),"qty":int(m.group(2))}

    if "刪" in text:
        m = re.search(r"(d\d+)", text)
        if m:
            return {"action":"delete","id":m.group(1)}

    if "確認" in text:
        m = re.search(r"(d\d+)", text)
        if m:
            return {"action":"confirm","id":m.group(1)}

    return None

# =========================
# AI FALLBACK
# =========================
def ai_parse(text, tr):

    if not can_use_ai():
        return None, "AI_BLOCKED"

    try:
        res = openai.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role":"user","content":text}],
            temperature=0
        )

        add_cost()

        content = res.choices[0].message.content
        log(tr,"AI","CALL",text,"AI_OK",get_cost())

        m = re.search(r"\{.*\}", content, re.S)
        if m:
            return json.loads(m.group()), "AI_OK"

        return None, "AI_PARSE_FAIL"

    except Exception as e:
        return None, f"AI_ERROR:{str(e)}"

# =========================
# HANDLE ENGINE
# =========================
def handle(data, tr):

    if not data:
        return "⚠️ 無法識別"

    try:

        if data["action"] == "order_multi":

            oid = "d" + uuid.uuid4().hex[:6]
            total = 0

            for i in data["items"]:
                p = price(i["p"])
                t = p * i["q"]
                total += t

                order_ws.append_row([
                    oid,
                    data["user"],
                    i["p"],
                    i["q"],
                    p,
                    t,
                    "pending",
                    datetime.now().strftime("%H:%M")
                ])

            return f"🧾 OK {oid}"

        if data["action"] == "update":
            row,_ = find_order_row(data["id"])
            order_ws.update_cell(row,4,data["qty"])
            return f"✏️ OK {data['id']}"

        if data["action"] == "delete":
            row,_ = find_order_row(data["id"])
            order_ws.delete_rows(row)
            return f"🗑️ OK {data['id']}"

        if data["action"] == "confirm":
            row,_ = find_order_row(data["id"])
            order_ws.update_cell(row,7,"confirmed")
            return f"✅ OK {data['id']}"

        return "⚠️ unknown"

    except Exception as e:
        log(tr,"ERROR","HANDLE",str(e))
        return f"❌ ERROR"

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
        if dedupe(mid):
            return "OK"

        tr = trace_id()
        text = e["message"]["text"]

        log(tr,"WEBHOOK","RECV",text)

        data = rule_parse(text)
        diag = "RULE"

        if not data:
            data, diag = ai_parse(text, tr)

            if diag == "AI_BLOCKED":
                result = "⚠️ AI額度已用完"
                log(tr,"AI","BLOCK",text,diag,get_cost())
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
        "version":"v20.2",
        "cost":get_cost(),
        "limit":DAILY_LIMIT
    })

@app.route("/")
def home():
    return "v20.2 running"