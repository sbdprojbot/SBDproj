from flask import Flask, request
import os, json
from datetime import datetime

import openai
import gspread
from oauth2client.service_account import ServiceAccountCredentials

from linebot import LineBotApi, WebhookHandler
from linebot.models import TextSendMessage

# =========================
# APP INIT
# =========================
app = Flask(__name__)

LINE_TOKEN = os.getenv("LINE_CHANNEL_ACCESS_TOKEN")
LINE_SECRET = os.getenv("LINE_CHANNEL_SECRET")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
SHEET_ID = os.getenv("GOOGLE_SHEET_ID")

openai.api_key = OPENAI_API_KEY
line_bot_api = LineBotApi(LINE_TOKEN)
handler = WebhookHandler(LINE_SECRET)

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
# SCHEMA（自動防呆核心🔥）
# =========================
SCHEMA = {
    "order": ["order_id","time","user_id","name","product","qty","price","total","status"],
    "member": ["user_id","name","phone","address","created_at","updated_at","note"],
    "product": ["product_id","product","price","category","stock","status"],
    "log": ["time","user_id","input","intent","status","error","ai_explain"]
}

# =========================
# TIME
# =========================
def now():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")

# =========================
# SHEET AUTO FIX（🔥核心）
# =========================
def ensure_sheet(name):
    try:
        return sheet.worksheet(name)
    except:
        return sheet.add_worksheet(title=name, rows="1000", cols="20")

def fix_columns(ws, cols):
    rows = ws.get_all_values()

    if not rows:
        ws.append_row(cols)
        return

    header = rows[0]
    missing = [c for c in cols if c not in header]

    if missing:
        ws.update("A1", [header + missing])

def init_sheets():
    for name, cols in SCHEMA.items():
        ws = ensure_sheet(name)
        fix_columns(ws, cols)

init_sheets()

# =========================
# PRODUCT PRICE (Sheet driven)
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
# AI PARSER（穩定版🔥）
# =========================
def parse_order(text):
    prompt = f"""
你是訂單解析器。

請把輸入轉成 JSON：

規則：
- 只能輸出 JSON
- 不要說明
- 不要 markdown

格式：
{{
  "name": "",
  "items": [
    {{"product": "", "qty": 1}}
  ]
}}

輸入：
{text}
"""

    try:
        res = openai.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role":"user","content":prompt}],
            temperature=0,
            response_format={"type":"json_object"}
        )

        return json.loads(res.choices[0].message.content)

    except:
        return None

# =========================
# MEMBER SYSTEM
# =========================
def upsert_member(user_id, name, address=None):
    ws = sheet.worksheet("member")
    rows = ws.get_all_values()

    for i,r in enumerate(rows):
        if i == 0:
            continue
        if r[0] == user_id:
            ws.update_cell(i+1,2,name)
            if address:
                ws.update_cell(i+1,4,address)
            ws.update_cell(i+1,6,now())
            return

    ws.append_row([user_id,name,"",address or "",now(),now(),""])

# =========================
# ORDER WRITE
# =========================
def write_order(user_id, data):
    ws = sheet.worksheet("order")

    order_id = f"ORD{datetime.now().strftime('%Y%m%d%H%M%S')}"
    name = data.get("name","未知")

    total_all = 0

    for item in data.get("items", []):
        product = item.get("product","")
        qty = int(item.get("qty",1))
        price = get_price(product)
        total = price * qty
        total_all += total

        ws.append_row([
            order_id,
            now(),
            user_id,
            name,
            product,
            qty,
            price,
            total,
            "confirmed"
        ])

    return order_id, total_all

# =========================
# QUERY SYSTEM（客服化🔥）
# =========================
def query(name):
    ws = sheet.worksheet("order")
    rows = ws.get_all_values()

    result = f"📦 {name} 的訂單\n\n"
    total = 0

    for r in rows[1:]:
        if len(r) < 9:
            continue
        if r[3] == name:
            result += f"• {r[4]} x{r[5]} = {r[7]}\n"
            total += int(r[7])

    result += f"\n💰 總金額：{total}"
    return result

# =========================
# INTENT
# =========================
def intent(text):
    if "查詢" in text:
        return "query"
    return "order"

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

            # QUERY
            if mode == "query":
                name = text.replace("查詢","").strip()
                reply = query(name or "未知")

            # ORDER
            else:
                data = parse_order(text)

                if not data:
                    reply = "❌ 我沒聽懂你的訂單，可以再說一次嗎？"
                else:
                    order_id, total = write_order(user_id, data)
                    upsert_member(user_id, data.get("name","未知"))
                    reply = f"✅ 訂單完成\n{order_id}\n💰 {total}"

        except Exception as ex:
            reply = f"⚠️ 系統忙碌中，請稍後再試"

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