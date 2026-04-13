import os
import json
import traceback
from datetime import datetime

from flask import Flask, request, abort

# LINE
from linebot import LineBotApi, WebhookHandler
from linebot.exceptions import InvalidSignatureError
from linebot.models import MessageEvent, TextMessage, TextSendMessage

# Google Sheet
import gspread
from oauth2client.service_account import ServiceAccountCredentials

# OpenAI（可選）
from openai import OpenAI

# ======================
# ENV
# ======================
LINE_CHANNEL_ACCESS_TOKEN = os.getenv("LINE_CHANNEL_ACCESS_TOKEN")
LINE_CHANNEL_SECRET = os.getenv("LINE_CHANNEL_SECRET")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

# Sheet config（不用填死，會自動找）
TARGET_SHEET_NAME = os.getenv("SHEET_NAME", "")
GOOGLE_CREDS_JSON = os.getenv("GOOGLE_CREDS_JSON")

# ======================
# INIT
# ======================
app = Flask(__name__)

line_bot_api = LineBotApi(LINE_CHANNEL_ACCESS_TOKEN)
handler = WebhookHandler(LINE_CHANNEL_SECRET)

openai_client = OpenAI(api_key=OPENAI_API_KEY) if OPENAI_API_KEY else None

sheet = None


# ======================
# LOG SYSTEM
# ======================
def log(event, data=None):
    print(json.dumps({
        "time": datetime.utcnow().isoformat(),
        "event": event,
        "data": data
    }, ensure_ascii=False))


# ======================
# SHEET AUTO RESOLVER
# ======================
def init_sheet():
    global sheet

    try:
        creds_dict = json.loads(GOOGLE_CREDS_JSON)

        scope = [
            "https://spreadsheets.google.com/feeds",
            "https://www.googleapis.com/auth/drive"
        ]

        creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scope)
        client = gspread.authorize(creds)

        spreadsheets = client.openall()

        # 🎯 1. 指定名稱優先
        if TARGET_SHEET_NAME:
            for s in spreadsheets:
                if TARGET_SHEET_NAME in s.title:
                    log("SHEET_FOUND_BY_NAME", s.title)
                    sheet = s.sheet1
                    return

        # 🎯 2. fallback：抓第一個
        if spreadsheets:
            log("SHEET_FALLBACK_FIRST", spreadsheets[0].title)
            sheet = spreadsheets[0].sheet1
            return

        log("SHEET_NOT_FOUND")

    except Exception as e:
        log("SHEET_INIT_FAIL", str(e))
        sheet = None


# ======================
# WRITE SHEET (RETRY)
# ======================
def write_sheet_safe(row):
    global sheet

    for i in range(3):
        try:
            if not sheet:
                init_sheet()

            if not sheet:
                raise Exception("sheet not ready")

            sheet.append_row(row)
            log("SHEET_WRITE_OK", row)
            return True

        except Exception as e:
            log("SHEET_WRITE_FAIL", {"retry": i, "error": str(e)})
            sheet = None

    return False


# ======================
# AI（可選）
# ======================
def ai_reply(text):
    if not openai_client:
        return "（AI 未啟用）"

    try:
        res = openai_client.chat.completions.create(
            model="gpt-4.1-mini",
            messages=[{"role": "user", "content": text}]
        )
        return res.choices[0].message.content

    except Exception as e:
        log("AI_FAIL", str(e))
        return "AI 發生錯誤"


# ======================
# CALLBACK
# ======================
@app.route("/callback", methods=["POST"])
def callback():
    body = request.get_data(as_text=True)
    signature = request.headers.get("X-Line-Signature")

    log("WEBHOOK_IN", body)

    try:
        handler.handle(body, signature)
    except InvalidSignatureError:
        log("SIGNATURE_FAIL")
        return "signature error", 400
    except Exception as e:
        log("WEBHOOK_ERROR", str(e))
        return "error", 500

    return "OK"


# ======================
# MESSAGE HANDLER
# ======================
@handler.add(MessageEvent, message=TextMessage)
def handle_message(event):
    text = event.message.text.strip()

    log("USER_MSG", text)

    # ======================
    # /heal（系統自檢）
    # ======================
    if text.lower() == "/heal":
        try:
            init_sheet()

            status = {
                "sheet": "OK" if sheet else "FAIL",
                "ai": "ON" if openai_client else "OFF"
            }

            reply = f"System Check:\nSheet: {status['sheet']}\nAI: {status['ai']}"
            send_reply(event, reply)

        except Exception as e:
            send_reply(event, f"heal error: {str(e)}")

        return

    # ======================
    # 一般訊息 → AI
    # ======================
    ai_text = ai_reply(text)

    # ======================
    # 寫入 Sheet（不中斷流程）
    # ======================
    row = [
        datetime.now().isoformat(),
        text,
        ai_text
    ]

    write_sheet_safe(row)

    send_reply(event, ai_text)


# ======================
# REPLY SAFE
# ======================
def send_reply(event, text):
    try:
        line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage(text=text[:5000])
        )
    except Exception as e:
        log("REPLY_FAIL", str(e))


# ======================
# ROOT（避免 404 誤判）
# ======================
@app.route("/", methods=["GET"])
def home():
    return "OK"


# ======================
# STARTUP
# ======================
init_sheet()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080)