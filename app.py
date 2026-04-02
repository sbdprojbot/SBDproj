from flask import Flask, request, abort
import requests
import os

app = Flask(__name__)

CHANNEL_ACCESS_TOKEN = os.environ.get("CHANNEL_ACCESS_TOKEN")

@app.route("/callback", methods=['POST'])
def callback():
    body = request.json

    # 取得 replyToken & 使用者訊息
    events = body.get("events", [])
    for event in events:
        if event["type"] == "message":
            reply_token = event["replyToken"]
            user_msg = event["message"]["text"]

            reply_message(reply_token, f"你說的是：{user_msg}")

    return "OK"


def reply_message(reply_token, text):
    url = "https://api.line.me/v2/bot/message/reply"

    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {CHANNEL_ACCESS_TOKEN}"
    }

    data = {
        "replyToken": reply_token,
        "messages": [
            {
                "type": "text",
                "text": text
            }
        ]
    }

    requests.post(url, headers=headers, json=data)


if __name__ == "__main__":
    app.run()