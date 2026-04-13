import os
import json
import time
import uuid
import queue
import threading
import traceback
from datetime import datetime

from flask import Flask, request, abort

import gspread
from google.oauth2.service_account import Credentials

# =========================
# CONFIG
# =========================

APP_NAME = "SBDPROJ_SYSTEM_BD"

SPREADSHEET_ID = os.getenv("SHEET_ID")  # 已凍結
LINE_CHANNEL_SECRET = os.getenv("LINE_CHANNEL_SECRET")
LINE_CHANNEL_ACCESS_TOKEN = os.getenv("LINE_CHANNEL_ACCESS_TOKEN")

MAX_RETRY = 3
QUEUE_SLEEP = 0.5

# =========================
# APP INIT
# =========================

app = Flask(__name__)

# =========================
# GOOGLE SHEETS INIT
# =========================

def init_gsheet():
    try:
        creds_dict = json.loads(os.getenv("GOOGLE_SERVICE_ACCOUNT"))
        creds = Credentials.from_service_account_info(
            creds_dict,
            scopes=["https://www.googleapis.com/auth/spreadsheets"]
        )
        client = gspread.authorize(creds)

        sheet = client.open_by_key(SPREADSHEET_ID)

        return sheet

    except Exception as e:
        print("[GSHEET INIT ERROR]", e)
        return None


gsheet = init_gsheet()

# =========================
# SHEET GETTERS
# =========================

def ws(name):
    try:
        return gsheet.worksheet(name)
    except Exception:
        return None


def now():
    return datetime.utcnow().isoformat()


# =========================
# RETRY CORE
# =========================

def retry(fn):
    def wrapper(*args, **kwargs):
        last_err = None
        for i in range(MAX_RETRY):
            try:
                return fn(*args, **kwargs)
            except Exception as e:
                last_err = e
                time.sleep(2 ** i)
        raise last_err
    return wrapper


# =========================
# LOG SYSTEM (ONLY STORAGE)
# =========================

def log_event(level="INFO", stage="SYSTEM", message="", parsed=None, missing=None, ai_summary="", ai_suggestion="", status="OK", _type="LOG"):
    try:
        sheet = ws("Log")
        if not sheet:
            return

        sheet.append_row([
            str(uuid.uuid4()),
            now(),
            now(),
            _type,
            level,
            stage,
            message,
            json.dumps(parsed, ensure_ascii=False),
            json.dumps(missing, ensure_ascii=False),
            ai_summary,
            ai_suggestion,
            status
        ])
    except Exception:
        pass


# =========================
# DEAD LETTER QUEUE (DLQ)
# =========================

DLQ = []


def push_dlq(payload, err):
    DLQ.append({
        "payload": payload,
        "error": str(err),
        "time": now()
    })

    log_event(
        level="ERROR",
        stage="DLQ",
        message=str(err),
        parsed=payload,
        status="DLQ"
    )


# =========================
# QUEUE SYSTEM (NO REDIS)
# =========================

task_queue = queue.Queue()


def worker():
    while True:
        task = task_queue.get()
        try:
            task()
        except Exception as e:
            push_dlq(str(task), e)
        task_queue.task_done()
        time.sleep(QUEUE_SLEEP)


threading.Thread(target=worker, daemon=True).start()


def enqueue(fn):
    task_queue.put(fn)


# =========================
# SHEET SAFE OPS
# =========================

@retry
def insert_row(sheet_name, row):
    sheet = ws(sheet_name)
    if not sheet:
        raise Exception(f"Sheet missing: {sheet_name}")
    sheet.append_row(row)


# =========================
# BUSINESS OPS (CORE)
# =========================

def create_product(data):
    def task():
        insert_row("Product", [
            data.get("Product_id", str(uuid.uuid4())),
            data.get("product"),
            data.get("price"),
            data.get("category"),
            data.get("stock", 0),
            data.get("status", "active"),
            now(),
            now()
        ])

        log_event(stage="PRODUCT_CREATE", message="created", parsed=data)

    enqueue(task)


def create_order(data):
    def task():
        insert_row("Order", [
            data.get("Order_id", str(uuid.uuid4())),
            now(),
            now(),
            data.get("user_id"),
            data.get("name"),
            data.get("product_id"),
            data.get("product"),
            data.get("qty", 1),
            data.get("unit_price", 0),
            data.get("subtotal", 0),
            data.get("order_total", 0),
            data.get("status", "created")
        ])

        log_event(stage="ORDER_CREATE", message="created", parsed=data)

    enqueue(task)


def create_user(data):
    def task():
        insert_row("User", [
            data.get("User_id", str(uuid.uuid4())),
            data.get("name"),
            data.get("phone"),
            data.get("address"),
            now(),
            now(),
            data.get("status", "active")
        ])

        log_event(stage="USER_CREATE", message="created", parsed=data)

    enqueue(task)


# =========================
# SIMPLE PARSER (LINE NLP v4 light)
# =========================

def parse_text(text):
    text = text.strip()

    # product create
    if text.startswith("add product"):
        parts = text.split()
        return {
            "type": "product",
            "action": "create",
            "product": parts[2] if len(parts) > 2 else None
        }

    # order create
    if text.startswith("order"):
        return {
            "type": "order",
            "raw": text
        }

    # user create
    if text.startswith("user"):
        return {
            "type": "user",
            "raw": text
        }

    return {
        "type": "unknown",
        "raw": text
    }


# =========================
# AI LAYER (OPTIONAL SAFE)
# =========================

def ai_analyze(text, parsed):
    try:
        # optional: plug OpenAI later
        return {
            "summary": "parsed",
            "suggestion": "ok"
        }
    except:
        return None


# =========================
# LINE WEBHOOK
# =========================

@app.route("/webhook", methods=["POST"])
def webhook():
    body = request.get_data(as_text=True)

    log_event(stage="WEBHOOK_IN", message=body)

    try:
        data = json.loads(body)
    except:
        return "OK"

    # simplified handler
    msg = data.get("message", {}).get("text", "")
    parsed = parse_text(msg)

    ai = ai_analyze(msg, parsed)

    log_event(
        stage="PARSE",
        message=msg,
        parsed=parsed,
        ai_summary=ai.get("summary") if ai else "",
        ai_suggestion=ai.get("suggestion") if ai else ""
    )

    # route
    if parsed["type"] == "product":
        create_product(parsed)

    elif parsed["type"] == "order":
        create_order(parsed)

    elif parsed["type"] == "user":
        create_user(parsed)

    return "OK"


# =========================
# HEALTH CHECK
# =========================

@app.route("/", methods=["GET"])
def health():
    return {"status": "ok", "app": APP_NAME}


# =========================
# START
# =========================

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", 10000)))