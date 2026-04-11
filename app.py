from flask import Flask, request, jsonify
import os, json, re
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
cost_usage = {}
DAILY_LIMIT = 0.03

def today():
    return str(date.today())

def can_ai():
    return cost_usage.get(today(), 0) < DAILY_LIMIT

def add_cost(v=0.001):
    cost_usage[today()] = cost_usage.get(today(), 0) + v

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

user_ws = ws("user", ["user_id","name","phone","address","time"])
product_ws = ws("product", ["product_id","product","price","time"])
order_ws = ws("order", ["order_id","user","product","qty","time"])
log_ws = ws("log", ["time","stage","type","message"])

# =========================
# LOG
# =========================
def log(stage, type_, msg):
    try:
        log_ws.append_row([
            datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            stage,
            type_,
            str(msg)[:500]
        ])
    except:
        print("LOG FAIL:", msg)

# =========================
# ID GENERATOR（6碼）
# =========================
def next_id(ws, prefix):
    try:
        records = ws.get_all_records()
        num = len(records) + 1
        return f"{prefix}{str(num).zfill(5)}"
    except:
        return f"{prefix}00001"

# =========================
# 防重
# =========================
processed = set()

def is_duplicate(eid):
    if eid in processed:
        return True
    processed.add(eid)
    return False

# =========================
# RULE PARSER
# =========================
def rule_parse(text):
    try:
        # 訂單
        m = re.search(r"(.*)買(.*?)(\d+)", text)
        if m:
            return {
                "action":"order",
                "name": m.group(1).strip(),
                "product": m.group(2).strip(),
                "qty": int(m.group(3))
            }

        # 產品
        m = re.search(r"(.*)\s*(\d+)元", text)
        if m:
            return {
                "action":"create_product",
                "product": m.group(1).strip(),
                "price": int(m.group(2))
            }

        # 會員
        if "電話" in text or "住" in text:
            phone = re.search(r"09\d{8}", text)
            addr = text.split("住")[-1] if "住" in text else ""

            return {
                "action":"create_user",
                "name": text.split()[0],
                "phone": phone.group() if phone else "",
                "address": addr
            }

        return None
    except:
        return None

# =========================
# AI PARSER（補強）
# =========================
def should_use_ai(text):
    if len(text) < 6:
        return True
    if not any(k in text for k in ["買","元","電話","住"]):
        return True
    return False

def ai_parse(text):
    if not can_ai():
        return None

    try:
        res = openai.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role":"user","content":f"轉JSON：{text}"}],
            temperature=0
        )
        add_cost()

        content = res.choices[0].message.content
        match = re.search(r"\{.*\}", content, re.S)
        if match:
            return json.loads(match.group())
    except:
        return None

# =========================
# HANDLE
# =========================
def handle(data):
    try:
        action = data.get("action")

        # USER
        if action == "create_user":
            if not data.get("name"):
                return "⚠️ 缺少姓名"

            uid = next_id(user_ws, "u")

            user_ws.append_row([
                uid,
                data.get("name"),
                data.get("phone",""),
                data.get("address",""),
                datetime.now().strftime("%H:%M")
            ])

            return f"👤 會員建立成功：{uid}"

        # PRODUCT
        if action == "create_product":
            if not data.get("product") or not data.get("price"):
                return "⚠️ 需名稱+價格"

            pid = next_id(product_ws, "p")

            product_ws.append_row([
                pid,
                data.get("product"),
                data.get("price"),
                datetime.now().strftime("%H:%M")
            ])

            return f"📦 產品建立成功：{pid}"

        # ORDER
        if action == "order":
            if not data.get("product"):
                return "⚠️ 無商品"

            oid = next_id(order_ws, "d")

            order_ws.append_row([
                oid,
                data.get("name","unknown"),
                data.get("product"),
                data.get("qty",1),
                datetime.now().strftime("%H:%M")
            ])

            return f"🧾 訂單完成：{oid}"

        return "⚠️ 無法識別"

    except Exception as e:
        log("HANDLE","FAIL",str(e))
        return "❌ 寫入失敗"

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

        eid = e["message"]["id"]
        if is_duplicate(eid):
            return "OK"

        text = e["message"]["text"]

        log("WEBHOOK","RECEIVED",text)

        data = rule_parse(text)

        if not data or should_use_ai(text):
            ai_data = ai_parse(text)
            if ai_data:
                data = ai_data

        if data:
            result = handle(data)
        else:
            result = "⚠️ 無法理解\n例：小明買牛奶2瓶"

        try:
            line_bot_api.reply_message(
                e["replyToken"],
                TextSendMessage(text=result)
            )
        except Exception as err:
            log("LINE","FAIL",str(err))

    return "OK"

# =========================
@app.route("/health")
def health():
    return jsonify({
        "status":"ok",
        "version":"v19.8",
        "cost": cost_usage.get(today(),0)
    })

@app.route("/")
def home():
    return "v19.8 stable"