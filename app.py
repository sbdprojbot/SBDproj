import os
import json
import uuid
import datetime
from flask import Flask, request, abort

import gspread
from google.oauth2.service_account import Credentials

from linebot import LineBotApi, WebhookHandler
from linebot.exceptions import InvalidSignatureError
from linebot.models import MessageEvent, TextMessage, TextSendMessage

from openai import OpenAI

# =========================
# ENV
# =========================

LINE_CHANNEL_ACCESS_TOKEN = os.getenv("LINE_CHANNEL_ACCESS_TOKEN")
LINE_CHANNEL_SECRET = os.getenv("LINE_CHANNEL_SECRET")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

GOOGLE_SHEET_ID = os.getenv("GOOGLE_SHEET_ID")
GOOGLE_CREDS_JSON = os.getenv("GOOGLE_CREDS_JSON")

SHEET_NAME = "SBDPROJ_SYSTEM_BD"

# =========================
# INIT APP
# =========================

app = Flask(__name__)
line_bot_api = LineBotApi(LINE_CHANNEL_ACCESS_TOKEN)
handler = WebhookHandler(LINE_CHANNEL_SECRET)
ai_client = OpenAI(api_key=OPENAI_API_KEY)

# =========================
# GOOGLE SHEETS INIT
# =========================

def init_gsheet():
    try:
        creds_dict = json.loads(GOOGLE_CREDS_JSON)
        creds = Credentials.from_service_account_info(
            creds_dict,
            scopes=["https://www.googleapis.com/auth/spreadsheets"]
        )
        client = gspread.authorize(creds)

        sheet = client.open_by_key(GOOGLE_SHEET_ID)

        orders_ws = sheet.worksheet("orders")
        logs_ws = sheet.worksheet("logs")

        print("[GSHEET INIT OK]")
        return orders_ws, logs_ws

    except Exception as e:
        print("[GSHEET INIT ERROR]", str(e))
        return None, None


ORDERS_WS, LOGS_WS = init_gsheet()

# =========================
# UTIL
# =========================

def now():
    return datetime.datetime.utcnow().isoformat()

def uid():
    return str(uuid.uuid4())

# =========================
# LOGGING (AI INCLUDED)
# =========================

def write_log(level, event, user_id="", user_input="", parsed_data="", error_msg=""):
    ai_summary = ""
    ai_suggestion = ""

    # AI fallback ONLY on error
    if level == "ERROR":
        try:
            resp = ai_client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[{
                    "role": "user",
                    "content": f"""
你是系統除錯助手，請分析錯誤：

event: {event}
error: {error_msg}
input: {user_input}
parsed: {parsed_data}

請輸出：
1. 問題原因
2. 使用者可能錯誤
3. 系統修正建議
"""
                }]
            )
            ai_summary = resp.choices[0].message.content
        except:
            ai_summary = "AI unavailable"

    row = [
        uid(),
        now(),
        now(),
        "SYSTEM",
        level,
        event,
        user_input,
        json.dumps(parsed_data, ensure_ascii=False),
        "",
        ai_summary,
        ai_suggestion,
        "OK"
    ]

    try:
        LOGS_WS.append_row(row)
    except Exception as e:
        print("[LOG WRITE FAIL]", str(e))

# =========================
# PARSER v4 (5 LAYERS)
# =========================

ALLOWED_PRODUCTS = set([
    "拿鐵",
    "紅茶",
    "奶茶"
])

# ---- Layer 1: preprocess ----
def preprocess(text):
    text = text.replace("　", " ")
    text = text.strip()
    return text

# ---- Layer 2: tokenize ----
def tokenize(text):
    items = text.split("\n")
    return [i.strip() for i in items if i.strip()]

# ---- Layer 3: normalize ----
def normalize(line):
    import re

    match = re.match(r"(.+?)[ xX*]?(\d+)$", line)
    if not match:
        return None

    product = match.group(1).strip()
    qty = match.group(2).strip()

    try:
        qty = int(qty)
    except:
        return None

    return {"product": product, "qty": qty}

# ---- Layer 4: validate ----
def validate(item):
    if not item:
        return False, "PARSE_FAIL"

    if item["qty"] <= 0 or item["qty"] > 100:
        return False, "PARSE_FAIL"

    if not item["product"]:
        return False, "PARSE_FAIL"

    return True, None

# ---- Layer 5: match ----
def match(item):
    if item["product"] not in ALLOWED_PRODUCTS:
        return False, "MATCH_FAIL"
    return True, None

# ---- FULL PIPELINE ----
def parse_input(text):
    try:
        text = preprocess(text)
        lines = tokenize(text)

        results = []

        for line in lines:
            norm = normalize(line)

            ok, err = validate(norm)
            if not ok:
                return {"ok": False, "error": {"type": err, "message": "格式錯誤"}}

            ok, err = match(norm)
            if not ok:
                return {"ok": False, "error": {"type": err, "message": "商品不存在"}}

            results.append(norm)

        return {"ok": True, "data": results}

    except Exception as e:
        return {"ok": False, "error": {"type": "EXCEPTION", "message": str(e)}}

# =========================
# ORDER WRITE
# =========================

def write_order(user_id, item):
    row = [
        uid(),
        now(),
        now(),
        user_id,
        user_id,
        item["product"],
        item["product"],
        item["qty"],
        "",
        "",
        item["qty"],
        "pending"
    ]

    try:
        ORDERS_WS.append_row(row)
    except Exception as e:
        print("[ORDER WRITE FAIL]", str(e))

# =========================
# WEBHOOK RESPONSE UX
# =========================

def reply_text(success, data=None, error=None):
    if success:
        msg = "已收到訂單：\n"
        for i in data:
            msg += f"{i['product']} x{i['qty']}\n"
        return msg

    return f"錯誤：{error.get('message','unknown')}"

# =========================
# LINE WEBHOOK
# =========================

@app.route("/callback", methods=["POST"])
def callback():
    signature = request.headers.get("X-Line-Signature")
    body = request.get_data(as_text=True)

    try:
        handler.handle(body, signature)
    except InvalidSignatureError:
        abort(400)

    return "OK"

@handler.add(MessageEvent, message=TextMessage)
def handle_message(event):
    user_id = event.source.user_id
    text = event.message.text

    result = parse_input(text)

    # FAIL
    if not result["ok"]:
        write_log(
            "ERROR",
            result["error"]["type"],
            user_id,
            text,
            result,
            result["error"]["message"]
        )

        msg = reply_text(False, error=result["error"])
        line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage(text=msg)
        )
        return

    # SUCCESS
    items = result["data"]

    for item in items:
        write_order(user_id, item)

    write_log(
        "INFO",
        "ORDER_CREATED",
        user_id,
        text,
        result
    )

    msg = reply_text(True, items)

    line_bot_api.reply_message(
        event.reply_token,
        TextSendMessage(text=msg)
    )

# =========================
# ROOT
# =========================

@app.route("/", methods=["GET"])
def home():
    return "Menu"

# =========================
# RUN
# =========================

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=10000)