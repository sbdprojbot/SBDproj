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
# 🧠 SCHEMA
# =========================

SCHEMA = {
    "create_order": ["product", "qty"],
    "create_product": ["product", "price"],
    "create_user": ["name", "phone"]
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
    "份":"set",
    "碗":"bowl"
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
            break

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
# 🧠 TIME
# =========================

def now():
    return int(time.time()), datetime.now().strftime("%Y-%m-%d %H:%M:%S")

# =========================
# 🧠 DUPLICATE
# =========================

def is_duplicate(event_id):
    if not event_id:
        return True
    if event_id in processed_events:
        return True
    processed_events.add(event_id)
    return False

# =========================
# 🤖 AI (CONTROLLED)
# =========================

def ai_missing(fields):
    return f"⚠️ 請補資料：{', '.join(fields)}"

def ai_log(msg):
    return f"LOG分析：{msg}"

# =========================
# 🧪 VALIDATION
# =========================

def validate(action, data):

    required = SCHEMA.get(action, [])
    missing = []

    for f in required:
        if f not in data or data[f] in ["", None, 0]:
            missing.append(f)

    return len(missing) == 0, missing

# =========================
# 📊 LOG (mock)
# =========================

def write_log(log):
    print("LOG:", log)

# =========================
# 🧠 CORE ENGINE
# =========================

def handle_event(event):

    event_id = event.get("id","")
    text = event.get("message","")

    if is_duplicate(event_id):
        return "duplicate ignored"

    parsed = unit_engine(text)

    ok, missing = validate("create_order", parsed)

    if not ok:
        return ai_missing(missing)

    ts, dt = now()

    write_log({
        "id": event_id,
        "product": parsed["product"],
        "qty": parsed["qty"],
        "unit": parsed["unit"],
        "time": dt
    })

    return f"✔ 已建立訂單：{parsed['product']} x{parsed['qty']} ({parsed['unit']})"

# =========================
# 🌐 ROUTE
# =========================

@app.route("/", methods=["GET"])
def home():
    return "LINE POS READY"

@app.route("/webhook", methods=["POST"])
def webhook():

    data = request.get_json()

    return handle_event(data)

# =========================
# RUN
# =========================

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=10000)