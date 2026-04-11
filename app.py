from flask import Flask, request, jsonify
import os, json, uuid
from datetime import datetime, date

import gspread
from oauth2client.service_account import ServiceAccountCredentials

from linebot import LineBotApi
from linebot.models import TextSendMessage

import openai

app = Flask(__name__)

# =========================
# CONFIG
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

def cost():
    return cost_map.get(today(), 0)

def add_cost(c=0.001):
    cost_map[today()] = cost() + c

def can_ai():
    return cost() < DAILY_LIMIT

# =========================
# STATE (防無限迴圈)
# =========================
session = {}
MAX_DEPTH = 2

# =========================
# DEDUP LINE
# =========================
seen_msg = set()

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
        return sheet.worksheet(name)
    except:
        w = sheet.add_worksheet(title=name, rows=5000, cols=len(cols))
        w.append_row(cols)
        return w

user_ws = ws("user", ["user_id","name","phone","address","time"])
product_ws = ws("product", ["product_id","product","price","status","time"])
order_ws = ws("order", ["order_id","user","product","qty","price","total","status","time"])
log_ws = ws("log", ["time","trace","stage","type","msg","diag","cost"])

# =========================
# TRACE
# =========================
def trace():
    return "T" + datetime.now().strftime("%Y%m%d%H%M%S") + "-" + uuid.uuid4().hex[:6]

# =========================
# LOG (AI診斷升級)
# =========================
def log(tr, stage, type_, msg, diag="", c=0):
    try:
        log_ws.append_row([
            datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            tr,
            stage,
            type_,
            str(msg)[:300],
            diag,
            c
        ])
    except:
        pass

# =========================
# HELP / COMMAND LIST
# =========================
HELP_TEXT = """
📌 指令總表

🧾 訂單：
- 王小明買紅茶2杯

📦 商品：
- 紅茶一杯25元

👤 會員：
- 王小明 電話0912...

🔍 查單：
- 查訂單

✏️ 修改：
- 改訂單 dxxxx

❌ 刪除：
- 刪訂單 dxxxx
"""

# =========================
# AI ENGINE (會問問題 + 補資料)
# =========================
def ai_engine(text):

    if not can_ai():
        return {"action":"limit","reply":"⚠️ AI額度已用完"}

    prompt = """
你是AI門市員工。

你可以：
- create_user
- create_product
- order
- update
- delete
- ask
- fix

規則：
1. 缺資料 → ask
2. 可補齊 → fix
3. 不可拒絕
4. 要主動幫忙補全

輸出 JSON：
{
  "action": "",
  "data": {},
  "reply": "",
  "ai_diagnosis": ""
}
"""

    res = openai.chat.completions.create(
        model="gpt-4o-mini",
        messages=[
            {"role":"system","content":prompt},
            {"role":"user","content":text}
        ],
        temperature=0
    )

    add_cost()

    raw = res.choices[0].message.content

    try:
        return json.loads(raw)
    except:
        return {
            "action":"ask",
            "reply":"⚠️ 我需要更多資訊"
        }

# =========================
# CRUD ENGINE
# =========================
def handle(ai, tr):

    a = ai.get("action")
    d = ai.get("data", {})

    if a == "ask":
        return ai.get("reply")

    if a == "create_product":
        pid = "p" + uuid.uuid4().hex[:5]
        product_ws.append_row([pid,d.get("product"),d.get("price"),"active",today()])
        return f"📦 OK {pid}"

    if a == "create_user":
        uid = "u" + uuid.uuid4().hex[:5]
        user_ws.append_row([uid,d.get("name"),d.get("phone"),d.get("address"),today()])
        return f"👤 OK {uid}"

    if a == "order":
        oid = "d" + uuid.uuid4().hex[:5]

        total = 0
        for i in d.get("items", []):
            price = 0
            total += price * i.get("qty",1)
            order_ws.append_row([oid,d.get("user"),i.get("product"),i.get("qty"),price,total,"ok",today()])

        return f"🧾 OK {oid}"

    if a == "delete":
        return "❌ 已刪除"

    if a == "update":
        return "✏️ 已修改"

    return "⚠️ 無法處理"

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

        if mid in seen_msg:
            return "OK"
        seen_msg.add(mid)

        text = e["message"]["text"]
        tr = trace()

        log(tr,"WEBHOOK","IN",text)

        ai = ai_engine(text)

        log(tr,"AI","RAW",ai,"AI_OK",cost())

        result = handle(ai, tr)

        log(tr,"RESULT","OUT",result,ai.get("ai_diagnosis",""),cost())

        try:
            line_bot_api.reply_message(
                e["replyToken"],
                TextSendMessage(text=result)
            )
        except Exception as ex:
            log(tr,"LINE","FAIL",str(ex))

    return "OK"

# =========================
@app.route("/help")
def help():
    return HELP_TEXT

@app.route("/health")
def health():
    return jsonify({
        "status":"ok",
        "version":"v20.6",
        "cost":cost(),
        "limit":DAILY_LIMIT
    })

@app.route("/")
def home():
    return "v20.6 AI EMPLOYEE SYSTEM RUNNING"