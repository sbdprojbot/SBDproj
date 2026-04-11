import time
from datetime import datetime

# =========================
# 🧠 GLOBAL SAFETY STATE
# =========================

processed_events = set()
MAX_RETRY = 3

ALLOWED_ACTIONS = {
    "create_order",
    "create_user",
    "create_product",
    "query",
    "update",
    "delete"
}

# =========================
# 🟢 DUPLICATE GUARD
# =========================

def is_duplicate(event_id):
    if event_id in processed_events:
        return True
    processed_events.add(event_id)
    return False

# =========================
# 🕒 TIME ENGINE (LOCKED)
# =========================

def get_time():

    ts = int(time.time())
    dt = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    return ts, dt

# =========================
# 🧠 SAFE NLP PARSER
# =========================

def safe_parse(ai_output):

    try:
        if not ai_output or not isinstance(ai_output, dict):
            return fallback("invalid_ai_output")

        if not ai_output.get("ok"):
            return fallback(ai_output.get("reason", "ai_failed"))

        if ai_output.get("action") not in ALLOWED_ACTIONS:
            return fallback("invalid_action")

        return ai_output

    except Exception:
        return fallback("parse_exception")


def fallback(reason):
    return {
        "action": "fallback",
        "reason": reason,
        "suggestion": "請使用：買 / 查 / 改 / 刪"
    }

# =========================
# 📊 LOG WRITER
# =========================

def write_log(sheet, log):

    ts, dt = get_time()

    row = [
        log.get("log_id", "")[:6],
        ts,
        dt,
        log.get("type", ""),
        log.get("event_id", ""),
        log.get("stage", ""),
        log.get("message", ""),
        log.get("ai_analysis", ""),
        log.get("status", "ok")
    ]

    safe_write(sheet, row)

# =========================
# 🧾 SHEET SAFE WRITE
# =========================

def safe_write(sheet, row):

    for _ in range(MAX_RETRY):
        try:
            sheet.append_row(row)
            return True
        except Exception:
            time.sleep(0.5)

    return False

# =========================
# 🧠 CORE HANDLER
# =========================

def handle_event(event, sheet_order, sheet_log):

    event_id = event.get("id")

    # 🟢 1. 防重複
    if is_duplicate(event_id):
        return "DUPLICATE IGNORED"

    # 🟢 2. AI parse
    ai = call_ai(event.get("message", ""))
    parsed = safe_parse(ai)

    # 🟢 3. fallback
    if parsed["action"] == "fallback":

        write_log(sheet_log, {
            "log_id": event_id,
            "type": "FALLBACK",
            "event_id": event_id,
            "stage": "NLP",
            "message": parsed["reason"],
            "ai_analysis": parsed["suggestion"],
            "status": "ok"
        })

        return "⚠️ 請補資料"

    # 🟢 4. ROUTER
    try:

        if parsed["action"] == "create_order":
            result = create_order(parsed, sheet_order)

        elif parsed["action"] == "create_user":
            result = create_user(parsed)

        elif parsed["action"] == "create_product":
            result = create_product(parsed)

        elif parsed["action"] == "query":
            result = query(parsed)

        elif parsed["action"] == "update":
            result = update(parsed)

        elif parsed["action"] == "delete":
            result = delete(parsed)

        write_log(sheet_log, {
            "log_id": event_id,
            "type": "DONE",
            "event_id": event_id,
            "stage": "ENGINE",
            "message": "success",
            "ai_analysis": parsed["action"],
            "status": "ok"
        })

        return result

    except Exception as e:

        write_log(sheet_log, {
            "log_id": event_id,
            "type": "ERROR",
            "event_id": event_id,
            "stage": "EXCEPTION",
            "message": str(e),
            "ai_analysis": "system_error",
            "status": "fail"
        })

        return "⚠️ 系統錯誤"

# =========================
# 🧠 PLACEHOLDERS
# =========================

def call_ai(text):
    return {"ok": False}

def create_order(parsed, sheet):
    return "ORDER OK"

def create_user(parsed):
    return "USER OK"

def create_product(parsed):
    return "PRODUCT OK"

def query(parsed):
    return "QUERY OK"

def update(parsed):
    return "UPDATE OK"

def delete(parsed):
    return "DELETE OK"