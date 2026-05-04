# آلارم قیمت — راهنمای نصب Koyeb

## ساختار فایل‌ها
```
price-alert-server/
├── app.py
├── requirements.txt
├── Procfile
└── static/
    └── index.html
```

## Koyeb (بدون کارت بانکی)

1. روی koyeb.com با GitHub لاگین کن
2. New App → GitHub → repo خودت رو انتخاب کن
3. تنظیمات:
   - Builder: Buildpack
   - Run: `gunicorn app:app --bind 0.0.0.0:$PORT --workers 1 --threads 4`
   - Plan: Free
4. متغیر محیطی اضافه کن:
   - Key: `BOT_TOKEN`
   - Value: توکن ربات تلگرامت
5. Deploy!

## متغیر محیطی BOT_TOKEN

در Koyeb → App → Settings → Environment Variables:
```
BOT_TOKEN = 123456789:AAF-xxxxxxxxxxxxxx
```

## ساخت ربات تلگرام

1. @BotFather → /newbot → توکن بگیر
2. @userinfobot → Chat ID خودت رو بگیر
3. توی پنل → تلگرام → Chat ID وارد کن

## نمادها

**کریپتو:** BTC, ETH, SOL, BNB, XRP, DOGE, ADA, ...
**فارکس:** EURUSD, GBPUSD, USDJPY, XAUUSD (طلا), XAGUSD (نقره), ...

## ویژگی‌ها

- آلارم قیمت: هر ۵ دقیقه چک
- آلارم کندل: هر ۱ دقیقه چک، پیام در زمان کلوز کندل
- منابع قیمت: Binance → Bybit → OKX → KuCoin → CoinGecko → CryptoCompare
- پشتیبانی از چندین کاربر تلگرام
- ساعت تهران در همه پیام‌ها
- نمایش خطاها در پنل
