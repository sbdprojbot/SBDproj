from flask import Flask, request
import os
import json
from datetime import datetime

import openai
import gspread
from oauth2client.service_account import ServiceAccountCredentials

from linebot import LineBotApi, WebhookHandler
from linebot.models import TextSendMessage

# =========================
# INIT
# =========================
app = Flask(__name__)

LINE_TOKEN = os.getenv("LINE_CHANNEL_ACCESS_TOKEN")
LINE_SECRET = os.getenv("LINE_CHANNEL_SECRET")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
SHEET_ID = os.getenv("GOOGLE_SHEET_ID")
GOOGLE_CREDS = json.loads(os.getenv("GOOGLE_CREDS_JSON"))

openai.api_key = OPENAI_API_KEY
line_bot_api = LineBotApi(LINE_TOKEN)
handler = WebhookHandler(LINE_SECRET)

# =========================
# SHEET
# =========================
scope = [
    "https://spreadsheets.google.com/feeds",
    "https://www.googleapis.com/auth/drive"
]

creds = ServiceAccountCredentials.from_json_keyfile_dict(GOOGLE_CREDS, scope)
gs_client = gspread.authorize(creds)
sheet = gs_client.open_by_key(SHEET_ID)

# =========================
# TIME
# =========================
def now():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")

# =========================
# ID GENERATOR (FIXED v11.2)
# =========================

def gen_user_id():
    try:
        ws = sheet.worksheet("member")
        count = len(ws.get_all_values()) - 1
    except:
        count = 0
    return "u" + str(count + 1).zfill(4)


def gen_order_id():
    try:
        ws = sheet.worksheet("order")
        count = len(ws.get_all_values()) - 1
    except:
        count = 0
    return "d" + str(count + 1).zfill(6)

# =========================
# PRICE FROM SHEET
# =========================
def get_price(product):
    try:
        ws = sheet.worksheet("product")
        rows = ws.get_all_values()
        for r in rows[1:]:
            if len(r) >= 3 and r[1] == product:
                return int(r[2])
    except:
        pass
    return 0

# =========================
# INTENT CONTROL (🔥防止亂下單)
# =========================
def intent(text):
    if any(k in text for k in ["會員", "電話", "地址", "新增", "修改"]):
        return "member"
    if "查詢" in text:
        return "query"
    return "order"

# =========================
# PARSE ORDER (AI fallback)
# =========================
def parse_order(text):
    prompt = f"""
把以下文字轉 JSON：

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
# WRITE ORDER
# =========================
def write_order(line_user_id, data):

    ws = sheet.worksheet("order")

    order_id = gen_order_id()

    name = data.get("name", "未知")
    items = data.get("items", [])

    total_all = 0

    for item in items:
        product = item.get("product", "")
        qty = int(item.get("qty", 1))
        price = get_price(product)
        total = price * qty
        total_all += total

        ws.append_row([
            order_id,
            now(),
            "",  # user_id 先不寫（可升級）
            name,
            product,
            qty,
            price,
            total,
            "confirmed"
        ])

    return order_id, total_all

# =========================
# MEMBER
# =========================
def upsert_member(line_user_id, name):

    ws = sheet.worksheet("member")

    rows = ws.get_all_values()

    # 找是否存在
    for i, r in enumerate(rows):
        if i == 0:
            continue
        if r[1] == name:
            return r[0]

    user_id = gen_user_id()

    ws.append_row([
        user_id,
        name,
        "",
        "",
        now(),
        now()
    ])

    return user_id

# =========================
# QUERY
# =========================
def query(name):
    ws = sheet.worksheet("order")
    rows = ws.get_all_values()

    total = 0
    text = f"📦 {name} 訂單\n\n"

    for r in rows[1:]:
        if len(r) >= 7 and r[3] == name:
            text += f"{r[4]} x{r[5]} = {r[7]}\n"
            total += int(r[7])

    text += f"\n💰 總計：{total}"
    return text

# =========================
# HELP
# =========================
def help_text():
    return """🧠 指令

🧾 下單：
小明買牛奶2

🔍 查詢：
查詢小明

👤 會員：
新增王小明
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

        text = e["message"]["text"]

        try:
            mode = intent(text)

            # HELP
            if text == "help":
                reply = help_text()

            # MEMBER
            elif mode == "member":
                user_id = upsert_member(e["source"]["userId"], text)
                reply = f"👤 會員已更新"

            # QUERY
            elif mode == "query":
                name = text.replace("查詢", "").strip()
                reply = query(name)

            # ORDER
            else:
                data = parse_order(text)

                if not data:
                    reply = "❌ 無法解析訂單"
                else:
                    oid, total = write_order(e["source"]["userId"], data)
                    reply = f"✅ 訂單成立\n{oid}\n💰 {total}"

        except Exception as ex:
            reply = "⚠️ 系統錯誤"

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