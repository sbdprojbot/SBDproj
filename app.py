from flask import Flask, request
import os
import json
import re
from datetime import datetime

import gspread
from oauth2client.service_account import ServiceAccountCredentials

from linebot import LineBotApi
from linebot.models import TextSendMessage

import openai

# =========================
# INIT
# =========================
app = Flask(__name__)

LINE_TOKEN = os.getenv("LINE_CHANNEL_ACCESS_TOKEN")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
SHEET_ID = os.getenv("GOOGLE_SHEET_ID")
GOOGLE_CREDS = os.getenv("GOOGLE_CREDS_JSON")

openai.api_key = OPENAI_API_KEY
line_bot_api = LineBotApi(LINE_TOKEN)

# =========================
# SHEET
# =========================
scope = [
    "https://spreadsheets.google.com/feeds",
    "https://www.googleapis.com/auth/drive"
]

creds = ServiceAccountCredentials.from_json_keyfile_dict(
    json.loads(GOOGLE_CREDS),
    scope
)

gs = gspread.authorize(creds)
sheet = gs.open_by_key(SHEET_ID)

# =========================
# TIME
# =========================
def now():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")

# =========================
# SAFE APPEND
# =========================
def safe_append(ws, row, retry=2):
    for _ in range(retry):
        try:
            ws.append_row(row)
            return True
        except:
            continue
    return False

# =========================
# PRODUCT
# =========================
def get_product(name):
    try:
        ws = sheet.worksheet("product")
        rows = ws.get_all_values()

        for r in rows[1:]:
            if r[1] == name:
                return {
                    "price": int(r[2]),
                    "status": r[5]
                }
    except:
        pass
    return None

# =========================
# MEMBER
# =========================
def upsert_member(user_id, phone=None, address=None):
    try:
        ws = sheet.worksheet("member")
    except:
        ws = sheet.add_worksheet("member", 1000, 10)
        ws.append_row(["user_id","name","phone","address","created_at","updated_at","note"])

    rows = ws.get_all_values()

    for i, r in enumerate(rows):
        if i == 0:
            continue
        if r[0] == user_id:
            if phone:
                ws.update_cell(i+1, 3, phone)
            if address:
                ws.update_cell(i+1, 4, address)
            ws.update_cell(i+1, 6, now())
            return

    ws.append_row([user_id,"未知",phone or "",address or "",now(),now(),""])

# =========================
# ORDER ID
# =========================
def gen_order_id():
    return "D" + datetime.now().strftime("%y%m%d%H%M%S")

# =========================
# AI PARSER
# =========================
def parse_order(text):
    try:
        prompt = f"""
只輸出 JSON：

{text}

格式：
{{
  "name": "",
  "items": [
    {{"product": "", "qty": 1}}
  ]
}}
"""

        res = openai.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role":"user","content":prompt}],
            temperature=0
        )

        match = re.search(r"\{.*\}", res.choices[0].message.content, re.DOTALL)
        if not match:
            return None

        return json.loads(match.group())

    except:
        return None

# =========================
# AI ERROR DIAGNOSIS
# =========================
def ai_analyze_error(message, raw):

    try:
        prompt = f"""
你是維運工程師，請分析：

錯誤：{message}
輸入：{raw}

用中文回答：
1. 原因
2. 模組（AI / Sheet / 使用者 / 系統）
3. 修正方式
"""

        res = openai.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role":"user","content":prompt}],
            temperature=0
        )

        return res.choices[0].message.content[:500]

    except:
        return "AI分析失敗"

# =========================
# LOG SYSTEM
# =========================
def write_log(level, user_id, action, message, raw=""):

    try:
        ws = sheet.worksheet("log")
    except:
        ws = sheet.add_worksheet("log", 1000, 10)
        ws.append_row([
            "time","level","user_id","action","message","raw","ai_diagnosis"
        ])

    ai_diag = ""

    if level == "ERROR":
        ai_diag = ai_analyze_error(message, raw)

    try:
        safe_append(ws, [
            now(),
            level,
            user_id,
            action,
            str(message)[:300],
            str(raw)[:500],
            ai_diag
        ])
    except:
        pass

# =========================
# VALIDATION
# =========================
def validate(data):

    if not data:
        return False, "❌ 無法解析訂單"

    if not data.get("name"):
        return False, "❌ 缺少姓名"

    for item in data["items"]:
        if not item.get("product"):
            return False, "❌ 缺少商品"

        try:
            qty = int(re.search(r"\d+", str(item.get("qty"))).group())
        except:
            qty = 0

        if qty <= 0:
            return False, "❌ 數量錯誤"

        p = get_product(item["product"])
        if not p:
            return False, f"❌ 商品不存在：{item['product']}"

        if p["status"] != "on":
            return False, f"❌ 商品已下架：{item['product']}"

    return True, "OK"

# =========================
# ORDER WRITE
# =========================
def write_order(user_id, data):

    ws = sheet.worksheet("order")

    order_id = gen_order_id()
    total = 0

    for item in data["items"]:

        p = get_product(item["product"])
        qty = int(re.search(r"\d+", str(item["qty"])).group())

        subtotal = p["price"] * qty
        total += subtotal

        safe_append(ws, [
            order_id,
            now(),
            user_id,
            data["name"],
            item["product"],
            qty,
            p["price"],
            subtotal,
            "confirmed"
        ])

    return order_id, total

# =========================
# INTENT
# =========================
def intent(text):
    if text in ["help","指令","說明"]:
        return "help"
    if "電話" in text or "地址" in text:
        return "member"
    return "order"

# =========================
# HELP
# =========================
def help_text():
    return """🧾 LINE系統指令

📦 下單：
小明買牛奶2 麵包1

📞 電話：
電話09xxxx

🏠 地址：
地址新北市

🔍 查詢：
查詢小明
"""

# =========================
# WEBHOOK
# =========================
@app.route("/callback", methods=["POST"])
def callback():

    body = request.get_json()
    events = body.get("events", [])

    for e in events:

        try:
            if e["type"] != "message":
                continue

            user_id = e["source"]["userId"]
            text = e["message"]["text"]

            mode = intent(text)

            if mode == "help":
                reply = help_text()

            elif mode == "member":
                if "電話" in text:
                    upsert_member(user_id, phone=text)
                    reply = "📞 已更新"

                elif "地址" in text:
                    upsert_member(user_id, address=text)
                    reply = "🏠 已更新"

                else:
                    reply = "請輸入電話或地址"

            else:
                data = parse_order(text)

                ok, msg = validate(data)

                if not ok:
                    write_log("ERROR", user_id, "validate_fail", msg, text)
                    reply = msg
                else:
                    order_id, total = write_order(user_id, data)
                    write_log("INFO", user_id, "order_success", order_id, text)
                    reply = f"✅ {order_id}\n💰 {total}"

        except Exception as ex:
            write_log("ERROR", "system", "callback_crash", str(ex), "")
            reply = "⚠️ 系統忙碌"

        try:
            line_bot_api.reply_message(
                e["replyToken"],
                TextSendMessage(text=reply)
            )
        except:
            write_log("ERROR","system","reply_fail","invalid_token",str(e))

    return "OK"

# =========================
# RUN
# =========================
if __name__ == "__main__":
    port = int(os.getenv("PORT", 10000))
    app.run(host="0.0.0.0", port=port)