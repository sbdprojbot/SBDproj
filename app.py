from flask import Flask, request, jsonify
import os, json, uuid, re, time
from datetime import datetime
import threading

import gspread
from oauth2client.service_account import ServiceAccountCredentials

from linebot import LineBotApi
from linebot.models import TextSendMessage

import openai

app = Flask(__name__)

# =========================
LINE_TOKEN = os.getenv("LINE_CHANNEL_ACCESS_TOKEN")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
SHEET_ID = os.getenv("GOOGLE_SHEET_ID")

line_bot_api = LineBotApi(LINE_TOKEN)
openai.api_key = OPENAI_API_KEY

# =========================
lock = threading.Lock()
session = {}
seen = {}

SESSION_TTL = 600  # 10 min

# =========================
scope = [
    "https://spreadsheets.google.com/feeds",
    "https://www.googleapis.com/auth/drive"
]

creds = ServiceAccountCredentials.from_json_keyfile_dict(
    json.loads(os.getenv("GOOGLE_CREDS_JSON")),
    scope
)

gs = gspread.authorize(creds)
sheet = gs.open_by_key(SHEET_ID)

# =========================
def ws(name, cols):
    try:
        w = sheet.worksheet(name)
    except:
        w = sheet.add_worksheet(title=name, rows=5000, cols=len(cols))
        w.append_row(cols)
    return w

user_ws = ws("user", ["user_id","name","phone","address","time"])
product_ws = ws("product", ["product_id","product","price","status","time"])
order_ws = ws("order", ["order_id","user","product","qty","price","total","status","time"])

# =========================
def now():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")

def safe_append(ws, row):
    with lock:
        ws.append_row(row)

def uid_key(uid):
    return uid

# =========================
# SESSION ENGINE
# =========================
def get_session(uid):
    s = session.get(uid)

    if not s:
        s = {
            "state": "IDLE",
            "buffer": {},
            "ts": time.time()
        }
        session[uid] = s

    # TTL reset
    if time.time() - s["ts"] > SESSION_TTL:
        s["state"] = "IDLE"
        s["buffer"] = {}

    s["ts"] = time.time()
    return s

# =========================
# INTENT DETECTOR
# =========================
def detect_intent(text):

    if "查" in text or "訂單" in text:
        return "QUERY"

    if any(k in text for k in ["買","要","訂"]):
        return "ORDER"

    return "CHAT"

# =========================
# PARSER
# =========================
def parse_full_order(text):

    product = None
    qty = None

    if "紅茶" in text:
        product = "紅茶"

    m = re.search(r"\d+", text)
    if m:
        qty = int(m.group())

    return product, qty

# =========================
def is_confirm(text):
    return text.lower() in ["確認","ok","okay","yes","y","好"]

# =========================
def get_price(product):

    rows = product_ws.get_all_records()
    for r in rows:
        if r.get("product") == product:
            return float(r.get("price",0))
    return None

# =========================
# STATE MACHINE
# =========================
def state_router(text, sess):

    state = sess["state"]

    # FULL SENTENCE FAST PATH
    product, qty = parse_full_order(text)
    if product and qty:
        sess["buffer"] = {"product": product, "qty": qty}
        sess["state"] = "CONFIRM"
        return f"{product} {qty}杯，是否確認？"

    # IDLE
    if state == "IDLE":

        intent = detect_intent(text)

        if intent == "ORDER":
            product, _ = parse_full_order(text)

            if not product:
                sess["state"] = "WAIT_PRODUCT"
                return "請問要買什麼？"

            sess["buffer"]["product"] = product
            sess["state"] = "WAIT_QTY"
            return "要幾杯？"

        if intent == "QUERY":
            return "🔍 查詢功能處理中"

        return "你好～請告訴我要買什麼"

    # WAIT_PRODUCT
    if state == "WAIT_PRODUCT":
        product, _ = parse_full_order(text)

        if not product:
            return "請提供商品名稱"

        sess["buffer"]["product"] = product
        sess["state"] = "WAIT_QTY"
        return "要幾杯？"

    # WAIT_QTY
    if state == "WAIT_QTY":

        product = sess["buffer"].get("product")
        m = re.search(r"\d+", text)

        if not m:
            return "請輸入數量"

        qty = int(m.group())

        sess["buffer"]["qty"] = qty
        sess["state"] = "CONFIRM"

        return f"{product} {qty}杯，是否確認？"

    # CONFIRM
    if state == "CONFIRM":

        if is_confirm(text):

            sess["state"] = "DONE"
            return "🧾 已完成訂單"

        sess["state"] = "IDLE"
        sess["buffer"] = {}
        return "已取消"

    # DONE
    if state == "DONE":
        sess["state"] = "IDLE"
        sess["buffer"] = {}
        return "需要再下一單嗎？"

    return "⚠️ 無法理解"

# =========================
def create_order(uid, sess):

    oid = "d" + uuid.uuid4().hex[:6]

    product = sess["buffer"].get("product")
    qty = sess["buffer"].get("qty", 1)

    price = get_price(product)

    if price is None:
        return "⚠️ 產品不存在"

    total = price * qty

    safe_append(order_ws,[
        oid,
        uid,
        product,
        qty,
        price,
        total,
        "ok",
        now()
    ])

    return f"🧾 OK {oid} ${total}"

# =========================
@app.route("/callback", methods=["POST"])
def callback():

    body = request.get_json()

    for e in body.get("events", []):

        uid = e["source"]["userId"]
        mid = e["message"]["id"]
        text = e["message"]["text"]

        key = uid + mid
        if key in seen:
            continue
        seen[key] = time.time()

        sess = get_session(uid)

        reply = state_router(text, sess)

        # CONFIRM trigger order
        if "已完成訂單" in reply:
            reply = create_order(uid, sess)

        try:
            line_bot_api.reply_message(
                e["replyToken"],
                TextSendMessage(text=reply)
            )
        except:
            pass

    return "OK"

# =========================
@app.route("/health")
def health():
    return jsonify({
        "status": "ok",
        "version": "v20.9.3-stable",
        "mode": "state-machine-pro"
    })

@app.route("/")
def home():
    return "v20.9.3 STABLE READY"