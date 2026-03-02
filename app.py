@handler.add()
def handle_message(event):
    text = event.message.text

    # 出貨格式：
    # 出貨,姓名,商品,數量,價格,備註
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

        response = requests.post(APPS_SCRIPT_URL, json=data)
        result = response.json()

        if result["status"] == "error":
            reply_text = "查無此商品，請確認商品名稱"
        else:
            reply_text = "出貨資料已成功儲存"

        line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage(text=reply_text)
        )

    # 會員格式：
    # 會員,姓名,電話,生日,備註
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

        requests.post(APPS_SCRIPT_URL, json=data)

        line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage(text="會員資料已成功儲存")
        )