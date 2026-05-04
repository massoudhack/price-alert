import os, json, time, threading, requests
from flask import Flask, request, jsonify, send_from_directory
from datetime import datetime, timezone, timedelta

app = Flask(__name__, static_folder='static')
DATA_FILE = "alerts.json"

# ── Tehran timezone (UTC+3:30) ─────────────────────────────────────────────────
TEHRAN = timezone(timedelta(hours=3, minutes=30))
def now_ir():
    return datetime.now(TEHRAN).strftime("%Y-%m-%d %H:%M:%S")

# ── Storage ────────────────────────────────────────────────────────────────────
_data_lock = threading.Lock()

def load_data():
    if os.path.exists(DATA_FILE):
        try:
            with open(DATA_FILE, "r") as f:
                return json.load(f)
        except Exception:
            pass
    return {"alerts": [], "archive": [],
            "telegram": {"bot_token": "8792456550:AAEuvnzzCUXniBhIWz6C-qabX96EvPWqN7A", "chat_ids": []},
            "users": []}

def save_data(data):
    with _data_lock:
        with open(DATA_FILE, "w") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)

# ── Price engine: multi-source with fallback ───────────────────────────────────
price_errors = {}   # symbol -> {msg, ts}
price_cache  = {}   # symbol -> {price, source, ts}
CACHE_TTL    = 90   # seconds — use cached price if fresher than this

def _cached(symbol):
    c = price_cache.get(symbol)
    if c and (time.time() - c["t"]) < CACHE_TTL:
        return c["price"], c["source"]
    return None, None

def _store(symbol, price, source):
    price_cache[symbol] = {"price": price, "source": source, "t": time.time()}
    price_errors.pop(symbol, None)
    return price, source

def _fail(symbol, errors):
    price_errors[symbol] = {"msg": " | ".join(errors), "ts": now_ir()}
    return None, " | ".join(errors)

# ── Crypto ─────────────────────────────────────────────────────────────────────
def get_crypto_price(raw_symbol):
    # Normalise: strip /USD /USDT suffixes but keep the coin name intact
    sym = raw_symbol.upper().strip()
    for s in ("/USDT", "/USDC", "/USD", "USDT", "USDC", "USD"):
        if sym.endswith(s):
            sym = sym[: -len(s)]
            break

    cached, src = _cached(sym)
    if cached:
        return cached, src

    errors = []

    # ── Source 1: Binance spot
    for q in ("USDT", "USDC"):
        try:
            r = requests.get(
                f"https://api.binance.com/api/v3/ticker/price?symbol={sym}{q}",
                timeout=6)
            if r.ok and "price" in r.json():
                return _store(sym, float(r.json()["price"]), f"Binance({q})")
            errors.append(f"Binance {sym}{q}:{r.status_code}")
        except Exception as e:
            errors.append(f"Binance:{e}")

    # ── Source 2: CoinGecko (free, no key)
    CG = {"BTC":"bitcoin","ETH":"ethereum","BNB":"binancecoin","SOL":"solana",
          "XRP":"ripple","ADA":"cardano","DOGE":"dogecoin","TRX":"tron",
          "TON":"toncoin","AVAX":"avalanche-2","LINK":"chainlink","DOT":"polkadot",
          "MATIC":"matic-network","UNI":"uniswap","ATOM":"cosmos","LTC":"litecoin",
          "SHIB":"shiba-inu","OP":"optimism","ARB":"arbitrum","FTM":"fantom",
          "NEAR":"near","APT":"aptos","SUI":"sui"}
    cg_id = CG.get(sym)
    if cg_id:
        try:
            r = requests.get(
                f"https://api.coingecko.com/api/v3/simple/price"
                f"?ids={cg_id}&vs_currencies=usd&precision=8", timeout=10)
            if r.ok and cg_id in r.json():
                return _store(sym, float(r.json()[cg_id]["usd"]), "CoinGecko")
            errors.append(f"CoinGecko:{r.status_code}")
        except Exception as e:
            errors.append(f"CoinGecko:{e}")

    # ── Source 3: CryptoCompare (free tier, no key for basic)
    try:
        r = requests.get(
            f"https://min-api.cryptocompare.com/data/price?fsym={sym}&tsyms=USD",
            timeout=8)
        if r.ok:
            d = r.json()
            if "USD" in d:
                return _store(sym, float(d["USD"]), "CryptoCompare")
        errors.append(f"CryptoCompare:{r.status_code}")
    except Exception as e:
        errors.append(f"CryptoCompare:{e}")

    # ── Source 4: KuCoin
    try:
        r = requests.get(
            f"https://api.kucoin.com/api/v1/market/orderbook/level1?symbol={sym}-USDT",
            timeout=8)
        if r.ok:
            d = r.json()
            if d.get("code") == "200000" and d.get("data", {}).get("price"):
                return _store(sym, float(d["data"]["price"]), "KuCoin")
        errors.append(f"KuCoin:{r.status_code}")
    except Exception as e:
        errors.append(f"KuCoin:{e}")

    return _fail(sym, errors)

# ── Forex & Gold ───────────────────────────────────────────────────────────────
def get_forex_price(raw_symbol):
    sym = raw_symbol.upper().replace("/", "").replace(" ", "")
    if len(sym) < 6:
        return None, "نماد کوتاه است"

    base  = sym[:3]
    quote = sym[3:6]

    cached, src = _cached(sym)
    if cached:
        return cached, src

    # ── Gold special path ──────────────────────────────────────────────────────
    if base == "XAU":
        errors = []

        # 1. metals.live  — returns [{metal,price,ask,bid,...}]
        try:
            r = requests.get("https://api.metals.live/v1/spot/gold", timeout=7)
            if r.ok:
                d = r.json()
                p = None
                if isinstance(d, list) and d:
                    item = d[0]
                    p = item.get("price") or item.get("ask")
                elif isinstance(d, dict):
                    p = d.get("price") or d.get("ask")
                if p and float(p) > 500:
                    return _store(sym, float(p), "metals.live")
            errors.append(f"metals.live:{r.status_code}")
        except Exception as e:
            errors.append(f"metals.live:{e}")

        # 2. ExchangeRate-API — XAU rate = oz per USD → invert
        try:
            r = requests.get("https://api.exchangerate-api.com/v4/latest/USD", timeout=7)
            if r.ok:
                xau = r.json().get("rates", {}).get("XAU")
                if xau and float(xau) > 0:
                    p = round(1.0 / float(xau), 2)
                    if 500 < p < 20000:
                        return _store(sym, p, "ExchangeRate-API")
            errors.append("ExchangeRate-API no XAU")
        except Exception as e:
            errors.append(f"ExchangeRate-API:{e}")

        # 3. open.er-api.com
        try:
            r = requests.get("https://open.er-api.com/v6/latest/USD", timeout=7)
            if r.ok:
                xau = r.json().get("rates", {}).get("XAU")
                if xau and float(xau) > 0:
                    p = round(1.0 / float(xau), 2)
                    if 500 < p < 20000:
                        return _store(sym, p, "Open.er-api")
            errors.append("open.er-api no XAU")
        except Exception as e:
            errors.append(f"open.er-api:{e}")

        # 4. CoinGecko PAXG (1 PAXG ≈ 1 oz gold)
        for cg_id in ("pax-gold", "tether-gold"):
            try:
                r = requests.get(
                    f"https://api.coingecko.com/api/v3/simple/price?ids={cg_id}&vs_currencies=usd",
                    timeout=10)
                if r.ok and cg_id in r.json():
                    p = float(r.json()[cg_id]["usd"])
                    if p > 500:
                        return _store(sym, p, f"CoinGecko({cg_id})")
            except Exception as e:
                errors.append(f"CoinGecko {cg_id}:{e}")

        return _fail(sym, errors)

    # ── Regular forex ──────────────────────────────────────────────────────────
    errors = []

    # 1. Frankfurter (ECB data, up to 5 decimal places, very accurate)
    try:
        r = requests.get(
            f"https://api.frankfurter.app/latest?from={base}&to={quote}",
            timeout=7)
        if r.ok:
            rate = r.json().get("rates", {}).get(quote)
            if rate:
                return _store(sym, float(rate), "Frankfurter(ECB)")
        errors.append(f"Frankfurter:{r.status_code}")
    except Exception as e:
        errors.append(f"Frankfurter:{e}")

    # 2. open.er-api (6 decimal places)
    try:
        r = requests.get(f"https://open.er-api.com/v6/latest/{base}", timeout=7)
        if r.ok:
            rate = r.json().get("rates", {}).get(quote)
            if rate:
                return _store(sym, float(rate), "Open.er-api")
        errors.append(f"Open.er-api:{r.status_code}")
    except Exception as e:
        errors.append(f"Open.er-api:{e}")

    # 3. exchangerate-api (fallback)
    try:
        r = requests.get(
            f"https://api.exchangerate-api.com/v4/latest/{base}", timeout=7)
        if r.ok:
            rate = r.json().get("rates", {}).get(quote)
            if rate:
                return _store(sym, float(rate), "ExchangeRate-API")
        errors.append(f"ExchangeRate-API:{r.status_code}")
    except Exception as e:
        errors.append(f"ExchangeRate-API:{e}")

    return _fail(sym, errors)

def get_price_full(symbol, asset_type):
    if asset_type == "crypto":
        return get_crypto_price(symbol)
    if asset_type == "forex":
        return get_forex_price(symbol)
    return None, "نوع دارایی نامعتبر"

def fmt_price(price, symbol, atype):
    if price is None:
        return "—"
    sym = symbol.upper().replace("/", "")
    if atype == "crypto":
        if price >= 1000: return f"{price:,.2f}"
        if price >= 1:    return f"{price:,.4f}"
        return f"{price:.8f}"
    if "JPY" in sym: return f"{price:,.3f}"
    if "XAU" in sym or "XAG" in sym: return f"{price:,.2f}"
    return f"{price:,.5f}"

def pips_str(cur, target, symbol, atype):
    diff = cur - target
    sym  = symbol.upper().replace("/", "")
    if atype == "crypto":
        return f"{diff/target*100:+.2f}%"
    if "JPY" in sym:
        return f"{diff/0.01:+.1f} pip"
    if "XAU" in sym or "XAG" in sym:
        return f"{diff:+.2f} USD"
    return f"{diff/0.0001:+.1f} pip"

# ── Telegram helpers ───────────────────────────────────────────────────────────
def tg_send(token, chat_id, text):
    try:
        r = requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat_id, "text": text, "parse_mode": "HTML"},
            timeout=10)
        return r.ok
    except Exception:
        return False

# ── Telegram /start poller ─────────────────────────────────────────────────────
def poll_tg():
    last_id = 0
    while True:
        try:
            data  = load_data()
            token = data.get("telegram", {}).get("bot_token", "")
            if not token:
                time.sleep(30); continue

            r = requests.get(
                f"https://api.telegram.org/bot{token}/getUpdates",
                params={"offset": last_id + 1, "timeout": 20, "limit": 100},
                timeout=30)
            if not r.ok:
                time.sleep(10); continue

            for upd in r.json().get("result", []):
                last_id = upd["update_id"]
                msg     = upd.get("message", {})
                text    = msg.get("text", "")
                chat    = msg.get("chat", {})
                cid     = str(chat.get("id", ""))
                uname   = chat.get("username", "") or chat.get("first_name", "ناشناس")
                if text.startswith("/start") and cid:
                    data  = load_data()
                    users = data.get("users", [])
                    if cid not in [str(u["chat_id"]) for u in users]:
                        users.append({"chat_id": cid, "username": uname, "joined_at": now_ir()})
                        data["users"] = users
                        ids = data["telegram"].setdefault("chat_ids", [])
                        if cid not in [str(x) for x in ids]:
                            ids.append(cid)
                        save_data(data)
                        tg_send(token, cid,
                            f"👋 سلام <b>{uname}</b>!\n\n"
                            "✅ ثبت شدید — آلارم‌های قیمت برای شما ارسال می‌شود 🔔")
                        print(f"[TG] new user: {uname} ({cid})")
        except Exception as e:
            print(f"[TG poll] {e}")
        time.sleep(5)

# ── Alert checker — every 3 min ────────────────────────────────────────────────
notified = set()

def check_loop():
    while True:
        try:
            data    = load_data()
            tg      = data.get("telegram", {})
            token   = tg.get("bot_token", "")
            ids     = list(tg.get("chat_ids", []))
            legacy  = tg.get("chat_id", "")
            if legacy and legacy not in [str(x) for x in ids]:
                ids.append(legacy)

            fired   = []
            changed = False

            for al in data.get("alerts", []):
                if not al.get("active", True):
                    continue
                aid   = al["id"]
                sym   = al["symbol"]
                atype = al.get("type", "crypto")
                tgt   = float(al["target_price"])
                cond  = al.get("condition", "above")

                price, source = get_price_full(sym, atype)
                ts = now_ir()
                al["last_checked"] = ts
                changed = True

                if price is None:
                    al["last_error"] = price_errors.get(sym, {}).get("msg", "خطا")
                    continue

                al["last_price"]  = price
                al["last_source"] = source
                al.pop("last_error", None)

                hit = (cond == "above" and price >= tgt) or \
                      (cond == "below" and price <= tgt)

                if hit and aid not in notified:
                    notified.add(aid)
                    al["active"]     = False
                    al["fired_at"]   = ts
                    al["fired_price"]= price
                    fired.append(aid)

                    if token and ids:
                        dirstr = "📈 از هدف بالا رفت" if cond == "above" else "📉 به هدف پایین آمد"
                        msg = (
                            f"🚨 <b>آلارم فعال شد!</b>\n\n"
                            f"💰 <b>{sym}</b> {dirstr}\n\n"
                            f"🎯 هدف: <b>${fmt_price(tgt,sym,atype)}</b>\n"
                            f"📊 قیمت: <b>${fmt_price(price,sym,atype)}</b>\n"
                            f"📏 فاصله: <b>{pips_str(price,tgt,sym,atype)}</b>\n"
                            f"📡 منبع: {source}\n\n"
                            f"⏰ {ts} (تهران)"
                        )
                        for cid in ids:
                            tg_send(token, str(cid), msg)
                        print(f"[ALERT] {sym} fired → {len(ids)} users")

            if fired:
                arch = data.get("archive", [])
                for aid in fired:
                    a = next((x for x in data["alerts"] if x["id"] == aid), None)
                    if a: arch.append(a)
                data["archive"] = arch
                data["alerts"]  = [x for x in data["alerts"] if x["id"] not in fired]

            if changed:
                save_data(data)

        except Exception as e:
            print(f"[CHECK] {e}")
        time.sleep(180)

# ── Flask routes ───────────────────────────────────────────────────────────────
@app.route("/")
def index():
    return send_from_directory("static", "index.html")

@app.route("/api/config", methods=["GET", "POST"])
def cfg():
    data = load_data()
    if request.method == "POST":
        body = request.json or {}
        tg   = data.setdefault("telegram", {})
        if body.get("bot_token"):
            tg["bot_token"] = body["bot_token"]
        if body.get("chat_id"):
            cid = str(body["chat_id"])
            ids = [str(x) for x in tg.get("chat_ids", [])]
            if cid not in ids:
                ids.append(cid)
            tg["chat_ids"] = ids
            tg["chat_id"]  = cid
        save_data(data)
        return jsonify({"ok": True})
    tg = data.get("telegram", {})
    return jsonify({"bot_token": tg.get("bot_token", ""),
                    "chat_id":   tg.get("chat_id", ""),
                    "chat_ids":  tg.get("chat_ids", []),
                    "user_count":len(data.get("users", []))})

@app.route("/api/users", methods=["GET"])
def users():
    return jsonify(load_data().get("users", []))

@app.route("/api/users/<cid>", methods=["DELETE"])
def del_user(cid):
    data = load_data()
    data["users"] = [u for u in data.get("users",[]) if str(u["chat_id"]) != cid]
    data["telegram"]["chat_ids"] = [x for x in data["telegram"].get("chat_ids",[]) if str(x) != cid]
    save_data(data)
    return jsonify({"ok": True})

@app.route("/api/alerts", methods=["GET"])
def get_alerts():
    return jsonify(load_data().get("alerts", []))

@app.route("/api/alerts", methods=["POST"])
def add_alert():
    data = load_data()
    body = request.json or {}
    sym  = body.get("symbol","").upper().strip()
    atype= body.get("type","crypto")
    tgt  = float(body.get("target_price", 0))

    # Fetch price right now so list shows it immediately
    price, source = get_price_full(sym, atype)

    al = {
        "id":           str(int(time.time()*1000)),
        "symbol":       sym,
        "type":         atype,
        "target_price": tgt,
        "condition":    body.get("condition","above"),
        "active":       True,
        "last_price":   price,
        "last_source":  source if price else None,
        "last_error":   None if price else source,
        "last_checked": now_ir(),
        "created_at":   now_ir()
    }
    data["alerts"].append(al)
    save_data(data)
    return jsonify({"ok": True, "alert": al,
                    "current_price": price, "source": source})

@app.route("/api/alerts/<aid>", methods=["DELETE"])
def del_alert(aid):
    data = load_data()
    data["alerts"] = [a for a in data["alerts"] if a["id"] != aid]
    save_data(data)
    notified.discard(aid)
    return jsonify({"ok": True})

@app.route("/api/archive", methods=["GET"])
def get_archive():
    return jsonify(load_data().get("archive", []))

@app.route("/api/archive", methods=["DELETE"])
def del_archive():
    data = load_data()
    data["archive"] = []
    save_data(data)
    return jsonify({"ok": True})

@app.route("/api/price/<atype>/<path:sym>", methods=["GET"])
def price_endpoint(atype, sym):
    sym = sym.upper().replace("-", "/")
    price, source = get_price_full(sym, atype)
    if price is None:
        return jsonify({"error": "قیمت پیدا نشد", "detail": source}), 404
    return jsonify({"symbol": sym, "price": price, "source": source, "ts": now_ir()})

@app.route("/api/errors", methods=["GET"])
def get_errors():
    return jsonify(price_errors)

@app.route("/api/test-telegram", methods=["POST"])
def test_tg():
    data  = load_data()
    tg    = data.get("telegram", {})
    token = tg.get("bot_token","")
    ids   = list(tg.get("chat_ids",[]))
    legacy= tg.get("chat_id","")
    if legacy and legacy not in [str(x) for x in ids]:
        ids.append(legacy)
    if not token or not ids:
        return jsonify({"ok":False,"error":"توکن یا chat_id نیست"})
    sent = sum(tg_send(token,str(c),
        f"✅ <b>تست موفق</b>\n⏰ {now_ir()} (تهران)") for c in ids)
    return jsonify({"ok": sent > 0, "sent": sent, "total": len(ids)})

@app.route("/health")
def health():
    data = load_data()
    return jsonify({"status":"ok","time_tehran":now_ir(),
                    "alerts":len(data.get("alerts",[])),
                    "users":len(data.get("users",[])),
                    "errors":len(price_errors)})

# ── Start ──────────────────────────────────────────────────────────────────────
threading.Thread(target=check_loop, daemon=True).start()
threading.Thread(target=poll_tg,    daemon=True).start()

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
