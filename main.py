import os
import logging
import psycopg2
import datetime
import jdatetime
import openpyxl

from telegram import Update, ReplyKeyboardMarkup, KeyboardButton, InputFile
from telegram.ext import (
    Application, ContextTypes,
    CommandHandler, MessageHandler, filters,
)

# -------------- تنظیم logging و بارگذاری متغیرها --------------
logging.basicConfig(level=logging.INFO)

BOT_TOKEN    = os.environ.get("BOT_TOKEN")
DATABASE_URL = os.environ.get("DATABASE_URL")
SUPER_ADMIN  = int(os.environ.get("SUPER_ADMIN", "0"))

if not BOT_TOKEN or not DATABASE_URL or not SUPER_ADMIN:
    logging.error("لطفاً متغیرهای BOT_TOKEN, DATABASE_URL, SUPER_ADMIN را تنظیم کنید.")
    exit(1)

# -------------- تابع اتصال به دیتابیس (بدون SSL) --------------
def get_db():
    return psycopg2.connect(DATABASE_URL)  # sslmode پیش‌فرض خاموش است

# -------------- توابع کمکی دیتابیس --------------
def ensure_user(user):
    conn = get_db(); cur = conn.cursor()
    cur.execute(
        "INSERT INTO users (user_id, full_name, username, display_name) "
        "VALUES (%s,%s,%s,%s) ON CONFLICT (user_id) DO NOTHING;",
        (user.id,
         f"{user.first_name or ''} {user.last_name or ''}".strip(),
         user.username or "",
         f"{user.first_name or ''} {user.last_name or ''}".strip())
    )
    conn.commit(); cur.close(); conn.close()

def is_admin(user_id):
    conn = get_db(); cur = conn.cursor()
    cur.execute("SELECT 1 FROM admins WHERE user_id=%s;", (user_id,))
    res = cur.fetchone()
    cur.close(); conn.close()
    return user_id == SUPER_ADMIN or (res is not None)

def save_attendance(user_id, status):
    conn = get_db(); cur = conn.cursor()
    now = datetime.datetime.now()
    cur.execute(
        "INSERT INTO attendance (user_id, status, timestamp) VALUES (%s,%s,%s);",
        (user_id, status, now)
    )
    conn.commit(); cur.close(); conn.close()

def fetch_attendance(user_id=None, start=None, end=None):
    conn = get_db(); cur = conn.cursor()
    q = "SELECT user_id, status, timestamp FROM attendance"
    p = []
    cond = []
    if user_id:
        cond.append("user_id=%s"); p.append(user_id)
    if start:
        cond.append("timestamp>=%s"); p.append(start)
    if end:
        cond.append("timestamp<=%s"); p.append(end)
    if cond:
        q += " WHERE " + " AND ".join(cond)
    q += " ORDER BY timestamp"
    cur.execute(q, tuple(p))
    rows = cur.fetchall()
    cur.close(); conn.close()
    return rows

def list_users():
    conn = get_db(); cur = conn.cursor()
    cur.execute("SELECT user_id, full_name, username, display_name FROM users ORDER BY user_id;")
    users = cur.fetchall()
    cur.close(); conn.close()
    return users

# -------------- تبدیل تاریخ به شمسی --------------
def to_shamsi(dt: datetime.datetime):
    tz = datetime.timezone(datetime.timedelta(hours=3, minutes=30))
    dt = dt.astimezone(tz)
    j = jdatetime.datetime.fromgregorian(datetime=dt)
    return j.strftime("%Y/%m/%d"), j.strftime("%H:%M:%S")

# -------------- تعریف کیبورد‌ها --------------
user_keyboard = ReplyKeyboardMarkup([
    [KeyboardButton("ثبت ورود"), KeyboardButton("ثبت خروج")],
    [KeyboardButton("گزارش روزانه"), KeyboardButton("گزارش ماهانه")],
], resize_keyboard=True)

admin_keyboard = ReplyKeyboardMarkup([
    [KeyboardButton("لیست کاربران"), KeyboardButton("گزارش روزانه همه")],
    [KeyboardButton("تعیین نام نمایشی"), KeyboardButton("گزارش ماهانه کاربر")],
    [KeyboardButton("دریافت بکاپ")],
], resize_keyboard=True)

# -------------- هندلر دستور /start --------------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    ensure_user(update.effective_user)
    await update.message.reply_text(
        "سلام! برای ثبت حضور یا دریافت گزارش‌ها از منوی زیر استفاده کنید:",
        reply_markup=user_keyboard
    )

# -------------- هندلر دستور /admin --------------
async def admin_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not is_admin(user_id):
        await update.message.reply_text("❌ شما در لیست ادمین‌ها نیستید.")
        return
    await update.message.reply_text(
        "پنل ادمین – گزینه مورد نظر را انتخاب کنید:",
        reply_markup=admin_keyboard
    )

# -------------- هندلر پیام‌های متنی معمولی --------------
async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    user_id = update.effective_user.id

    # ـــــــــــ منوی عادی کاربر ـــــــــــ
    if text == "ثبت ورود":
        save_attendance(user_id, "enter")
        await update.message.reply_text("✅ ورود شما ثبت شد.")
        return

    if text == "ثبت خروج":
        save_attendance(user_id, "exit")
        await update.message.reply_text("✅ خروج شما ثبت شد.")
        return

    if text in ("گزارش روزانه", "گزارش ماهانه"):
        # بازخوانی لاگین یوزر
        ensure_user(update.effective_user)
        # محاسبه بازه
        now = datetime.datetime.now()
        if text == "گزارش روزانه":
            start = now.replace(hour=0, minute=0, second=0, microsecond=0)
            end   = now.replace(hour=23, minute=59, second=59)
            title = "گزارش روزانه"
        else:
            start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
            end   = now.replace(hour=23, minute=59, second=59)
            title = "گزارش ماهانه"

        items = fetch_attendance(user_id=user_id, start=start, end=end)
        if not items:
            await update.message.reply_text(f"{title}\n📋 هیچ موردی ثبت نشده است.")
            return

        out = [f"{title} شما:\n"]
        for u_id, status, ts in items:
            date_sh, time_sh = to_shamsi(ts)
            typ = "ورود" if status=="enter" else "خروج"
            out.append(f"{date_sh} – {time_sh} | {typ}")
        await update.message.reply_text("\n".join(out))
        return

    # ـــــــــــ منوی ادمین ـــــــــــ
    if text == "لیست کاربران":
        if not is_admin(user_id):
            await update.message.reply_text("دسترسی فقط برای ادمین‌ها.")
            return
        users = list_users()
        lines = ["لیست کاربران:"]
        for u in users:
            uid, full, uname, disp = u
            lines.append(f"{uid}\t{disp or full}\t@{uname}")
        await update.message.reply_text("\n".join(lines))
        return

    if text == "گزارش روزانه همه":
        if not is_admin(user_id):
            await update.message.reply_text("دسترسی فقط برای ادمین‌ها.")
            return
        now = datetime.datetime.now()
        start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        end   = now.replace(hour=23, minute=59, second=59)
        items = fetch_attendance(start=start, end=end)
        if not items:
            await update.message.reply_text("🏷️ امروز هیچ گزارشی ثبت نشده.")
            return
        users = {u[0]:u for u in list_users()}
        out = []
        for uid, status, ts in items:
            full = users[uid][3] or users[uid][1]
            d, t = to_shamsi(ts)
            typ = "ورود" if status=="enter" else "خروج"
            out.append(f"{full}: {d} {t} | {typ}")
        await update.message.reply_text("\n".join(out))
        return

    if text == "تعیین نام نمایشی":
        if not is_admin(user_id):
            await update.message.reply_text("دسترسی فقط برای ادمین‌ها.")
            return
        await update.message.reply_text("برای تغییر نام نمایشی از دستور زیر استفاده کنید:\n\n"
                                        "/setname <user_id> <نام جدید>")
        return

    if text == "گزارش ماهانه کاربر":
        if not is_admin(user_id):
            await update.message.reply_text("دسترسی فقط برای ادمین‌ها.")
            return
        await update.message.reply_text("برای گزارش ماهانه یک کاربر:\n\n"
                                        "/report_month <user_id>")
        return

    if text == "دریافت بکاپ":
        if not is_admin(user_id):
            await update.message.reply_text("دسترسی فقط برای ادمین‌ها.")
            return
        # فرمان داخلی بکاپ
        await backup_command(update, context)
        return

    # ـــــــــــ اگر پیام ناشناخته بود، راهنمایی کنیم ـــــــــــ
    await update.message.reply_text("متوجه نشدم 😕\nلطفاً از منوی زیر استفاده کنید:", reply_markup=user_keyboard)

# -------------- کامندهای ادمین (/setname, /report_month, /backup) --------------
async def setname_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("دسترسی فقط برای ادمین‌ها.")
        return
    try:
        user_id = int(context.args[0])
        newname = " ".join(context.args[1:])
        conn = get_db(); cur = conn.cursor()
        cur.execute("UPDATE users SET display_name=%s WHERE user_id=%s;", (newname, user_id))
        conn.commit(); cur.close(); conn.close()
        await update.message.reply_text("✅ نام نمایشی تغییر کرد.")
    except:
        await update.message.reply_text("فرمت دستور صحیح نیست:\n/setname <user_id> <نام جدید>")

async def report_month_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("دسترسی فقط برای ادمین‌ها.")
        return
    try:
        user_id = int(context.args[0])
        now = datetime.datetime.now()
        start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        end   = now.replace(hour=23, minute=59, second=59)
        items = fetch_attendance(user_id=user_id, start=start, end=end)
        if not items:
            await update.message.reply_text("❗️ این کاربر هیچ ورودی/خروجی ثبت نکرده.")
            return
        lines = [f"گزارش ماه جاری ({user_id}):"]
        for _, status, ts in items:
            d, t = to_shamsi(ts)
            typ = "ورود" if status=="enter" else "خروج"
            lines.append(f"{d} {t} | {typ}")
        await update.message.reply_text("\n".join(lines))
    except:
        await update.message.reply_text("فرمت دستور صحیح نیست:\n/report_month <user_id>")

async def backup_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("دسترسی فقط برای ادمین‌ها.")
        return

    # اکسل
    xlsx = "/tmp/all_attendance.xlsx"
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(['user_id','display_name','full_name','status','تاریخ','ساعت'])
    users = {u[0]:u for u in list_users()}
    for uid, status, ts in fetch_attendance():
        disp = users[uid][3] or '-'
        full = users[uid][1] or '-'
        d, t = to_shamsi(ts)
        ws.append([uid, disp, full, 'ورود' if status=='enter' else 'خروج', d, t])
    wb.save(xlsx)

    # فایل متنی
    txt = "/tmp/users_admins.txt"
    with open(txt, "w", encoding="utf8") as f:
        f.write("ادمین‌ها:\n")
        cur = get_db().cursor()
        cur.execute("SELECT user_id FROM admins;")
        for row in cur.fetchall():
            f.write(f"{row[0]}\n")
        cur.close()
        f.write("\nکاربران:\n")
        for u in list_users():
            f.write(f"{u[0]}\t{u[3] or u[1]}\t@{u[2]}\n")

    # ارسال به تلگرام
    await update.message.reply_document(document=InputFile(xlsx), filename="all_attendance.xlsx")
    await update.message.reply_document(document=InputFile(txt), filename="users_admins.txt")
    await update.message.reply_text("✅ بکاپ ارسال شد.")

# -------------- ستاپ و اجرای بات --------------
def main():
    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("admin", admin_cmd))
    app.add_handler(CommandHandler("setname", setname_command))
    app.add_handler(CommandHandler("report_month", report_month_command))
    app.add_handler(CommandHandler("backup", backup_command))

    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

    logging.info("Bot Started ...")
    app.run_polling()

if __name__ == "__main__":
    main()
