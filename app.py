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
order_ws = ws("order", ["order_id","user","product","qty","status","time"])
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
# ID（6碼）
# =========================
def next_id(ws, prefix):
    try:
        records = ws.get_all_records()
        return f"{prefix}{str(len(records)+1).zfill(5)}"
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
# 查單
# =========================
def find_order(oid):
    records = order_ws.get_all_records()
    for idx, r in enumerate(records):
        if r["order_id"] == oid:
            return idx+2, r
    return None, None

# =========================
# RULE PARSER
# =========================
def rule_parse(text):

    # 查單
    m = re.search(r"(查|看).*(d\d{5})", text)
    if m:
        return {"action":"query_order","order_id":m.group(2)}

    # 修改
    if "改" in text:
        m = re.search(r"(d\d{5}).*(\d+)", text)
        if m:
            return {
                "action":"update_order",
                "order_id": m.group(1),
                "qty": int(m.group(2))
            }

    # 刪除
    if "刪" in text:
        m = re.search(r"(d\d{5})", text)
        if m:
            return {"action":"delete_order","order_id":m.group(1)}

    # 確認
    if "確認" in text:
        m = re.search(r"(d\d{5})", text)
        if m:
            return {"action":"confirm_order","order_id":m.group(1)}

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

# =========================
# AI PARSER
# =========================
def should_use_ai(text):
    if len(text) < 6:
        return True
    if not any(k in text for k in ["買","元","電話","住","查","改","刪","確認"]):
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

        match = re.search(r"\{.*\}", res.choices[0].message.content, re.S)
        if match:
            return json.loads(match.group())
    except:
        return None

# =========================
# HANDLE
# =========================
def handle(data):

    action = data.get("action")

    try:
        # USER
        if action == "create_user":
            uid = next_id(user_ws,"u")
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
            pid = next_id(product_ws,"p")
            product_ws.append_row([
                pid,
                data.get("product"),
                data.get("price"),
                datetime.now().strftime("%H:%M")
            ])
            return f"📦 產品建立成功：{pid}"

        # ORDER
        if action == "order":
            oid = next_id(order_ws,"d")
            order_ws.append_row([
                oid,
                data.get("name"),
                data.get("product"),
                data.get("qty",1),
                "pending",
                datetime.now().strftime("%H:%M")
            ])
            return f"🧾 訂單完成：{oid}"

        # QUERY
        if action == "query_order":
            row, o = find_order(data.get("order_id"))
            if not o:
                return "❌ 找不到訂單"
            return f"""📄 訂單
編號:{o['order_id']}
姓名:{o['user']}
商品:{o['product']}
數量:{o['qty']}
狀態:{o['status']}"""

        # UPDATE
        if action == "update_order":
            row, o = find_order(data.get("order_id"))
            if not o:
                return "❌ 找不到訂單"
            order_ws.update_cell(row,4,data.get("qty"))
            return f"✏️ 已更新 {o['order_id']} → {data.get('qty')}"

        # DELETE
        if action == "delete_order":
            row, o = find_order(data.get("order_id"))
            if not o:
                return "❌ 找不到訂單"
            order_ws.delete_rows(row)
            return f"🗑️ 已刪除 {o['order_id']}"

        # CONFIRM
        if action == "confirm_order":
            row, o = find_order(data.get("order_id"))
            if not o:
                return "❌ 找不到訂單"
            order_ws.update_cell(row,5,"confirmed")
            return f"✅ 已確認 {o['order_id']}"

        return "⚠️ 無法識別"

    except Exception as e:
        log("HANDLE","FAIL",str(e))
        return "❌ 系統錯誤"

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

        result = handle(data) if data else "⚠️ 無法理解"

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
        "version":"v20",
        "cost": cost_usage.get(today(),0)
    })

@app.route("/")
def home():
    return "v20 running"