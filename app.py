import os, json, time, threading, requests, pytz
from flask import Flask, request, jsonify, send_from_directory
from datetime import datetime, timedelta
from groq import Groq
import re

app = Flask(__name__, static_folder='static')
VERSION = "8.0"

TEHRAN = pytz.timezone("Asia/Tehran")

# ==================== متغیرهای محیطی ====================
GIST_TOKEN = os.environ.get("GIST_TOKEN", "")
GIST_ID_ALERTS = os.environ.get("GIST_ID", "")
GIST_ID_JOURNAL = os.environ.get("GIST_ID_JOURNAL", "")
ALERTS_FILE = "alerts.json"
JOURNAL_FILE = "journal_data.json"
GROQ_API_KEY = os.environ.get("GROQ_API_KEY", "")
BOT_TOKEN_ENV = os.environ.get("BOT_TOKEN", "")
YOUR_CHAT_ID = "109419675"
BROADCAST_MODE = os.environ.get("BROADCAST_MODE", "false").lower() == "true"

_cache_alerts = None
_cache_journal = None

def now_teh():
    return datetime.now(TEHRAN).strftime("%Y-%m-%d %H:%M:%S")

def now_pretty():
    return now_teh()  # alias برای سازگاری

def get_pip_multiplier(symbol):
    sym_up = symbol.upper()
    crypto_list = ['BTC','ETH','SOL','BNB','XRP','ADA','DOGE','TRX','TON','AVAX','MATIC','DOT','LINK','UNI','ATOM','LTC','SHIB','OP','ARB','NEAR','FTM','SAND','MANA']
    if any(x in sym_up for x in crypto_list):
        return 1
    if "XAU" in sym_up or "XAG" in sym_up:
        return 10
    if "JPY" in sym_up:
        return 100
    return 10000

def is_crypto_symbol(sym):
    sym_up = sym.upper()
    crypto_list = ['BTC','ETH','SOL','BNB','XRP','ADA','DOGE','TRX','TON','AVAX','MATIC','DOT','LINK','UNI','ATOM','LTC','SHIB','OP','ARB','NEAR','FTM','SAND','MANA']
    return any(c in sym_up for c in crypto_list)

def _empty_alerts():
    return {
        "alerts": [], "archive": [], "telegram": {"bot_token": "", "chat_ids": []},
        "users": [], "errors": [], "last_update": None
    }

def fix_alerts(data):
    e = _empty_alerts()
    for k in e:
        if k not in data:
            data[k] = e[k]
    return data

def load_alerts():
    global _cache_alerts
    if _cache_alerts is not None:
        return _cache_alerts
    if GIST_ID_ALERTS and GIST_TOKEN:
        try:
            print(f"[alerts] Loading from Gist {GIST_ID_ALERTS}...")
            r = requests.get(f"https://api.github.com/gists/{GIST_ID_ALERTS}",
                             headers={"Authorization": f"token {GIST_TOKEN}"}, timeout=10)
            if r.status_code == 200:
                content = r.json()["files"][ALERTS_FILE]["content"]
                _cache_alerts = fix_alerts(json.loads(content))
                print(f"[alerts] Loaded from Gist")
                return _cache_alerts
            else:
                print(f"[alerts] Gist read failed: {r.status_code}")
        except Exception as e:
            print(f"[alerts] Exception: {e}")
    if os.path.exists(ALERTS_FILE):
        try:
            with open(ALERTS_FILE, "r", encoding="utf-8") as f:
                _cache_alerts = fix_alerts(json.load(f))
                print(f"[alerts] Loaded from local")
                return _cache_alerts
        except Exception as e:
            print(f"[alerts] Local error: {e}")
    _cache_alerts = _empty_alerts()
    return _cache_alerts

def save_alerts(data):
    global _cache_alerts
    _cache_alerts = data
    if GIST_ID_ALERTS and GIST_TOKEN:
        try:
            r = requests.patch(f"https://api.github.com/gists/{GIST_ID_ALERTS}",
                               headers={"Authorization": f"token {GIST_TOKEN}"},
                               json={"files": {ALERTS_FILE: {"content": json.dumps(data, indent=2, ensure_ascii=False)}}},
                               timeout=10)
            if r.status_code == 200:
                print(f"[alerts] Saved to Gist")
            else:
                print(f"[alerts] Gist save failed: {r.status_code}")
        except Exception as e:
            print(f"[alerts] Exception: {e}")
    with open(ALERTS_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

def load_journal():
    global _cache_journal
    if _cache_journal is not None:
        print(f"[JOURNAL:LOAD] از cache — {len(_cache_journal)} ترید")
        return _cache_journal
    print(f"[JOURNAL:LOAD] شروع لود — GIST_ID={GIST_ID_JOURNAL[:8] if GIST_ID_JOURNAL else 'ندارد'}")
    if GIST_ID_JOURNAL and GIST_TOKEN:
        try:
            print(f"[JOURNAL:LOAD] درخواست Gist...")
            r = requests.get(f"https://api.github.com/gists/{GIST_ID_JOURNAL}",
                             headers={"Authorization": f"token {GIST_TOKEN}"}, timeout=10)
            print(f"[JOURNAL:LOAD] Gist status: {r.status_code}")
            if r.status_code == 200:
                files = r.json().get("files", {})
                if JOURNAL_FILE in files:
                    content = files[JOURNAL_FILE]["content"]
                    _cache_journal = json.loads(content)
                    if not isinstance(_cache_journal, list):
                        print(f"[JOURNAL:LOAD] ⚠️ داده Gist list نیست — reset")
                        _cache_journal = []
                    print(f"[JOURNAL:LOAD] ✅ {len(_cache_journal)} ترید از Gist لود شد")
                    return _cache_journal
                else:
                    print(f"[JOURNAL:LOAD] ⚠️ فایل {JOURNAL_FILE} در Gist نیست — فایل‌های موجود: {list(files.keys())}")
            else:
                print(f"[JOURNAL:LOAD] ❌ Gist خطا: {r.status_code} — {r.text[:100]}")
        except Exception as e:
            print(f"[JOURNAL:LOAD] ❌ Exception Gist: {e}")
    if os.path.exists(JOURNAL_FILE):
        try:
            with open(JOURNAL_FILE, "r", encoding="utf-8") as f:
                _cache_journal = json.load(f)
                if not isinstance(_cache_journal, list):
                    _cache_journal = []
                print(f"[JOURNAL:LOAD] ✅ {len(_cache_journal)} ترید از فایل لوکال لود شد")
                return _cache_journal
        except Exception as e:
            print(f"[JOURNAL:LOAD] ❌ خطا فایل لوکال: {e}")
    print(f"[JOURNAL:LOAD] ⚠️ هیچ داده‌ای نیست — لیست خالی برگشت")
    _cache_journal = []
    return _cache_journal

def save_journal(journal_list):
    global _cache_journal
    _cache_journal = journal_list
    print(f"[JOURNAL:SAVE] شروع ذخیره {len(journal_list)} ترید...")
    if GIST_ID_JOURNAL and GIST_TOKEN:
        try:
            print(f"[JOURNAL:SAVE] ارسال به Gist...")
            r = requests.patch(f"https://api.github.com/gists/{GIST_ID_JOURNAL}",
                               headers={"Authorization": f"token {GIST_TOKEN}"},
                               json={"files": {JOURNAL_FILE: {"content": json.dumps(journal_list, indent=2, ensure_ascii=False)}}},
                               timeout=10)
            if r.status_code == 200:
                print(f"[JOURNAL:SAVE] ✅ Gist ذخیره شد — {len(journal_list)} ترید")
            else:
                print(f"[JOURNAL:SAVE] ❌ Gist خطا: {r.status_code} — {r.text[:120]}")
        except Exception as e:
            print(f"[JOURNAL:SAVE] ❌ Exception Gist: {e}")
    try:
        with open(JOURNAL_FILE, "w", encoding="utf-8") as f:
            json.dump(journal_list, f, indent=2, ensure_ascii=False)
        print(f"[JOURNAL:SAVE] ✅ فایل لوکال ذخیره شد")
    except Exception as e:
        print(f"[JOURNAL:SAVE] ❌ خطا فایل لوکال: {e}")

def log_error(msg):
    try:
        data = load_alerts()
        errs = data.get("errors", [])
        errs.append({"time": now_teh(), "msg": str(msg)})
        data["errors"] = errs[-20:]
        save_alerts(data)
    except:
        pass
    print(f"[ERR] {msg}")

def is_forex_market_open():
    now_utc = datetime.utcnow()
    wd = now_utc.weekday()
    if wd == 5: return False
    if wd == 6: return now_utc.hour >= 21
    return True

H = {"User-Agent": "Mozilla/5.0 (compatible; PriceBot/1.0)"}
_last_known = {}

def get_forex_prices_batch(symbols):
    if not symbols: return {}
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
        if result: return result
    except Exception: pass
    result = {}
    for sym in clean:
        if sym in _last_known:
            cached = _last_known[sym]
            _last_known[sym]["stale"] = True
            result[sym] = cached["price"]
        else:
            try:
                base, quote = sym[:3], sym[3:6]
                r3 = requests.get(f"https://api.frankfurter.app/latest?from={base}&to={quote}", timeout=7)
                if r3.ok:
                    rate = r3.json().get("rates", {}).get(quote)
                    if rate:
                        result[sym] = float(rate)
                        _last_known[sym] = {"price": float(rate), "ts": now_teh(), "stale": False}
            except Exception: pass
    return result

def get_forex_price(symbol):
    sym = symbol.upper().replace("/","").replace(" ","")
    batch = get_forex_prices_batch([sym])
    return batch.get(sym)

CG_MAP = {
    "BTC":"bitcoin","ETH":"ethereum","BNB":"binancecoin","SOL":"solana",
    "XRP":"ripple","ADA":"cardano","DOGE":"dogecoin","TRX":"tron",
    "TON":"toncoin","AVAX":"avalanche-2","LINK":"chainlink","DOT":"polkadot",
    "MATIC":"matic-network","UNI":"uniswap","ATOM":"cosmos","LTC":"litecoin",
    "SHIB":"shiba-inu","OP":"optimism","ARB":"arbitrum","NEAR":"near",
}

def _cg_price(base):
    gid = CG_MAP.get(base)
    if not gid: return None
    try:
        d = requests.get(f"https://api.coingecko.com/api/v3/simple/price?ids={gid}&vs_currencies=usd", headers=H, timeout=8).json()
        return float(d[gid]["usd"])
    except:
        return None

def get_crypto_price(symbol):
    base = symbol.upper()
    for s in ["USDT","USDC","USD","BUSD"]:
        base = base.replace(s,"")
    base = base.replace("/","").strip()
    try:
        r = requests.get(f"https://biquote.io/api/latest?symbols={base}USD", timeout=8, headers=H)
        if r.ok:
            raw = r.json()
            bid = None
            if isinstance(raw, list) and raw:
                bid = raw[0].get("bid") or raw[0].get("price") or raw[0].get("last")
            elif isinstance(raw, dict):
                bid = raw.get("bid") or raw.get("price") or raw.get("last")
            if bid and float(bid) > 100:
                return float(bid)
    except Exception: pass
    sources = [
        ("OKX", lambda: float(requests.get(f"https://www.okx.com/api/v5/market/ticker?instId={base}-USDT", headers=H).json()["data"][0]["last"])),
        ("Binance-USDT", lambda: float(requests.get(f"https://api.binance.com/api/v3/ticker/price?symbol={base}USDT", headers=H).json()["price"])),
    ]
    for name, fn in sources:
        try:
            p = fn()
            if p and p > 0:
                return float(p)
        except: pass
    log_error(f"Crypto price failed for {symbol}")
    return None

def get_price(symbol, asset_type):
    if asset_type == "crypto":
        return get_crypto_price(symbol)
    return get_forex_price(symbol)

def send_tg(token, chat_id, text):
    try:
        r = requests.post(f"https://api.telegram.org/bot{token}/sendMessage", json={"chat_id": str(chat_id), "text": text, "parse_mode": "HTML"}, timeout=10, headers=H)
        return r.status_code == 200
    except: return False

def broadcast(token, chat_ids, text):
    return [send_tg(token, c, text) for c in chat_ids]

def _get_token_and_cids():
    data = load_alerts()
    tg = data.get("telegram", {})
    token = BOT_TOKEN_ENV or tg.get("bot_token", "")
    cids = list(tg.get("chat_ids", []))
    leg = tg.get("chat_id", "")
    if leg and leg not in [str(x) for x in cids]:
        cids.append(leg)
    return token, cids, data


FF_NEWS_HOUR = int(os.environ.get("NEWS_HOUR", "7"))   # ساعت ارسال روزانه (تهران)
FF_NEWS_MINUTE = int(os.environ.get("NEWS_MINUTE", "0"))

def fetch_ff_news():
    """
    تقویم اقتصادی ForexFactory رو از RSS می‌گیره.
    فقط رویدادهای USD با impact بالا (⭐⭐⭐) برمی‌گردونه.
    """
    try:
        import xml.etree.ElementTree as ET
        from datetime import timezone
        r = requests.get(
            "https://nfs.faireconomy.media/ff_calendar_thisweek.json",
            headers={**H, "User-Agent": "Mozilla/5.0"},
            timeout=10)
        if r.status_code == 200:
            events = r.json()
        else:
            # fallback: RSS
            r2 = requests.get("https://www.forexfactory.com/ffcal_week_this.xml",
                headers={**H, "User-Agent": "Mozilla/5.0"}, timeout=10)
            if r2.status_code != 200:
                return None, "❌ دریافت داده از ForexFactory ناموفق بود"
            root = ET.fromstring(r2.content)
            events = []
            for ev in root.findall("event"):
                events.append({
                    "title": ev.findtext("title",""),
                    "country": ev.findtext("country",""),
                    "date": ev.findtext("date",""),
                    "time": ev.findtext("time",""),
                    "impact": ev.findtext("impact",""),
                    "forecast": ev.findtext("forecast",""),
                    "previous": ev.findtext("previous",""),
                })

        # فیلتر: فقط رویدادهای high/medium impact — همه ارزها — امروز
        today_teh = datetime.now(TEHRAN).strftime("%Y-%m-%d")
        high_events = []
        for ev in events:
            impact = ev.get("impact","").lower()
            if impact not in ("high","medium","3","2"):
                continue
            ev_date = ev.get("date","")
            try:
                from datetime import datetime as dt
                parsed = dt.strptime(ev_date, "%Y-%m-%dT%H:%M:%S%z")
                ev_date_teh = parsed.astimezone(TEHRAN).strftime("%Y-%m-%d")
                if ev_date_teh != today_teh:
                    continue
                ev["time_teh"] = parsed.astimezone(TEHRAN).strftime("%H:%M")
            except:
                if today_teh not in ev_date:
                    continue
                ev["time_teh"] = ev.get("time","—")
            high_events.append(ev)

        # مرتب‌سازی بر اساس ساعت
        high_events.sort(key=lambda x: x.get("time_teh","99:99"))

        if not high_events:
            return [], "📭 امروز رویداد مهم فارکس نداریم."

        return high_events, None

    except Exception as e:
        return None, f"❌ خطا: {e}"


def format_ff_message(events):
    """پیام تلگرام رو فرمت می‌کنه"""
    today_str = datetime.now(TEHRAN).strftime("%A %d %B %Y")
    lines = [f"📅 <b>تقویم اقتصادی فارکس — امروز</b>\n{today_str}\n"]
    for ev in events:
        impact = ev.get("impact","").lower()
        star = "🔴" if impact in ("high","3") else "🟡"
        time_str = ev.get("time_teh") or ev.get("time","—")
        title = ev.get("title","—")
        forecast = ev.get("forecast","—") or "—"
        previous = ev.get("previous","—") or "—"
        country = ev.get("country","").upper()
        flag_map = {"USD":"🇺🇸","EUR":"🇪🇺","GBP":"🇬🇧","JPY":"🇯🇵","CAD":"🇨🇦",
                    "AUD":"🇦🇺","NZD":"🇳🇿","CHF":"🇨🇭","CNY":"🇨🇳","GER":"🇩🇪"}
        flag = flag_map.get(country, "🌐")
        lines.append(
            f"{star} {flag} <b>{title}</b>\n"
            f"   🕐 {time_str} (تهران)\n"
            f"   پیش‌بینی: <b>{forecast}</b>  |  قبلی: {previous}"
        )
    return "\n\n".join(lines)


def daily_news_scheduler():
    """هر روز سر ساعت NEWS_HOUR تهران اخبار می‌فرسته"""
    sent_today = None
    while True:
        try:
            now = datetime.now(TEHRAN)
            today = now.date()
            if (now.hour == FF_NEWS_HOUR and now.minute == FF_NEWS_MINUTE
                    and sent_today != today):
                token, cids, _ = _get_token_and_cids()
                if token and cids:
                    events, err = fetch_ff_news()
                    if err and not events:
                        msg = err
                    else:
                        msg = format_ff_message(events) if events else "📭 امروز رویداد مهم فارکس نداریم."
                    broadcast(token, cids, msg)
                    sent_today = today
                    print(f"[news] ارسال شد — {len(events or [])} رویداد")
        except Exception as e:
            print(f"[news_scheduler] {e}")
        time.sleep(50)

def _get_sender_name(msg):
    """اسم فرستنده رو از آبجکت message تلگرام میگیره"""
    u = msg.get("from", {})
    fn = u.get("first_name", "")
    ln = u.get("last_name", "")
    un = u.get("username", "")
    return (fn + " " + ln).strip() or ("@" + un if un else "ناشناس")

def poll_telegram():
    last_id = 0
    while True:
        try:
            token, _, _ = _get_token_and_cids()
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
                msg = upd.get("message", {})
                raw_txt = msg.get("text", "") or ""
                # normalize: /cmd@botname → /cmd
                txt = raw_txt.split("@")[0] if raw_txt.startswith("/") else raw_txt
                ch = msg.get("chat", {})
                cid = str(ch.get("id", ""))
                uname = ch.get("username", "") or ch.get("first_name", "")

                # ── /start ──────────────────────────────────────────
                if txt.startswith("/start") and cid:
                    data = load_alerts()
                    users = data.get("users", [])
                    if cid not in [str(u["chat_id"]) for u in users]:
                        users.append({"chat_id": cid, "username": uname, "joined_at": now_teh()})
                        data["users"] = users
                        ids = data.get("telegram", {}).get("chat_ids", [])
                        if cid not in [str(x) for x in ids]:
                            ids.append(cid)
                        data["telegram"]["chat_ids"] = ids
                        save_alerts(data)
                    send_tg(token, cid, f"👋 سلام <b>{uname}</b>!\n✅ در سیستم آلارم ثبت شدید. 🔔")

                # ── /sos ─────────────────────────────────────────────
                elif txt.startswith("/sos") and (cid == YOUR_CHAT_ID or BROADCAST_MODE):
                    parts = txt.split(maxsplit=3)
                    if len(parts) < 2:
                        send_tg(token, cid,
                            "⚠️ فرمت:\n<code>/sos SYMBOL [buy|sell] [کامنت]</code>\n"
                            "مثال:\n<code>/sos GBPUSD sell</code>")
                    else:
                        sym = parts[1].upper().replace("/", "")
                        raw_dir = parts[2].lower() if len(parts) > 2 else "sell"
                        comment = parts[3] if len(parts) > 3 else ""
                        condition = "above" if raw_dir in ("sell","s","سل","above") else "below"
                        atype = "forex" if any(x in sym for x in ["EUR","GBP","JPY","XAU","XAG","CHF","CAD","AUD","NZD"]) else "crypto"
                        sender_name = _get_sender_name(msg)
                        cur = None
                        try: cur = get_price(sym, atype)
                        except: pass
                        arrow = "📈 ناحیه سل" if condition == "above" else "📉 ناحیه بای"
                        cmt = f"\n💬 <i>{comment}</i>" if comment else ""
                        price_text = fmt_price(cur, sym) if cur else "—"
                        out_msg = (
                            f"🚨 <b>آلارم فوری!</b>\n\n"
                            f"💰 <b>{sym}</b> — {arrow}\n"
                            f"👤 ارسال‌کننده: <b>{sender_name}</b>\n\n"
                            f"📊 قیمت لحظه‌ای: <b>{price_text}</b>"
                            f"{cmt}\n\n⏰ {now_pretty()} (تهران)"
                        )
                        _, all_cids, _ = _get_token_and_cids()
                        targets = all_cids if BROADCAST_MODE else [YOUR_CHAT_ID]
                        for tc in targets:
                            send_tg(token, tc, out_msg)
                        d = load_alerts()
                        arch = d.get("archive", [])
                        arch.append({"id": str(int(time.time()*1000)), "symbol": sym, "type": atype,
                            "condition": condition, "comment": comment, "created_by": sender_name,
                            "active": False, "fired_at": now_teh(), "fired_price": cur,
                            "instant": True, "created_at": now_teh()})
                        d["archive"] = arch
                        save_alerts(d)
                        mode_txt = f"به {len(targets)} نفر" if BROADCAST_MODE else "فقط برای شما"
                        send_tg(token, cid, f"✅ آلارم فوری {sym} ارسال شد ({mode_txt})")

                # ── /alarm ───────────────────────────────────────────
                elif txt.startswith("/alarm") and (cid == YOUR_CHAT_ID or BROADCAST_MODE):
                    parts = txt.split(maxsplit=4)
                    if len(parts) < 4:
                        send_tg(token, cid,
                            "⚠️ فرمت:\n<code>/alarm SYMBOL buy|sell PRICE [کامنت]</code>\n\n"
                            "مثال‌ها:\n"
                            "<code>/alarm eurusd sell 1.12345 ناحیه سل</code>\n"
                            "<code>/alarm xauusd sell 2350 مقاومت مهم</code>")
                    else:
                        sym = parts[1].upper().replace("/", "")
                        raw_dir = parts[2].lower()
                        raw_price = parts[3]
                        comment = parts[4] if len(parts) > 4 else ""
                        condition = "above" if raw_dir in ("sell","s","سل","above") else "below"
                        atype = "forex" if any(x in sym for x in ["EUR","GBP","JPY","XAU","XAG","CHF","CAD","AUD","NZD"]) else "crypto"
                        sender_name = _get_sender_name(msg)
                        tgt_f = None
                        try:
                            tgt_f = float(raw_price)
                        except ValueError:
                            send_tg(token, cid, f"❌ قیمت نامعتبر: <code>{raw_price}</code>")
                        if tgt_f is not None:
                            # پیام در حال ثبت
                            send_tg(token, cid, f"⏳ <b>{sym}</b> در حال ثبت آلارم...")
                            cur = None
                            try: cur = get_price(sym, atype)
                            except: pass
                            d = load_alerts()
                            new_alert = {
                                "id": str(int(time.time()*1000)),
                                "symbol": sym, "type": atype,
                                "target_price": tgt_f, "condition": condition,
                                "comment": comment, "created_by": sender_name,
                                "active": True, "last_price": cur,
                                "last_checked": now_teh() if cur else None,
                                "created_at": now_teh(),
                                "notify_only": YOUR_CHAT_ID if not BROADCAST_MODE else None
                            }
                            d["alerts"].append(new_alert)
                            save_alerts(d)
                            arrow = "سل 📈" if condition == "above" else "بای 📉"
                            price_now = fmt_price(cur, sym) if cur else "—"
                            send_tg(token, cid,
                                f"✅ <b>آلارم ثبت شد</b>\n\n"
                                f"💰 <b>{sym}</b> — {arrow}\n"
                                f"🎯 هدف: <b>{fmt_price(tgt_f, sym)}</b>\n"
                                f"📊 قیمت الان: <b>{price_now}</b>"
                                + (f"\n💬 <i>{comment}</i>" if comment else "") +
                                f"\n\n⏰ {now_pretty()} (تهران)")

                # ── /news ────────────────────────────────────────────
                elif txt.startswith("/news") and cid == YOUR_CHAT_ID:
                    send_tg(token, cid, "⏳ در حال دریافت تقویم اقتصادی...")
                    events, err = fetch_ff_news()
                    if err and not events:
                        send_tg(token, cid, err)
                    else:
                        msg = format_ff_message(events) if events else "📭 امروز رویداد مهم فارکس نداریم."
                        send_tg(token, cid, msg)

        except Exception as e:
            print(f"[poll] {e}")
        time.sleep(5)

notified = set()
_loop_count = 0

def check_alerts():
    global _loop_count
    while True:
        try:
            _loop_count += 1
            global _cache_alerts
            _cache_alerts = None
            token, cids, data = _get_token_and_cids()
            active = [a for a in data.get("alerts", []) if a.get("active")]
            if not active:
                save_alerts(data)
                time.sleep(60)
                continue
            forex_open = is_forex_market_open()
            due_forex, due_crypto = [], []
            for a in active:
                sym = a["symbol"]
                atype = a.get("type", "crypto")
                if atype == "forex" and not forex_open:
                    continue
                if atype == "forex":
                    due_forex.append(sym)
                else:
                    due_crypto.append(sym)
            price_map = {}
            if due_forex:
                batch = get_forex_prices_batch(due_forex)
                for sym, p in batch.items():
                    price_map[(sym, "forex")] = p
            for sym in due_crypto:
                p = get_crypto_price(sym)
                price_map[(sym.upper(), "crypto")] = p
            fired = []
            for a in active:
                sym = a["symbol"]
                atype = a.get("type", "crypto")
                key = (sym.upper(), atype)
                if key not in price_map: continue
                cur = price_map[key]
                if cur is None: continue
                tgt = float(a["target_price"])
                cond = a.get("condition", "above")
                triggered = (cond == "above" and cur >= tgt) or (cond == "below" and cur <= tgt)
                if triggered and a["id"] not in notified:
                    notified.add(a["id"])
                    a["active"] = False
                    fired.append(a["id"])
                    if token and cids:
                        arrow = "📈 ناحیه سل" if cond == "above" else "📉 ناحیه بای"
                        creator = a.get("created_by") or "سیستم"
                        comment = a.get("comment", "")
                        cmt = f"\n💬 <i>{comment}</i>" if comment else ""
                        price_text = fmt_price(cur, sym) if cur else "—"
                        tgt_text = fmt_price(tgt, sym)
                        notify_cids = cids
                        if a.get("notify_only"):
                            notify_cids = [str(a["notify_only"])]
                        fired_msg = (
                            f"🚨 <b>آلارم قیمت!</b>\n\n"
                            f"💰 <b>{sym}</b> — {arrow}\n"
                            f"👤 ارسال‌کننده: <b>{creator}</b>\n\n"
                            f"🎯 هدف: <b>{tgt_text}</b>\n"
                            f"📊 قیمت لحظه‌ای: <b>{price_text}</b>"
                            f"{cmt}\n\n⏰ {now_pretty()} (تهران)"
                        )
                        broadcast(token, notify_cids, fired_msg)
            if fired:
                arch = data.get("archive", [])
                for fid in fired:
                    obj = next((x for x in data["alerts"] if x["id"] == fid), None)
                    if obj: arch.append(obj)
                data["archive"] = arch
                data["alerts"] = [x for x in data["alerts"] if x["id"] not in fired]
            save_alerts(data)
        except Exception as e:
            log_error(f"check_alerts: {e}")
        time.sleep(60)

def fmt_price(p, sym=""):
    if p is None: return "—"
    v = float(p)
    su = sym.upper()
    if "XAU" in su or "XAG" in su:
        return f"${v:.2f}"
    if "JPY" in su:
        return f"{v:.3f}"
    return f"{v:.5f}"

def tehran_to_utc(tehran_str):
    try:
        parts = tehran_str.strip().split(" ")
        dparts = parts[0].split("-")
        tparts = (parts[1] if len(parts) > 1 else "00:00").split(":")
        y, m, d = int(dparts[0]), int(dparts[1]), int(dparts[2])
        h, mi = int(tparts[0]), int(tparts[1])
        dt_teh = datetime(y, m, d, h, mi)
        return dt_teh - timedelta(hours=3, minutes=30)
    except: return None

# ====================================================================
# تابع اصلی بررسی کندل‌ها – اصلاح شده برای snapshot تا SL یا 3R
# ====================================================================
def check_sltp_hit_with_details(symbol, tf, entry_time_str, direction, entry_price, sl_price, tp_price, size=1.0, max_post_sl_pips=300, r3_override=None):
    """
    بازگشت: (hit, hit_price, last_close, pnl, mfe_pip, mae_pip, candle_lines, found_3r,
             free_risk_was_possible, free_risk_saved, reached_1r_at, pullback_after_1r,
             mfe_before_sl, passed_1r, snapshot_bars)
    * hit: 'sl' / 'tp' / 'tp3' (اولین رویداد)
    * snapshot_bars: کندل‌ها تا آخرین برخورد با SL یا 3R (حتی اگر TP زودتر خورده باشد)
    """
    try:
        # محاسبه limit بر اساس تایم‌فریم — پوشش کافی برای ۷ روز معامله
        tf_limits = {"1m":5000, "5m":3000, "15m":800, "1h":200, "4h":50, "1d":30}
        bar_limit = tf_limits.get(tf, 200)
        print(f"[CANDLE] {symbol} tf={tf} limit={bar_limit}")
        url = f"https://biquote.io/api/{symbol}/ohlc?interval={tf}&limit={bar_limit}"
        r = requests.get(url, timeout=12, headers=H)
        if r.status_code != 200:
            return (None, None, None, None, None, None, None, False, False, False, None, False, None, None, None, None, None, 0.0, False, [])
        data = r.json()
        bars = data.get("bars") or data.get("data") or (data if isinstance(data, list) else [])
        if not bars:
            return (None, None, None, None, None, None, None, False, False, False, None, False, None, None, None, None, None, 0.0, False, [])

        entry_utc = tehran_to_utc(entry_time_str)
        if not entry_utc:
            return (None, None, None, None, None, None, None, False, False, False, None, False, None, None, None, None, None, 0.0, False, [])

        all_bars_sorted = []
        for b in bars:
            ts = b.get("openTime") or b.get("time") or b.get("timestamp")
            if not ts: continue
            if isinstance(ts, (int, float)):
                bdt = datetime.utcfromtimestamp(ts)
            else:
                try:
                    bdt = datetime.strptime(ts.replace("Z",""), "%Y-%m-%dT%H:%M:%S")
                except: continue
            all_bars_sorted.append((bdt, b))
        all_bars_sorted.sort(key=lambda x: x[0])

        entry_bar_idx = 0
        for i, (bdt, b) in enumerate(all_bars_sorted):
            if bdt >= entry_utc:
                entry_bar_idx = i
                break

        after = all_bars_sorted[entry_bar_idx:]
        if not after:
            return (None, None, None, None, None, None, None, False, False, False, None, False, None, None, None, None, None, 0.0, False, [])

        is_buy = (direction == "BUY")
        hit = None
        hit_price = None
        hit_idx = None
        mul = get_pip_multiplier(symbol)
        mfe_pip = 0.0
        mae_pip = 0.0
        candle_lines = []
        found_3r = False
        risk_pips = None
        if sl_price and entry_price:
            # abs تضمین می‌کنه risk_pips همیشه مثبته حتی اگه کاربر SL اشتباه وارد کرده
            if is_buy:
                risk_pips = abs(entry_price - sl_price) * mul
            else:
                risk_pips = abs(sl_price - entry_price) * mul

        passed_1r = False
        reached_1r_at = None
        free_risk_was_possible = False
        free_risk_saved = False
        pullback_after_1r = False
        mae_stopped = False

        sl_hit_occurred = False
        mfe_before_sl = 0.0

        stop_price = None
        if sl_price and max_post_sl_pips > 0:
            if is_buy:
                stop_price = sl_price + (max_post_sl_pips / mul)
            else:
                stop_price = sl_price - (max_post_sl_pips / mul)

        # محاسبه قیمت 3R برای تعیین پایان snapshot
        _r3_price = r3_override if r3_override else (
            ((entry_price + 3*risk_pips/mul) if is_buy else (entry_price - 3*risk_pips/mul)) if risk_pips else None
        )

        # متغیرهای مربوط به snapshot: snap_end_idx تا جایی که SL یا 3R برخورد کند
        snap_end_idx = len(after) - 1  # پیش‌فرض آخرین کندل
        snap_resolved = False

        for i, (bar_dt_i, b) in enumerate(after):
            high = float(b.get("high", 0))
            low  = float(b.get("low",  0))
            close= float(b.get("close",0))
            open_= float(b.get("open", 0))

            if is_buy:
                profit_now = (high - entry_price) * mul
                if not mae_stopped and low < entry_price:
                    d = (entry_price - low) * mul
                    if d > mae_pip: mae_pip = d
            else:
                profit_now = (entry_price - low) * mul
                if not mae_stopped and high > entry_price:
                    d = (high - entry_price) * mul
                    if d > mae_pip: mae_pip = d

            mfe_pip = max(mfe_pip, profit_now)
            if risk_pips and mfe_pip > risk_pips * 3.0:
                mfe_pip = risk_pips * 3.0  # cap 3R

            if hit is None:
                mfe_before_sl = max(mfe_before_sl, profit_now)

            if risk_pips and not passed_1r and profit_now >= risk_pips:
                passed_1r = True
                reached_1r_at = i
                mae_stopped = True

            if passed_1r and reached_1r_at is not None and i > reached_1r_at:
                if not free_risk_was_possible:
                    free_risk_was_possible = True
                if not pullback_after_1r:
                    if is_buy and low <= entry_price:
                        pullback_after_1r = True
                    elif not is_buy and high >= entry_price:
                        pullback_after_1r = True

            dt_teh = bar_dt_i + timedelta(hours=3, minutes=30)
            thr = dt_teh.strftime("%m/%d %H:%M")
            dir_c = "▲" if close >= open_ else "▼"
            body_p = abs(close - open_) * mul
            candle_lines.append(f"{thr}: {dir_c} {body_p:.1f}pip | H:{high:.5f} L:{low:.5f} C:{close:.5f}")

            # ===== تعیین اولین رویداد (hit) برای بستن ترید =====
            if hit is None:
                if is_buy:
                    if sl_price is not None and low <= sl_price:
                        hit, hit_price, hit_idx = "sl", sl_price, i
                        sl_hit_occurred = True
                    elif tp_price is not None and high >= tp_price:
                        hit, hit_price, hit_idx = "tp", tp_price, i
                    elif _r3_price and high >= _r3_price:
                        hit, hit_price, hit_idx = "tp3", _r3_price, i
                        found_3r = True
                else:
                    if sl_price is not None and high >= sl_price:
                        hit, hit_price, hit_idx = "sl", sl_price, i
                        sl_hit_occurred = True
                    elif tp_price is not None and low <= tp_price:
                        hit, hit_price, hit_idx = "tp", tp_price, i
                    elif _r3_price and low <= _r3_price:
                        hit, hit_price, hit_idx = "tp3", _r3_price, i
                        found_3r = True

            # ===== تعیین پایان snapshot (فقط SL یا 3R) =====
            if not snap_resolved:
                if is_buy:
                    if sl_price is not None and low <= sl_price:
                        snap_end_idx = i
                        snap_resolved = True
                    elif _r3_price and high >= _r3_price:
                        snap_end_idx = i
                        snap_resolved = True
                else:
                    if sl_price is not None and high >= sl_price:
                        snap_end_idx = i
                        snap_resolved = True
                    elif _r3_price and low <= _r3_price:
                        snap_end_idx = i
                        snap_resolved = True
                if not snap_resolved:
                    snap_end_idx = i  # هنوز نرسیده، آپدیت کن

            # برگشت بعد SL — دستی توسط کاربر وارد میشه، محاسبه خودکار نداریم

            # stop بعد از SL (max_post_sl_pips)
            if sl_hit_occurred and stop_price is not None:
                if is_buy and high >= stop_price:
                    snap_end_idx = i
                    snap_resolved = True
                if not is_buy and low <= stop_price:
                    snap_end_idx = i
                    snap_resolved = True

            # اگه TP/3R یا SL خورد تموم
            if snap_resolved and hit is not None:
                break

        last_close = float(after[-1][1]["close"]) if not found_3r else (after[-1][1]["close"] if after else 0)
        pnl = None
        if hit:
            diff = (hit_price - entry_price) if is_buy else (entry_price - hit_price)
            pnl = diff * size

        # free_risk_saved = تصمیم کاربر است، خودکار set نمی‌شه
        # (کاربر در review_trade این را تأیید می‌کند)
        free_risk_saved = False

        # ساخت snapshot بر اساس snap_end_idx
        snap_start = max(0, entry_bar_idx - 20)
        snap_end = entry_bar_idx + snap_end_idx + 1
        snapshot_bars = []
        for bar_dt_snap, b_snap in all_bars_sorted[snap_start:snap_end]:
            dt_teh_snap = bar_dt_snap + timedelta(hours=3, minutes=30)
            snapshot_bars.append({
                "t": dt_teh_snap.strftime("%Y-%m-%d %H:%M"),
                "o": float(b_snap.get("open", 0)),
                "h": float(b_snap.get("high", 0)),
                "l": float(b_snap.get("low", 0)),
                "c": float(b_snap.get("close", 0)),
            })

        return (hit, hit_price, last_close, pnl, mfe_pip, mae_pip, candle_lines, found_3r,
                free_risk_was_possible, free_risk_saved, reached_1r_at, pullback_after_1r,
                None, None, None, None, None,
                mfe_before_sl, passed_1r, snapshot_bars)
    except Exception as e:
        log_error(f"check_sltp_hit_with_details: {e}")
        return (None, None, None, None, None, None, None, False, False, False, None, False, None, None, None, None, None, 0.0, False, [])

def groq_analyze(prompt):
    if not GROQ_API_KEY:
        return "⚠️ کلید API Groq تنظیم نشده است."
    try:
        client = Groq(api_key=GROQ_API_KEY)
        completion = client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.3, max_tokens=800
        )
        return completion.choices[0].message.content
    except Exception as e:
        log_error(f"Groq error: {e}")
        return f"❌ خطا: {str(e)}"

# ==================== Routes ====================
@app.route("/")
def index():
    return send_from_directory("static", "index.html")

@app.route("/journal")
def journal():
    return send_from_directory("static", "journal.html")

@app.route("/api/config", methods=["GET","POST"])
def config():
    data = load_alerts()
    if request.method == "POST":
        body = request.json or {}
        tg = data.get("telegram", {})
        if body.get("bot_token"):
            tg["bot_token"] = body["bot_token"]
        if body.get("chat_id"):
            cid = str(body["chat_id"])
            ids = [str(x) for x in tg.get("chat_ids", [])]
            if cid not in ids: ids.append(cid)
            tg["chat_ids"] = ids
            tg["chat_id"] = cid
        data["telegram"] = tg
        save_alerts(data)
        return jsonify({"ok": True})
    tg = data.get("telegram", {})
    return jsonify({
        "bot_token": tg.get("bot_token",""), "chat_id": tg.get("chat_id",""),
        "chat_ids": tg.get("chat_ids",[]), "user_count": len(data.get("users",[]))
    })

@app.route("/api/alerts", methods=["GET"])
def get_alerts():
    return jsonify(load_alerts().get("alerts", []))

@app.route("/api/alerts", methods=["POST"])
def add_alert():
    data = load_alerts()
    body = request.json or {}
    sym = body.get("symbol","").upper().strip()
    atype = body.get("type","forex")
    tgt = float(body.get("target_price", 0))
    cur = get_price(sym, atype) if (atype!="forex" or is_forex_market_open()) else None
    creator = body.get("creator", "").strip()
    a = {
        "id": str(int(time.time() * 1000)), "symbol": sym, "type": atype,
        "target_price": tgt, "condition": body.get("condition","above"),
        "comment": body.get("comment","").strip(), "active": True,
        "created_by": creator or "ناشناس",
        "notify_only": None,
        "last_price": cur, "last_checked": now_teh() if cur else None,
        "created_at": now_teh()
    }
    data["alerts"].append(a)
    save_alerts(data)
    return jsonify({"ok": True, "alert": a})

@app.route("/api/alerts/<aid>", methods=["DELETE"])
def del_alert(aid):
    data = load_alerts()
    data["alerts"] = [a for a in data.get("alerts", []) if a["id"] != aid]
    save_alerts(data)
    return jsonify({"ok": True})

@app.route("/api/archive", methods=["GET"])
def get_archive():
    return jsonify(load_alerts().get("archive", []))

@app.route("/api/archive", methods=["DELETE"])
def clear_archive():
    data = load_alerts()
    data["archive"] = []
    save_alerts(data)
    return jsonify({"ok": True})

@app.route("/api/archive/<aid>", methods=["DELETE"])
def del_archive(aid):
    data = load_alerts()
    data["archive"] = [a for a in data.get("archive",[]) if a["id"] != aid]
    save_alerts(data)
    return jsonify({"ok": True})

@app.route("/api/users", methods=["GET"])
def get_users():
    return jsonify(load_alerts().get("users", []))

@app.route("/api/users/<cid>", methods=["DELETE"])
def del_user(cid):
    data = load_alerts()
    data["users"] = [u for u in data.get("users",[]) if str(u["chat_id"]) != str(cid)]
    data["telegram"]["chat_ids"] = [x for x in data["telegram"].get("chat_ids",[]) if str(x) != str(cid)]
    save_alerts(data)
    return jsonify({"ok": True})

@app.route("/api/price/<atype>/<symbol>")
def live_price(atype, symbol):
    sym = symbol.upper().replace("-","/")
    p = get_price(sym, atype)
    if p is None:
        return jsonify({"error": "قیمت پیدا نشد"}), 404
    return jsonify({"symbol": sym, "price": p})

@app.route("/api/instant-alert", methods=["POST"])
def instant_alert():
    body = request.json or {}
    sym = body.get("symbol", "").upper().strip()
    if not sym:
        return jsonify({"ok": False, "error": "نماد وارد نشده"}), 400
    atype = body.get("type", "forex")
    condition = body.get("condition", "above")
    comment = body.get("comment", "").strip()
    creator = body.get("creator", "").strip()
    target_price = body.get("target_price")
    only_me = body.get("only_me", False)

    token, all_cids, data = _get_token_and_cids()
    if not token:
        return jsonify({"ok": False, "error": "توکن تلگرام تنظیم نشده"}), 400

    targets = [YOUR_CHAT_ID] if only_me else (all_cids if BROADCAST_MODE else [YOUR_CHAT_ID])
    if not targets:
        return jsonify({"ok": False, "error": "هیچ chat_id‌ای ثبت نشده"}), 400

    # قیمت لحظه‌ای
    cur = None
    try:
        cur = get_price(sym, atype)
    except:
        pass

    arrow = "📈 ناحیه سل" if condition == "above" else "📉 ناحیه بای"
    cmt = f"\n💬 <i>{comment}</i>" if comment else ""
    price_text = fmt_price(cur, sym) if cur else "—"
    tp_text = f"\n🎯 قیمت هدف: <b>{fmt_price(target_price, sym)}</b>" if target_price else ""
    alert_title = "آلارم قیمت" if target_price else "آلارم فوری"
    out_msg = (
        f"🚨 <b>{alert_title}!</b>\n\n"
        f"💰 <b>{sym}</b> — {arrow}\n"
        f"👤 ارسال‌کننده: <b>{creator or 'سیستم'}</b>\n\n"
        + (f"🎯 هدف: <b>{fmt_price(target_price, sym)}</b>\n" if target_price else "")
        + f"📊 قیمت لحظه‌ای: <b>{price_text}</b>"
        f"{cmt}\n\n⏰ {now_pretty()} (تهران)"
    )

    results = [send_tg(token, cid, out_msg) for cid in targets]
    sent_count = sum(results)

    # ذخیره در آرشیو
    try:
        d = load_alerts()
        d.setdefault("archive", []).append({
            "id": str(int(time.time() * 1000)),
            "symbol": sym, "type": atype,
            "condition": condition, "comment": comment,
            "created_by": creator, "active": False,
            "fired_at": now_teh(), "fired_price": cur,
            "target_price": target_price,
            "instant": True, "created_at": now_teh()
        })
        save_alerts(d)
    except Exception as e:
        log_error(f"instant_alert archive: {e}")

    print(f"[INSTANT] {sym} ارسال شد به {sent_count}/{len(targets)} نفر")
    return jsonify({"ok": True, "sent": sent_count, "total": len(targets)})


def test_tg():
    token, cids, _ = _get_token_and_cids()
    if not token or not cids:
        return jsonify({"ok": False, "error": "توکن یا chat_id ست نشده"})
    res = broadcast(token, cids, f"✅ تست موفق\n⏰ {now_pretty()}")
    return jsonify({"ok": any(res), "sent": sum(res), "total": len(cids)})

@app.route("/api/status")
def status():
    alerts = load_alerts()
    journal = load_journal()
    return jsonify({
        "status": "ok", "last_update": alerts.get("last_update"),
        "errors": alerts.get("errors", [])[-5:], "time_tehran": now_teh(),
        "alert_count": len(alerts.get("alerts",[])), "forex_open": is_forex_market_open(),
        "loop_count": _loop_count, "journal_count": len(journal)
    })

@app.route("/api/version")
def version():
    return jsonify({"version": VERSION})

@app.route("/health")
def health():
    return jsonify({"status": "ok"})

# ==================== ژورنال ====================
@app.route("/api/journal", methods=["GET"])
def get_journal():
    trades = load_journal()
    print(f"[GET /api/journal] {len(trades)} ترید برگشت")
    return jsonify(trades)

@app.route("/api/journal", methods=["POST"])
def add_journal():
    journal = load_journal()
    body = request.json or {}
    sym = body.get("sym", "").upper().strip()
    if not sym:
        return jsonify({"ok": False, "error": "sym الزامی است"}), 400
    entry = float(body.get("entry", 0))
    direction = body.get("direction", "BUY")
    size = 1.0
    is_missed_zone = bool(body.get("is_missed_zone", False))
    sl_pips = body.get("sl_pips")
    tp_pips = body.get("tp_pips")
    sl_price = body.get("sl_price")
    tp_price = body.get("tp_price")
    print(f"[AUTO] دریافت ترید — sym={sym} direction={direction} entry={entry} missed={is_missed_zone}")

    mul = get_pip_multiplier(sym)
    if sl_price is None and sl_pips is not None:
        sl_diff = sl_pips / mul
        sl_price = entry - sl_diff if direction == "BUY" else entry + sl_diff
    if tp_price is None and tp_pips is not None:
        tp_diff = tp_pips / mul
        tp_price = entry + tp_diff if direction == "BUY" else entry - tp_diff
    if sl_pips is None and sl_price is not None:
        sl_pips = abs(entry - sl_price) * mul
    if tp_pips is None and tp_price is not None:
        tp_pips = abs(tp_price - entry) * mul

    trade = {
        "id": str(int(time.time() * 1000)), "sym": sym, "tf": body.get("tf", "1h"),
        "direction": direction, "entry": entry, "size": size,
        "sl_pips": round(sl_pips, 1) if sl_pips else None,
        "tp_pips": round(tp_pips, 1) if tp_pips else None,
        "sl_price": sl_price, "tp_price": tp_price,
        "note": body.get("note", "").strip(), "entryTime": body.get("entryTime", now_teh()),
        "createdAt": now_teh(), "status": "open", "exit": None, "exitTime": None,
        "candle_snapshot": [], "pending_check": True,
        "exitNote": None, "pnl": None, "outcome": None,
        "ai_analysis": None, "ai_summary": None,
        "review_mfe": None, "review_mae": None, "review_pullback": None, "review_note": None,
        "review_reversal_occurred": None, "review_reversal_from_sl": None, "review_reversal_target_pips": None,
        "found_3r": False, "mae_pip": 0, "mfe_pip": 0,
        "free_risk_was_possible": False, "free_risk_saved": False, "pullback_after_1r": False,

        "mfe_before_sl_pip": 0, "passed_1r": False,
        "is_missed_zone": is_missed_zone
    }
    print(f"[AUTO] ترید ساخته شد — id={trade['id']} missed={is_missed_zone}")
    try:
        # یک بار فراخوانی که snapshot تا SL/3R ادامه پیدا می‌کند و hit اولین رویداد است
        res = check_sltp_hit_with_details(sym, trade["tf"], trade["entryTime"], direction, entry, sl_price, tp_price, size, r3_override=None)
        (hit, hit_price, last_close, pnl, mfe_pip, mae_pip, candle_lines, found_3r,
         fr_possible, fr_saved, fr_at, pullback, post_max, post_1r, post_1_5r, post_2r, post_3r,
         mfe_before_sl, passed_1r, snapshot_bars) = res

        if hit == "sl":
            # استاپ خورد → بسته شو
            trade["exit"] = hit_price
            trade["exitTime"] = now_teh()
            trade["exitNote"] = "خودکار: استاپ لاس در زمان ثبت"
            trade["pnl"] = round(pnl, 2) if pnl is not None else 0
            trade["outcome"] = "loss"
            trade["exit_type"] = "sl"
            trade["status"] = "closed"
            trade["pending_check"] = False
            trade["mfe_pip"] = round(mfe_pip, 1)
            trade["mae_pip"] = round(mae_pip, 1)
            trade["found_3r"] = False
            trade["free_risk_was_possible"] = fr_possible
            trade["free_risk_saved"] = fr_saved
            trade["pullback_after_1r"] = pullback
            trade["mfe_before_sl_pip"] = round(mfe_before_sl, 1) if mfe_before_sl else 0
            trade["passed_1r"] = passed_1r
            trade["candle_snapshot"] = snapshot_bars
            print(f"[AUTO] ✅ استاپ خورد — outcome=loss missed={is_missed_zone}")
        elif hit in ("tp", "tp3"):
            # TP یا 3R خورد → ثبت برد ولی watching برای چارت کامل تا SL/3R
            trade["exit"] = hit_price
            trade["exitTime"] = now_teh()
            trade["pnl"] = round(pnl, 2) if pnl is not None else 0
            trade["outcome"] = "win"
            trade["exit_type"] = hit
            trade["mfe_pip"] = round(mfe_pip, 1)
            trade["mae_pip"] = round(mae_pip, 1)
            trade["found_3r"] = (hit == "tp3")
            trade["free_risk_was_possible"] = fr_possible
            trade["free_risk_saved"] = fr_saved
            trade["pullback_after_1r"] = pullback
            trade["mfe_before_sl_pip"] = round(mfe_before_sl, 1) if mfe_before_sl else 0
            trade["passed_1r"] = passed_1r
            trade["candle_snapshot"] = snapshot_bars
            if hit == "tp3":
                # 3R کامل → مستقیم بسته شو
                trade["exitNote"] = "خودکار: 3R کامل در زمان ثبت"
                trade["status"] = "closed"
                trade["pending_check"] = False
                print(f"[AUTO] ✅ 3R کامل — outcome=tp3 missed={is_missed_zone}")
            else:
                # TP خورد → برد ثبت، watching برای snapshot تا SL/3R
                trade["exitNote"] = "خودکار: تارگت در زمان ثبت"
                trade["status"] = "watching"
                trade["pending_check"] = True
                trade["last_poll"] = now_teh()
                print(f"[AUTO] ✅ TP خورد — outcome=win, watching برای SL/3R missed={is_missed_zone}")
        else:
            # هیچ چیز نخورده → در جریان
            trade["status"] = "open"
            trade["pending_check"] = True
            trade["candle_snapshot"] = snapshot_bars
            trade["last_poll"] = now_teh()
    except Exception as e:
        log_error(f"auto check error: {e}")
        return jsonify({"ok": False, "error": f"خطا: {str(e)}"}), 500
    journal.insert(0, trade)
    print(f"[AUTO] ✅ ترید {trade['id']} ذخیره شد — sym={sym} missed={is_missed_zone}")
    save_journal(journal)
    return jsonify({"ok": True, "trade": trade})

def calc_exit_type(outcome, risk_pips, mfe_pip, found_3r=False, exit_type_stored=None):
    if exit_type_stored in ("sl", "tp", "tp3"):
        return exit_type_stored
    if outcome == "loss":
        return "sl"
    if found_3r:
        return "tp3"
    if risk_pips and risk_pips > 0 and mfe_pip:
        mfe_r = mfe_pip / risk_pips
        if mfe_r >= 3.0:
            return "tp3"
    return "tp"

@app.route("/api/journal/manual", methods=["POST"])
def add_journal_manual():
    journal = load_journal()
    body = request.json or {}
    sym = body.get("sym", "").upper().strip()
    if not sym:
        return jsonify({"ok": False, "error": "sym الزامی است"}), 400
    entry = float(body.get("entry", 0))
    direction = body.get("direction", "BUY")
    size = 1.0
    tf = body.get("tf", "1h")
    entryTime = body.get("entryTime", now_teh())
    note = body.get("note", "").strip()
    sl_price = body.get("sl_price")
    tp_price = body.get("tp_price")
    exit_price = body.get("exit")
    outcome = body.get("outcome")
    exitTime = body.get("exitTime", now_teh())
    exitNote = body.get("exitNote", "ثبت دستی")
    mul = get_pip_multiplier(sym)
    sl_pips = abs(entry - sl_price) * mul if sl_price else None
    tp_pips = abs(tp_price - entry) * mul if tp_price else None
    pnl = None
    # اگه exit وارد نشده، از tp_price (win) یا sl_price (loss) fallback بگیر
    exit_for_pnl = exit_price or (tp_price if outcome == "win" else (sl_price if outcome == "loss" else None))
    if exit_for_pnl and entry:
        diff = (float(exit_for_pnl) - entry) if direction == "BUY" else (entry - float(exit_for_pnl))
        pnl = diff * size
    # exit_price هم اگه خالیه ولی outcome داریم، پر کن
    if not exit_price and exit_for_pnl:
        exit_price = exit_for_pnl
    review_mfe = body.get("review_mfe")
    review_mae = body.get("review_mae")
    review_pullback = body.get("review_pullback", False)
    review_note = body.get("review_note", "")
    review_reversal_occurred = body.get("review_reversal_occurred", False)
    review_reversal_from_sl = body.get("review_reversal_from_sl")
    review_reversal_target_pips = body.get("review_reversal_target_pips")
    review_free_risk_saved = body.get("review_free_risk_saved", False)
    is_missed_zone = bool(body.get("is_missed_zone", False))
    print(f"[MANUAL] sym={body.get('sym')} outcome={body.get('outcome')} missed={is_missed_zone}")
    is_crypto = is_crypto_symbol(sym)
    risk_pips = None
    if sl_price and entry:
        if direction == "BUY":
            risk_pips = (entry - sl_price) * mul
        else:
            risk_pips = (sl_price - entry) * mul
    if is_crypto:
        mfe_r = body.get("review_mfe_r")
        mae_r = body.get("review_mae_r")
        reversal_target_r = body.get("review_reversal_target_pips_r")
        if mfe_r is not None and risk_pips and risk_pips > 0:
            review_mfe = mfe_r * risk_pips
        if mae_r is not None and risk_pips and risk_pips > 0:
            review_mae = mae_r * risk_pips
        if reversal_target_r is not None and risk_pips and risk_pips > 0:
            review_reversal_target_pips = reversal_target_r * risk_pips
    trade = {
        "id": str(int(time.time() * 1000)), "sym": sym, "tf": tf,
        "direction": direction, "entry": entry, "size": size,
        "sl_pips": round(sl_pips, 1) if sl_pips else None,
        "tp_pips": round(tp_pips, 1) if tp_pips else None,
        "sl_price": sl_price, "tp_price": tp_price,
        "note": note, "entryTime": entryTime, "createdAt": now_teh(),
        "status": "closed", "exit": exit_price, "exitTime": exitTime, "exitNote": exitNote,
        "pnl": round(pnl, 2) if pnl is not None else 0, "outcome": outcome,
        "ai_analysis": None, "ai_summary": None,
        "review_mfe": review_mfe, "review_mae": review_mae,
        "review_pullback": review_pullback, "review_note": review_note,
        "review_reversal_occurred": review_reversal_occurred,
        "review_reversal_from_sl": review_reversal_from_sl,
        "review_reversal_target_pips": review_reversal_target_pips,
        "review_free_risk_saved": review_free_risk_saved,
        "found_3r": False, "mae_pip": review_mae if review_mae else 0,
        "mfe_pip": review_mfe if review_mfe else 0,
        "free_risk_was_possible": False, "free_risk_saved": review_free_risk_saved,
        "pullback_after_1r": review_pullback,

        "mfe_before_sl_pip": 0, "passed_1r": False, "candle_snapshot": [],
        "is_missed_zone": is_missed_zone
    }
    mfe_for_calc = float(review_mfe) if review_mfe else 0
    trade["exit_type"] = calc_exit_type(outcome, risk_pips, mfe_for_calc)
    if trade.get("outcome") and not trade.get("candle_snapshot"):
        try:
            # برای ترید دستی هم snapshot در صورت امکان بگیریم (اختیاری)
            r3_guess = None
            if risk_pips and risk_pips > 0:
                if direction == "BUY":
                    r3_guess = entry + 3 * risk_pips / mul
                else:
                    r3_guess = entry - 3 * risk_pips / mul
            res_snap = check_sltp_hit_with_details(
                sym, tf, entryTime, direction, entry, sl_price,
                tp_price if tp_price else r3_guess,
                size, r3_override=r3_guess
            )
            trade["candle_snapshot"] = res_snap[19] if len(res_snap) > 19 else []
        except Exception as e:
            log_error(f"manual snapshot: {e}")
            trade["candle_snapshot"] = []
    journal.insert(0, trade)
    save_journal(journal)
    return jsonify({"ok": True, "trade": trade})

@app.route("/api/journal/<tid>/edit", methods=["PUT"])
def edit_trade(tid):
    journal = load_journal()
    trade = next((t for t in journal if t["id"] == tid), None)
    if not trade:
        return jsonify({"ok": False, "error": "ترید یافت نشد"}), 404
    body = request.json or {}
    for key in ["sym","direction","entry","exit","sl_price","tp_price","entryTime","exitTime","pnl","outcome","note","exitNote"]:
        if key in body:
            trade[key] = body[key] if key in ["note","exitNote","outcome","direction","sym"] else float(body[key]) if body[key] is not None else None
    for key in ["review_mfe","review_mae","review_pullback","review_note","review_reversal_occurred","review_reversal_from_sl","review_reversal_target_pips","review_free_risk_saved"]:
        if key in body:
            trade[key] = body[key]
    mul = get_pip_multiplier(trade["sym"])
    if trade.get("sl_price") and trade.get("entry"):
        trade["sl_pips"] = abs(trade["entry"] - trade["sl_price"]) * mul
    if trade.get("tp_price") and trade.get("entry"):
        trade["tp_pips"] = abs(trade["tp_price"] - trade["entry"]) * mul
    if trade.get("exit") and trade.get("entry"):
        diff = (trade["exit"] - trade["entry"]) if trade["direction"] == "BUY" else (trade["entry"] - trade["exit"])
        trade["pnl"] = diff * 1.0
    save_journal(journal)
    return jsonify({"ok": True, "trade": trade})

@app.route("/api/journal/<tid>/delete", methods=["DELETE"])
def delete_trade(tid):
    print(f"[DELETE] درخواست حذف ترید: {tid}")
    journal = load_journal()
    before = len(journal)
    journal = [t for t in journal if str(t.get("id","")) != str(tid)]
    after = len(journal)
    if after == before:
        print(f"[DELETE] ❌ ترید {tid} یافت نشد — IDs موجود: {[str(t.get('id')) for t in journal[:5]]}")
        return jsonify({"ok": False, "error": f"ترید {tid} یافت نشد"}), 404
    global _cache_journal
    _cache_journal = journal
    save_journal(journal)
    print(f"[DELETE] ✅ ترید {tid} حذف شد — باقیمانده: {after}")
    return jsonify({"ok": True})

@app.route("/api/journal/<tid>/review", methods=["POST"])
def review_trade(tid):
    journal = load_journal()
    trade = next((t for t in journal if t["id"] == tid), None)
    if not trade:
        return jsonify({"ok": False, "error": "ترید یافت نشد"}), 404
    body = request.json or {}
    sym = trade.get("sym", "")
    mul = get_pip_multiplier(sym)
    entry = float(trade.get("entry", 0))
    sl_px = trade.get("sl_price")
    risk_pips = abs(entry - float(sl_px)) * mul if sl_px and entry else None
    if "review_mfe" in body:
        mfe_val = body["review_mfe"]
        if mfe_val is not None and is_crypto_symbol(sym) and risk_pips and risk_pips > 0:
            mfe_raw = round(float(mfe_val) * risk_pips, 4)
        else:
            mfe_raw = mfe_val
        # cap روی 3R — بیشتر از 3R در محاسبات تفاوتی نمیکنه
        if mfe_raw is not None and risk_pips and risk_pips > 0:
            cap_3r = risk_pips * 3.0
            if float(mfe_raw) > cap_3r:
                print(f"[REVIEW] MFE cap: {mfe_raw} → {cap_3r} (3R)")
                mfe_raw = round(cap_3r, 4)
        trade["review_mfe"] = mfe_raw
    # ---- بازمحاسبه passed_1r از روی review_mfe دستی ----
    if "review_mfe" in body and risk_pips and risk_pips > 0:
        mfe_corrected = trade.get("review_mfe")
        if mfe_corrected is not None:
            mfe_f = float(mfe_corrected)
            if mfe_f < risk_pips * 0.98:
                old_p1r = trade.get("passed_1r")
                o                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                