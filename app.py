from flask import Flask, request
import os
import json
from datetime import datetime

import openai
import gspread
from oauth2client.service_account import ServiceAccountCredentials

from linebot import LineBotApi, WebhookHandler
from linebot.models import MessageEvent, TextMessage, TextSendMessage

# =========================
# APP INIT
# =========================
app = Flask(__name__)

# =========================
# ENV
# =========================
LINE_TOKEN = os.getenv("LINE_CHANNEL_ACCESS_TOKEN")
LINE_SECRET = os.getenv("LINE_CHANNEL_SECRET")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
SHEET_ID = os.getenv("GOOGLE_SHEET_ID")

openai.api_key = OPENAI_API_KEY

line_bot_api = LineBotApi(LINE_TOKEN)
handler = WebhookHandler(LINE_SECRET)

# =========================
# HEALTH CHECK
# =========================
@app.route("/")
def home():
    return "OK"

# =========================
# GOOGLE SHEET AUTH
# =========================
scope = [
    "https://spreadsheets.google.com/feeds",
    "https://www.googleapis.com/auth/drive"
]

creds_dict = json.loads(os.getenv("GOOGLE_CREDS_JSON"))
creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scope)

gs_client = gspread.authorize(creds)
sheet = gs_client.open_by_key(SHEET_ID)

# =========================
# TIME
# =========================
def now():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")

# =========================
# AI PARSER（多商品）
# =========================
def parse_order(text):
    prompt = f"""
將使用者輸入轉 JSON（可多商品）：

{text}

格式：
{{
  "name": "",
  "items": [
    {{"product": "", "qty": 1}}
  ]
}}

只輸出 JSON
"""

    try:
        res = openai.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": prompt}],
            temperature=0
        )
        return json.loads(res.choices[0].message.content)
    except:
        return None

# =========================
# 價格表（可未來改 Sheet）
# =========================
PRICE_MAP = {
    "牛奶": 30,
    "麵包": 25,
    "水": 20
}

# =========================
# MEMBER（含地址）
# =========================
def upsert_member(user_id, name, address=None):
    try:
        ws = sheet.worksheet("member")
    except:
        ws = sheet.add_worksheet("member", rows="1000", cols="10")
        ws.append_row(["user_id", "name", "phone", "address", "created_at", "updated_at", "note"])

    rows = ws.get_all_values()

    for i, r in enumerate(rows):
        if i == 0:
            continue
        if r[0] == user_id:
            ws.update_cell(i+1, 2, name)
            if address:
                ws.update_cell(i+1, 4, address)
            ws.update_cell(i+1, 6, now())
            return

    ws.append_row([user_id, name, "", address or "", now(), now(), ""])

# =========================
# ADDRESS DETECTION
# =========================
def detect_address(text):
    if "地址" in text or "住在" in text or "住址" in text:
        return text.split("是")[-1].strip()
    return None

# =========================
# WRITE ORDER
# =========================
def write_order(user_id, data):
    try:
        ws = sheet.worksheet("order")
    except:
        ws = sheet.add_worksheet("order", rows="2000", cols="10")
        ws.append_row(["time", "user_id", "name", "product", "qty", "price", "total"])

    name = data.get("name", "未知")
    items = data.get("items", [])

    upsert_member(user_id, name)

    for item in items:
        product = item["product"]
        qty = int(item["qty"])
        price = PRICE_MAP.get(product, 0)
        total = price * qty

        ws.append_row([
            now(),
            user_id,
            name,
            product,
            qty,
            price,
            total
        ])

# =========================
# QUERY ORDER
# =========================
def query(name):
    ws = sheet.worksheet("order")
    rows = ws.get_all_values()

    text = f"📦 {name} 訂單\n\n"
    total_sum = 0

    for r in rows[1:]:
        if len(r) < 7:
            continue
        if r[2] == name:
            text += f"{r[3]} x{r[4]} = {r[6]}\n"
            total_sum += int(r[6])

    text += f"\n💰 總金額：{total_sum}"
    return text

# =========================
# QUERY ADDRESS
# =========================
def query_address(user_id):
    ws = sheet.worksheet("member")
    rows = ws.get_all_values()

    for r in rows[1:]:
        if r[0] == user_id:
            return f"🏠 你的地址：{r[3] or '未設定'}"

    return "查無會員資料"

# =========================
# INTENT
# =========================
def intent(msg):
    if msg in ["help", "指令"]:
        return "help"
    if "查詢" in msg:
        return "query"
    if "地址" in msg:
        return "address"
    return "order"

# =========================
# HELP
# =========================
def help_text():
    return """🧠 LINE 訂單系統 v3-lite

🧾 下單：
小明買牛奶2瓶麵包1個

🔍 查詢：
查詢小明

🏠 地址：
住址是台北板橋文化路

📊 月報（未開放）
"""

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

        user_id = e["source"]["userId"]
        text = e["message"]["text"]

        try:
            mode = intent(text)

            # HELP
            if mode == "help":
                reply = help_text()

            # ADDRESS QUERY
            elif mode == "address":
                addr = detect_address(text)
                if addr:
                    upsert_member(user_id, "未知", addr)
                    reply = f"🏠 已更新地址：{addr}"
                else:
                    reply = query_address(user_id)

            # ORDER QUERY
            elif mode == "query":
                name = text.replace("查詢", "").strip()
                reply = query(name or "未知")

            # ORDER
            else:
                data = parse_order(text)

                if not data:
                    reply = "❌ 無法解析訂單"
                else:
                    write_order(user_id, data)
                    reply = f"✅ 已記錄訂單"

        except Exception as ex:
            reply = f"⚠️ 系統錯誤：{ex}"

        line_bot_api.reply_message(
            e["replyToken"],
            TextSendMessage(text=reply)
        )

    return "OK"

# =========================
# RUN
# =========================
if __name__ == "__main__":
    port = int(os.getenv("PORT", 10000))
    app.run(host="0.0.0.0", port=port)