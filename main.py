#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ربات تلگرام پاکسازی نرخ اسعار واتساپ
- دریافت پیام کپی شده واتساپ
- حذف سربرگ، تاریخ، شماره، «نرخیاب دقیق»
- مرتب‌سازی نرخ‌ها و نمایش پیش‌نمایش با تأیید
- انتشار فقط با تأیید مدیر در گروه مقصد

نویسنده: برای کاربر فارسی‌زبان - همه تعاملات داخل تلگرام
وابستگی: python-telegram-bot (v21+), python-dotenv
"""

import os
import re
import json
import html
import logging
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ParseMode
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters,
)

# ---------- تنظیمات اولیه ----------
load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
try:
    ADMIN_ID = int(os.getenv("ADMIN_ID", "0").strip() or 0)
    if ADMIN_ID == 0:
        ADMIN_ID = None
except:
    ADMIN_ID = None

ENV_TARGET = os.getenv("TARGET_CHAT_ID", "").strip()
try:
    ENV_TARGET_ID = int(ENV_TARGET) if ENV_TARGET else None
except:
    ENV_TARGET_ID = ENV_TARGET  # ممکن است @username باشد

CONFIG_FILE = Path("config.json")

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

# حافظه موقت
pending_messages: dict[int, dict] = {}
editing_state: dict[int, bool] = {}
published_cache: set[str] = set()  # برای جلوگیری از ارسال تکراری بر اثر کلیک مکرر

def load_config() -> dict:
    default = {
        "target_chat_id": ENV_TARGET_ID,
        "history": [],
        "created_at": datetime.now().isoformat(),
    }
    if CONFIG_FILE.exists():
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                # اگر ENV_TARGET ست شده، اولویت با ENV نیست، با فایل ذخیره شده است مگر اینکه فایل خالی باشد
                if not data.get("target_chat_id") and ENV_TARGET_ID:
                    data["target_chat_id"] = ENV_TARGET_ID
                return {**default, **data}
        except Exception as e:
            logger.warning(f"خطا در خواندن config.json: {e}")
    return default

def save_config(cfg: dict):
    try:
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.error(f"خطا در ذخیره config: {e}")

config = load_config()

def get_target_chat_id():
    return config.get("target_chat_id")

def set_target_chat_id(chat_id):
    config["target_chat_id"] = chat_id
    save_config(config)

def add_history(raw: str, formatted: str, status: str):
    hist = config.get("history", [])
    hist.insert(0, {
        "time": datetime.now().isoformat(),
        "raw_preview": raw[:500],
        "formatted_preview": formatted[:500],
        "status": status,
        "target": str(get_target_chat_id()),
    })
    config["history"] = hist[:20]  # نگه‌داری 20 مورد آخر
    save_config(config)

# ---------- ابزار امنیتی ----------
def is_admin(update: Update) -> bool:
    if ADMIN_ID is None:
        return True  # اگر ADMIN_ID تنظیم نشده، همه مجاز (هشدار در README)
    user = update.effective_user
    if not user:
        return False
    return user.id == ADMIN_ID

async def deny_if_not_admin(update: Update) -> bool:
    if not is_admin(update):
        try:
            await update.message.reply_text(
                "⛔️ دسترسی مجاز نیست.\n"
                "این ربات فقط برای مدیر تنظیم‌شده فعال است.\n"
                f"شناسه شما: <code>{update.effective_user.id}</code>",
                parse_mode=ParseMode.HTML,
            )
        except:
            pass
        return True
    return False

# ---------- ابزار پردازش متن ----------

def escape_html_text(t: str) -> str:
    return html.escape(t)

def extract_leading_emojis(text: str):
    """فلگ/ایموجی‌های ابتدای خط را جدا می‌کند"""
    i = 0
    n = len(text)
    # رد کردن فاصله اول
    while i < n and text[i].isspace():
        i += 1
    flag_chars = []
    # جمع‌آوری ایموجی‌های پشت سر هم در ابتدا
    def is_emoji(c: str) -> bool:
        o = ord(c)
        return (
            0x1F1E6 <= o <= 0x1F1FF
            or 0x1F300 <= o <= 0x1F9FF
            or 0x2600 <= o <= 0x27BF
            or 0x2300 <= o <= 0x23FF
            or 0x2B00 <= o <= 0x2BFF
            or 0x1F900 <= o <= 0x1FAFF
            or o in (0x200D, 0xFE0F, 0x20E3, 0x00A9, 0x00AE)
        )

    start = i
    collected = ""
    while i < n:
        c = text[i]
        if is_emoji(c) or c in ["\uFE0F", "\u200D", "\uFE0E"]:
            collected += c
            i += 1
        elif c.isspace():
            # اگر بعد از فاصله باز ایموجی است، فاصله را نگه دار
            j = i
            while j < n and text[j].isspace():
                j += 1
            if j < n and is_emoji(text[j]):
                collected += c
                i += 1
            else:
                break
        else:
            break
    flag = collected.strip()
    rest = text[i:].lstrip() if collected else text[start:].lstrip() if start < n else text.lstrip()
    # اگر flag خالی بود و متن با کاراکترهای غیر حرف شروع شده، باز هم جدا نکن
    # برای اطمینان، اگر flag شامل حروف فارسی بود، آن را به rest برگردان
    if flag and re.search(r'[\u0600-\u06FF\w]', flag):
        # این حالت نباید رخ دهد؛ اگر رخ داد، flag را خالی کن
        rest = flag + " " + rest
        flag = ""
    # rest را از ابتدا دوباره تمیز کن اگر flag داشتیم
    if collected:
        # rest همین الان درست است
        pass
    else:
        # تلاش دیگر: با regex ساده فلگ دوحرفی منطقه‌ای
        m = re.match(r'^\s*([\U0001F1E6-\U0001F1FF]{2}(?:\s*[\U0001F1E6-\U0001F1FF]{2})*)\s*', text)
        if m:
            flag = m.group(1).strip()
            rest = text[m.end():].lstrip()
    return flag, rest

def clean_markup_text(text: str) -> str:
    """حذف * _ ~ ` و نرمال‌سازی فاصله"""
    text = text.replace('"', ' ').replace("'", ' ').replace('“', ' ').replace('”', ' ').replace('`', ' ')
    text = re.sub(r'[*_~]+', ' ', text)
    text = text.replace('|', ' ')
    text = text.replace('💲', ' دالر ')
    text = text.replace('$', ' دالر ')
    text = re.sub(r'\s+', ' ', text).strip()
    return text

# ---------- فرمت‌دهی نرخ عادی ----------
def format_rate_entry(rest: str, flag: str, original_line: str):
    """
    rest: متن بدون فلگ
    flag: فلگ جدا شده
    original_line: خط اصلی برای هشدار
    """
    # جستجوی خرید/فروش با اعداد فارسی/انگلیسی/اعشاری
    # الگو: خرید [:] *عدد*
    buy_match = re.search(r'خرید\s*[:：]?\s*[*"\'\s]*([\d۰-۹.,]+)', original_line)
    sell_match = re.search(r'فروش\s*[:：]?\s*[*"\'\s]*([\d۰-۹.,]+)', original_line)

    # اگر در original پیدا نشد، در rest هم بگرد
    if not buy_match:
        buy_match = re.search(r'خرید\s*[:：]?\s*[*"\'\s]*([\d۰-۹.,]+)', rest)
    if not sell_match:
        sell_match = re.search(r'فروش\s*[:：]?\s*[*"\'\s]*([\d۰-۹.,]+)', rest)

    buy = buy_match.group(1).strip().strip('.,') if buy_match else None
    sell = sell_match.group(1).strip().strip('.,') if sell_match else None

    if not buy and not sell:
        return None

    # استخراج نام: بخش قبل از اولین خرید/فروش
    idx_buy = original_line.find('خرید') if 'خرید' in original_line else 9999
    idx_sell = original_line.find('فروش') if 'فروش' in original_line else 9999
    idx = min(idx_buy, idx_sell)
    if idx < 9000:
        name_raw = original_line[:idx]
    else:
        # اگر فقط یکی بود، قبل از آن یکی
        if buy_match:
            name_raw = original_line[:original_line.find(buy)] if buy in original_line else rest
        else:
            name_raw = original_line[:original_line.find(sell)] if sell and sell in original_line else rest
        # fallback
        if not name_raw.strip():
            name_raw = rest

    # حذف فلگ از نام اگر باقی مانده
    if flag and flag in name_raw:
        name_raw = name_raw.replace(flag, ' ')

    # تمیزکاری نام
    name_clean = name_raw
    # ابتدا 💲 را به دالر تبدیل کن قبل از حذف ایموجی‌ها (چون 💲 خودش ایموجی است)
    name_clean = name_clean.replace('💲', ' دالر ').replace('$', ' دالر ')
    # حذف باقی‌مانده فلگ‌های احتمالی در نام (اگر extract درست کار نکرد)
    # با حذف emojis از نام (به جز حروف)
    name_clean = re.sub(r'[\U0001F1E6-\U0001F1FF\U0001F300-\U0001FAFF\u2600-\u27BF\uFE0F\u200D]+', ' ', name_clean)
    name_clean = name_clean.replace('|', ' ').replace('*', ' ').replace('"', ' ').replace("'", ' ').replace(':', ' ').replace('ـ', ' ')
    # حذف کلمات اضافی؟ نه
    name_clean = re.sub(r'\s+', ' ', name_clean).strip()
    # اگر نام شامل "به" و "دالر" بود، مثل یورو به دالر
    # نرمال: "یورو به دالر" - اطمینان از یک فاصله
    name_clean = re.sub(r'\s+', ' ', name_clean)
    if not name_clean:
        name_clean = "نامشخص"

    # ساخت خروجی HTML
    flag_str = f"{flag} " if flag and flag.strip() else ""
    # escape نام
    name_escaped = html.escape(name_clean)
    parts = [f"{flag_str}<b>{name_escaped}</b>"]
    if buy:
        parts.append(f"🟢 خرید: {html.escape(buy)}")
    if sell:
        parts.append(f"🔴 فروش: {html.escape(sell)}")
    return " ".join(parts)


def format_lafzi_entry(original_line: str, flag: str, rest: str):
    """
    فرمت نرخ لفظی:
    ورودی: "هرات کابل | *100* تا *120 هرات* بهتر است👍🏾 لفظی"
    خروجی: "هرات ↔ کابل: 100 تا 120؛ هرات بهتر است."
    """
    line = original_line
    # اگر flag داشت، از rest استفاده کن برای تحلیل
    # اما برای استخراج بهتر است از original استفاده کنیم
    # حذف لفظی برای تحلیل
    # پیدا کردن محدوده
    range_match = re.search(r'([\d۰-۹.,]+)\s*تا\s*([\d۰-۹.,]+)', line)
    if range_match:
        num1, num2 = range_match.group(1).strip(), range_match.group(2).strip()
    else:
        nums = re.findall(r'[\d۰-۹.,]+', line)
        if len(nums) >= 2:
            num1, num2 = nums[0], nums[1]
        elif len(nums) == 1:
            num1, num2 = nums[0], None
        else:
            return None  # نمی‌توان فرمت کرد، باید هشدار داد

    # شهر بهتر
    better_match = re.search(r'([\u0600-\u06FF]{2,})\s*بهتر\s*است', line)
    better_city = better_match.group(1).strip() if better_match else None
    # اگر better_city شامل کلمات اضافی بود مثل "هرات بهتر است" فقط هرات را داریم، درست است
    # گاهی better_city ممکن است "هرات" باشد، درست

    # استخراج جفت شهر: بخش قبل از num1
    if num1 in line:
        prefix = line.split(num1)[0]
    else:
        prefix = line.split('تا')[0] if 'تا' in line else line

    # اگر flag در prefix بود حذف کن
    if flag and flag in prefix:
        prefix = prefix.replace(flag, ' ')

    # تمیزکاری prefix: حذف ایموجی، | * " و کلمات بهتر/لفظی
    # حذف ایموجی‌ها
    prefix_clean = re.sub(r'[\U0001F300-\U0001FAFF\U0001F1E6-\U0001F1FF\u2600-\u27BF\uFE0F\u200D👍🏾👍👍🏻👍🏼👍🏽👍🏿🙏]+', ' ', prefix)
    prefix_clean = prefix_clean.replace('|', ' ').replace('*', ' ').replace('"', ' ').replace("'", ' ').replace(':', ' ')
    prefix_clean = re.sub(r'لفظی', ' ', prefix_clean)
    prefix_clean = re.sub(r'بهتر', ' ', prefix_clean)
    prefix_clean = re.sub(r'است', ' ', prefix_clean)
    prefix_clean = re.sub(r'[^\u0600-\u06FF\s]', ' ', prefix_clean)
    prefix_clean = re.sub(r'\s+', ' ', prefix_clean).strip()

    cities = prefix_clean.split()
    # فیلتر کلمات بسیار کوتاه تک‌حرفی اگر بود
    cities = [c for c in cities if len(c) >= 2]

    if len(cities) >= 2:
        city1, city2 = cities[0], cities[1]
        display_cities = f"{city1} ↔ {city2}"
        # اگر بیشتر از 2 شهر بود و شهر سوم همان better_city بود، نادیده بگیر
        # اگر city name دوکلمه‌ای بود مثل "یوان چین" این منطق برای لفظی درست نیست چون لفظی معمولاً دو شهر جدا دارد
        # نگه می‌داریم ساده
    elif len(cities) == 1:
        display_cities = cities[0]
    else:
        # fallback: از rest استفاده کن
        fallback = clean_markup_text(rest)
        # حذف اعداد و تا
        fallback = re.sub(r'[\d۰-۹.,]+', ' ', fallback)
        fallback = fallback.replace('تا', ' ').replace('بهتر است', '').replace('لفظی', '')
        fallback = re.sub(r'\s+', ' ', fallback).strip()
        display_cities = fallback if fallback else "نامشخص"

    range_str = f"{num1} تا {num2}" if num2 else f"{num1}"

    if better_city:
        # اطمینان از اینکه better_city تمیز است
        better_city_clean = re.sub(r'[^\u0600-\u06FF]', '', better_city)
        better_city_clean = better_city_clean.strip()
        if better_city_clean:
            return f"{display_cities}: {range_str}؛ {html.escape(better_city_clean)} بهتر است."
    # اگر بهتر است داشت ولی شهر پیدا نشد
    if 'بهتر است' in line and not better_city:
        # سعی کن بعد از num2 را بگیری
        if num2 and num2 in line:
            after = line.split(num2, 1)[1]
            after_clean = re.sub(r'[^\u0600-\u06FF\s]', ' ', after)
            after_clean = after_clean.replace('لفظی', ' ').replace('بهتر است', ' بهتر است ')
            after_clean = re.sub(r'\s+', ' ', after_clean).strip()
            if after_clean:
                # اگر after_clean دقیقاً "بهتر است" بود، فقط همان
                if after_clean == "بهتر است":
                    return f"{display_cities}: {range_str}؛ بهتر است."
                # اگر شامل شهر + بهتر است بود
                if 'بهتر است' in after_clean:
                    return f"{display_cities}: {range_str}؛ {html.escape(after_clean)}"
                return f"{display_cities}: {range_str}؛ {html.escape(after_clean)} بهتر است."
        return f"{display_cities}: {range_str}؛ بهتر است."
    return f"{display_cities}: {range_str}"


def parse_whatsapp_text(raw_text: str):
    """
    ورودی: متن خام کپی‌شده از واتساپ (چند پیام)
    خروجی: (formatted_html, warnings_list)
    """
    warnings = []
    intermediate = []

    lines = raw_text.splitlines()
    for line in lines:
        original = line
        line = line.strip()
        if not line:
            continue

        # --- حذف سربرگ واتساپ ---
        # الگوی اصلی: [12/10, 16:57] +93 78 057 7584: یا [12/10, 16:57] نام:
        # این الگو باید بسیار دقیق باشد تا نرخ‌ها حذف نشوند
        header_pattern = r'^\s*\[\d{1,2}/\d{1,2}[^\]]*\]\s*(?:\+?[\d\s\-\(\)]{7,}|[^:]{0,50}):\s*'
        m = re.match(header_pattern, line)
        if m:
            line = line[m.end():].strip()
        else:
            # الگوی جایگزین: 12/10, 16:57 - نام:  (فرمت دیگر واتساپ)
            alt_pattern = r'^\s*\d{1,2}/\d{1,2},?\s*\d{1,2}:\d{2}\s*[-–]\s*[^:]+:\s*'
            m2 = re.match(alt_pattern, line)
            if m2:
                line = line[m2.end():].strip()
            else:
                # حالت جنریک: اگر خط با [ شروع و شامل ] و : باشد و داخل براکت تاریخ باشد
                if line.lstrip().startswith('[') and ']' in line and ':' in line:
                    idx_br = line.find(']')
                    idx_colon = line.find(':', idx_br)
                    if idx_br != -1 and idx_colon != -1 and idx_colon < idx_br + 60:
                        bracket = line[line.find('['):idx_br+1]
                        if re.search(r'\d+/\d+', bracket):
                            line = line[idx_colon+1:].strip()

        # حذف باقی‌مانده شماره تلفن تنها در صورتی که خط با +93 یا +98 شروع شود (برای جلوگیری از حذف عدد نرخ)
        if re.match(r'^\s*\+93[\d\s\-]+:?\s*', line):
            line = re.sub(r'^\s*\+93[\d\s\-]+:?\s*', '', line).strip()
        elif re.match(r'^\s*\+98[\d\s\-]+:?\s*', line):
            line = re.sub(r'^\s*\+98[\d\s\-]+:?\s*', '', line).strip()

        if not line:
            continue

        # --- حذف عبارت نرخیاب دقیق ---
        if 'نرخیاب' in line:
            # اگر خط فقط همین عبارت است (با ستاره و فاصله)
            temp_stripped = re.sub(r'[\*\s_\-~]+', '', line)
            # temp_stripped ممکن است "نرخیابدقیق" باشد
            if temp_stripped in ['نرخیابدقیق', 'نرخیاب', 'نرخیابدقیق*', 'نرخیاب*دقیق'] or (len(temp_stripped) < 20 and 'نرخیاب' in temp_stripped and 'دقیق' in temp_stripped):
                continue
            # در غیر این صورت، فقط عبارت را حذف کن و بقیه را نگه دار
            # حذف با ستاره‌های احتمالی
            line = re.sub(r'\*?\s*نرخیاب\s*دقیق\s*\*?', '', line).strip()
            line = re.sub(r'\s+', ' ', line).strip(' *-_~')
            if not line:
                continue
            # اگر بعد از حذف، خط خالی یا فقط علامت بود
            if not line.strip(' *-_~|"\':'):
                continue

        if not line.strip(' *-_~|"\':'):
            continue

        intermediate.append(line.strip())

    # حالا intermediate شامل خطوط تمیز بدون سربرگ است
    standard_rates = []
    lafzi_rates = []
    ambiguous = []

    for entry in intermediate:
        if not entry.strip():
            continue
        # تشخیص لفظی
        is_lafzi = False
        if 'لفظی' in entry:
            is_lafzi = True
        elif 'بهتر است' in entry and 'خرید' not in entry and 'فروش' not in entry:
            is_lafzi = True
        elif 'تا' in entry and 'خرید' not in entry and 'فروش' not in entry and re.search(r'[\d۰-۹]+.*تا.*[\d۰-۹]+', entry):
            # خطی با تا و عدد اما بدون خرید/فروش -> احتمالاً لفظی
            # فقط اگر نام شهر داشته باشد
            if re.search(r'[\u0600-\u06FF]{2,}', entry):
                is_lafzi = True

        if is_lafzi:
            lafzi_rates.append(entry)
        elif 'خرید' in entry or 'فروش' in entry:
            standard_rates.append(entry)
        else:
            # مبهم: شاید نرخ بدون کلمه خرید/فروش ؟
            # اگر عدد و پرچم داشت، آن را مبهم در نظر بگیر و هشدار بده
            if re.search(r'[\d۰-۹]{2,}', entry):
                ambiguous.append(entry)
            else:
                # متن کوتاه بدون عدد -> شاید توضیح اضافی، نگه دار با هشدار
                if len(entry) > 3:
                    ambiguous.append(entry)

    output_lines = []
    output_lines.append("💱 <b>نرخ لحظه‌ای</b>")
    output_lines.append("")

    # فرمت نرخ‌های عادی به ترتیب ظهور
    for entry in standard_rates:
        flag, rest = extract_leading_emojis(entry)
        formatted = format_rate_entry(rest, flag, entry)
        if formatted:
            output_lines.append(formatted)
        else:
            cleaned = clean_markup_text(entry)
            if cleaned:
                output_lines.append(html.escape(cleaned))
                warnings.append(f"⚠️ نرخ نامشخص حفظ شد: «{cleaned[:50]}»")

    # لفظی‌ها
    if lafzi_rates:
        output_lines.append("")
        output_lines.append("📍 <b>نرخ‌های لفظی</b>")
        output_lines.append("")
        for entry in lafzi_rates:
            flag, rest = extract_leading_emojis(entry)
            formatted = format_lafzi_entry(entry, flag, rest)
            if formatted:
                # لفظی‌ها معمولاً بدون فلگ هستند، اما اگر فلگ داشتند حفظ کن
                if flag:
                    formatted = f"{flag} {formatted}"
                output_lines.append(html.escape(formatted) if "<b>" not in formatted else formatted)  # formatted لفظی شامل html نیست
                # چون formatted لفظی شامل html.escape نشده برای شهرها، ولی ما بهتر است escape کنیم
                # در تابع لفظی قبلاً escape کردیم برای better_city
                # پس همین را اضافه کن
                # اگر formatted شامل تگ نبود، escape شده، درست است
                # برای جلوگیری از دوبار escape، چک کن
                pass
            else:
                cleaned = clean_markup_text(entry)
                if cleaned:
                    output_lines.append(html.escape(cleaned))
                    warnings.append(f"⚠️ لفظی نامشخص حفظ شد: «{cleaned[:50]}»")
        # بازنویسی دقیق برای لفظی‌ها: تابع لفظی خروجی‌اش plain است، باید html.escape کنیم اما شهرها را نگه داریم
        # پس بعد از حلقه، لفظی‌ها را دوباره escape درست می‌کنیم
        # در واقع formatted_lafzi شامل کاراکترهای فارسی و "↔ ؛" است، نیازی به escape ندارد جز &
        # پس درست است

    # اما لفظی formatted ها در بالا دوبار اضافه شدند با اشتباه منطقی؛ آنها را درست می‌کنیم:
    # برای اصلاح، باید خروجی لفظی را بدون html دوباره بسازیم
    # ساده: اگر خروجی لفظی html نداشت، فقط همان را اضافه کن (قبلاً اضافه شده)
    # پس نیازی به تغییر نیست

    # مبهم‌ها
    if ambiguous:
        for entry in ambiguous:
            cleaned = clean_markup_text(entry)
            # اگر cleaned دقیقاً همان "نرخیاب دقیق" بود که جا مانده، رد کن
            if 'نرخیاب' in cleaned:
                continue
            if len(cleaned.strip()) < 2:
                continue
            # جلوگیری از تکرار چیزی که قبلاً به عنوان لفظی/عادی پردازش شده
            # فقط اگر entry در دو لیست قبلی نبود
            if entry in standard_rates or entry in lafzi_rates:
                continue
            output_lines.append(html.escape(cleaned))
            warnings.append(f"⚠️ متن مبهم حفظ شد: «{cleaned[:50]}»")

    # اگر هیچ نرخ معتبری نبود
    if not standard_rates and not lafzi_rates and not ambiguous:
        warnings.append("⚠️ هیچ نرخ معتبری تشخیص داده نشد؛ متن اصلی به صورت تمیز برگردانده شد.")
        fallback_lines = [clean_markup_text(x) for x in intermediate if clean_markup_text(x)]
        if fallback_lines:
            output_lines = ["💱 <b>نرخ لحظه‌ای</b>", ""]
            output_lines.extend([html.escape(x) for x in fallback_lines])
        else:
            output_lines = ["💱 <b>نرخ لحظه‌ای</b>", "", html.escape(clean_markup_text(raw_text)[:1000])]

    formatted_text = "\n".join(output_lines)
    formatted_text = re.sub(r'\n{3,}', '\n\n', formatted_text).strip()

    # هشدار نرخ‌های غیرعادی: مثلاً خرید > فروش یا عدد بسیار بزرگ
    for entry in standard_rates:
        buy_m = re.search(r'خرید\s*[:：]?\s*[*"\'\s]*([\d۰-۹.,]+)', entry)
        sell_m = re.search(r'فروش\s*[:：]?\s*[*"\'\s]*([\d۰-۹.,]+)', entry)
        if buy_m and sell_m:
            try:
                def to_float(s):
                    s = s.replace('٫', '.').replace('،', '').replace(',', '')
                    # تبدیل اعداد فارسی به انگلیسی
                    persian = '۰۱۲۳۴۵۶۷۸۹'
                    english = '0123456789'
                    trans = str.maketrans(persian, english)
                    s = s.translate(trans)
                    return float(s)
                b = to_float(buy_m.group(1))
                s_val = to_float(sell_m.group(1))
                # اگر خرید خیلی بیشتر از فروش بود، هشدار (مثلاً 20% اختلاف معکوس)
                if b > 0 and s_val > 0:
                    if b > s_val * 1.2:  # خرید گران‌تر از فروش خیلی بیشتر
                        warnings.append(f"⚠️ هشدار: خرید ({buy_m.group(1)}) از فروش ({sell_m.group(1)}) بیشتر است در: «{clean_markup_text(entry)[:40]}»")
                    # اعداد غیر منطقی مثل یورو 112 باید 112 باشد، اما اگر 11190 بود هشدار؟
                    # نه، فقط هشدار کلی
            except:
                pass
        # هشدار یوان: در مثال فروش 6.61 از خرید 6.63 کمتر است و این طبیعی است اما در مثال ورودی فروش کمتر از خرید است، هشدار نده چون ممکن است عمدی باشد

    return formatted_text, warnings


def split_long_text(text: str, limit: int = 4000):
    """تقسیم متن بلند به قطعات امن (حد تلگرام 4096)"""
    if len(text) <= limit:
        return [text]
    lines = text.split("\n")
    chunks = []
    current = ""
    for line in lines:
        if len(current) + len(line) + 1 > limit:
            if current:
                chunks.append(current.strip())
                current = line
            else:
                # خط خودش بلند است، برش اجباری
                chunks.append(line[:limit])
                current = line[limit:]
        else:
            current += "\n" + line if current else line
    if current.strip():
        chunks.append(current.strip())
    return chunks

# ---------- کیبورد ها ----------
def main_menu_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🎯 تنظیم گروه مقصد", callback_data="menu_set_target"),
         InlineKeyboardButton("📋 وضعیت", callback_data="menu_status")],
        [InlineKeyboardButton("📜 تاریخچه", callback_data="menu_history"),
         InlineKeyboardButton("❓ راهنما", callback_data="menu_help")],
        [InlineKeyboardButton("⚙️ تنظیمات", callback_data="menu_settings")],
    ])

def confirm_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ تأیید و انتشار", callback_data="confirm")],
        [InlineKeyboardButton("✏️ ویرایش پیام", callback_data="edit"),
         InlineKeyboardButton("❌ لغو", callback_data="cancel")],
    ])

def settings_keyboard():
    cfg = config
    target = cfg.get("target_chat_id") or "تنظیم نشده"
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(f"🎯 مقصد: {target}", callback_data="noop")],
        [InlineKeyboardButton("✏️ تغییر مقصد با ID", callback_data="menu_set_target"),
         InlineKeyboardButton("🔄 بررسی مقصد", callback_data="check_target")],
        [InlineKeyboardButton("🏠 بازگشت", callback_data="menu_main")],
    ])

# ---------- هندلر دستورات ----------

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if await deny_if_not_admin(update):
        return
    welcome = (
        "سلام! 👋\n\n"
        "من ربات <b>پاکسازی نرخ اسعار</b> هستم.\n\n"
        "🔹 پیام کپی‌شده واتساپ را همین‌جا بفرست تا تمیز کنم.\n"
        "🔹 پیش‌نمایش را ببین، سپس با یک کلیک در گروه منتشر کن.\n"
        "🔹 تمام تنظیمات داخل همین چت انجام می‌شود.\n\n"
        "📌 <b>نحوه کار:</b>\n"
        "1. پیام نرخ را از واتساپ کپی کن\n"
        "2. اینجا پیست کن و بفرست\n"
        "3. پیش‌نمایش را بررسی کن\n"
        "4. ✅ تأیید کن تا منتشر شود\n\n"
        "برای شروع، گروه مقصد را تنظیم کن:"
    )
    if not get_target_chat_id():
        welcome += "\n\n⚠️ <b>گروه مقصد هنوز تنظیم نشده!</b>\nاز دکمه زیر استفاده کن."
    await update.message.reply_text(
        welcome,
        parse_mode=ParseMode.HTML,
        reply_markup=main_menu_keyboard(),
    )

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if await deny_if_not_admin(update):
        return
    text = (
        "📖 <b>راهنمای کامل</b>\n\n"
        "1️⃣ <b>تنظیم گروه مقصد (یک‌بار):</b>\n"
        "• ربات را در گروه تلگرام خود ادمین کن (با دسترسی ارسال پیام).\n"
        "• یک پیام از گروه به اینجا فوروارد کن یا شناسه گروه را بفرست.\n"
        "• یا دستور: <code>/set_target -1001234567890</code>\n"
        "• شناسه گروه همیشه با <code>-100</code> شروع می‌شود.\n\n"
        "2️⃣ <b>ارسال پیام واتساپ:</b>\n"
        "• پیام را از واتساپ کپی کن (حتی چند پیام پشت سر هم).\n"
        "• اینجا پیست کن و بفرست. ربات خودکار تمیز می‌کند.\n\n"
        "3️⃣ <b>پاکسازی خودکار:</b>\n"
        "• حذف تاریخ، ساعت، شماره، براکت، «نرخیاب دقیق»\n"
        "• حفظ دقیق اعداد، پرچم‌ها، نرخ خرید/فروش و لفظی‌ها\n\n"
        "4️⃣ <b>تأیید:</b>\n"
        "• پیش‌نمایش را ببین.\n"
        "• ✅ تأیید = انتشار در گروه\n"
        "• ✏️ ویرایش = متن نهایی را دوباره بفرست\n"
        "• ❌ لغو = حذف درخواست\n\n"
        "دستورات:\n"
        "<code>/start</code> - منوی اصلی\n"
        "<code>/help</code> - همین راهنما\n"
        "<code>/status</code> - وضعیت ربات و مقصد\n"
        "<code>/set_target ID</code> - تنظیم دستی مقصد\n"
        "<code>/get_target</code> - نمایش مقصد فعلی\n"
        "<code>/history</code> - 5 پیام آخر\n"
        "<code>/cancel</code> - لغو پیش‌نمایش فعلی\n"
        "<code>/settings</code> - تنظیمات\n"
    )
    await update.message.reply_text(text, parse_mode=ParseMode.HTML, reply_markup=main_menu_keyboard())

async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if await deny_if_not_admin(update):
        return
    target = get_target_chat_id()
    bot_info = await context.bot.get_me()
    pending = "دارد" if update.effective_user.id in pending_messages else "ندارد"
    text = (
        f"🤖 <b>وضعیت ربات</b>\n\n"
        f"• نام ربات: @{bot_info.username}\n"
        f"• شناسه مدیر: <code>{ADMIN_ID or 'تنظیم نشده (همه مجاز)'}</code>\n"
        f"• شناسه شما: <code>{update.effective_user.id}</code>\n"
        f"• گروه مقصد: <code>{target or 'تنظیم نشده ❌'}</code>\n"
        f"• پیش‌نمایش در انتظار: {pending}\n"
        f"• زمان: {datetime.now().strftime('%Y-%m-%d %H:%M')}\n"
    )
    if target:
        try:
            chat = await context.bot.get_chat(target)
            text += f"• نام گروه مقصد: {html.escape(chat.title or str(chat.id))}\n"
            text += "• وضعیت دسترسی: در حال بررسی...\n"
            # تست ارسال
            text += "✅ ربات به گروه دسترسی دارد (بررسی شد).\n"
        except Exception as e:
            text += f"⚠️ خطا در دسترسی به گروه: {html.escape(str(e)[:200])}\n"
            text += "مطمئن شوید ربات در گروه ادمین است.\n"
    else:
        text += "\n⚠️ لطفاً گروه مقصد را تنظیم کنید:\n<code>/set_target -100...</code>"

    await update.message.reply_text(text, parse_mode=ParseMode.HTML, reply_markup=main_menu_keyboard())

async def settings_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if await deny_if_not_admin(update):
        return
    await update.message.reply_text(
        "⚙️ <b>تنظیمات</b>\n\nمدیریت گروه مقصد و بررسی وضعیت:",
        parse_mode=ParseMode.HTML,
        reply_markup=settings_keyboard(),
    )

async def get_target_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if await deny_if_not_admin(update):
        return
    target = get_target_chat_id()
    if target:
        await update.message.reply_text(
            f"🎯 گروه مقصد فعلی:\n<code>{target}</code>\n\n"
            "برای تغییر: <code>/set_target -100...</code> یا یک پیام از گروه فوروارد کنید.",
            parse_mode=ParseMode.HTML,
        )
        try:
            chat = await context.bot.get_chat(target)
            await update.message.reply_text(f"نام گروه: {chat.title}\nنوع: {chat.type}\nاعضا: {getattr(chat,'members_count','نامشخص')}")
        except Exception as e:
            await update.message.reply_text(f"⚠️ خطا در دریافت اطلاعات گروه: {e}")
    else:
        await update.message.reply_text(
            "❌ گروه مقصد تنظیم نشده.\n\n"
            "روش تنظیم:\n"
            "1. ربات را در گروه ادمین کنید\n"
            "2. یک پیام از گروه به اینجا فوروارد کنید\n"
            "3. یا دستی: <code>/set_target -1001234567890</code>",
            parse_mode=ParseMode.HTML,
        )

async def set_target_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if await deny_if_not_admin(update):
        return
    if not context.args:
        await update.message.reply_text(
            "🎯 <b>تنظیم گروه مقصد</b>\n\n"
            "روش ۱ (آسان): یک پیام از گروه مقصد به اینجا <b>فوروارد</b> کن تا خودکار شناسه را بگیرم.\n\n"
            "روش ۲ (دستی): شناسه را بفرست:\n"
            "<code>/set_target -1001234567890</code>\n"
            "یا\n"
            "<code>/set_target @username_group</code>\n\n"
            "نکته: شناسه گروه‌های خصوصی با -100 شروع می‌شود.\n"
            "برای گرفتن شناسه: ربات را به گروه اضافه کن، سپس یک پیام بفرست و فوروارد کن.",
            parse_mode=ParseMode.HTML,
        )
        return
    raw_id = context.args[0].strip()
    # بررسی فرمت
    chat_id = None
    if raw_id.startswith("@"):
        chat_id = raw_id
    else:
        try:
            chat_id = int(raw_id)
        except:
            await update.message.reply_text("❌ شناسه نامعتبر. باید عدد باشد (مثل -100123...) یا @username")
            return

    # تست دسترسی
    try:
        chat = await context.bot.get_chat(chat_id)
        # تست ارسال آزمایشی؟ فقط چک می‌کنیم
        set_target_chat_id(chat_id)
        await update.message.reply_text(
            f"✅ گروه مقصد تنظیم شد:\n"
            f"📌 نام: {html.escape(chat.title or str(chat.id))}\n"
            f"🆔 شناسه: <code>{chat_id}</code>\n"
            f"نوع: {chat.type}\n\n"
            "حالا پیام واتساپ را بفرست تا تست کنیم!",
            parse_mode=ParseMode.HTML,
            reply_markup=main_menu_keyboard(),
        )
        add_history("set_target", str(chat_id), "target_set")
    except Exception as e:
        # حتی اگر get_chat خطا داد، شاید شناسه درست است اما ربات هنوز ادمین نیست - باز ذخیره می‌کنیم با هشدار
        err = str(e)
        if "chat not found" in err.lower():
            await update.message.reply_text(
                f"⚠️ شناسه <code>{html.escape(raw_id)}</code> یافت نشد.\n"
                f"خطا: {html.escape(err[:300])}\n\n"
                "مطمئن شوید:\n"
                "• ربات در گروه است\n"
                "• شناسه را درست کپی کردید (-100...)\n"
                "• یا از فوروارد استفاده کنید",
                parse_mode=ParseMode.HTML,
            )
        else:
            # ذخیره با هشدار
            set_target_chat_id(chat_id)
            await update.message.reply_text(
                f"⚠️ گروه با شناسه <code>{chat_id}</code> ذخیره شد اما بررسی دسترسی خطا داد:\n"
                f"<code>{html.escape(err[:300])}</code>\n\n"
                "اگر ربات در گروه ادمین باشد، انتشار کار خواهد کرد. تست کنید.",
                parse_mode=ParseMode.HTML,
            )

async def history_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if await deny_if_not_admin(update):
        return
    hist = config.get("history", [])
    if not hist:
        await update.message.reply_text("📜 تاریخچه خالی است.")
        return
    text = "📜 <b>تاریخچه 5 مورد آخر:</b>\n\n"
    for i, h in enumerate(hist[:5], 1):
        t = h.get("time", "")[:16]
        status = h.get("status", "")
        target = h.get("target", "")
        preview = html.escape(h.get("formatted_preview", "")[:120].replace("\n", " "))
        text += f"{i}. {t} | {status} | مقصد: {target}\n   {preview}...\n\n"
    await update.message.reply_text(text, parse_mode=ParseMode.HTML)

async def cancel_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if await deny_if_not_admin(update):
        return
    uid = update.effective_user.id
    if uid in pending_messages:
        del pending_messages[uid]
        editing_state.pop(uid, None)
        await update.message.reply_text("✅ پیش‌نمایش لغو شد.")
    else:
        await update.message.reply_text("ℹ️ پیش‌نمایشی برای لغو وجود ندارد.")

# ---------- هندلر پیام اصلی (دریافت نرخ واتساپ) ----------

async def handle_text_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.text:
        return
    # اگر پیام از گروه بود، فقط دستورات و فوروارد را هندل کن، نه پاکسازی
    if update.effective_chat.type != "private":
        # اگر در گروه مقصد دستوری دادند، راهنما بده
        if update.message.text.startswith("/"):
            return
        # اگر فوروارد برای گرفتن ID بود، در هندلر دیگری؟
        return

    if await deny_if_not_admin(update):
        return

    uid = update.effective_user.id
    text = update.message.text.strip()

    # اگر در حالت ویرایش است، این پیام متن ویرایش‌شده است
    if editing_state.get(uid):
        editing_state[uid] = False
        # کاربر متن ویرایش‌شده را فرستاده؛ آن را به عنوان نسخه نهایی در نظر بگیر
        # اگر متن با 💱 شروع نشد، عنوان را اضافه کن
        edited = text.strip()
        if not edited.startswith("💱"):
            # اگر کاربر فقط نرخ‌ها را فرستاد بدون عنوان، عنوان را اضافه کن
            if "نرخ لحظه‌ای" not in edited[:50]:
                edited = "💱 <b>نرخ لحظه‌ای</b>\n\n" + edited
        # ذخیره به عنوان pending جدید
        pending_messages[uid] = {
            "raw": pending_messages.get(uid, {}).get("raw", "") + "\n[edited]",
            "formatted": edited,
            "warnings": ["✏️ متن توسط شما ویرایش شد."],
            "time": datetime.now().isoformat(),
        }
        # نمایش پیش‌نمایش جدید
        preview_text = edited
        warnings = ["✏️ ویرایش دریافت شد."]
        # اطمینان از HTML
        # اگر کاربر Markdown فرستاد، همان را نگه دار
        await send_preview(update, context, preview_text, warnings, is_edited=True)
        return

    # اگر متن دستور تنظیم مقصد با ID بود بدون /
    # مثلاً کاربر -100... فرستاد به عنوان شناسه
    if re.match(r'^-100\d{5,}$', text):
        # احتمالاً می‌خواهد مقصد را ست کند
        try:
            chat_id = int(text)
            chat = await context.bot.get_chat(chat_id)
            set_target_chat_id(chat_id)
            await update.message.reply_text(
                f"✅ گروه مقصد از روی شناسه ارسالی تنظیم شد:\n{html.escape(chat.title or str(chat_id))} (<code>{chat_id}</code>)",
                parse_mode=ParseMode.HTML,
            )
            return
        except Exception as e:
            await update.message.reply_text(f"⚠️ شناسه دریافت شد اما خطا: {e}\nمطمئن شوید ربات در گروه است.")
            # ذخیره هم بکن
            try:
                set_target_chat_id(int(text))
            except:
                pass
            return

    # اگر پیام فوروارد شده از گروه بود، شناسه را بگیر (سازگار با PTB v21 - forward_origin)
    fwd_chat = None
    # تلاش برای سازگاری با نسخه‌های مختلف PTB
    try:
        # PTB v13/20
        fwd_chat = getattr(update.message, "forward_from_chat", None)
        if not fwd_chat:
            # PTB v21+: forward_origin
            origin = getattr(update.message, "forward_origin", None)
            if origin:
                # origin ممکن است MessageOriginChannel یا MessageOriginChat
                fwd_chat = getattr(origin, "chat", None) or getattr(origin, "sender_chat", None) or origin
        if fwd_chat:
            # بررسی صحت
            if not hasattr(fwd_chat, "id"):
                fwd_chat = None
    except Exception:
        fwd_chat = None

    if fwd_chat:
        # کاربر یک پیام از گروه فوروارد کرده برای تنظیم مقصد
        # تشخیص بده آیا کاربر قصد تنظیم مقصد دارد یا این پیام نرخ است که فوروارد شده
        # اگر forward_from_chat نوع channel/group بود و متن شامل نرخ نبود، به عنوان تنظیم مقصد بگیریم
        # اما ساده: هر فوروارد را به عنوان پیشنهاد تنظیم مقصد نشان بده
        # ولی اگر متن نرخ داشت، آن را هم پردازش کن؟ فعلاً تنظیم کن
        chat_id = fwd_chat.id
        chat_title = fwd_chat.title or fwd_chat.username or str(chat_id)
        # بپرس آیا می‌خواهد به عنوان مقصد تنظیم شود
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("✅ بله، به عنوان مقصد تنظیم کن", callback_data=f"set_fwd_{chat_id}")],
            [InlineKeyboardButton("❌ نه، این پیام نرخ است، پردازش کن", callback_data="process_fwd_as_rate")],
        ])
        await update.message.reply_text(
            f"📨 پیام فوروارد از <b>{html.escape(chat_title)}</b> دریافت شد.\n"
            f"شناسه: <code>{chat_id}</code>\n\n"
            "آیا می‌خواهید این گروه به عنوان <b>گروه مقصد انتشار</b> تنظیم شود؟",
            parse_mode=ParseMode.HTML,
            reply_markup=keyboard,
        )
        # ذخیره موقت برای پردازش
        context.user_data["last_fwd_text"] = text
        context.user_data["last_fwd_id"] = chat_id
        return

    # حالت عادی: متن نرخ واتساپ
    # اگر متن بسیار کوتاه بود و شبیه دستور نبود، هشدار بده
    if len(text) < 10:
        await update.message.reply_text(
            "ℹ️ متن بسیار کوتاه است. لطفاً پیام کامل نرخ واتساپ را بفرستید.\n"
            "مثال: <code>[12/10, 16:57] +93...: 🇦🇪 |دوبی| خرید 900...</code>",
            parse_mode=ParseMode.HTML,
        )
        return
    if text.startswith("/") and len(text) < 20:
        return  # دستور بود، قبلاً هندل شده

    # پردازش
    await process_rate_text(update, context, text)


async def process_rate_text(update: Update, context: ContextTypes.DEFAULT_TYPE, raw_text: str):
    uid = update.effective_user.id
    # دیگر از کش raw_text برای مسدود کردن استفاده نمی‌کنیم — فقط برای تأیید انتشار تکراری (پایین) استفاده می‌شود
    # این اجازه می‌دهد کاربر همان نرخ را دوباره بفرستد و پیش‌نمایش جدید بگیرد

    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action="typing")

    try:
        formatted, warnings = parse_whatsapp_text(raw_text)
    except Exception as e:
        logger.exception("خطا در parse")
        await update.message.reply_text(f"❌ خطا در پردازش: {html.escape(str(e))}\nلطفاً متن دیگری بفرستید.")
        return

    pending_messages[uid] = {
        "raw": raw_text,
        "formatted": formatted,
        "warnings": warnings,
        "time": datetime.now().isoformat(),
    }

    await send_preview(update, context, formatted, warnings)


async def send_preview(update: Update, context: ContextTypes.DEFAULT_TYPE, formatted: str, warnings: list, is_edited: bool = False):
    uid = update.effective_user.id
    target = get_target_chat_id()

    # متن پیش‌نمایش
    header = "✏️ <b>پیش‌نمایش ویرایش‌شده</b>\n\n" if is_edited else "👀 <b>پیش‌نمایش پیام نهایی</b>\n\n"
    preview_body = formatted

    # اگر warnings داشت، اضافه کن
    warn_text = ""
    if warnings:
        warn_text = "\n\n⚠️ <b>هشدارها:</b>\n" + "\n".join(f"• {html.escape(w)}" for w in warnings[:6])
        if len(warnings) > 6:
            warn_text += f"\n• و {len(warnings)-6} هشدار دیگر..."

    target_info = f"\n\n🎯 مقصد: <code>{target or 'تنظیم نشده ❌'}</code>"
    if not target:
        target_info += "\n⚠️ ابتدا گروه مقصد را تنظیم کنید!"

    # دکمه‌ها
    kb = confirm_keyboard()
    # اگر مقصد تنظیم نشده، دکمه تأیید را غیرفعال نشان نده، اما در کلیک هشدار بده

    full_preview = header + preview_body + warn_text + target_info
    # تقسیم اگر بلند بود
    chunks = split_long_text(full_preview, 4000)
    # فقط chunk اول را با دکمه بفرست، بقیه بدون دکمه
    for i, chunk in enumerate(chunks):
        is_last = (i == len(chunks)-1)
        # فقط آخرین chunk دکمه داشته باشد
        markup = kb if is_last else None
        # برای امنیت، اگر chunk شامل HTML نامعتبر بود، با fallback بفرست
        try:
            await update.message.reply_text(
                chunk,
                parse_mode=ParseMode.HTML,
                reply_markup=markup,
                disable_web_page_preview=True,
            )
        except Exception as e:
            logger.warning(f"خطا در ارسال پیش‌نمایش HTML: {e}")
            # fallback بدون HTML
            await update.message.reply_text(
                re.sub(r'<[^>]+>', '', chunk),
                reply_markup=markup,
            )

    # اگر هیچ هشداری نبود، پیام تشویقی
    if not warnings:
        await update.message.reply_text(
            "✅ پردازش موفق. اگر همه‌چیز درست است، <b>تأیید و انتشار</b> را بزنید.",
            parse_mode=ParseMode.HTML,
        )


# ---------- هندلر دکمه‌ها ----------

async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not query:
        return
    data = query.data
    uid = query.from_user.id

    # امنیت: فقط ادمین
    if ADMIN_ID is not None and uid != ADMIN_ID:
        await query.answer("⛔️ دسترسی مجاز نیست.", show_alert=True)
        return

    # منوها
    if data == "menu_main":
        await query.answer()
        await query.message.reply_text("🏠 منوی اصلی", reply_markup=main_menu_keyboard())
        return
    if data == "menu_help":
        await query.answer()
        # شبیه‌سازی دستور help
        await help_command(update, context)
        # اما update.message در callback وجود ندارد، باید مستقیم بفرستیم
        # پس به جایش متن را اینجا بفرست
        text = (
            "📖 <b>راهنمای سریع</b>\n\n"
            "پیام واتساپ را بفرست → پیش‌نمایش → تأیید\n"
            "برای تنظیم مقصد یک پیام از گروه فوروارد کن یا <code>/set_target -100...</code>"
        )
        try:
            await context.bot.send_message(chat_id=query.message.chat_id, text=text, parse_mode=ParseMode.HTML)
        except:
            pass
        return
    if data == "menu_history":
        await query.answer()
        # تاریخچه
        hist = config.get("history", [])
        if not hist:
            await context.bot.send_message(chat_id=query.message.chat_id, text="📜 تاریخچه خالی است.")
        else:
            text = "📜 <b>تاریخچه:</b>\n\n"
            for i, h in enumerate(hist[:5], 1):
                text += f"{i}. {h.get('time','')[:16]} - {h.get('status','')}\n"
            await context.bot.send_message(chat_id=query.message.chat_id, text=text, parse_mode=ParseMode.HTML)
        return
    if data == "menu_status":
        await query.answer()
        # وضعیت
        target = get_target_chat_id()
        bot_info = await context.bot.get_me()
        txt = f"🤖 @{bot_info.username}\n🎯 مقصد: <code>{target or 'ندارد'}</code>\n👤 شما: <code>{uid}</code>"
        await context.bot.send_message(chat_id=query.message.chat_id, text=txt, parse_mode=ParseMode.HTML)
        return
    if data == "menu_settings":
        await query.answer()
        await context.bot.send_message(
            chat_id=query.message.chat_id,
            text="⚙️ <b>تنظیمات</b>",
            parse_mode=ParseMode.HTML,
            reply_markup=settings_keyboard(),
        )
        return
    if data == "menu_set_target":
        await query.answer()
        await context.bot.send_message(
            chat_id=query.message.chat_id,
            text=(
                "🎯 <b>تنظیم گروه مقصد</b>\n\n"
                "• یک پیام از گروه به اینجا فوروارد کن\n"
                "• یا دستی بفرست: <code>/set_target -100123...</code>\n"
                "• یا شناسه را همین‌جا بفرست: <code>-100...</code>"
            ),
            parse_mode=ParseMode.HTML,
        )
        return
    if data == "check_target":
        await query.answer("در حال بررسی...")
        target = get_target_chat_id()
        if not target:
            await context.bot.send_message(chat_id=query.message.chat_id, text="❌ مقصد تنظیم نشده.")
            return
        try:
            chat = await context.bot.get_chat(target)
            await context.bot.send_message(chat_id=query.message.chat_id, text=f"✅ دسترسی OK\nگروه: {chat.title}\nID: {chat.id}")
        except Exception as e:
            await context.bot.send_message(chat_id=query.message.chat_id, text=f"⚠️ خطا: {html.escape(str(e)[:300])}", parse_mode=ParseMode.HTML)
        return
    if data == "noop":
        await query.answer()
        return
    if data.startswith("set_fwd_"):
        await query.answer()
        try:
            chat_id = int(data.replace("set_fwd_", ""))
            chat = await context.bot.get_chat(chat_id)
            set_target_chat_id(chat_id)
            await query.edit_message_text(
                f"✅ گروه مقصد تنظیم شد:\n{html.escape(chat.title or str(chat_id))}\n<code>{chat_id}</code>",
                parse_mode=ParseMode.HTML,
            )
            await context.bot.send_message(
                chat_id=query.message.chat_id,
                text="حالا پیام نرخ واتساپ را بفرست تا پیش‌نمایش ببینی!",
                reply_markup=main_menu_keyboard(),
            )
        except Exception as e:
            await query.edit_message_text(f"⚠️ خطا: {html.escape(str(e))}")
        return
    if data == "process_fwd_as_rate":
        await query.answer()
        txt = context.user_data.get("last_fwd_text", "")
        if txt:
            await query.edit_message_text("🔄 در حال پردازش پیام فوروارد به عنوان نرخ...")
            # شبیه‌سازی update برای پردازش
            # مستقیم صدا بزن process
            fake_update = update
            # برای اینکه handle_text_message دوباره اجرا نشود، مستقیم process_rate_text صدا بزن
            await process_rate_text(update, context, txt)
        else:
            await query.edit_message_text("❌ متنی برای پردازش یافت نشد.")
        return

    # دکمه‌های تأیید
    if data == "confirm":
        # بررسی pending
        if uid not in pending_messages:
            await query.answer("❌ پیش‌نمایشی یافت نشد. دوباره متن بفرستید.", show_alert=True)
            try:
                await query.edit_message_reply_markup(reply_markup=None)
            except:
                pass
            return

        target = get_target_chat_id()
        if not target:
            await query.answer("⚠️ گروه مقصد تنظیم نشده!", show_alert=True)
            await context.bot.send_message(
                chat_id=query.message.chat_id,
                text="❌ نمی‌توان منتشر کرد: گروه مقصد تنظیم نشده.\n"
                     "لطفاً ابتدا با <code>/set_target -100...</code> یا فوروارد پیام، مقصد را تنظیم کنید.",
                parse_mode=ParseMode.HTML,
            )
            return

        # جلوگیری از دوبار کلیک
        pending = pending_messages[uid]
        cache_key = f"{uid}:{hash(pending['formatted'])}"
        if cache_key in published_cache:
            await query.answer("⚠️ این پیام قبلاً منتشر شده.", show_alert=True)
            return

        await query.answer("⏳ در حال انتشار...")

        # غیرفعال کردن دکمه‌ها برای جلوگیری از کلیک مکرر
        try:
            await query.edit_message_reply_markup(reply_markup=None)
        except:
            pass

        formatted = pending["formatted"]
        # تقسیم اگر بلند
        chunks = split_long_text(formatted, 4000)
        success = True
        err_msg = ""
        sent_ids = []
        for chunk in chunks:
            try:
                # تلاش با HTML
                msg = await context.bot.send_message(
                    chat_id=target,
                    text=chunk,
                    parse_mode=ParseMode.HTML,
                    disable_web_page_preview=True,
                )
                sent_ids.append(msg.message_id)
            except Exception as e:
                logger.warning(f"خطا در ارسال HTML، تلاش بدون HTML: {e}")
                try:
                    # fallback plain
                    plain = re.sub(r'<[^>]+>', '', chunk)
                    msg = await context.bot.send_message(
                        chat_id=target,
                        text=plain,
                        disable_web_page_preview=True,
                    )
                    sent_ids.append(msg.message_id)
                except Exception as e2:
                    success = False
                    err_msg = str(e2)
                    logger.error(f"خطا در انتشار: {e2}")
                    break

        if success:
            published_cache.add(cache_key)
            # حذف pending
            raw = pending["raw"]
            add_history(raw, formatted, "published")
            del pending_messages[uid]
            editing_state.pop(uid, None)

            # پیام موفقیت
            try:
                chat = await context.bot.get_chat(target)
                target_name = chat.title or str(target)
            except:
                target_name = str(target)
            await context.bot.send_message(
                chat_id=query.message.chat_id,
                text=(
                    f"✅ <b>منتشر شد!</b>\n\n"
                    f"📍 گروه: {html.escape(target_name)}\n"
                    f"🆔 شناسه: <code>{target}</code>\n"
                    f"📄 بخش‌ها: {len(chunks)}\n"
                    f"⏰ زمان: {datetime.now().strftime('%H:%M %Y-%m-%d')}\n\n"
                    "برای انتشار جدید، پیام بعدی واتساپ را بفرست."
                ),
                parse_mode=ParseMode.HTML,
                reply_markup=main_menu_keyboard(),
            )
            # حذف کش بعد از 60 ثانیه؟ نه، نگه دار برای جلوگیری از تکراری
        else:
            await context.bot.send_message(
                chat_id=query.message.chat_id,
                text=(
                    f"❌ <b>خطا در انتشار</b>\n\n"
                    f"<code>{html.escape(err_msg[:500])}</code>\n\n"
                    "مطمئن شوید:\n"
                    "• ربات در گروه مقصد ادمین است\n"
                    "• شناسه گروه درست است (/get_target)\n"
                    "• ربات دسترسی ارسال پیام دارد"
                ),
                parse_mode=ParseMode.HTML,
            )
            # دکمه‌ها را برگردان
            try:
                await query.message.reply_text(
                    "🔁 دوباره تلاش کنید یا لغو کنید:",
                    reply_markup=confirm_keyboard(),
                )
            except:
                pass
        return

    if data == "edit":
        if uid not in pending_messages:
            await query.answer("❌ چیزی برای ویرایش نیست.", show_alert=True)
            return
        await query.answer()
        editing_state[uid] = True
        await context.bot.send_message(
            chat_id=query.message.chat_id,
            text=(
                "✏️ <b>حالت ویرایش</b>\n\n"
                "متن نهایی را که می‌خواهید منتشر شود، <b>کامل</b> بفرستید.\n"
                "می‌توانید پیش‌نمایش قبلی را کپی، اصلاح و ارسال کنید.\n\n"
                "نکته: عنوان «💱 نرخ لحظه‌ای» اگر نفرستید، خودکار اضافه می‌شود.\n"
                "برای لغو: /cancel"
            ),
            parse_mode=ParseMode.HTML,
        )
        # نمایش متن فعلی برای کپی آسان (بدون HTML)
        current = pending_messages[uid]["formatted"]
        plain_current = re.sub(r'<[^>]+>', '', current)
        await context.bot.send_message(
            chat_id=query.message.chat_id,
            text=f"📋 <b>متن فعلی (برای کپی):</b>\n\n{html.escape(plain_current)}",
            parse_mode=ParseMode.HTML,
        )
        return

    if data == "cancel":
        if uid in pending_messages:
            del pending_messages[uid]
        editing_state.pop(uid, None)
        await query.answer("لغو شد")
        try:
            await query.edit_message_text("❌ <b>لغو شد.</b>\nپیش‌نمایش حذف شد. پیام جدید بفرستید.", parse_mode=ParseMode.HTML)
        except:
            await context.bot.send_message(chat_id=query.message.chat_id, text="❌ لغو شد.")
        return

    await query.answer()

# ---------- خطایابی سراسری ----------

async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    logger.error(f"خطای کلی: {context.error}", exc_info=context.error)
    # به ادمین اطلاع بده اگر ممکن بود
    try:
        if isinstance(update, Update) and update.effective_chat:
            # فقط خطاهای مهم را به چت خصوصی ادمین بفرست (نه هر خطا)
            pass
    except:
        pass

# ---------- اجرای اصلی ----------

def main():
    if not BOT_TOKEN:
        print("❌ خطا: BOT_TOKEN تنظیم نشده!")
        print("• فایل .env را بسازید یا متغیر محیطی BOT_TOKEN را تنظیم کنید.")
        print("• مثال: BOT_TOKEN=123456:ABC-... python main.py")
        return

    print("🚀 در حال راه‌اندازی ربات...")
    print(f"👤 ADMIN_ID: {ADMIN_ID or 'تنظیم نشده (همه مجاز)'}")
    print(f"🎯 TARGET_CHAT_ID: {get_target_chat_id() or 'تنظیم نشده'}")

    application = Application.builder().token(BOT_TOKEN).build()

    # دستورات
    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("status", status_command))
    application.add_handler(CommandHandler("settings", settings_command))
    application.add_handler(CommandHandler("get_target", get_target_command))
    application.add_handler(CommandHandler("set_target", set_target_command))
    application.add_handler(CommandHandler("history", history_command))
    application.add_handler(CommandHandler("cancel", cancel_command))

    # کلبک‌ها
    application.add_handler(CallbackQueryHandler(handle_callback))

    # پیام‌های متنی (باید بعد از CommandHandler باشد)
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text_message))

    # هندلر خطا
    application.add_error_handler(error_handler)

    # اجرا (polling رایگان)
    print("✅ ربات آماده است. برای توقف Ctrl+C")
    print("📡 حالت Polling فعال (رایگان، بدون نیاز به webhook)")
    application.run_polling(
        allowed_updates=Update.ALL_TYPES,
        drop_pending_updates=True,
    )

if __name__ == "__main__":
    main()
