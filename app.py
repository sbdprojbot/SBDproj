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
# COST CONTROL (SAFE)
# =========================
cost_usage = {}
DAILY_LIMIT = 0.03
TEST_LIMIT = 0.07
MODE = os.getenv("COST_MODE", "prod")

def today():
    return str(date.today())

def limit():
    return TEST_LIMIT if MODE == "test" else DAILY_LIMIT

def can_ai():
    return cost_usage.get(today(), 0) < limit()

def add_cost(v=0.001):
    cost_usage[today()] = cost_usage.get(today(), 0) + v

# =========================
# SHEET INIT (SAFE)
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
product_ws = ws("product", ["product","price","time"])
order_ws = ws("order", ["order_id","user","product","qty","time"])
log_ws = ws("log", ["time","trace_id","stage","type","message"])

# =========================
# ID GENERATOR (SAFE + UNIQUE)
# =========================
def gen_user_id():
    return "u" + str(uuid.uuid4().int)[:4]

def gen_order_id():
    return "d" + datetime.now().strftime("%f")[:6]

def trace_id():
    return "T" + datetime.now().strftime("%Y%m%d%H%M%S") + "-" + str(uuid.uuid4())[:6]

# =========================
# SAFE LOG
# =========================
def log(tid, stage, type_, msg):
    try:
        log_ws.append_row([
            datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            tid,
            stage,
            type_,
            str(msg)[:500]
        ])
    except:
        print("LOG FAIL:", msg)

# =========================
# RULE PARSER (FAST + FREE)
# =========================
def rule_parse(text):
    try:
        # order pattern
        m = re.search(r"(.*)買(.*?)(\d+)", text)
        if m:
            return {
                "action":"order",
                "name": m.group(1).strip(),
                "product": m.group(2).strip(),
                "qty": int(m.group(3))
            }

        # product quick
        m2 = re.search(r"(.*) (\d+)元", text)
        if m2:
            return {
                "action":"create_product",
                "product": m2.group(1).strip(),
                "price": int(m2.group(2))
            }

        return None
    except:
        return None

# =========================
# AI PARSER (SAFE + CONTROLLED)
# =========================
def ai_parse(text, tid):
    if not can_ai():
        log(tid,"AI","SKIP","limit reached")
        return None

    try:
        prompt = f"""
你是 NLP 系統，只輸出 JSON。

action:
- create_user
- create_product
- order

格式：
{{
 "action":"",
 "name":"",
 "phone":"",
 "address":"",
 "product":"",
 "price":0,
 "qty":0
}}

規則：
- 沒有填 null
- 不要解釋
- 只 JSON

輸入：
{text}
"""

        res = openai.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role":"user","content":prompt}],
            temperature=0
        )

        add_cost()

        content = res.choices[0].message.content

        match = re.search(r"\{.*\}", content, re.S)
        if not match:
            raise ValueError("NO JSON")

        data = json.loads(match.group())

        if "action" not in data:
            raise ValueError("NO ACTION")

        return data

    except Exception as e:
        log(tid,"AI","FAIL",str(e))
        return None

# =========================
# ROUTER (CORE LOGIC)
# =========================
def handle(data, tid):

    action = data.get("action")

    # ================= USER =================
    if action == "create_user":
        uid = gen_user_id()

        if not data.get("name"):
            return "⚠️ 需要提供姓名"

        user_ws.append_row([
            uid,
            data.get("name"),
            data.get("phone",""),
            data.get("address",""),
            datetime.now().strftime("%H:%M")
        ])

        return f"👤 會員已建立：{uid}"

    # ================= PRODUCT =================
    if action == "create_product":

        if not data.get("product") or not data.get("price"):
            return "⚠️ 產品需包含：名稱 + 價格"

        product_ws.append_row([
            data.get("product"),
            data.get("price"),
            datetime.now().strftime("%H:%M")
        ])

        return f"📦 產品已建立：{data.get('product')}"

    # ================= ORDER =================
    if action == "order":
        oid = gen_order_id()

        if not data.get("product"):
            return "⚠️ 無商品資訊，無法建立訂單"

        order_ws.append_row([
            oid,
            data.get("name","unknown"),
            data.get("product"),
            data.get("qty",1),
            datetime.now().strftime("%H:%M")
        ])

        return f"🧾 訂單完成：{oid}"

    return "⚠️ 無法識別操作，請重新輸入"

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
        tid = trace_id()

        log(tid,"WEBHOOK","RECEIVED",text)

        # reply fast first (avoid LINE timeout)
        try:
            line_bot_api.reply_message(
                e["replyToken"],
                TextSendMessage(text="✅ 已收到，處理中")
            )
        except Exception as e:
            log(tid,"LINE","FAIL",str(e))

        # parse
        data = rule_parse(text)

        if not data:
            data = ai_parse(text, tid)

        if data:
            result = handle(data, tid)
        else:
            result = "⚠️ 無法理解內容，請改成：\n例：小明買牛奶2瓶"

        # optional follow-up (safe)
        try:
            line_bot_api.push_message(
                e["source"]["userId"],
                TextSendMessage(text=result)
            )
        except Exception as e:
            log(tid,"LINE_PUSH","FAIL",str(e))

    return "OK"

# =========================
@app.route("/health")
def health():
    return jsonify({
        "status":"ok",
        "version":"v19.5",
        "cost": cost_usage.get(today(),0),
        "limit": limit()
    })

@app.route("/")
def home():
    return "v19.5 hardened NLP system"