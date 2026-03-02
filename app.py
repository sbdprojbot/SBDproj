# app.py
from flask import Flask, request
import requests
import os
from linebot import LineBotApi, WebhookHandler
from linebot.models import TextSendMessage
from linebot.exceptions import InvalidSignatureError

# ------------------------------
# Flask app 初始化
# ------------------------------
app = Flask(__name__)

# ------------------------------
# 環境變數設定（在 Render 設定）
# ------------------------------
# LINE Channel
LINE_CHANNEL_ACCESS_TOKEN = os.getenv("LINE_CHANNEL_ACCESS_TOKEN")
LINE_CHANNEL_SECRET = os.getenv("LINE_CHANNEL_SECRET")
# Google Apps Script Web App URL
APPS_SCRIPT_URL = os.getenv("APPS_SCRIPT_URL")

# ------------------------------
# Line Bot 初始化
# ------------------------------
line_bot_api = LineBotApi(LINE_CHANNEL_ACCESS_TOKEN)
handler = WebhookHandler(LINE_CHANNEL_SECRET)

# ------------------------------
# Flask 路由：接收 Line Webhook
# ------------------------------
@app.route("/callback", methods=['POST'])
def callback():
    signature = request.headers.get('X-Line-Signature')
    body = request.get_data(as_text=True)

    try:
        handler.handle(body, signature)
    except InvalidSignatureError:
        return 'Invalid signature', 400

    return 'OK'

# ------------------------------
# Line Bot 處理訊息
# ------------------------------
@handler.add()
def handle_message(event):
    text = event.message.text

    # --------------------------
    # 出貨格式：出貨,姓名,商品,數量,價格,備註
    # --------------------------
    if text.startswith("出貨"):
        parts = text.split(",")

        if len(parts) < 4:
            line_bot_api.reply_message(
                event.reply_token,
                TextSendMessage(text="格式錯誤，請用：出貨,姓名,商品名稱,數量,備註")
            )
            return

        data = {
            "type": "shipment",
            "name": parts[1],
            "product": parts[2],
            "quantity": int(parts[3]),
            "note": parts[4] if len(parts) > 4 else ""
        }

        try:
            response = requests.post(APPS_SCRIPT_URL, json=data)
            result = response.json()
        except Exception as e:
            line_bot_api.reply_message(
                event.reply_token,
                TextSendMessage(text=f"伺服器錯誤：{e}")
            )
            return

        reply_text = "查無此商品，請確認商品名稱" if result.get("status") == "error" else "出貨資料已成功儲存"

        line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage(text=reply_text)
        )

    # --------------------------
    # 會員格式：會員,姓名,電話,生日,備註
    # --------------------------
    elif text.startswith("會員"):
        parts = text.split(",")

        if len(parts) < 5:
            line_bot_api.reply_message(
                event.reply_token,
                TextSendMessage(text="格式錯誤，請用：會員,姓名,電話,生日,備註")
            )
            return

        data = {
            "type": "member",
            "name": parts[1],
            "phone": parts[2],
            "birthday": parts[3],
            "note": parts[4]
        }

        try:
            requests.post(APPS_SCRIPT_URL, json=data)
        except Exception as e:
            line_bot_api.reply_message(
                event.reply_token,
                TextSendMessage(text=f"伺服器錯誤：{e}")
            )
            return

        line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage(text="會員資料已成功儲存")
        )

# ------------------------------
# Flask app 執行
# ------------------------------
if __name__ == "__main__":
    # Render 會自動用 gunicorn 啟動，但本地測試可以用這行
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)), debug=True)