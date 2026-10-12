# استقرار دائمی ۲۴ ساعته - Render (رایگان)

این ربات با Polling کار می‌کند و برای ۲۴ ساعته نیاز به یک سرور کوچک دارد. Render بهترین گزینه رایگان است.

## روش ۱: Render (پیشنهادی - ۵ دقیقه)

### ۱. آپلود به GitHub
1. در github.com یک Repo جدید بساز (مثلاً `naerkhyab-bot`) - Public
2. تمام فایل‌های این پوشه را آپلود کن (Drag & Drop):
   - `main.py`, `requirements.txt`, `render.yaml`, `Dockerfile`, `.gitignore`
   - ⚠️ هرگز `.env` را آپلود نکن! (در .gitignore است)
3. Commit

### ۲. ساخت سرویس در Render
1. برو به https://dashboard.render.com → New → Background Worker
2. GitHub را وصل کن و Repo خودت را انتخاب کن
3. تنظیمات:
   - **Name:** `naerkhyab-bot`
   - **Region:** Singapore (نزدیک‌ترین به افغانستان)
   - **Branch:** main
   - **Build Command:** `pip install -r requirements.txt`
   - **Start Command:** `python main.py`
   - **Plan:** Free
4. در بخش **Environment** → Add:
   ```
   BOT_TOKEN = 8857130353:AAEmVKoPJRX70zQ-y9syj0UIoIp2nmXSzCc
   ADMIN_ID = 8531976889
   TARGET_CHAT_ID = -5536690458
   ```
5. Create → صبر کن تا `Live` شود (لاگ‌ها را ببین)

### ۳. تست
- به `@naerkhyabbot` در تلگرام یک نرخ بفرست → باید مثل قبل پیش‌نمایش بیاید.

---

## روش ۲: Railway / Fly.io (جایگزین)
- Railway: مشابه Render، فقط `Start Command` را بگذار
- Fly: `fly launch` → `fly deploy`

## روش ۳: سرور شخصی / VPS
```bash
git clone <repo>
cd naerkhyab-bot
pip install -r requirements.txt
cp .env.example .env
nano .env # پر کن
nohup python3 -u main.py > bot.log 2>&1 &
```

## نکته مهم برای Render Free
پلن Free بعد از 15 دقیقه بی‌کاری Sleep می‌شود، اما چون ربات Polling است، همیشه فعال می‌ماند. اگر Sleep شد، از https://uptimerobot.com هر 5 دقیقه به URL سرویس‌ات ping بزن تا بیدار بماند (برای Worker لازم نیست، فقط Web Service).

## هزینه
- خود ربات: **صفر**
- Render Free: **750 ساعت/ماه رایگان** (≈ 31 روز کامل)
- اگر از حد گذشت: 7$ ماهانه

برای سوال، به همین چت پیام بده.
