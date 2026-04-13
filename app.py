import os, json, time, hmac, hashlib
from datetime import datetime
from flask import Flask, request, abort

import gspread

# =========================
# CONFIG
# =========================
CHANNEL_SECRET = os.getenv("LINE_CHANNEL_SECRET")
QUEUE_FILE = "order_queue.json"
CACHE_TTL = 10

app = Flask(__name__)

CACHE = {
    "ws": None,
    "ts": 0
}

# =========================
# LOG
# =========================
def log(event, msg):
    print(f"{event} | {msg}", flush=True)

# =========================
# LINE VERIFY
# =========================
def verify_signature(body, signature):
    hash = hmac.new(
        CHANNEL_SECRET.encode('utf-8'),
        body,
        hashlib.sha256
    ).digest()

    import base64
    expected = base64.b64encode(hash)

    return hmac.compare_digest(expected, signature.encode('utf-8'))

# =========================
# QUEUE (永不丟資料)
# =========================
def load_queue():
    if not os.path.exists(QUEUE_FILE):
        return []
    try:
        with open(QUEUE_FILE, "r") as f:
            return json.load(f)
    except:
        return []

def save_queue(q):
    with open(QUEUE_FILE, "w") as f:
        json.dump(q, f)

def enqueue(data):
    q = load_queue()
    q.append({
        "data": data,
        "ts": datetime.utcnow().isoformat()
    })
    save_queue(q)
    log("QUEUE_ADD", data)

# =========================
# SHEET RESOLVER（核心）
# =========================
def get_client():
    try:
        return gspread.service_account(filename="service_account.json")
    except Exception as e:
        log("AUTH_FAIL", str(e))
        return None

def resolve_sheet(force=False):
    now = time.time()

    if CACHE["ws"] and not force and now - CACHE["ts"] < CACHE_TTL:
        return CACHE["ws"]

    gc = get_client()
    if not gc:
        return None

    try:
        sheets = gc.openall()
        log("SCAN", [s.title for s in sheets])

        target = None
        keywords = ["訂單", "order", "sheet"]

        for k in keywords:
            for s in sheets:
                if k.lower() in s.title.lower():
                    target = s
                    break

        if not target and sheets:
            target = sheets[0]

        if not target:
            raise Exception("no sheet found")

        try:
            ws = target.worksheet("orders")
        except:
            ws = target.add_worksheet(title="orders", rows="1000", cols="20")
            log("WS_CREATE", "orders")

        CACHE["ws"] = ws
        CACHE["ts"] = now

        return ws

    except Exception as e:
        log("RESOLVE_FAIL", str(e))
        return None

# =========================
# WRITE（主寫入 + fallback）
# =========================
def write_row(data):
    try:
        ws = resolve_sheet()
        if not ws:
            raise Exception("no worksheet")

        ws.append_row(data)
        log("WRITE_OK", data)
        return True

    except Exception as e:
        log("WRITE_FAIL", str(e))
        enqueue(data)
        return False

# =========================
# RETRY（補寫）
# =========================
def retry_queue():
    q = load_queue()
    if not q:
        return

    log("RETRY_START", len(q))

    new_q = []

    for item in q:
        try:
            ws = resolve_sheet(force=True)
            if not ws:
                raise Exception("no sheet")

            ws.append_row(item["data"])
            log("RETRY_OK", item["data"])

        except Exception as e:
            log("RETRY_FAIL", str(e))
            new_q.append(item)

    save_queue(new_q)

# =========================
# HEAL
# =========================
def heal():
    log("HEAL", "start")
    resolve_sheet(force=True)
    retry_queue()
    log("HEAL", "done")

# =========================
# LINE HANDLER
# =========================
@app.route("/callback", methods=['POST'])
def callback():
    body = request.get_data()
    signature = request.headers.get('X-Line-Signature', '')

    if not verify_signature(body, signature):
        abort(400)

    data = request.get_json()

    try:
        for event in data.get("events", []):
            if event["type"] != "message":
                continue

            text = event["message"]["text"]

            # =========================
            # OPS COMMAND
            # =========================
            if text == "/report":
                return {
                    "queue": load_queue()
                }

            if text == "/heal":
                heal()
                return "heal done"

            if text == "/queue":
                return f"queue size: {len(load_queue())}"

            # =========================
            # NORMAL WRITE
            # =========================
            row = [
                datetime.utcnow().isoformat(),
                text
            ]

            write_row(row)

    except Exception as e:
        log("LINE_FAIL", str(e))

    return "OK"

# =========================
# BACKGROUND SELF HEAL（輕量）
# =========================
@app.before_request
def auto_heal():
    try:
        retry_queue()
    except:
        pass

# =========================
# RUN
# =========================
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=3000)