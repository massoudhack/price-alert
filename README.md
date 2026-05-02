# 🔔 آلارم قیمت — راهنمای نصب روی Render

## ساختار فایل‌ها
```
price-alert-server/
├── app.py              ← سرور اصلی Python
├── requirements.txt    ← کتابخانه‌ها
├── render.yaml         ← تنظیمات Render
└── static/
    └── index.html      ← پنل وب
```

---

## مرحله ۱: آپلود روی GitHub

1. برو روی [github.com](https://github.com) و اکانت بساز (اگه نداری)
2. یه repo جدید بساز به اسم `price-alert`
3. همه فایل‌ها رو آپلود کن (drag & drop توی GitHub هم کار می‌کنه)
   - مطمئن شو که `static/index.html` داخل پوشه `static` باشه

---

## مرحله ۲: Deploy روی Render

1. برو روی [render.com](https://render.com) و با GitHub وصل شو
2. روی **New → Web Service** کلیک کن
3. repo ی `price-alert` رو انتخاب کن
4. تنظیمات:
   - **Name**: price-alert-bot
   - **Runtime**: Python 3
   - **Build Command**: `pip install -r requirements.txt`
   - **Start Command**: `gunicorn app:app --bind 0.0.0.0:$PORT --workers 1 --threads 4`
   - **Plan**: Free
5. روی **Create Web Service** کلیک کن
6. چند دقیقه صبر کن تا deploy بشه ✅

---

## مرحله ۳: ساخت ربات تلگرام

1. به [@BotFather](https://t.me/BotFather) پیام بده
2. بنویس `/newbot`
3. یه اسم و username بده
4. **توکن** رو که بهت میده کپی کن

5. برای گرفتن Chat ID:
   - به [@userinfobot](https://t.me/userinfobot) پیام بده
   - عدد `Id` رو کپی کن

---

## مرحله ۴: تنظیم پنل

1. آدرس سایتت رو از Render بگیر (مثل: `https://price-alert-bot.onrender.com`)
2. توکن ربات و Chat ID رو وارد کن
3. روی **ذخیره** و بعد **تست پیام** کلیک کن

---

## نمادهای پشتیبانی‌شده

### کریپتو (Binance)
- BTC, ETH, BNB, SOL, XRP, DOGE, ADA, DOT, ...
- هر ارزی که در Binance باشه

### فارکس (ExchangeRate API)
- EURUSD, GBPUSD, USDJPY, XAUUSD (طلا), ...
- فرمت: ۳ حرف ارز اول + ۳ حرف ارز دوم

---

## ⚠️ نکته مهم درباره Render Free

سرور رایگان Render بعد از ۱۵ دقیقه بدون درخواست **sleep** میشه.  
برای جلوگیری از این مشکل، می‌تونی از [UptimeRobot](https://uptimerobot.com) استفاده کنی:
1. اکانت رایگان بساز
2. یه **HTTP Monitor** بساز با آدرس: `https://your-app.onrender.com/health`
3. interval رو روی ۱۴ دقیقه تنظیم کن

اینطوری سرور **۲۴/۷ بیدار** می‌مونه!
