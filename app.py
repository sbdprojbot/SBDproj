import os
import json
import datetime
from flask import Flask, request, abort

from linebot import LineBotApi, WebhookHandler
from linebot.exceptions import InvalidSignatureError
from linebot.models import MessageEvent, TextMessage, TextSendMessage

import gspread
from google.oauth2.service_account import Credentials
import openai

# =========================
# ENV
# =========================
LINE_CHANNEL_ACCESS_TOKEN = os.getenv("LINE_CHANNEL_ACCESS_TOKEN")
LINE_CHANNEL_SECRET = os.getenv("LINE_CHANNEL_SECRET")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
GOOGLE_SHEET_ID = os.getenv("GOOGLE_SHEET_ID")
GOOGLE_CREDS_JSON = os.getenv("GOOGLE_CREDS_JSON")

openai.api_key = OPENAI_API_KEY

# =========================
# APP INIT
# =========================
app = Flask(__name__)
line_bot_api = LineBotApi(LINE_CHANNEL_ACCESS_TOKEN)
handler = WebhookHandler(LINE_CHANNEL_SECRET)

# =========================
# GSHEET INIT
# =========================
sheet_db = {}

def init_gsheet():
    try:
        creds_dict = json.loads(GOOGLE_CREDS_JSON)
        creds = Credentials.from_service_account_info(
            creds_dict,
            scopes=[
                "https://www.googleapis.com/auth/spreadsheets",
                "https://www.googleapis.com/auth/drive"
            ]
        )
        client = gspread.authorize(creds)
        db = client.open_by_key(GOOGLE_SHEET_ID)

        # 🔒 完全對齊你 schema（小寫）
        sheet_db["product"] = db.worksheet("product")
        sheet_db["user"] = db.worksheet("user")
        sheet_db["order"] = db.worksheet("order")
        sheet_db["log"] = db.worksheet("log")
        sheet_db["metrics"] = db.worksheet("metrics")
        sheet_db["ai_cost"] = db.worksheet("ai_cost")
        sheet_db["member"] = db.worksheet("member")

        print("[GSHEET INIT OK]")

    except Exception as e:
        print("[GSHEET INIT ERROR]", e)

init_gsheet()

# =========================
# UTIL
# =========================
def now():
    return datetime.datetime.now()

def iso():
    return now().isoformat()

def display_time():
    return now().strftime("%Y-%m-%d %H:%M:%S")

def gen_id(prefix):
    return f"{prefix}_{int(now().timestamp())}"

# =========================
# LOG（完全對齊 schema）
# =========================
def write_log(stage, message, level="info", parsed="", missing="", ai_sum="", ai_sug="", status="ok"):
    try:
        ws = sheet_db["log"]
        ws.append_row([
            gen_id("log"),
            iso(),
            display_time(),
            "system",
            level,
            stage,
            message,
            parsed,
            missing,
            ai_sum,
            ai_sug,
            status
        ])
    except Exception as e:
        print("[LOG ERROR]", e)

# =========================
# PRODUCT CREATE
# =========================
def create_product(data):
    ws = sheet_db["product"]

    row = [
        gen_id("prod"),
        data.get("product"),
        data.get("price"),
        data.get("category"),
        data.get("stock", 0),
        "active",
        iso(),
        iso()
    ]

    ws.append_row(row)
    return "✅ PRODUCT CREATED"

# =========================
# USER CREATE
# =========================
def create_user(data):
    ws = sheet_db["user"]

    row = [
        gen_id("user"),
        data.get("name"),
        data.get("phone"),
        data.get("address"),
        iso(),
        iso(),
        "active"
    ]

    ws.append_row(row)
    return "✅ USER CREATED"

# =========================
# ORDER CREATE（含計算）
# =========================
def create_order(data):
    ws = sheet_db["order"]

    qty = int(data.get("qty", 1))
    price = int(data.get("unit_price", 0))

    subtotal = qty * price

    row = [
        gen_id("order"),
        iso(),
        display_time(),
        data.get("user_id"),
        data.get("name"),
        data.get("product_id"),
        data.get("product"),
        qty,
        price,
        subtotal,
        subtotal,
        "created"
    ]

    ws.append_row(row)
    return f"🧾 ORDER CREATED: {subtotal}"

# =========================
# AI PARSER
# =========================
def ai_parse(text):
    prompt = f"""
Return JSON only.

{text}

Format:
{{
 "intent": "product|user|order|unknown",
 "data": {{}}
}}
"""
    try:
        res = openai.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": prompt}],
            temperature=0
        )
        return json.loads(res.choices[0].message.content)
    except Exception as e:
        return {"intent": "unknown", "error": str(e)}

# =========================
# HARD COMMAND
# =========================
def hard_router(text):
    t = text.lower()

    if t == "ping sheet":
        return str(list(sheet_db.keys()))

    return None

# =========================
# MAIN HANDLER
# =========================
@handler.add(MessageEvent, message=TextMessage)
def handle_message(event):
    text = event.message.text

    # HARD ROUTE
    hard