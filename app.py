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
# GOOGLE SHEET INIT (SAFE)
# =========================

sheet_client = None
sheet_db = {}

def init_gsheet():
    global sheet_client, sheet_db
    try:
        if not GOOGLE_CREDS_JSON:
            print("[GSHEET INIT ERROR] GOOGLE_CREDS_JSON is empty")
            return

        creds_dict = json.loads(GOOGLE_CREDS_JSON)

        scopes = [
            "https://www.googleapis.com/auth/spreadsheets",
            "https://www.googleapis.com/auth/drive"
        ]

        creds = Credentials.from_service_account_info(creds_dict, scopes=scopes)
        sheet_client = gspread.authorize(creds)

        db = sheet_client.open_by_key(GOOGLE_SHEET_ID)

        sheet_db["User"] = db.worksheet("User")
        sheet_db["Product"] = db.worksheet("Product")
        sheet_db["Order"] = db.worksheet("Order")
        sheet_db["Log"] = db.worksheet("Log")

        print("[GSHEET INIT OK]")

    except Exception as e:
        print(f"[GSHEET INIT ERROR] {e}")
        sheet_client = None

init_gsheet()

# =========================
# UTIL
# =========================

def now():
    return datetime.datetime.now().isoformat()

def safe_log(row):
    try:
        ws = sheet_db.get("Log")
        if not ws:
            return

        ws.append_row([
            now(),
            "display",
            "system",
            "info",
            "v4",
            str(row),
            "",
            "",
            "",
            "",
            "ok"
        ])
    except Exception as e:
        print("[LOG ERROR]", e)

# =========================
# HARD COMMAND LAYER (FIX UNKNOWN ISSUE)
# =========================

def hard_router(text, user_id):
    t = text.lower().strip()

    # TEST SHEET
    if t in ["ping sheet", "test sheet", "sheet ping"]:
        return test_sheet()

    # CRUD SHORTCUTS (basic)
    if t.startswith("add user"):
        return "[USER ADD] parsed but not fully implemented safe mode"

    if t.startswith("add product"):
        return "[PRODUCT ADD] parsed but not fully implemented safe mode"

    return None

# =========================
# SHEET TEST
# =========================

def test_sheet():
    try:
        if not sheet_db:
            return "❌ SHEET NOT INIT"

        tabs = list(sheet_db.keys())
        return f"""[SHEET TEST OK]
tabs: {tabs}
status: connected
write: ready"""
    except Exception as e:
        return f"[SHEET TEST FAIL] {e}"

# =========================
# AI FALLBACK ENGINE
# =========================

def ai_parse(text):
    try:
        if not OPENAI_API_KEY:
            return "AI_DISABLED"

        prompt = f"""
You are a POS system parser.
Return JSON only.

TEXT:
{text}

Return format:
{{
  "intent": "user|product|order|log|unknown",
  "action": "create|update|delete|query",
  "missing_fields": []
}}
"""

        res = openai.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": prompt}],
            temperature=0
        )

        return res.choices[0].message.content

    except Exception as e:
        return f"AI_ERROR: {e}"

# =========================
# WEBHOOK
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

# =========================
# MESSAGE HANDLER
# =========================

@handler.add(MessageEvent, message=TextMessage)
def handle_message(event):
    text = event.message.text
    user_id = event.source.user_id

    safe_log(f"IN: {text}")

    # 1. HARD ROUTER FIRST (FIX unknown issue)
    hard = hard_router(text, user_id)
    if hard:
        line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage(text=hard)
        )
        safe_log(f"HARD: {hard}")
        return

    # 2. AI PARSE
    ai_result = ai_parse(text)

    # 3. FALLBACK RESPONSE
    reply = f"""[AI MODE]
input: {text}
result: {ai_result}
"""

    line_bot_api.reply_message(
        event.reply_token,
        TextSendMessage(text=reply)
    )

    safe_log(f"AI: {ai_result}")

# =========================
# ROOT
# =========================

@app.route("/")
def home():
    return "SBDPROJ SYSTEM BD v4 ONLINE"

# =========================
# MAIN
# =========================

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", 10000)))