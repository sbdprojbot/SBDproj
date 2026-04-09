from flask import Flask, request
import os
import requests
from linebot import LineBotApi, WebhookHandler
from linebot.models import MessageEvent, TextMessage, TextSendMessage

app = Flask(__name__)

LINE_CHANNEL_ACCESS_TOKEN = os.environ.get("LINE_CHANNEL_ACCESS_TOKEN")
LINE_CHANNEL_SECRET = os.environ.get("LINE_CHANNEL_SECRET")
HF_API_TOKEN = os.environ.get("HF_API_TOKEN")
APPS_SCRIPT_URL = os.environ.get("APPS_SCRIPT_URL")

line_bot_api = LineBotApi(LINE_CHANNEL_ACCESS_TOKEN)
handler = WebhookHandler(LINE_CHANNEL_SECRET)

@app.route("/callback", methods=['POST'])
def callback():
    body = request.get_json()
    events = body.get("events", [])
    for event in events:
        if event["type"] == "message":
            reply_token = event["replyToken"]
            user_msg = event["message"]["text"]
            handle_user_text(reply_token, user_msg)
    return "OK"

def handle_user_text(reply_token, user_text):
    # 1️⃣ 送 HuggingFace API 解析
    headers = {"Authorization": f"Bearer {HF_API_TOKEN}"}
    payload = {
        "inputs": f"將使用者指令轉成 JSON：{user_text}"
    }
    try:
        res = requests.post(
            "https://api-inference.huggingface.co/models/google/flan-t5-base",
            headers=headers,
            json=payload
        )
        result = res.json()
        json_str = result[0]["generated_text"]
        data = json.loads(json_str)
    except Exception as e:
        line_bot_api.reply_message(
            reply_token,
            TextSendMessage(text=f"解析失敗 ❌ {e}")
        )
        return

    # 2️⃣ 傳送到 Apps Script
    try:
        r = requests.post(APPS_SCRIPT_URL, json=data)
        reply_text = "資料已儲存 ✅" if r.status_code == 200 else "存檔失敗 ❌"
    except Exception as e:
        reply_text = f"連線失敗 ❌ {e}"

    # 3️⃣ 回覆 LINE 使用者
    line_bot_api.reply_message(
        reply_token,
        TextSendMessage(text=reply_text)
    )

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))