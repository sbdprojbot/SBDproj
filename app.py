from flask import Flask, request, abort
import os
import requests
from transformers import pipeline
from linebot import LineBotApi, WebhookHandler
from linebot.models import MessageEvent, TextMessage, TextSendMessage
from linebot.exceptions import InvalidSignatureError

app = Flask(__name__)

# -----------------------------
# 環境變數（Render 設定）
# -----------------------------
LINE_CHANNEL_ACCESS_TOKEN = os.environ.get("LINE_CHANNEL_ACCESS_TOKEN")
LINE_CHANNEL_SECRET = os.environ.get("LINE_CHANNEL_SECRET")
HF_API_TOKEN = os.environ.get("HF_API_TOKEN")
APPS_SCRIPT_URL = os.environ.get("APPS_SCRIPT_URL")

# 環境變數檢查
for var_name, var_value in [
    ("LINE_CHANNEL_ACCESS_TOKEN", LINE_CHANNEL_ACCESS_TOKEN),
    ("LINE_CHANNEL_SECRET", LINE_CHANNEL_SECRET),
    ("HF_API_TOKEN", HF_API_TOKEN),
    ("APPS_SCRIPT_URL", APPS_SCRIPT_URL)
]:
    if not var_value:
        raise ValueError(f"{var_name} 未設定！請先在 Render 環境變數設定。")

# -----------------------------
# LINE Bot 初始化
# -----------------------------
line_bot_api = LineBotApi(LINE_CHANNEL_ACCESS_TOKEN)
handler = WebhookHandler(LINE_CHANNEL_SECRET)

# -----------------------------
# HuggingFace NLP Pipeline
# -----------------------------
# 將自然語言轉 JSON
nlp = pipeline(
    "text2text-generation",
    model="google/flan-t5-base",
    tokenizer="google/flan-t5-base",
    use_auth_token=os.environ.get("HF_API_TOKEN")
)

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
# 處理文字訊息
# -----------------------------
@handler.add(MessageEvent, message=TextMessage)
def handle_text_message(event):
    user_text = event.message.text

    # 1️⃣ NLP 解析使用者指令
    prompt = f"""
    將使用者指令轉成 JSON：
    使用者指令: "{user_text}"

    規則：
    - 出貨：type="shipment"，包含 name, product, quantity, note
    - 會員：type="member"，包含 name, phone, birthday, note
    - JSON 僅保留必要欄位
    """
    try:
        result = nlp(prompt, max_length=512)[0]["generated_text"]
        data = eval(result)  # 可改成 json.loads(result) 更安全
    except Exception as e:
        line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage(text=f"解析失敗 ❌，請確認指令格式。\n錯誤：{e}")
        )
        return

    # 2️⃣ 傳送 JSON 到 Apps Script
    try:
        res = requests.post(APPS_SCRIPT_URL, json=data)
        result = res.json()
        if result.get("status") == "success":
            reply_text = "資料已成功儲存 ✅"
        else:
            reply_text = f"資料儲存失敗 ❌ ({result.get('message')})"
    except Exception as e:
        reply_text = f"連線失敗：{e}"

    # 3️⃣ 回覆 LINE 使用者
    line_bot_api.reply_message(
        event.reply_token,
        TextSendMessage(text=reply_text)
    )

# -----------------------------
# 主程式
# -----------------------------
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))