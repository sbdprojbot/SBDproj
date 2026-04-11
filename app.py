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
# SHEET AUTH
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
# SCHEMA (v9 防呆核心)
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
# SHEET AUTO FIX (v9🔥)
# =========================
def ensure_sheet(name):
    try:
        return sheet.worksheet(name)
    except:
        return sheet.add_worksheet(title=name, rows="1000", cols="20")

def fix_columns(ws, expected):
    rows = ws.get_all_values()
    if not rows:
        ws.append_row(expected)
        return

    header = rows[0]
    missing = [c for c in expected if c not in header]

    if missing:
        ws.update("A1", [header + missing])

def init_sheets():
    for name, cols in SCHEMA.items():
        ws = ensure_sheet(name)
        fix_columns(ws, cols)

init_sheets()

# =========================
# PRICE FROM PRODUCT SHEET
# =========================
def get_price(product):
    try:
        ws = sheet.worksheet("product")
        rows = ws.get_all_values()
        for r in rows[1:]:
            if r[1] == product:
                return int(r[2])
    except:
        pass
    return 0

# =========================
# AI PARSER
# =========================
def parse_order(text):
    prompt = f"""
轉 JSON（可多商品）：

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
            messages=[{"role":"user","content":prompt}],
            temperature=0
        )
        return json.loads(res.choices[0].message.content)
    except:
        return None

# =========================
# MEMBER UPSERT
# =========================
def upsert_member(user_id, name, address=None):
    ws = sheet.worksheet("member")
    rows = ws.get_all_values()

    for i,r in enumerate(rows):
        if i==0: continue
        if r[0]==user_id:
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

    for item in data["items"]:
        product = item["product"]
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
# QUERY
# =========================
def query(name):
    ws = sheet.worksheet("order")
    rows = ws.get_all_values()

    text = f"📦 {name} 訂單\n\n"
    total = 0

    for r in rows[1:]:
        if len(r)<9: continue
        if r[3]==name:
            text += f"{r[4]} x{r[5]} = {r[7]}\n"
            total += int(r[7])

    text += f"\n💰 總金額：{total}"
    return text

# =========================
# INTENT
# =========================
def intent(msg):
    if msg.startswith("修正"): return "fix"
    if msg.startswith("刪除"): return "delete"
    if "查詢" in msg: return "query"
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
            elif mode == "order":
                data = parse_order(text)

                if not data:
                    reply = "❌ 無法解析訂單"
                else:
                    order_id, total = write_order(user_id,data)
                    upsert_member(user_id,data.get("name","未知"))
                    reply = f"✅ 已建立訂單\n{order_id}\n💰 {total}"

            else:
                reply = "⚠️ 功能尚未開放"

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