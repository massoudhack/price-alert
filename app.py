import os
import json
import time
import threading
import requests
from flask import Flask, request, jsonify, send_from_directory
from datetime import datetime

app = Flask(__name__, static_folder='static')

DATA_FILE = "alerts.json"

# ─── Storage ──────────────────────────────────────────────────────────────────
def load_data():
    if os.path.exists(DATA_FILE):
        with open(DATA_FILE, "r") as f:
            return json.load(f)
    return {
        "alerts": [],
        "archive": [],
        "telegram": {"bot_token": "", "chat_ids": []},
        "users": []   # list of {chat_id, username, joined_at}
    }

def save_data(data):
    with open(DATA_FILE, "w") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

# ─── Price Fetching ────────────────────────────────────────────────────────────
def get_crypto_price(symbol):
    """Binance — free, no key. symbol = BTC, ETH, etc."""
    sym = symbol.upper().replace("/", "").replace("USDT","").replace("USD","")
    # Try USDT pair
    for quote in ["USDT", "USDC", "BUSD"]:
        try:
            url = f"https://api.binance.com/api/v3/ticker/price?symbol={sym}{quote}"
            r = requests.get(url, timeout=8)
            if r.status_code == 200:
                return float(r.json()["price"])
        except Exception:
            pass
    # Fallback: CoinGecko
    try:
        cg_map = {
            "BTC":"bitcoin","ETH":"ethereum","BNB":"binancecoin",
            "SOL":"solana","XRP":"ripple","ADA":"cardano","DOGE":"dogecoin",
            "TRX":"tron","TON":"toncoin","AVAX":"avalanche-2",
            "LINK":"chainlink","DOT":"polkadot","MATIC":"matic-network",
            "UNI":"uniswap","ATOM":"cosmos","LTC":"litecoin",
        }
        cg_id = cg_map.get(sym)
        if cg_id:
            url = f"https://api.coingecko.com/api/v3/simple/price?ids={cg_id}&vs_currencies=usd"
            r = requests.get(url, timeout=8)
            if r.status_code == 200:
                d = r.json()
                if cg_id in d:
                    return float(d[cg_id]["usd"])
    except Exception:
        pass
    return None

def get_forex_price(symbol):
    """symbol format: EURUSD or EUR/USD or XAUUSD"""
    sym = symbol.upper().replace("/", "").replace(" ", "")
    if len(sym) < 6:
        return None
    base = sym[:3]
    quote = sym[3:6]

    # Special: gold/silver
    if base == "XAU":
        # Try metals-api via open.er-api
        for url in [
            "https://api.metals.live/v1/spot/gold",
            "https://api.gold-api.com/price/XAU",
        ]:
            try:
                r = requests.get(url, timeout=8)
                if r.status_code == 200:
                    d = r.json()
                    # metals.live returns list [{price: ...}]
                    if isinstance(d, list) and d:
                        return float(d[0].get("price", 0))
                    # gold-api returns {price: ...}
                    if isinstance(d, dict) and "price" in d:
                        return float(d["price"])
            except Exception:
                pass

        # Fallback: CoinGecko Tether Gold (XAUT) — tracks real gold closely
        for cg_id in ["tether-gold", "pax-gold"]:
            try:
                url = f"https://api.coingecko.com/api/v3/simple/price?ids={cg_id}&vs_currencies=usd"
                r = requests.get(url, timeout=8)
                if r.status_code == 200:
                    d = r.json()
                    if cg_id in d:
                        return float(d[cg_id]["usd"])
            except Exception:
                pass

        # Last resort: exchangerate with XAU
        try:
            r = requests.get("https://api.exchangerate-api.com/v4/latest/USD", timeout=8)
            if r.status_code == 200:
                rates = r.json().get("rates", {})
                xau_rate = rates.get("XAU")
                if xau_rate:
                    return float(1 / xau_rate)
        except Exception:
            pass
        return None

    # exchangerate-api.com
    try:
        r = requests.get(f"https://api.exchangerate-api.com/v4/latest/{base}", timeout=8)
        if r.status_code == 200:
            rates = r.json().get("rates", {})
            if quote in rates:
                return float(rates[quote])
    except Exception:
        pass

    # frankfurter.app fallback
    try:
        r = requests.get(f"https://api.frankfurter.app/latest?from={base}&to={quote}", timeout=8)
        if r.status_code == 200:
            rates = r.json().get("rates", {})
            if quote in rates:
                return float(rates[quote])
    except Exception:
        pass
    return None

def get_price(symbol, asset_type):
    if asset_type == "crypto":
        return get_crypto_price(symbol)
    elif asset_type == "forex":
        return get_forex_price(symbol)
    return None

# ─── Telegram ─────────────────────────────────────────────────────────────────
def send_telegram(bot_token, chat_id, message):
    try:
        url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
        r = requests.post(url, json={"chat_id": chat_id, "text": message, "parse_mode": "HTML"}, timeout=10)
        return r.status_code == 200
    except Exception:
        return False

def send_to_all(bot_token, chat_ids, message):
    """Send message to all registered users"""
    results = []
    for cid in chat_ids:
        ok = send_telegram(bot_token, str(cid), message)
        results.append(ok)
    return all(results)

# ─── Telegram Webhook / polling for /start ────────────────────────────────────
def poll_telegram_updates():
    """Poll Telegram for new /start messages to auto-register users"""
    last_update_id = 0
    while True:
        try:
            data = load_data()
            bot_token = data.get("telegram", {}).get("bot_token", "")
            if not bot_token:
                time.sleep(30)
                continue

            url = f"https://api.telegram.org/bot{bot_token}/getUpdates"
            params = {"offset": last_update_id + 1, "timeout": 20, "limit": 100}
            r = requests.get(url, params=params, timeout=30)
            if r.status_code != 200:
                time.sleep(10)
                continue

            updates = r.json().get("result", [])
            for upd in updates:
                last_update_id = upd["update_id"]
                msg = upd.get("message", {})
                text = msg.get("text", "")
                chat = msg.get("chat", {})
                cid = str(chat.get("id", ""))
                username = chat.get("username", "") or chat.get("first_name", "")

                if text.startswith("/start") and cid:
                    # Register user
                    data = load_data()
                    users = data.get("users", [])
                    ids = [str(u["chat_id"]) for u in users]
                    if cid not in ids:
                        users.append({
                            "chat_id": cid,
                            "username": username,
                            "joined_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                        })
                        data["users"] = users
                        # Also update chat_ids list
                        chat_ids = data.get("telegram", {}).get("chat_ids", [])
                        if cid not in [str(x) for x in chat_ids]:
                            chat_ids.append(cid)
                            data["telegram"]["chat_ids"] = chat_ids
                        save_data(data)
                        # Welcome message
                        send_telegram(bot_token, cid,
                            f"👋 سلام <b>{username}</b>!\n\n"
                            "✅ شما در سیستم آلارم قیمت ثبت شدید.\n"
                            "از این به بعد آلارم‌های قیمت برای شما ارسال می‌شود. 🔔"
                        )
                        print(f"New user registered: {username} ({cid})")

        except Exception as e:
            print(f"Telegram poll error: {e}")

        time.sleep(5)

# ─── Alert Checker — every 3 minutes ──────────────────────────────────────────
notified_alerts = set()

def check_alerts():
    while True:
        try:
            data = load_data()
            tg = data.get("telegram", {})
            bot_token = tg.get("bot_token", "")
            # Support both single chat_id (legacy) and chat_ids list
            chat_ids = tg.get("chat_ids", [])
            legacy = tg.get("chat_id", "")
            if legacy and legacy not in [str(x) for x in chat_ids]:
                chat_ids.append(legacy)

            fired_ids = []
            for alert in data.get("alerts", []):
                if not alert.get("active", True):
                    continue

                aid = alert.get("id")
                symbol = alert.get("symbol", "")
                asset_type = alert.get("type", "crypto")
                target_price = float(alert.get("target_price", 0))
                condition = alert.get("condition", "above")

                current_price = get_price(symbol, asset_type)
                if current_price is None:
                    print(f"Could not get price for {symbol}")
                    continue

                alert["last_price"] = current_price
                alert["last_checked"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

                triggered = (
                    (condition == "above" and current_price >= target_price) or
                    (condition == "below" and current_price <= target_price)
                )

                if triggered and aid not in notified_alerts:
                    notified_alerts.add(aid)
                    alert["active"] = False
                    alert["fired_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                    alert["fired_price"] = current_price
                    fired_ids.append(aid)

                    if bot_token and chat_ids:
                        direction = "📈 از هدف بالا رفت" if condition == "above" else "📉 به هدف پایین آمد"
                        msg = (
                            f"🚨 <b>آلارم قیمت فعال شد!</b>\n\n"
                            f"💰 <b>{symbol}</b> {direction}\n\n"
                            f"🎯 هدف: <b>${target_price:,.4f}</b>\n"
                            f"📊 قیمت فعلی: <b>${current_price:,.4f}</b>\n\n"
                            f"⏰ {datetime.now().strftime('%Y-%m-%d %H:%M')}"
                        )
                        for cid in chat_ids:
                            send_telegram(bot_token, str(cid), msg)
                        print(f"✅ Alert fired for {symbol} → sent to {len(chat_ids)} users")

            # Move fired alerts to archive
            if fired_ids:
                archive = data.get("archive", [])
                for aid in fired_ids:
                    fired = next((a for a in data["alerts"] if a["id"] == aid), None)
                    if fired:
                        archive.append(fired)
                data["archive"] = archive
                data["alerts"] = [a for a in data["alerts"] if a["id"] not in fired_ids]

            save_data(data)

        except Exception as e:
            print(f"Alert check error: {e}")

        time.sleep(180)  # every 3 minutes

# ─── API Routes ────────────────────────────────────────────────────────────────
@app.route("/")
def index():
    return send_from_directory("static", "index.html")

@app.route("/api/config", methods=["GET", "POST"])
def config():
    data = load_data()
    if request.method == "POST":
        body = request.json
        tg = data.get("telegram", {})
        tg["bot_token"] = body.get("bot_token", "")
        # Keep chat_ids list, also handle single chat_id for backward compat
        if body.get("chat_id"):
            cid = str(body["chat_id"])
            ids = [str(x) for x in tg.get("chat_ids", [])]
            if cid not in ids:
                ids.append(cid)
            tg["chat_ids"] = ids
            tg["chat_id"] = cid
        data["telegram"] = tg
        save_data(data)
        return jsonify({"ok": True})
    tg = data.get("telegram", {})
    return jsonify({
        "bot_token": tg.get("bot_token", ""),
        "chat_id": tg.get("chat_id", ""),
        "chat_ids": tg.get("chat_ids", []),
        "user_count": len(data.get("users", []))
    })

@app.route("/api/users", methods=["GET"])
def get_users():
    data = load_data()
    return jsonify(data.get("users", []))

@app.route("/api/users/<chat_id>", methods=["DELETE"])
def delete_user(chat_id):
    data = load_data()
    data["users"] = [u for u in data.get("users", []) if str(u["chat_id"]) != str(chat_id)]
    data["telegram"]["chat_ids"] = [x for x in data["telegram"].get("chat_ids", []) if str(x) != str(chat_id)]
    save_data(data)
    return jsonify({"ok": True})

@app.route("/api/alerts", methods=["GET"])
def get_alerts():
    data = load_data()
    return jsonify(data.get("alerts", []))

@app.route("/api/archive", methods=["GET"])
def get_archive():
    data = load_data()
    return jsonify(data.get("archive", []))

@app.route("/api/archive", methods=["DELETE"])
def clear_archive():
    data = load_data()
    data["archive"] = []
    save_data(data)
    return jsonify({"ok": True})

@app.route("/api/alerts", methods=["POST"])
def add_alert():
    data = load_data()
    body = request.json
    alert = {
        "id": str(int(time.time() * 1000)),
        "symbol": body.get("symbol", "").upper(),
        "type": body.get("type", "crypto"),
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

@app.route("/api/price/<asset_type>/<symbol>", methods=["GET"])
def live_price(asset_type, symbol):
    price = get_price(symbol.upper(), asset_type)
    if price is None:
        return jsonify({"error": "قیمت پیدا نشد"}), 404
    return jsonify({"symbol": symbol.upper(), "price": price})

@app.route("/api/test-telegram", methods=["POST"])
def test_telegram():
    data = load_data()
    tg = data.get("telegram", {})
    bot_token = tg.get("bot_token", "")
    chat_ids = tg.get("chat_ids", [])
    if tg.get("chat_id") and str(tg["chat_id"]) not in [str(x) for x in chat_ids]:
        chat_ids.append(tg["chat_id"])
    if not bot_token or not chat_ids:
        return jsonify({"ok": False, "error": "توکن یا chat_id ست نشده"})
    results = []
    for cid in chat_ids:
        ok = send_telegram(bot_token, str(cid),
            "✅ <b>آلارم قیمت — تست موفق</b>\n\nاتصال تلگرام برقرار است. 🔔")
        results.append(ok)
    return jsonify({"ok": any(results), "sent": sum(results), "total": len(chat_ids)})

@app.route("/health")
def health():
    data = load_data()
    return jsonify({
        "status": "ok",
        "time": datetime.now().isoformat(),
        "alerts": len(data.get("alerts", [])),
        "users": len(data.get("users", []))
    })

# ─── Background Threads ────────────────────────────────────────────────────────
threading.Thread(target=check_alerts, daemon=True).start()
threading.Thread(target=poll_telegram_updates, daemon=True).start()

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
