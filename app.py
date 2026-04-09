from flask import Flask, request, abort
import os
import requests
import openai
from linebot import LineBotApi, WebhookHandler
from linebot.models import MessageEvent, TextMessage, TextSendMessage
from linebot.exceptions import InvalidSignatureError

app = Flask(__name__)

# -----------------------------
# 環境變數（Render 設定）
# -----------------------------
LINE_CHANNEL_ACCESS_TOKEN = os.environ.get("LINE_CHANNEL_ACCESS_TOKEN")
LINE_CHANNEL_SECRET = os.environ.get("LINE_CHANNEL_SECRET")
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY")
APPS_SCRIPT_URL = os.environ.get("APPS_SCRIPT_URL")

# 檢查環境變數
for var_name, var_value in [
    ("LINE_CHANNEL_ACCESS_TOKEN", LINE_CHANNEL_ACCESS_TOKEN),
    ("LINE_CHANNEL_SECRET", LINE_CHANNEL_SECRET),
    ("OPENAI_API_KEY", OPENAI_API_KEY),
    ("APPS_SCRIPT_URL", APPS_SCRIPT_URL)
]:
    if not var_value:
        raise ValueError(f"{var_name} 未設定！請先在 Render 環境變數設定。")

line_bot_api = LineBotApi(LINE_CHANNEL_ACCESS_TOKEN)
handler = WebhookHandler(LINE_CHANNEL_SECRET)
openai.api_key = OPENAI_API_KEY

# -----------------------------
# Webhook 接收 LINE 訊息
# -----------------------------
@app.route("/callback", methods=['POST'])
def callback():
    signature = request.headers.get('X-Line-Signature')
    body = request.get_data(as_text=True)
    try:
        handler.handle(body, signature)
    except InvalidSignatureError:
        abort(400)
    return 'OK'

# -----------------------------
# 處理文字訊息 & NLP
# -----------------------------
@handler.add(MessageEvent, message=TextMessage)
def handle_text_message(event):
    user_text = event.message.text

    # 1️⃣ 呼叫 GPT 解析自然語言 → JSON
    prompt = f"""
    將以下使用者指令轉成 JSON：
    使用者指令: "{user_text}"

    規則：
    - 出貨：type = "shipment"，包含 name, product, quantity, note
    - 會員：type = "member"，包含 name, phone, birthday, note
    - 輸出 JSON 僅包含必要欄位
    """
    try:
        response = openai.ChatCompletion.create(
            model="gpt-4-turbo",
            messages=[{"role": "user", "content": prompt}],
            temperature=0
        )
        json_str = response.choices[0].message.content.strip()
        data = eval(json_str)  # 可再加檢查安全性
    except Exception as e:
        line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage(text=f"解析失敗，請確認指令格式。\n錯誤：{e}")
        )
        return

    # 2️⃣ 傳送到 Apps Script
    try:
        res = requests.post(APPS_SCRIPT_URL, json=data)
        result = res.json()
        if result.get("status") == "success":
            reply_text = "資料已成功儲存 ✅"
        else:
            reply_text = f"資料儲存失敗 ❌ ({result.get('message')})"
    except Exception as e:
        reply_text = f"連線失敗：{e}"

    # 3️⃣ 回覆使用者
    line_bot_api.reply_message(
        event.reply_token,
        TextSendMessage(text=reply_text)
    )

# -----------------------------
# 主程式
# -----------------------------
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))