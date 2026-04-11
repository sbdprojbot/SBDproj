from flask import Flask, request
import os
import json
from datetime import datetime
import openai
import gspread
from oauth2client.service_account import ServiceAccountCredentials
from linebot import LineBotApi, WebhookHandler
from linebot.models import MessageEvent, TextMessage, TextSendMessage

app = Flask(__name__)

# =====================
# ENV
# =====================
LINE_TOKEN = os.getenv("LINE_CHANNEL_ACCESS_TOKEN")
LINE_SECRET = os.getenv("LINE_CHANNEL_SECRET")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
SHEET_ID = os.getenv("GOOGLE_SHEET_ID")

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
sheet = gs_client.open_by_key(SHEET_ID)

# =====================
# TIME
# =====================
def now():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")

# =====================
# HELP MENU
# =====================
def help_menu():
    return """📘 AI 訂單系統指令

🧾 下單：
小明買牛奶2瓶

🔍 查詢：
查詢小明
小明本月

👤 會員：
新增會員 小明 0912...

💬 輸入自然語言即可 😊
"""

# =====================
# INTENT
# =====================
def intent(msg):
    if msg in ["指令", "help", "?", "？"]:
        return "help"
    if "查" in msg:
        return "query"
    if "會員" in msg:
        return "member"
    return "order"

# =====================
# AI PARSER
# =====================
def ai_parse(text):
    prompt = f"""
轉 JSON：

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
# QUERY SHEET
# =====================
def query_order(name):
    ws = sheet.worksheet("order")
    data = ws.get_all_values()

    total = 0
    result = []

    for r in data[1:]:
        if len(r) < 6:
            continue
        if r[1] == name:
            result.append(r)
            total += float(r[5])

    if not result:
        return f"查無 {name} 訂單"

    text = f"📦 {name} 訂單\n\n"

    for r in result:
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
                reply = help_menu()

            # QUERY
            elif mode == "query":
                name = msg.replace("查詢", "").strip()
                reply = query_order(name or "客戶")

            # MEMBER
            elif mode == "member":
                reply = "👤 會員功能已啟用"

            # ORDER
            else:
                data = ai_parse(msg)

                if not data:
                    reply = "❌ 無法解析訂單"
                else:
                    reply = f"已收到訂單：{data}"

        except Exception as ex:
            reply = f"⚠️ 錯誤：{ex}"

        line_bot_api.reply_message(
            e["replyToken"],
            TextSendMessage(text=reply)
        )

    return "OK"


if __name__ == "__main__":
    app.run()