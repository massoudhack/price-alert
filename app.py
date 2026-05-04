import os
import json
import time
import threading
import requests
import pytz
from flask import Flask, request, jsonify, send_from_directory
from datetime import datetime, timedelta

app = Flask(__name__, static_folder='static')

DATA_FILE  = "alerts.json"
TEHRAN     = pytz.timezone("Asia/Tehran")
BOT_TOKEN  = os.environ.get("BOT_TOKEN", "")   # set in Koyeb env vars

# ── Time helpers ──────────────────────────────────────────────────────────────
def now_teh():
    return datetime.now(TEHRAN)

def fmt_teh(dt=None):
    if dt is None:
        dt = now_teh()
    return dt.strftime("%Y-%m-%d %H:%M:%S")

def fmt_teh_pretty(dt=None):
    if dt is None:
        dt = now_teh()
    return dt.strftime("%Y/%m/%d %H:%M")

# ── Storage ───────────────────────────────────────────────────────────────────
def load_data():
    if os.path.exists(DATA_FILE):
        try:
            with open(DATA_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {
        "alerts":       [],
        "candle_alerts": [],
        "archive":      [],
        "telegram":     {"bot_token": BOT_TOKEN, "chat_ids": []},
        "users":        [],
        "errors":       [],
        "last_update":  None,
    }

def save_data(data):
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

def log_error(msg):
    data = load_data()
    errors = data.get("errors", [])
    errors.append({"time": fmt_teh(), "msg": msg})
    errors = errors[-20:]          # keep last 20 errors
    data["errors"] = errors
    save_data(data)
    print(f"[ERR] {msg}")

def set_last_update(symbol=""):
    data = load_data()
    data["last_update"] = fmt_teh()
    data["last_update_symbol"] = symbol
    save_data(data)

# ── Price sources — tries each in order until one works ──────────────────────
HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; PriceBot/1.0)"}

def fetch_url(url, timeout=8):
    r = requests.get(url, timeout=timeout, headers=HEADERS)
    r.raise_for_status()
    return r

def get_crypto_price(symbol):
    """Try multiple sources for crypto price"""
    sym = symbol.upper().strip()
    # normalise: remove common suffixes so BTC / BTCUSDT both work
    base = sym.replace("USDT","").replace("USDC","").replace("USD","").replace("BUSD","")

    sources = [
        # 1. Binance
        lambda: float(fetch_url(f"https://api.binance.com/api/v3/ticker/price?symbol={base}USDT").json()["price"]),
        lambda: float(fetch_url(f"https://api.binance.com/api/v3/ticker/price?symbol={base}USDC").json()["price"]),
        # 2. Bybit
        lambda: float(fetch_url(f"https://api.bybit.com/v5/market/tickers?category=spot&symbol={base}USDT").json()["result"]["list"][0]["lastPrice"]),
        # 3. OKX
        lambda: float(fetch_url(f"https://www.okx.com/api/v5/market/ticker?instId={base}-USDT").json()["data"][0]["last"]),
        # 4. KuCoin
        lambda: float(fetch_url(f"https://api.kucoin.com/api/v1/market/orderbook/level1?symbol={base}-USDT").json()["data"]["price"]),
        # 5. CoinGecko (slower, no key needed)
        lambda: _coingecko(base),
        # 6. CryptoCompare
        lambda: float(fetch_url(f"https://min-api.cryptocompare.com/data/price?fsym={base}&tsyms=USD").json()["USD"]),
    ]

    for fn in sources:
        try:
            price = fn()
            if price and price > 0:
                return price
        except Exception:
            pass

    log_error(f"All crypto sources failed for {symbol}")
    return None

CG_MAP = {
    "BTC":"bitcoin","ETH":"ethereum","BNB":"binancecoin",
    "SOL":"solana","XRP":"ripple","ADA":"cardano","DOGE":"dogecoin",
    "TRX":"tron","TON":"toncoin","AVAX":"avalanche-2",
    "LINK":"chainlink","DOT":"polkadot","MATIC":"matic-network",
    "UNI":"uniswap","ATOM":"cosmos","LTC":"litecoin","SHIB":"shiba-inu",
}

def _coingecko(base):
    cg_id = CG_MAP.get(base)
    if not cg_id:
        return None
    d = fetch_url(f"https://api.coingecko.com/api/v3/simple/price?ids={cg_id}&vs_currencies=usd").json()
    return float(d[cg_id]["usd"])

def get_forex_price(symbol):
    """Try multiple sources for forex/gold price"""
    sym = symbol.upper().replace("/", "").strip()
    if len(sym) < 6:
        return None
    base  = sym[:3]
    quote = sym[3:6]

    # ── Gold (XAUUSD) special handling ────────────────────────────────────
    if base == "XAU":
        gold_sources = [
            lambda: float(fetch_url("https://api.metals.live/v1/spot/gold").json()[0]["price"]),
            lambda: float(fetch_url("https://api.gold-api.com/price/XAU").json()["price"]),
            lambda: _gold_via_forex(),
            lambda: _gold_via_coingecko(),
        ]
        for fn in gold_sources:
            try:
                p = fn()
                if p and p > 0:
                    return p
            except Exception:
                pass
        log_error(f"All gold sources failed for {symbol}")
        return None

    # ── Regular forex ──────────────────────────────────────────────────────
    forex_sources = [
        lambda: float(fetch_url(f"https://api.exchangerate-api.com/v4/latest/{base}").json()["rates"][quote]),
        lambda: float(fetch_url(f"https://api.frankfurter.app/latest?from={base}&to={quote}").json()["rates"][quote]),
        lambda: float(fetch_url(f"https://open.er-api.com/v6/latest/{base}").json()["rates"][quote]),
        lambda: float(fetch_url(f"https://cdn.jsdelivr.net/npm/@fawazahmed0/currency-api@latest/v1/currencies/{base.lower()}.json").json()[base.lower()][quote.lower()]),
    ]
    for fn in forex_sources:
        try:
            p = fn()
            if p and p > 0:
                return p
        except Exception:
            pass
    log_error(f"All forex sources failed for {symbol}")
    return None

def _gold_via_forex():
    """Get gold price through USD/XAU exchange rate"""
    r = fetch_url("https://api.exchangerate-api.com/v4/latest/USD")
    xau = r.json()["rates"].get("XAU")
    if xau:
        return float(1 / xau)
    return None

def _gold_via_coingecko():
    d = fetch_url("https://api.coingecko.com/api/v3/simple/price?ids=tether-gold&vs_currencies=usd").json()
    return float(d["tether-gold"]["usd"])

def get_price(symbol, asset_type):
    if asset_type == "crypto":
        return get_crypto_price(symbol)
    return get_forex_price(symbol)

# ── Distance helper ────────────────────────────────────────────────────────────
def calc_distance(symbol, asset_type, cur, tgt):
    if not cur or not tgt:
        return ""
    diff = abs(tgt - cur)
    if asset_type == "crypto":
        return f"{diff / cur * 100:.2f}٪"
    is_jpy = "JPY" in symbol.upper()
    return f"{round(diff * (100 if is_jpy else 10000)):,} پیپ"

# ── Telegram helpers ───────────────────────────────────────────────────────────
def _token(data):
    return data.get("telegram", {}).get("bot_token") or BOT_TOKEN

def send_tg(token, chat_id, text):
    try:
        r = requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": str(chat_id), "text": text, "parse_mode": "HTML"},
            timeout=10, headers=HEADERS
        )
        return r.status_code == 200
    except Exception:
        return False

def broadcast(token, chat_ids, text):
    return [send_tg(token, cid, text) for cid in chat_ids]

# ── Telegram polling (/start auto-register) ────────────────────────────────────
def poll_telegram():
    last_id = 0
    while True:
        try:
            data  = load_data()
            token = _token(data)
            if not token:
                time.sleep(30)
                continue
            r = requests.get(
                f"https://api.telegram.org/bot{token}/getUpdates",
                params={"offset": last_id + 1, "timeout": 20, "limit": 100},
                timeout=30, headers=HEADERS
            )
            if r.status_code != 200:
                time.sleep(10)
                continue
            for upd in r.json().get("result", []):
                last_id = upd["update_id"]
                msg  = upd.get("message", {})
                text = msg.get("text", "")
                ch   = msg.get("chat", {})
                cid  = str(ch.get("id", ""))
                uname = ch.get("username", "") or ch.get("first_name", "")
                if text.startswith("/start") and cid:
                    data  = load_data()
                    users = data.get("users", [])
                    if cid not in [str(u["chat_id"]) for u in users]:
                        users.append({"chat_id": cid, "username": uname, "joined_at": fmt_teh()})
                        data["users"] = users
                        ids = data.get("telegram", {}).get("chat_ids", [])
                        if cid not in [str(x) for x in ids]:
                            ids.append(cid)
                        data["telegram"]["chat_ids"] = ids
                        save_data(data)
                        send_tg(token, cid,
                            f"👋 سلام <b>{uname}</b>!\n✅ در سیستم آلارم ثبت شدید. 🔔")
        except Exception as e:
            print(f"[poll] {e}")
        time.sleep(5)

# ── Price alert checker — every 5 minutes ─────────────────────────────────────
notified = set()

def check_price_alerts():
    while True:
        try:
            data   = load_data()
            token  = _token(data)
            cids   = list(data.get("telegram", {}).get("chat_ids", []))
            legacy = data.get("telegram", {}).get("chat_id", "")
            if legacy and legacy not in [str(x) for x in cids]:
                cids.append(legacy)

            fired_ids = []
            for a in data.get("alerts", []):
                if not a.get("active"):
                    continue
                sym  = a["symbol"]
                atype = a.get("type", "crypto")
                tgt  = float(a["target_price"])
                cond = a.get("condition", "above")
                cur  = get_price(sym, atype)

                if cur is None:
                    continue

                a["last_price"]   = cur
                a["last_checked"] = fmt_teh()
                set_last_update(sym)

                triggered = (cond == "above" and cur >= tgt) or (cond == "below" and cur <= tgt)

                if triggered and a["id"] not in notified:
                    notified.add(a["id"])
                    a["active"]     = False
                    a["fired_at"]   = fmt_teh()
                    a["fired_price"] = cur
                    fired_ids.append(a["id"])

                    if token and cids:
                        dist = calc_distance(sym, atype, cur, tgt)
                        comment_line = f"\n💬 یادداشت: <i>{a['comment']}</i>" if a.get("comment") else ""
                        msg = (
                            f"🚨 <b>آلارم قیمت فعال شد!</b>\n\n"
                            f"💰 <b>{sym}</b> {'📈 از هدف بالا رفت' if cond=='above' else '📉 به هدف پایین آمد'}\n\n"
                            f"🎯 هدف: <b>${tgt:,.4f}</b>\n"
                            f"📊 قیمت فعلی: <b>${cur:,.4f}</b>"
                            f"\n📏 فاصله: <b>{dist}</b>"
                            f"{comment_line}\n\n"
                            f"⏰ {fmt_teh_pretty()} (تهران)"
                        )
                        broadcast(token, cids, msg)

            if fired_ids:
                archive = data.get("archive", [])
                for fid in fired_ids:
                    obj = next((x for x in data["alerts"] if x["id"] == fid), None)
                    if obj:
                        archive.append(obj)
                data["archive"] = archive
                data["alerts"]  = [x for x in data["alerts"] if x["id"] not in fired_ids]

            save_data(data)
        except Exception as e:
            log_error(f"check_price_alerts: {e}")

        time.sleep(300)   # every 5 minutes

# ── Candle close alert checker ─────────────────────────────────────────────────
def get_candle_close_price(symbol, asset_type):
    """For candle alerts we just need current price — close price detection is time-based"""
    return get_price(symbol, asset_type)

def check_candle_alerts():
    """Fires when a candle closes on the specified timeframe"""
    while True:
        try:
            now = now_teh()
            data   = load_data()
            token  = _token(data)
            cids   = list(data.get("telegram", {}).get("chat_ids", []))
            legacy = data.get("telegram", {}).get("chat_id", "")
            if legacy and legacy not in [str(x) for x in cids]:
                cids.append(legacy)

            for a in data.get("candle_alerts", []):
                if not a.get("active"):
                    continue

                tf = int(a.get("timeframe", 60))   # minutes
                last_fire = a.get("last_fired")

                # Check if a new candle has closed since last fire
                should_fire = False
                if last_fire is None:
                    should_fire = True
                else:
                    lt = datetime.strptime(last_fire, "%Y-%m-%d %H:%M:%S").replace(tzinfo=TEHRAN)
                    delta = (now - lt).total_seconds() / 60
                    should_fire = delta >= tf

                if not should_fire:
                    continue

                sym   = a["symbol"]
                atype = a.get("type", "crypto")
                cur   = get_candle_close_price(sym, atype)

                if cur is None:
                    continue

                a["last_fired"] = fmt_teh()
                a["last_price"] = cur

                if token and cids:
                    tf_label = {5:"۵ دقیقه", 15:"۱۵ دقیقه", 30:"۳۰ دقیقه",
                                60:"۱ ساعت", 240:"۴ ساعت"}.get(tf, f"{tf} دقیقه")
                    comment_line = f"\n💬 {a['comment']}" if a.get("comment") else ""
                    msg = (
                        f"🕯 <b>کلوز کندل {tf_label}</b>\n\n"
                        f"💰 <b>{sym}</b>\n"
                        f"📊 قیمت کلوز: <b>${cur:,.4f}</b>"
                        f"{comment_line}\n\n"
                        f"⏰ {fmt_teh_pretty()} (تهران)"
                    )
                    broadcast(token, cids, msg)

            save_data(data)
        except Exception as e:
            log_error(f"check_candle_alerts: {e}")

        time.sleep(60)   # check every minute

# ── Routes ────────────────────────────────────────────────────────────────────
@app.route("/")
def index():
    return send_from_directory("static", "index.html")

@app.route("/api/config", methods=["GET", "POST"])
def config():
    data = load_data()
    if request.method == "POST":
        body = request.json or {}
        tg   = data.get("telegram", {})
        if body.get("bot_token"):
            tg["bot_token"] = body["bot_token"]
        if body.get("chat_id"):
            cid = str(body["chat_id"])
            ids = [str(x) for x in tg.get("chat_ids", [])]
            if cid not in ids:
                ids.append(cid)
            tg["chat_ids"] = ids
            tg["chat_id"]  = cid
        data["telegram"] = tg
        save_data(data)
        return jsonify({"ok": True})
    tg = data.get("telegram", {})
    return jsonify({
        "bot_token":   "***" if tg.get("bot_token") else "",
        "chat_id":     tg.get("chat_id", ""),
        "chat_ids":    tg.get("chat_ids", []),
        "user_count":  len(data.get("users", [])),
    })

# ── Price alerts ──────────────────────────────────────────────────────────────
@app.route("/api/alerts", methods=["GET"])
def get_alerts():
    return jsonify(load_data().get("alerts", []))

@app.route("/api/alerts", methods=["POST"])
def add_alert():
    data = load_data()
    body = request.json or {}
    alert = {
        "id":           str(int(time.time() * 1000)),
        "symbol":       body.get("symbol", "").upper().strip(),
        "type":         body.get("type", "crypto"),
        "target_price": float(body.get("target_price", 0)),
        "condition":    body.get("condition", "above"),
        "comment":      body.get("comment", "").strip(),
        "active":       True,
        "last_price":   None,
        "last_checked": None,
        "created_at":   fmt_teh(),
    }
    data["alerts"].append(alert)
    save_data(data)
    return jsonify({"ok": True, "alert": alert})

@app.route("/api/alerts/<aid>", methods=["DELETE"])
def delete_alert(aid):
    data = load_data()
    data["alerts"] = [a for a in data["alerts"] if a["id"] != aid]
    save_data(data)
    notified.discard(aid)
    return jsonify({"ok": True})

# ── Candle alerts ─────────────────────────────────────────────────────────────
@app.route("/api/candle-alerts", methods=["GET"])
def get_candle_alerts():
    return jsonify(load_data().get("candle_alerts", []))

@app.route("/api/candle-alerts", methods=["POST"])
def add_candle_alert():
    data = load_data()
    body = request.json or {}
    alert = {
        "id":         str(int(time.time() * 1000)),
        "symbol":     body.get("symbol", "").upper().strip(),
        "type":       body.get("type", "crypto"),
        "timeframe":  int(body.get("timeframe", 60)),
        "comment":    body.get("comment", "").strip(),
        "active":     True,
        "last_price": None,
        "last_fired": None,
        "created_at": fmt_teh(),
    }
    if not alert["symbol"]:
        return jsonify({"ok": False, "error": "نماد الزامی است"}), 400
    data.setdefault("candle_alerts", []).append(alert)
    save_data(data)
    return jsonify({"ok": True, "alert": alert})

@app.route("/api/candle-alerts/<aid>", methods=["DELETE"])
def delete_candle_alert(aid):
    data = load_data()
    data["candle_alerts"] = [a for a in data.get("candle_alerts", []) if a["id"] != aid]
    save_data(data)
    return jsonify({"ok": True})

@app.route("/api/candle-alerts/<aid>/toggle", methods=["POST"])
def toggle_candle_alert(aid):
    data = load_data()
    for a in data.get("candle_alerts", []):
        if a["id"] == aid:
            a["active"] = not a.get("active", True)
            break
    save_data(data)
    return jsonify({"ok": True})

# ── Archive ───────────────────────────────────────────────────────────────────
@app.route("/api/archive", methods=["GET"])
def get_archive():
    return jsonify(load_data().get("archive", []))

@app.route("/api/archive", methods=["DELETE"])
def clear_archive():
    data = load_data()
    data["archive"] = []
    save_data(data)
    return jsonify({"ok": True})

@app.route("/api/archive/<aid>", methods=["DELETE"])
def del_archive_item(aid):
    data = load_data()
    data["archive"] = [a for a in data.get("archive", []) if a["id"] != aid]
    save_data(data)
    return jsonify({"ok": True})

# ── Users ─────────────────────────────────────────────────────────────────────
@app.route("/api/users", methods=["GET"])
def get_users():
    return jsonify(load_data().get("users", []))

@app.route("/api/users/<cid>", methods=["DELETE"])
def del_user(cid):
    data = load_data()
    data["users"] = [u for u in data.get("users", []) if str(u["chat_id"]) != str(cid)]
    data["telegram"]["chat_ids"] = [x for x in data["telegram"].get("chat_ids", []) if str(x) != str(cid)]
    save_data(data)
    return jsonify({"ok": True})

# ── Price endpoint ────────────────────────────────────────────────────────────
@app.route("/api/price/<atype>/<symbol>")
def live_price(atype, symbol):
    price = get_price(symbol.upper(), atype)
    if price is None:
        return jsonify({"error": "قیمت پیدا نشد"}), 404
    return jsonify({"symbol": symbol.upper(), "price": price})

# ── Test Telegram ─────────────────────────────────────────────────────────────
@app.route("/api/test-telegram", methods=["POST"])
def test_tg():
    data = load_data()
    token = _token(data)
    cids  = list(data.get("telegram", {}).get("chat_ids", []))
    if data.get("telegram", {}).get("chat_id"):
        leg = str(data["telegram"]["chat_id"])
        if leg not in [str(x) for x in cids]:
            cids.append(leg)
    if not token or not cids:
        return jsonify({"ok": False, "error": "توکن یا chat_id ست نشده"})
    results = broadcast(token, cids, f"✅ <b>تست موفق</b>\n🔔 اتصال برقرار است.\n⏰ {fmt_teh_pretty()} (تهران)")
    return jsonify({"ok": any(results), "sent": sum(results), "total": len(cids)})

# ── Status / Errors ───────────────────────────────────────────────────────────
@app.route("/api/status")
def status():
    data = load_data()
    return jsonify({
        "status":       "ok",
        "last_update":  data.get("last_update"),
        "last_symbol":  data.get("last_update_symbol"),
        "alerts":       len(data.get("alerts", [])),
        "candle_alerts":len(data.get("candle_alerts", [])),
        "users":        len(data.get("users", [])),
        "errors":       data.get("errors", [])[-5:],
        "time_tehran":  fmt_teh(),
    })

@app.route("/health")
def health():
    return jsonify({"status": "ok", "time": fmt_teh()})

# ── Start threads ─────────────────────────────────────────────────────────────
threading.Thread(target=check_price_alerts,  daemon=True).start()
threading.Thread(target=check_candle_alerts, daemon=True).start()
threading.Thread(target=poll_telegram,       daemon=True).start()

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
