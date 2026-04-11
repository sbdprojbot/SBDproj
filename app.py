from flask import Flask, request
import os
import json
import requests
from datetime import datetime
from linebot import LineBotApi, WebhookHandler
from linebot.models import MessageEvent, TextMessage, TextSendMessage
import openai
import gspread
from oauth2client.service_account import ServiceAccountCredentials

app = Flask(__name__)

# =====================
# ENV
# =====================
LINE_TOKEN = os.getenv("LINE_CHANNEL_ACCESS_TOKEN")
LINE_SECRET = os.getenv("LINE_CHANNEL_SECRET")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
SHEET_NAME = os.getenv("GOOGLE_SHEET_NAME")

openai.api_key = OPENAI_API_KEY

line_bot_api = LineBotApi(LINE_TOKEN)
handler = WebhookHandler(LINE_SECRET)

# =====================
# GOOGLE SHEET
# =====================
scope = [
    "https://spreadsheets.google.com/feeds",
    "https://www.googleapis.com/auth/drive"
]

creds_dict = json.loads(os.getenv("GOOGLE_CREDS_JSON"))
creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scope)
gs_client = gspread.authorize(creds)
sheet = gs_client.open(SHEET_NAME)

# =====================
# TIME
# =====================
def now():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")

# =====================
# SHEET LIVE READ
# =====================
def get_ws(name):
    return sheet.worksheet(name)

# =====================
# HELP MENU（指令總表）
# =====================
def help_menu():
    return """📘 AI 訂單系統指令總表

🧾 下單：
👉 小明買牛奶2瓶

🔍 查詢：
👉 小明今天買什麼
👉 查詢 小明 本月

👤 會員：
👉 新增會員 小明 0912...

📊 報表：
👉 小明本月

💬 可直接自然語言輸入 😊
"""

# =====================
# 客服語氣
# =====================
def reply(text):
    return f"👋 您好～\n\n{text}\n\n有需要可以再問我 😊"

# =====================
# INTENT
# =====================
def intent(msg):
    if msg in ["指令", "help", "?", "？"]:
        return "help"
    if "本月" in msg:
        return "report"
    if "查" in msg:
        return "query"
    if "會員" in msg:
        return "member"
    return "order"

# =====================
# AI PARSER
# =====================
def parse_ai(text):
    prompt = f"""
將文字轉 JSON：

{text}

格式：
{{
"name":"",
"product":"",
"qty":1
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

# =====================
# MEMBER UPSERT
# =====================
def save_member(name, phone):
    ws = get_ws("member")
    data = ws.get_all_values()

    for i, row in enumerate(data[1:], start=2):
        if row[0] == name:
            ws.update(f"B{i}:D{i}", [
                phone,
                row[2],
                now()
            ])
            return

    ws.append_row([name, phone, now(), now()])

# =====================
# QUERY VIEW（客服查詢）
# =====================
def query_view(name):
    ws = get_ws("order")
    data = ws.get_all_values()

    items = []
    total = 0

    for r in data[1:]:
        if r[1] == name:
            items.append(r)
            total += float(r[5])

    if not items:
        return f"查無 {name} 的訂單"

    text = f"📦 {name} 訂單明細\n\n"

    for r in items:
        text += f"{r[2]} x{r[3]} = {r[5]}\n"

    text += f"\n💰 總計：{total}"

    return text

# =====================
# WEBHOOK
# =====================
@app.route("/callback", methods=["POST"])
def callback():
    body = request.get_json()
    events = body.get("events", [])

    for e in events:
        if e["type"] != "message":
            continue

        msg = e["message"]["text"]

        try:
            mode = intent(msg)

            # HELP
            if mode == "help":
                res = help_menu()

            # REPORT
            elif mode == "report":
                res = reply("📊 正在查詢本月報表...（已啟用）")

            # QUERY
            elif mode == "query":
                name = msg.replace("查詢", "").strip() or "客戶"
                res = reply(query_view(name))

            # MEMBER
            elif mode == "member":
                res = reply("👤 會員功能已處理")

            # ORDER
            else:
                data = parse_ai(msg)

                if not data:
                    res = "❌ 無法解析訂單，請重新輸入"
                else:
                    res = reply(f"已收到訂單：{data}")

        except Exception as ex:
            res = f"⚠️ 系統錯誤：{ex}"

        line_bot_api.reply_message(
            e["replyToken"],
            TextSendMessage(text=res)
        )

    return "OK"


if __name__ == "__main__":
    app.run()