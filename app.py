import os
import json
import time
import threading
import requests
from flask import Flask, request, jsonify, send_from_directory
from datetime import datetime

app = Flask(__name__, static_folder='static')

# ─── Storage (simple JSON file) ───────────────────────────────────────────────
DATA_FILE = "alerts.json"

def load_data():
    if os.path.exists(DATA_FILE):
        with open(DATA_FILE, "r") as f:
            return json.load(f)
    return {"alerts": [], "telegram": {"bot_token": "", "chat_id": ""}}

def save_data(data):
    with open(DATA_FILE, "w") as f:
        json.dump(data, f, indent=2)

# ─── Price Fetching ────────────────────────────────────────────────────────────
def get_crypto_price(symbol):
    """Get crypto price from Binance (free, no API key needed)"""
    try:
        symbol_upper = symbol.upper()
        # Try USDT pair first
        url = f"https://api.binance.com/api/v3/ticker/price?symbol={symbol_upper}USDT"
        r = requests.get(url, timeout=10)
        if r.status_code == 200:
            return float(r.json()["price"])
        # Try USDC pair
        url = f"https://api.binance.com/api/v3/ticker/price?symbol={symbol_upper}USDC"
        r = requests.get(url, timeout=10)
        if r.status_code == 200:
            return float(r.json()["price"])
    except Exception as e:
        print(f"Crypto price error for {symbol}: {e}")
    return None

def get_forex_price(symbol):
    """Get forex price from multiple free sources"""
    try:
        # Try exchangerate-api (free tier)
        # symbol format: EURUSD -> base=EUR, quote=USD
        sym = symbol.upper()
        if len(sym) == 6:
            base = sym[:3]
            quote = sym[3:]
        else:
            return None

        url = f"https://api.exchangerate-api.com/v4/latest/{base}"
        r = requests.get(url, timeout=10)
        if r.status_code == 200:
            rates = r.json().get("rates", {})
            if quote in rates:
                return float(rates[quote])

        # Fallback: frankfurter.app (free, no key needed)
        url = f"https://api.frankfurter.app/latest?from={base}&to={quote}"
        r = requests.get(url, timeout=10)
        if r.status_code == 200:
            rates = r.json().get("rates", {})
            if quote in rates:
                return float(rates[quote])

    except Exception as e:
        print(f"Forex price error for {symbol}: {e}")
    return None

def get_price(symbol, asset_type):
    """Unified price getter"""
    if asset_type == "crypto":
        return get_crypto_price(symbol)
    elif asset_type == "forex":
        return get_forex_price(symbol)
    return None

# ─── Telegram ─────────────────────────────────────────────────────────────────
def send_telegram(bot_token, chat_id, message):
    try:
        url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
        payload = {
            "chat_id": chat_id,
            "text": message,
            "parse_mode": "HTML"
        }
        r = requests.post(url, json=payload, timeout=10)
        return r.status_code == 200
    except Exception as e:
        print(f"Telegram error: {e}")
        return False

# ─── Alert Checker (runs every 2 minutes) ─────────────────────────────────────
notified_alerts = set()  # track which alerts already fired

def check_alerts():
    while True:
        try:
            data = load_data()
            tg = data.get("telegram", {})
            bot_token = tg.get("bot_token", "")
            chat_id = tg.get("chat_id", "")

            for alert in data.get("alerts", []):
                if not alert.get("active", True):
                    continue

                alert_id = alert.get("id")
                symbol = alert.get("symbol", "")
                asset_type = alert.get("type", "crypto")
                target_price = float(alert.get("target_price", 0))
                condition = alert.get("condition", "above")  # "above" or "below"

                current_price = get_price(symbol, asset_type)
                if current_price is None:
                    continue

                # Update last known price
                alert["last_price"] = current_price
                alert["last_checked"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

                # Check condition
                triggered = False
                if condition == "above" and current_price >= target_price:
                    triggered = True
                elif condition == "below" and current_price <= target_price:
                    triggered = True

                if triggered and alert_id not in notified_alerts:
                    notified_alerts.add(alert_id)
                    alert["active"] = False  # disable after firing

                    if bot_token and chat_id:
                        direction = "📈 بالاتر از" if condition == "above" else "📉 پایین‌تر از"
                        msg = (
                            f"🔔 <b>آلارم قیمت!</b>\n\n"
                            f"💰 <b>{symbol.upper()}</b>\n"
                            f"قیمت فعلی: <b>{current_price:,.4f}</b>\n"
                            f"هدف: {direction} <b>{target_price:,.4f}</b>\n\n"
                            f"⏰ {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
                        )
                        send_telegram(bot_token, chat_id, msg)
                        print(f"Alert fired for {symbol}! Price: {current_price}")

            save_data(data)

        except Exception as e:
            print(f"Alert check error: {e}")

        time.sleep(120)  # every 2 minutes

# ─── API Routes ────────────────────────────────────────────────────────────────
@app.route("/")
def index():
    return send_from_directory("static", "index.html")

@app.route("/api/config", methods=["GET", "POST"])
def config():
    data = load_data()
    if request.method == "POST":
        body = request.json
        data["telegram"] = {
            "bot_token": body.get("bot_token", ""),
            "chat_id": body.get("chat_id", "")
        }
        save_data(data)
        return jsonify({"ok": True})
    return jsonify(data.get("telegram", {}))

@app.route("/api/alerts", methods=["GET"])
def get_alerts():
    data = load_data()
    return jsonify(data.get("alerts", []))

@app.route("/api/alerts", methods=["POST"])
def add_alert():
    data = load_data()
    body = request.json
    alert = {
        "id": str(int(time.time() * 1000)),
        "symbol": body.get("symbol", "").upper(),
        "type": body.get("type", "crypto"),  # crypto or forex
        "target_price": float(body.get("target_price", 0)),
        "condition": body.get("condition", "above"),
        "active": True,
        "last_price": None,
        "last_checked": None,
        "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    }
    data["alerts"].append(alert)
    save_data(data)
    return jsonify({"ok": True, "alert": alert})

@app.route("/api/alerts/<alert_id>", methods=["DELETE"])
def delete_alert(alert_id):
    data = load_data()
    data["alerts"] = [a for a in data["alerts"] if a["id"] != alert_id]
    save_data(data)
    notified_alerts.discard(alert_id)
    return jsonify({"ok": True})

@app.route("/api/alerts/<alert_id>/toggle", methods=["POST"])
def toggle_alert(alert_id):
    data = load_data()
    for alert in data["alerts"]:
        if alert["id"] == alert_id:
            alert["active"] = not alert.get("active", True)
            if alert["active"]:
                notified_alerts.discard(alert_id)
            break
    save_data(data)
    return jsonify({"ok": True})

@app.route("/api/price/<asset_type>/<symbol>", methods=["GET"])
def live_price(asset_type, symbol):
    price = get_price(symbol.upper(), asset_type)
    if price is None:
        return jsonify({"error": "نمیشه قیمت رو گرفت"}), 404
    return jsonify({"symbol": symbol.upper(), "price": price})

@app.route("/api/test-telegram", methods=["POST"])
def test_telegram():
    data = load_data()
    tg = data.get("telegram", {})
    ok = send_telegram(
        tg.get("bot_token", ""),
        tg.get("chat_id", ""),
        "✅ اتصال تلگرام موفق بود! آلارم‌های قیمت فعال هستن."
    )
    return jsonify({"ok": ok})

@app.route("/health")
def health():
    return jsonify({"status": "ok", "time": datetime.now().isoformat()})

# ─── Start Background Thread ───────────────────────────────────────────────────
checker_thread = threading.Thread(target=check_alerts, daemon=True)
checker_thread.start()

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
