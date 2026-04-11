from flask import Flask, request
import time
import re
from datetime import datetime

app = Flask(__name__)

# =========================
# 🧠 STATE
# =========================

processed_events = set()

# =========================
# 🧠 SCHEMA ENGINE
# =========================

SCHEMA = {
    "order": ["product", "qty"],
    "product": ["product", "price"],
    "user": ["name", "phone"]
}

# =========================
# 🧠 UNIT ENGINE
# =========================

NUM_MAP = {
    "一":1,"壹":1,"1":1,
    "兩":2,"二":2,"2":2,
    "三":3,"3":3,
    "四":4,"4":4,
    "五":5,"5":5,
    "六":6,"6":6,
    "七":7,"7":7,
    "八":8,"8":8,
    "九":9,"9":9,
    "十":10,"10":10
}

UNIT_MAP = {
    "杯":"cup",
    "瓶":"bottle",
    "個":"piece",
    "份":"set"
}

NOISE = ["我要","幫我","請","來","買","給我"]

def clean(text):
    for w in NOISE:
        text = text.replace(w,"")
    return text.strip()

def parse_unit(text):

    qty = 1
    unit = "piece"

    for k,v in NUM_MAP.items():
        if k in text:
            qty = v

    for k,v in UNIT_MAP.items():
        if k in text:
            unit = v

    m = re.search(r"\d+", text)
    if m:
        qty = int(m.group())

    return qty, unit

def unit_engine(text):

    text = clean(text)

    qty, unit = parse_unit(text)

    product = text

    for k in list(NUM_MAP.keys()) + list(UNIT_MAP.keys()):
        product = product.replace(k,"")

    return {
        "product": product.strip(),
        "qty": qty,
        "unit": unit
    }

# =========================
# 🧠 DEDUP
# =========================

def is_duplicate(event_id):
    if not event_id:
        return True
    if event_id in processed_events:
        return True
    processed_events.add(event_id)
    return False

# =========================
# 🧠 TIME
# =========================

def now():
    return int(time.time()), datetime.now().strftime("%Y-%m-%d %H:%M:%S")

# =========================
# 🧠 VALIDATION
# =========================

def validate(action, data):

    required = SCHEMA.get(action, [])
    missing = []

    for f in required:
        if f not in data or data[f] in ["", None, 0]:
            missing.append(f)

    return len(missing) == 0, missing

# =========================
# 🤖 AI LOG (SAFE ONLY)
# =========================

def ai_log(log):

    if log["type"] == "MISSING":
        return "缺少欄位：" + ",".join(log.get("missing_fields", []))

    if log["type"] == "ERROR":
        return "系統錯誤，需要檢查Sheet / API"

    return "正常運行"

# =========================
# 📊 LOG BUILDER
# =========================

def build_log(event, parsed, log_type, missing=None):

    ts, dt = now()

    log = {
        "log_id": event.get("id",""),
        "timestamp": ts,
        "display_time": dt,
        "type": log_type,
        "level": "INFO" if log_type=="ORDER" else "WARN",
        "stage": "ENGINE",
        "message": event.get("message",""),
        "parsed": parsed,
        "missing_fields": missing or [],
        "ai_summary": "",
        "ai_suggestion": ""
    }

    log["ai_summary"] = ai_log(log)
    log["ai_suggestion"] = "check schema or sheet"

    return log

# =========================
# 🧠 CORE ENGINE
# =========================

def handle_event(event):

    event_id = event.get("id","")
    text = event.get("message","")

    if is_duplicate(event_id):
        return "duplicate ignored"

    parsed = unit_engine(text)

    ok, missing = validate("order", parsed)

    if not ok:
        log = build_log(event, parsed, "MISSING", missing)
        return "⚠️ 請補資料：" + ",".join(missing)

    log = build_log(event, parsed, "ORDER")

    return f"✔ 訂單成立：{parsed['product']} x{parsed['qty']} ({parsed['unit']})"

# =========================
# 🌐 ROUTE
# =========================

@app.route("/", methods=["GET"])
def home():
    return "LINE POS SYSTEM READY"

@app.route("/webhook", methods=["POST"])
def webhook():
    data = request.get_json()
    return handle_event(data)

# =========================
# 🚀 RUN
# =========================

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=10000)