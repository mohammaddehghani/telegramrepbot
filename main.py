# main.py
import os
import logging
import psycopg2
import datetime
import jdatetime
import openpyxl

from telegram import (
    Update,
    ReplyKeyboardMarkup,
    ReplyKeyboardRemove,
    InputFile,
)
from telegram.ext import (
    Application,
    ContextTypes,
    CommandHandler,
    MessageHandler,
    filters,
)

logging.basicConfig(level=logging.INFO)

# ————————— متغیرهای محیطی —————————
BOT_TOKEN    = os.getenv("BOT_TOKEN")
DATABASE_URL = os.getenv("DATABASE_URL")
SUPER_ADMIN  = int(os.getenv("SUPER_ADMIN"))

# ————————— کمک‌فانکشن‌های دیتابیس —————————
def get_db():
    return psycopg2.connect(DATABASE_URL)

def ensure_user(user):
    conn = get_db(); cur = conn.cursor()
    cur.execute(
        "INSERT INTO users (user_id, full_name, username, display_name) "
        "VALUES (%s, %s, %s, %s) ON CONFLICT (user_id) DO NOTHING;",
        (user.id,
         f"{user.first_name or ''} {user.last_name or ''}".strip(),
         user.username or '',
         f"{user.first_name or ''} {user.last_name or ''}".strip())
    )
    conn.commit()
    cur.close(); conn.close()

def is_admin(user_id):
    if user_id == SUPER_ADMIN:
        return True
    conn = get_db(); cur = conn.cursor()
    cur.execute("SELECT 1 FROM admins WHERE user_id=%s;", (user_id,))
    ok = cur.fetchone() is not None
    cur.close(); conn.close()
    return ok

def list_users():
    conn = get_db(); cur = conn.cursor()
    cur.execute("SELECT user_id, full_name, username, display_name FROM users ORDER BY user_id;")
    rows = cur.fetchall()
    cur.close(); conn.close()
    return rows

def set_display_name(user_id, new_name):
    conn = get_db(); cur = conn.cursor()
    cur.execute("UPDATE users SET display_name=%s WHERE user_id=%s;", (new_name, user_id))
    conn.commit()
    cur.close(); conn.close()

def save_attendance(user_id, status):
    """فقط یک ثبت در یک روز"""
    now = get_iran_now()
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    conn = get_db(); cur = conn.cursor()
    cur.execute(
        "SELECT 1 FROM attendance WHERE user_id=%s AND status=%s AND timestamp >= %s;",
        (user_id, status, today_start)
    )
    if cur.fetchone():
        cur.close(); conn.close()
        return False, now
    cur.execute(
        "INSERT INTO attendance (user_id, status, timestamp) VALUES (%s, %s, %s);",
        (user_id, status, now)
    )
    conn.commit(); cur.close(); conn.close()
    return True, now

def fetch_attendance(user_id=None, start=None, end=None):
    q = "SELECT user_id, status, timestamp FROM attendance"
    cond = []; params = []
    if user_id is not None:
        cond.append("user_id=%s"); params.append(user_id)
    if start is not None:
        cond.append("timestamp >= %s"); params.append(start)
    if end is not None:
        cond.append("timestamp <= %s"); params.append(end)
    if cond:
        q += " WHERE " + " AND ".join(cond)
    q += " ORDER BY timestamp"
    conn = get_db(); cur = conn.cursor()
    cur.execute(q, tuple(params))
    rows = cur.fetchall()
    cur.close(); conn.close()
    return rows

# —————— زمان ایران و تبدیل به شمسی ——————
def get_iran_now():
    # UTC+3:30
    tz = datetime.timezone(datetime.timedelta(hours=3, minutes=30))
    return datetime.datetime.now(tz)

def to_shamsi(dt):
    s = jdatetime.datetime.fromgregorian(datetime=dt)
    return s.strftime("%Y/%m/%d"), s.strftime("%H:%M:%S")

def get_display_name(user_id):
    for u in list_users():
        if u[0] == user_id:
            return u[3] or u[1]
    return str(user_id)

# —————— کیبوردها ——————
MAIN_KEYBOARD = ReplyKeyboardMarkup(
    [
        ["ثبت ورود", "ثبت خروج"],
        ["گزارش روزانه", "گزارش ماهانه"],
        ["ادمین"]
    ], resize_keyboard=True
)

ADMIN_KEYBOARD = ReplyKeyboardMarkup(
    [
        ["لیست کاربران", "تعیین نام نمایشی"],
        ["گزارش روزانه همه", "گزارش ماهانه کاربر"],
        ["دریافت بکاپ", "بازگشت"]
    ], resize_keyboard=True
)

# —————— هندلر استارت ——————
async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    u = update.effective_user
    ensure_user(u)
    text = "سلام! برای ثبت حضور یا دریافت گزارش‌ها از منوی زیر استفاده کنید."
    await update.message.reply_text(text, reply_markup=MAIN_KEYBOARD)

# —————— هندلر ورودی دکمه‌ها ——————
async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    txt = update.message.text.strip()
    uid = update.effective_user.id

    # — ثبت ورود —
    if txt == "ثبت ورود":
        ok, now = save_attendance(uid, "enter")
        if ok:
            d, t = to_shamsi(now)
            await update.message.reply_text(f"✅ ورود ثبت شد: {d} | {t}")
        else:
            await update.message.reply_text("⚠️ شما قبلاً امروز ورود را ثبت کرده‌اید.")
        return

    # — ثبت خروج —
    if txt == "ثبت خروج":
        ok, now = save_attendance(uid, "exit")
        if ok:
            d, t = to_shamsi(now)
            await update.message.reply_text(f"✅ خروج ثبت شد: {d} | {t}")
        else:
            await update.message.reply_text("⚠️ شما قبلاً امروز خروج را ثبت کرده‌اید.")
        return

    # — گزارش روزانه کاربر —
    if txt == "گزارش روزانه":
        now = get_iran_now()
        start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        end   = now.replace(hour=23, minute=59, second=59)
        items = fetch_attendance(user_id=uid, start=start, end=end)
        if not items:
            await update.message.reply_text("📋 موردی ثبت نشده است.")
        else:
            lines = ["📅 گزارش روزانه شما:"]
            for _, st, ts in items:
                d, t = to_shamsi(ts)
                lines.append(f"{d} | {t} | {'ورود' if st=='enter' else 'خروج'}")
            await update.message.reply_text("\n".join(lines))
        return

    # — گزارش ماهانه کاربر —
    if txt == "گزارش ماهانه":
        now = get_iran_now()
        start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        end   = now.replace(hour=23, minute=59, second=59)
        items = fetch_attendance(user_id=uid, start=start, end=end)
        if not items:
            await update.message.reply_text("📋 موردی ثبت نشده است.")
        else:
            lines = [f"📅 گزارش ماه جاری ({get_display_name(uid)}):"]
            for _, st, ts in items:
                d, t = to_shamsi(ts)
                lines.append(f"{d} | {t} | {'ورود' if st=='enter' else 'خروج'}")
            await update.message.reply_text("\n".join(lines))
        return

    # — منوی ادمین —
    if txt == "ادمین":
        if is_admin(uid):
            await update.message.reply_text("🔐 پنل ادمین:", reply_markup=ADMIN_KEYBOARD)
        else:
            await update.message.reply_text("❌ شما ادمین نیستید.", reply_markup=MAIN_KEYBOARD)
        return

    # — لیست کاربران (ادمین) —
    if txt == "لیست کاربران":
        if not is_admin(uid):
            return await update.message.reply_text("❌ دسترسی فقط برای ادمین‌ها.", reply_markup=MAIN_KEYBOARD)
        users = list_users()
        lines = ["👥 لیست کاربران:"]
        for u in users:
            lines.append(f"{u[0]}\t{u[3] or u[1]}\t@{u[2]}")
        await update.message.reply_text("\n".join(lines))
        return

    # — تعیین نام نمایشی (ادمین) —
    if txt == "تعیین نام نمایشی":
        if not is_admin(uid):
            return await update.message.reply_text("❌ دسترسی فقط برای ادمین‌ها.", reply_markup=MAIN_KEYBOARD)
        await update.message.reply_text(
            "📌 برای تغییر نام نمایشی از دستور زیر استفاده کنید:\n"
            "/setname <user_id> نام_جدید"
        )
        return

    # — گزارش روزانه همه (ادمین) —
    if txt == "گزارش روزانه همه":
        if not is_admin(uid):
            return await update.message.reply_text("❌ دسترسی فقط برای ادمین‌ها.", reply_markup=MAIN_KEYBOARD)
        now = get_iran_now()
        s = now.replace(hour=0, minute=0, second=0, microsecond=0)
        e = now.replace(hour=23, minute=59, second=59)
        items = fetch_attendance(start=s, end=e)
        if not items:
            return await update.message.reply_text("📋 موردی ثبت نشده است.")
        users = {u[0]: u for u in list_users()}
        lines = ["📅 گزارش روزانه همه:"]
        for uid2, st, ts in items:
            name = users[uid2][3] or users[uid2][1]
            d, t = to_shamsi(ts)
            lines.append(f"{name} | {d} | {t} | {'ورود' if st=='enter' else 'خروج'}")
        await update.message.reply_text("\n".join(lines))
        return

    # — گزارش ماهانه کاربر (ادمین) —
    if txt == "گزارش ماهانه کاربر":
        if not is_admin(uid):
            return await update.message.reply_text("❌ دسترسی فقط برای ادمین‌ها.", reply_markup=MAIN_KEYBOARD)
        await update.message.reply_text(
            "📌 برای گزارش ماهانه یک کاربر از دستور زیر استفاده کنید:\n"
            "/report_month <user_id>"
        )
        return

    # — دریافت بکاپ (ادمین) —
    if txt == "دریافت بکاپ":
        if not is_admin(uid):
            return await update.message.reply_text("❌ دسترسی فقط برای ادمین‌ها.", reply_markup=MAIN_KEYBOARD)
        # تولید فایل‌های اکسل و تکست
        xlsx = create_total_attendance_excel()
        txtfile = create_users_admins_txt()
        await update.message.reply_document(InputFile(xlsx), filename="all_attendance.xlsx")
        await update.message.reply_document(InputFile(txtfile), filename="users_admins.txt")
        return

    # — بازگشت به منوی اصلی —
    if txt == "بازگشت":
        await update.message.reply_text("🔙 بازگشت به منوی اصلی", reply_markup=MAIN_KEYBOARD)
        return

    # — سایر پیام‌ها —
    # (مثلاً دستورات /setname، /report_month، /backup با CommandHandler بالای‌ست)
    # به‌صورت پیش‌فرض کاری انجام نمی‌دهیم.
    return

# —————— ستاپ بات ——————
def main():
    app = Application.builder().token(BOT_TOKEN).build()

    # CommandHandlers
    app.add_handler(CommandHandler("start", start_cmd))
    app.add_handler(CommandHandler("setname", setname_command))         # مثل قبل
    app.add_handler(CommandHandler("report_month", report_month_command))
    app.add_handler(CommandHandler("backup", backup_command))

    # MessageHandler برای دکمه‌های متنی
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

    logging.info("Bot Started ...")
    app.run_polling()

if __name__ == "__main__":
    main()
