import os, json, time, threading, requests, pytz
from flask import Flask, request, jsonify, send_from_directory
from datetime import datetime

app = Flask(__name__, static_folder='static')

VERSION = "2.1"


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

# ── Weekend check ─────────────────────────────────────────────────
def is_forex_market_open():
    """
    فارکس شنبه و یکشنبه بسته‌ست.
    شنبه از ساعت ۰۰:۰۰ UTC تا یکشنبه ۲۱:۰۰ UTC بازار بسته است.
    """
    now_utc = datetime.utcnow()
    wd = now_utc.weekday()  # 0=Mon, 5=Sat, 6=Sun
    if wd == 5:  # شنبه — کل روز بسته
        return False
    if wd == 6:  # یکشنبه — تا ۲۱:۰۰ UTC بسته
        return now_utc.hour >= 21
    return True

# ── Pip distance ──────────────────────────────────────────────────
def calc_pips(symbol, cur, tgt):
    if not cur or not tgt:
        return None
    diff   = abs(float(cur) - float(tgt))
    sym_up = symbol.upper()
    if "XAU" in sym_up or "XAG" in sym_up:
        return diff  # دلار فاصله برای طلا
    if "JPY" in sym_up:
        return diff * 100
    return diff * 10000

def calc_dist_str(symbol, atype, cur, tgt):
    if not cur or not tgt:
        return ""
    diff   = abs(float(cur) - float(tgt))
    sym_up = symbol.upper()
    if atype == "crypto":
        return f"{diff/float(tgt)*100:.2f}%"
    if "XAU" in sym_up or "XAG" in sym_up:
        return f"{diff:.2f} $"
    if "JPY" in sym_up:
        return f"{round(diff*100):,} pip"
    return f"{round(diff*10000):,} pip"

# ── Smart interval ────────────────────────────────────────────────
def get_check_interval(symbol, atype, cur, tgt):
    """
    کریپتو: همیشه ۱۲۰ ثانیه (batch یه‌جا همه رو می‌گیره)
    فارکس معمولی:
      < 5 pip  → 60s
      < 30 pip → 120s (2 min)
      باقی     → 120s (batch)
    طلا (XAU):
      < 50 pip → 60s
      باقی     → 120s (batch)
    """
    if atype == "crypto":
        return 120

    sym_up = symbol.upper()
    is_gold = "XAU" in sym_up or "XAG" in sym_up

    pips = calc_pips(symbol, cur, tgt)
    if pips is None:
        return 120

    if is_gold:
        return 60 if pips < 50 else 120

    # فارکس معمولی
    return 60 if pips < 5 else 120

# ── Price — H ─────────────────────────────────────────────────────
H = {"User-Agent": "Mozilla/5.0 (compatible; PriceBot/1.0)"}

def _get(url, timeout=8):
    r = requests.get(url, timeout=timeout, headers=H)
    r.raise_for_status()
    return r.json()

# ── cache آخرین قیمت‌های موفق ────────────────────────────────────
_last_known = {}  # {"EURUSD": {"price": 1.175, "ts": "2026-05-09 10:00:00", "stale": False}}

# ── Forex batch — biquote با فرمت درست ───────────────────────────
def get_forex_prices_batch(symbols):
    """
    biquote.io/api/latest?symbols=EURUSD&symbols=GBPUSD&...
    اگه ۴۰۴ یا خطا داد → از آخرین قیمت موفق استفاده کن (stale)
    """
    if not symbols:
        return {}

    clean = [s.upper().replace("/", "").replace(" ", "") for s in symbols]
    qs = "&".join(f"symbols={s}" for s in clean)
    url = f"https://biquote.io/api/latest?{qs}"

    try:
        r = requests.get(url, timeout=12, headers=H)
        r.raise_for_status()
        raw = r.json()
        result = {}
        if isinstance(raw, list):
            for item in raw:
                sym = item.get("symbol","").upper().replace("/","")
                bid = item.get("bid") or item.get("price") or item.get("last")
                if sym and bid and float(bid) > 0:
                    result[sym] = float(bid)
                    _last_known[sym] = {"price": float(bid), "ts": now_teh(), "stale": False}
                    print(f"[biquote] {sym} = {float(bid)}")
        elif isinstance(raw, dict):
            for sym, data in raw.items():
                if isinstance(data, dict):
                    bid = data.get("bid") or data.get("price") or data.get("last")
                elif isinstance(data, (int, float)):
                    bid = data
                else:
                    bid = None
                if bid and float(bid) > 0:
                    result[sym.upper()] = float(bid)
                    _last_known[sym.upper()] = {"price": float(bid), "ts": now_teh(), "stale": False}
                    print(f"[biquote] {sym} = {float(bid)}")
        if result:
            print(f"[batch-OK] {len(result)}/{len(clean)} prices")
            return result
        log_error(f"biquote batch empty for {clean}")
    except Exception as e:
        status = "404" if "404" in str(e) else str(e)
        print(f"[batch-ERR] {status} — using last known prices")

    # ── Fallback: آخرین قیمت موفق ────────────────────────────────
    result = {}
    for sym in clean:
        if sym in _last_known:
            cached = _last_known[sym]
            _last_known[sym]["stale"] = True
            result[sym] = cached["price"]
            print(f"[stale] {sym} = {cached['price']} (از {cached['ts']})")
        else:
            # اگه اصلاً قیمتی نداریم → frankfurter fallback
            try:
                base, quote = sym[:3], sym[3:6]
                r3 = requests.get(
                    f"https://api.frankfurter.app/latest?from={base}&to={quote}",
                    timeout=7)
                if r3.ok:
                    rate = r3.json().get("rates", {}).get(quote)
                    if rate:
                        result[sym] = float(rate)
                        _last_known[sym] = {"price": float(rate), "ts": now_teh(), "stale": False}
                        print(f"[frankfurter] {sym} = {float(rate)}")
            except Exception as e3:
                print(f"[frankfurter-ERR] {sym}: {e3}")
    return result

def get_forex_price(symbol):
    """تک نماد فارکس — برای endpoint /api/price"""
    sym = symbol.upper().replace("/","").replace(" ","")
    batch = get_forex_prices_batch([sym])
    return batch.get(sym)

# ── Crypto ────────────────────────────────────────────────────────
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

def get_crypto_price(symbol):
    base = symbol.upper()
    for s in ["USDT","USDC","USD","BUSD"]:
        base = base.replace(s,"")
    base = base.replace("/","").strip()

    # biquote هم کریپتو داره — BTCUSD فرمت می‌خواد
    try:
        r = requests.get(f"https://biquote.io/api/latest?symbols={base}USD",
                         timeout=8, headers=H)
        if r.ok:
            raw = r.json()
            bid = None
            if isinstance(raw, list) and raw:
                bid = raw[0].get("bid") or raw[0].get("price") or raw[0].get("last")
            elif isinstance(raw, dict):
                bid = raw.get("bid") or raw.get("price") or raw.get("last")
            if bid and float(bid) > 100:  # کریپتو باید > 100 باشه
                print(f"[biquote-crypto] {base} = {float(bid)}")
                return float(bid)
    except Exception:
        pass

    sources = [
        ("OKX",           lambda: float(_get(f"https://www.okx.com/api/v5/market/ticker?instId={base}-USDT")["data"][0]["last"])),
        ("KuCoin",        lambda: float(_get(f"https://api.kucoin.com/api/v1/market/orderbook/level1?symbol={base}-USDT")["data"]["price"])),
        ("CoinGecko",     lambda: _cg_price(base)),
        ("CryptoCompare", lambda: float(_get(f"https://min-api.cryptocompare.com/data/price?fsym={base}&tsyms=USD")["USD"])),
        ("Binance-USDT",  lambda: float(_get(f"https://api.binance.com/api/v3/ticker/price?symbol={base}USDT")["price"])),
        ("Binance-USDC",  lambda: float(_get(f"https://api.binance.com/api/v3/ticker/price?symbol={base}USDC")["price"])),
        ("Bybit",         lambda: float(_get(f"https://api.bybit.com/v5/market/tickers?category=spot&symbol={base}USDT")["result"]["list"][0]["lastPrice"])),
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

def get_price(symbol, asset_type):
    if asset_type == "crypto":
        return get_crypto_price(symbol)
    return get_forex_price(symbol)

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

# ── Main check loop ───────────────────────────────────────────────
#
# منطق جدید:
# - هر ۶۰ ثانیه loop می‌زنه
# - یه batch برای همه فارکس‌ها می‌فرسته (اگه بازار باز باشه)
# - کریپتوها هم جداگانه
# - آلارم‌هایی که interval=60s دارن هر دور چک می‌شن
# - بقیه (interval=120s) یک در میان
# - شنبه/یکشنبه فارکس چک نمی‌شه مگه کلیک دستی
#
notified = set()
_loop_count = 0  # شمارنده دور

def check_alerts():
    global _loop_count
    while True:
        try:
            _loop_count += 1
            global _cache
            _cache = None
            token, cids, data = _get_token_and_cids()
            now_dt = datetime.now(TEHRAN)
            active = [a for a in data.get("alerts", []) if a.get("active")]

            if not active:
                save_data(data)
                time.sleep(60)
                continue

            forex_open = is_forex_market_open()

            # ── تعیین کدوم آلارم‌ها این دور باید چک بشن ──────────
            due_forex  = []
            due_crypto = []

            for a in active:
                sym   = a["symbol"]
                atype = a.get("type", "crypto")
                cur   = a.get("last_price")
                tgt   = float(a["target_price"])
                iv    = get_check_interval(sym, atype, cur, tgt)

                # فارکس شنبه/یکشنبه skip (مگه آلارم تازه و قیمت نداره)
                if atype == "forex" and not forex_open:
                    continue

                # آلارم‌های 60s هر دور، آلارم‌های 120s یک در میان
                if iv <= 60:
                    pass  # همیشه due
                else:
                    if _loop_count % 2 != 0:  # دور فرد skip
                        continue

                if atype == "forex":
                    due_forex.append(sym)
                else:
                    due_crypto.append(sym)

            # ── یه batch برای همه فارکس‌ها ───────────────────────
            price_map = {}

            if due_forex:
                uniq_forex = list(dict.fromkeys(due_forex))
                batch = get_forex_prices_batch(uniq_forex)
                for sym, p in batch.items():
                    price_map[(sym, "forex")] = p
                    # stale flag
                    known = _last_known.get(sym, {})
                    if known.get("stale"):
                        price_map[(sym, "forex", "stale")] = known.get("ts", "")

            # ── کریپتو ───────────────────────────────────────────
            uniq_crypto = list(dict.fromkeys(due_crypto))
            for sym in uniq_crypto:
                p = get_crypto_price(sym)
                price_map[(sym.upper(), "crypto")] = p

            print(f"[check] loop={_loop_count} forex_open={forex_open} due_f={len(due_forex)} due_c={len(due_crypto)} prices={len(price_map)}")

            # ── بررسی آلارم‌ها ────────────────────────────────────
            fired = []
            for a in active:
                sym   = a["symbol"]
                atype = a.get("type", "crypto")
                key   = (sym.upper(), atype)

                if key not in price_map:
                    continue

                cur = price_map[key]
                if cur is None:
                    continue

                tgt  = float(a["target_price"])
                cond = a.get("condition", "above")

                a["last_price"]     = cur
                a["last_checked"]   = now_teh()
                a["check_interval"] = get_check_interval(sym, atype, cur, tgt)
                # اگه قیمت stale (تعطیل بازار) بود نشون بده
                stale_ts = price_map.get((sym.upper(), atype, "stale"))
                a["price_stale"]    = stale_ts if stale_ts else None
                data["last_update"] = now_teh()

                triggered = (cond == "above" and cur >= tgt) or (cond == "below" and cur <= tgt)

                if triggered and a["id"] not in notified:
                    notified.add(a["id"])
                    a["active"]      = False
                    a["fired_at"]    = now_teh()
                    a["fired_price"] = cur
                    fired.append(a["id"])

                if token and cids:
                    cmt = f"\n💬 {a['comment']}" if a.get("comment") else ""
                if cond == "above":
                   zone_text = "🎯 ناحیه خرید فعال شد!"
                   arrow_emoji = "📈"
                else:
                   zone_text = "🎯 ناحیه فروش فعال شد!"
                   arrow_emoji = "📉"
    
                msg = (
                         f"{zone_text}\n\n"
                         f"{arrow_emoji} <b>{sym}</b>\n\n"
                         f"💰 قیمت فعلی: <b>{fmt_price(cur, sym)}</b>\n"
                         f"🎯 قیمت آلارم: <b>{fmt_price(tgt, sym)}</b>"
                         f"{cmt}\n\n⏰ {now_pretty()} (تهران)"
                     )
    
    results = broadcast(token, cids, msg)
    print(f"[FIRED] {sym} → sent={sum(results)}/{len(cids)}")
                        results = broadcast(token, cids, msg)
                        print(f"[FIRED] {sym} → sent={sum(results)}/{len(cids)}")

            # ── آرشیو کردن فایرشده‌ها ────────────────────────────
            if fired:
                arch = data.get("archive", [])
                for fid in fired:
                    obj = next((x for x in data["alerts"] if x["id"] == fid), None)
                    if obj: arch.append(obj)
                data["archive"] = arch
                data["alerts"]  = [x for x in data["alerts"] if x["id"] not in fired]
                print(f"[check] fired={len(fired)} → archived")

            save_data(data)

        except Exception as e:
            log_error(f"check_alerts: {e}")

        time.sleep(60)  # هر ۶۰ ثانیه

def fmt_price(p, sym=""):
    if p is None: return "—"
    v  = float(p)
    su = sym.upper()
    if "XAU" in su or "XAG" in su: return f"${v:,.2f}"
    if "JPY" in su: return f"${v:.3f}"
    if v >= 10000:  return f"${v:,.1f}"
    if v >= 100:    return f"${v:,.2f}"
    if v >= 1:      return f"${v:.5f}"
    return f"${v:.6f}"

# ── Routes ────────────────────────────────────────────────────────
@app.route("/")
def index():
    return send_from_directory("static", "index.html")

@app.route("/journal")
def journal():
    return send_from_directory("static", "journal.html")

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
    return jsonify({
        "bot_token":  tg.get("bot_token",""),
        "chat_id":    tg.get("chat_id",""),
        "chat_ids":   tg.get("chat_ids",[]),
        "user_count": len(data.get("users",[]))
    })

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

    # قیمت لحظه‌ای رو هنگام ثبت بگیر
    cur = get_price(sym, atype) if (atype!="forex" or is_forex_market_open()) else None

    a = {
        "id":             str(int(time.time() * 1000)),
        "symbol":         sym,
        "type":           atype,
        "target_price":   tgt,
        "condition":      body.get("condition","above"),
        "comment":        body.get("comment","").strip(),
        "active":         True,
        "last_price":     cur,
        "last_checked":   now_teh() if cur else None,
        "check_interval": get_check_interval(sym, atype, cur, tgt),
        "created_at":     now_teh()
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
    """همیشه قیمت لحظه‌ای رو برمی‌گردونه — حتی آخر هفته (برای کلیک دستی)"""
    sym = symbol.upper().replace("-","/")
    p = get_price(sym, atype)
    if p is None:
        return jsonify({"error": "قیمت پیدا نشد"}), 404
    return jsonify({"symbol": sym, "price": p})

@app.route("/api/test-telegram", methods=["POST"])
def test_tg():
    token, cids, _ = _get_token_and_cids()
    if not token or not cids:
        return jsonify({"ok": False, "error": "توکن یا chat_id ست نشده"})
    res = broadcast(token, cids,
        f"✅ <b>تست موفق</b>\n🔔 اتصال برقرار است.\n⏰ {now_pretty()} (تهران)")
    return jsonify({"ok": any(res), "sent": sum(res), "total": len(cids)})

@app.route("/api/status")
def status():
    data = load_data()
    return jsonify({
        "status":       "ok",
        "last_update":  data.get("last_update"),
        "errors":       data.get("errors", [])[-5:],
        "time_tehran":  now_teh(),
        "alert_count":  len(data.get("alerts",[])),
        "forex_open":   is_forex_market_open(),
        "loop_count":   _loop_count,
    })

@app.route("/api/version")
def version():
    return jsonify({"version": VERSION})

@app.route("/health")
def health():
    return jsonify({"status": "ok", "time": now_teh()})

threading.Thread(target=check_alerts,  daemon=True).start()
threading.Thread(target=poll_telegram, daemon=True).start()

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
