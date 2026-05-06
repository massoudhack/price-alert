import os, json, time, threading, requests, pytz
from flask import Flask, request, jsonify, send_from_directory
from datetime import datetime

app = Flask(__name__, static_folder='static')

TEHRAN     = pytz.timezone("Asia/Tehran")
GIST_ID    = os.environ.get("GIST_ID", "")
GIST_TOKEN = os.environ.get("GIST_TOKEN", "")
GIST_FILE  = "alerts.json"
_cache     = None

def now_teh():
    return datetime.now(TEHRAN).strftime("%Y-%m-%d %H:%M:%S")

def now_pretty():
    return datetime.now(TEHRAN).strftime("%Y/%m/%d %H:%M")

def _empty():
    return {
        "alerts": [], "archive": [],
        "telegram": {"bot_token": "", "chat_ids": []},
        "users": [], "errors": [], "last_update": None
    }

def _fix(data):
    """مطمئن میشه همه کلیدها وجود دارن"""
    e = _empty()
    for k, v in e.items():
        if k not in data:
            data[k] = v
    return data

# ── Storage ───────────────────────────────────────────────────────
def load_data():
    global _cache
    if _cache is not None:
        return _cache
    if GIST_ID and GIST_TOKEN:
        try:
            r = requests.get(
                f"https://api.github.com/gists/{GIST_ID}",
                headers={"Authorization": f"token {GIST_TOKEN}"},
                timeout=10)
            if r.status_code == 200:
                content = r.json()["files"][GIST_FILE]["content"]
                _cache = _fix(json.loads(content))
                return _cache
        except Exception as e:
            print(f"[gist load] {e}")
    # fallback local
    if os.path.exists("alerts.json"):
        try:
            with open("alerts.json", "r", encoding="utf-8") as f:
                _cache = _fix(json.load(f))
                return _cache
        except Exception:
            pass
    _cache = _empty()
    return _cache

def save_data(data):
    global _cache
    _cache = data
    if GIST_ID and GIST_TOKEN:
        try:
            requests.patch(
                f"https://api.github.com/gists/{GIST_ID}",
                headers={"Authorization": f"token {GIST_TOKEN}"},
                json={"files": {GIST_FILE: {"content": json.dumps(data, indent=2, ensure_ascii=False)}}},
                timeout=10)
            return
        except Exception as e:
            print(f"[gist save] {e}")
    try:
        with open("alerts.json", "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
    except Exception as e:
        print(f"[local save] {e}")

def log_error(msg):
    try:
        data = load_data()
        errs = data.get("errors", [])
        errs.append({"time": now_teh(), "msg": str(msg)})
        data["errors"] = errs[-20:]
        save_data(data)
    except Exception:
        pass
    print(f"[ERR] {msg}")

# ── Price Fetching — multi-source with full precision ─────────────
H = {"User-Agent": "Mozilla/5.0 (compatible; PriceBot/1.0)"}

def _get(url, timeout=8):
    r = requests.get(url, timeout=timeout, headers=H)
    r.raise_for_status()
    return r.json()

def get_crypto_price(symbol):
    """Try 6 sources in order, return first successful price"""
    base = symbol.upper()
    for s in ["USDT","USDC","USD","BUSD"]:
        base = base.replace(s,"")
    base = base.replace("/","").strip()

    sources = [
        ("Binance-USDT",   lambda: float(_get(f"https://api.binance.com/api/v3/ticker/price?symbol={base}USDT")["price"])),
        ("Binance-USDC",   lambda: float(_get(f"https://api.binance.com/api/v3/ticker/price?symbol={base}USDC")["price"])),
        ("Bybit",          lambda: float(_get(f"https://api.bybit.com/v5/market/tickers?category=spot&symbol={base}USDT")["result"]["list"][0]["lastPrice"])),
        ("OKX",            lambda: float(_get(f"https://www.okx.com/api/v5/market/ticker?instId={base}-USDT")["data"][0]["last"])),
        ("KuCoin",         lambda: float(_get(f"https://api.kucoin.com/api/v1/market/orderbook/level1?symbol={base}-USDT")["data"]["price"])),
        ("CoinGecko",      lambda: _cg_price(base)),
        ("CryptoCompare",  lambda: float(_get(f"https://min-api.cryptocompare.com/data/price?fsym={base}&tsyms=USD")["USD"])),
    ]
    for name, fn in sources:
        try:
            p = fn()
            if p and p > 0:
                print(f"[price] {base} = {p} via {name}")
                return float(p)
        except Exception as e:
            print(f"[{name}] {base} failed: {e}")
    log_error(f"All crypto sources failed for {symbol}")
    return None

CG_MAP = {
    "BTC":"bitcoin","ETH":"ethereum","BNB":"binancecoin","SOL":"solana",
    "XRP":"ripple","ADA":"cardano","DOGE":"dogecoin","TRX":"tron",
    "TON":"toncoin","AVAX":"avalanche-2","LINK":"chainlink","DOT":"polkadot",
    "MATIC":"matic-network","UNI":"uniswap","ATOM":"cosmos","LTC":"litecoin",
    "SHIB":"shiba-inu","OP":"optimism","ARB":"arbitrum","NEAR":"near",
    "FTM":"fantom","SAND":"the-sandbox","MANA":"decentraland",
}

def _cg_price(base):
    gid = CG_MAP.get(base)
    if not gid: return None
    d = _get(f"https://api.coingecko.com/api/v3/simple/price?ids={gid}&vs_currencies=usd")
    return float(d[gid]["usd"])

def get_forex_price(symbol):
    sym = symbol.upper().replace("/", "").replace(" ", "").strip()
    try:
        r = requests.get(f"https://biquote.io/api/{sym}", timeout=8, headers=H)
        r.raise_for_status()
        d = r.json()
        bid = d.get("bid")
        if bid is not None and float(bid) > 0:
            print(f"[biquote] {sym} = {float(bid)}")
            return float(bid)
        log_error(f"biquote zero for {sym}")
        return None
    except Exception as e:
        log_error(f"biquote failed for {sym}: {e}")
        return None


def get_price(symbol, asset_type):
    if asset_type == "crypto": return get_crypto_price(symbol)
    return get_forex_price(symbol)

def calc_dist(symbol, atype, cur, tgt):
    if not cur or not tgt: return ""
    diff = abs(tgt - cur)
    if atype == "crypto":
        return f"{diff/cur*100:.2f}%"
    is_jpy = "JPY" in symbol.upper()
    return f"{round(diff*(100 if is_jpy else 10000)):,} pip"

# ── Telegram ──────────────────────────────────────────────────────
def send_tg(token, chat_id, text):
    try:
        r = requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": str(chat_id), "text": text, "parse_mode": "HTML"},
            timeout=10, headers=H)
        return r.status_code == 200
    except Exception:
        return False

def broadcast(token, chat_ids, text):
    return [send_tg(token, c, text) for c in chat_ids]

def _get_token_and_cids():
    data  = load_data()
    tg    = data.get("telegram", {})
    token = tg.get("bot_token", "")
    cids  = list(tg.get("chat_ids", []))
    leg   = tg.get("chat_id", "")
    if leg and leg not in [str(x) for x in cids]:
        cids.append(leg)
    return token, cids, data

# ── Telegram polling ──────────────────────────────────────────────
def poll_telegram():
    last_id = 0
    while True:
        try:
            token, _, data = _get_token_and_cids()
            if not token:
                time.sleep(30)
                continue
            r = requests.get(
                f"https://api.telegram.org/bot{token}/getUpdates",
                params={"offset": last_id+1, "timeout": 20, "limit": 100},
                timeout=30, headers=H)
            if r.status_code != 200:
                time.sleep(10)
                continue
            for upd in r.json().get("result", []):
                last_id = upd["update_id"]
                msg   = upd.get("message", {})
                txt   = msg.get("text", "")
                ch    = msg.get("chat", {})
                cid   = str(ch.get("id", ""))
                uname = ch.get("username", "") or ch.get("first_name", "")
                if txt.startswith("/start") and cid:
                    data  = load_data()
                    users = data.get("users", [])
                    if cid not in [str(u["chat_id"]) for u in users]:
                        users.append({"chat_id": cid, "username": uname, "joined_at": now_teh()})
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

# ── Price alert checker — every 1 minute ─────────────────────────
notified = set()

def check_alerts():
    while True:
        try:
            global _cache
            _cache = None  # هر دور از Gist بخون
            token, cids, data = _get_token_and_cids()
            fired = []
            for a in data.get("alerts", []):
                if not a.get("active"):
                    continue
                sym, atype = a["symbol"], a.get("type", "crypto")
                tgt  = float(a["target_price"])
                cond = a.get("condition", "above")
                cur  = get_price(sym, atype)
                if cur is None:
                    continue
                a["last_price"]   = cur
                a["last_checked"] = now_teh()
                data["last_update"] = now_teh()
                triggered = (cond == "above" and cur >= tgt) or (cond == "below" and cur <= tgt)
                if triggered and a["id"] not in notified:
                    notified.add(a["id"])
                    a["active"] = False
                    a["fired_at"]    = now_teh()
                    a["fired_price"] = cur
                    fired.append(a["id"])
                    if token and cids:
                        dist = calc_dist(sym, atype, cur, tgt)
                        cmt  = f"\n💬 <i>{a['comment']}</i>" if a.get("comment") else ""
                        msg  = (
                            f"🚨 <b>آلارم قیمت!</b>\n\n"
                            f"💰 <b>{sym}</b> "
                            f"{'📈 از هدف رد شد' if cond=='above' else '📉 به هدف رسید'}\n\n"
                            f"🎯 هدف: <b>${tgt:,.5f}</b>\n"
                            f"📊 قیمت: <b>${cur:,.5f}</b>\n"
                            f"📏 فاصله: <b>{dist}</b>"
                            f"{cmt}\n\n⏰ {now_pretty()} (تهران)"
                        )
                        broadcast(token, cids, msg)
            if fired:
                arch = data.get("archive", [])
                for fid in fired:
                    obj = next((x for x in data["alerts"] if x["id"] == fid), None)
                    if obj: arch.append(obj)
                data["archive"] = arch
                data["alerts"]  = [x for x in data["alerts"] if x["id"] not in fired]
            save_data(data)
        except Exception as e:
            log_error(f"check_alerts: {e}")
        time.sleep(120)  # every 2 minutes

# ── Routes ────────────────────────────────────────────────────────
@app.route("/")
def index():
    return send_from_directory("static", "index.html")

@app.route("/api/config", methods=["GET","POST"])
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
            if cid not in ids: ids.append(cid)
            tg["chat_ids"] = ids
            tg["chat_id"]  = cid
        data["telegram"] = tg
        save_data(data)
        return jsonify({"ok": True})
    tg = data.get("telegram", {})
    return jsonify({"bot_token": tg.get("bot_token",""), "chat_id": tg.get("chat_id",""),
                    "chat_ids": tg.get("chat_ids",[]), "user_count": len(data.get("users",[]))})

@app.route("/api/alerts", methods=["GET"])
def get_alerts():
    return jsonify(load_data().get("alerts", []))

@app.route("/api/alerts", methods=["POST"])
def add_alert():
    data = load_data()
    body = request.json or {}
    a = {
        "id": str(int(time.time() * 1000)),
        "symbol":       body.get("symbol","").upper().strip(),
        "type":         body.get("type","crypto"),
        "target_price": float(body.get("target_price", 0)),
        "condition":    body.get("condition","above"),
        "comment":      body.get("comment","").strip(),
        "active":       True,
        "last_price":   None,
        "last_checked": None,
        "created_at":   now_teh()
    }
    data["alerts"].append(a)
    save_data(data)
    return jsonify({"ok": True, "alert": a})

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
def clear_archive():
    data = load_data()
    data["archive"] = []
    save_data(data)
    return jsonify({"ok": True})

@app.route("/api/archive/<aid>", methods=["DELETE"])
def del_archive(aid):
    data = load_data()
    data["archive"] = [a for a in data.get("archive",[]) if a["id"] != aid]
    save_data(data)
    return jsonify({"ok": True})

@app.route("/api/users", methods=["GET"])
def get_users():
    return jsonify(load_data().get("users", []))

@app.route("/api/users/<cid>", methods=["DELETE"])
def del_user(cid):
    data = load_data()
    data["users"] = [u for u in data.get("users",[]) if str(u["chat_id"]) != str(cid)]
    data["telegram"]["chat_ids"] = [x for x in data["telegram"].get("chat_ids",[]) if str(x) != str(cid)]
    save_data(data)
    return jsonify({"ok": True})

@app.route("/api/price/<atype>/<symbol>")
def live_price(atype, symbol):
    p = get_price(symbol.upper(), atype)
    if p is None:
        return jsonify({"error": "قیمت پیدا نشد"}), 404
    return jsonify({"symbol": symbol.upper(), "price": p})

@app.route("/api/test-telegram", methods=["POST"])
def test_tg():
    token, cids, _ = _get_token_and_cids()
    if not token or not cids:
        return jsonify({"ok": False, "error": "توکن یا chat_id ست نشده"})
    res = broadcast(token, cids, f"✅ <b>تست موفق</b>\n🔔 اتصال برقرار است.\n⏰ {now_pretty()} (تهران)")
    return jsonify({"ok": any(res), "sent": sum(res), "total": len(cids)})

@app.route("/api/status")
def status():
    data = load_data()
    return jsonify({
        "status":      "ok",
        "last_update": data.get("last_update"),
        "errors":      data.get("errors", [])[-5:],
        "time_tehran": now_teh(),
        "alert_count": len(data.get("alerts",[])),
    })

@app.route("/health")
def health():
    return jsonify({"status": "ok", "time": now_teh()})

threading.Thread(target=check_alerts,  daemon=True).start()
threading.Thread(target=poll_telegram, daemon=True).start()

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
