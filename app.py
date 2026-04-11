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
# LOG SYSTEM
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
# JSON SAFE PARSER
# =========================
def extract_json(text):
    try:
        s = text.find("{")
        e = text.rfind("}") + 1
        if s == -1 or e == -1:
            return None
        return json.loads(text[s:e])
    except:
        return None

# =========================
# RULE PARSER (cheap first)
# =========================
def rule_parse(text):

    if "買" in text:
        m = re.match(r"(.+?)買(.+)", text)
        if m:
            user = m.group(1)
            items = re.findall(r"([\u4e00-\u9fa5A-Za-z]+)(\d+)", m.group(2))
            return {
                "action":"order_multi",
                "user":user,
                "items":[{"p":i[0],"q":int(i[1])} for i in items]
            }

    if "改" in text:
        m = re.search(r"(d\d+).*(\d+)", text)
        if m:
            return {"action":"update","id":m.group(1),"qty":int(m.group(2))}

    if "刪" in text:
        m = re.search(r"(d\d+)", text)
        if m:
            return {"action":"delete","id":m.group(1)}

    return None

# =========================
# AI PARSER (v20.4)
# =========================
def ai_parse(text, tr):

    if not can_use_ai():
        return None, "AI_BLOCKED"

    try:
        res = openai.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {
                    "role":"system",
                    "content":"""
你是企業級 LINE 商務解析 AI。

你必須將自然語言轉成 JSON。

輸出格式：
{
  "ok": true,
  "action": "",
  "data": {},
  "confidence": 0-1,
  "error_type": "",
  "reason": "",
  "suggestion": ""
}

規則：
- 不可輸出任何多餘文字
- 不可 markdown
- 無法理解時 ok=false 並解釋原因
"""
                },
                {"role":"user","content":text}
            ],
            temperature=0
        )

        add_cost()

        raw = res.choices[0].message.content.strip()
        log(tr,"AI","RAW",raw,"AI_OK",get_cost())

        data = extract_json(raw)

        if not data:
            return None, "AI_PARSE_FAIL"

        return data, "AI_OK"

    except Exception as e:
        return None, f"AI_ERROR:{str(e)}"

# =========================
# PRICE LOOKUP
# =========================
def price(product):
    rows = product_ws.get_all_records()
    for r in rows:
        if r["product"] == product:
            return int(r.get("price",0))
    return 0

# =========================
# HANDLE ENGINE
# =========================
def handle(data, tr):

    if not data:
        return "⚠️ 無法識別"

    try:

        action = data.get("action")

        if action == "order_multi":

            oid = "d" + uuid.uuid4().hex[:6]
            user = data.get("user")
            items = data.get("items", [])

            total = 0

            for i in items:
                p = price(i["p"])
                total += p * i["q"]

                order_ws.append_row([
                    oid,
                    user,
                    i["p"],
                    i["q"],
                    p,
                    p*i["q"],
                    "pending",
                    datetime.now().strftime("%H:%M")
                ])

            return f"🧾 OK {oid}"

        if action == "update":
            # naive update (safe version)
            rows = order_ws.get_all_values()
            for idx, r in enumerate(rows):
                if r[0] == data["id"]:
                    order_ws.update_cell(idx+1,4,data["qty"])
                    return f"✏️ OK {data['id']}"

        if action == "delete":
            rows = order_ws.get_all_values()
            for idx, r in enumerate(rows):
                if r[0] == data["id"]:
                    order_ws.delete_rows(idx+1)
                    return f"🗑️ OK {data['id']}"

        return "⚠️ unknown"

    except Exception as e:
        log(tr,"ERROR","HANDLE",str(e))
        return "❌ ERROR"

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
        mid = e["message"]["id"]

        tr = trace_id()

        log(tr,"WEBHOOK","RECV",text)

        # RULE FIRST (省錢)
        data = rule_parse(text)
        diag = "RULE"

        if not data:
            data, diag = ai_parse(text, tr)

            if diag == "AI_BLOCKED":
                result = "⚠️ AI額度已用完"
                log(tr,"AI","BLOCK",text,diag,get_cost())
            elif data and data.get("ok") is False:
                # AI explain error
                result = f"⚠️ {data.get('reason','無法理解')}\n💡 {data.get('suggestion','請調整輸入')}"
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
        "version":"v20.4",
        "cost":get_cost(),
        "limit":DAILY_LIMIT
    })

@app.route("/")
def home():
    return "v20.4 running"