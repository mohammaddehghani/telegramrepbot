import os
import logging
import psycopg2
import datetime
import jdatetime
import openpyxl

from telegram import Update, ReplyKeyboardMarkup, KeyboardButton, InputFile
from telegram.ext import Application, ContextTypes, CommandHandler, MessageHandler, filters

# -------------- تنظیم logging --------------
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

# -------------- توابع دیتابیس --------------
def ensure_user(user):
    conn = get_db()
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO users (user_id, full_name, username, display_name) "
        "VALUES (%s, %s, %s, %s) ON CONFLICT (user_id) DO NOTHING;",
        (
            user.id,
            f"{user.first_name or ''} {user.last_name or ''}".strip(),
            user.username or "",
            f"{user.first_name or ''} {user.last_name or ''}".strip(),
        ),
    )
    conn.commit()
    cur.close()
    conn.close()

def is_admin(user_id):
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT 1 FROM admins WHERE user_id=%s;", (user_id,))
    res = cur.fetchone()
    cur.close()
    conn.close()
    return user_id == SUPER_ADMIN or (res is not None)

def list_users():
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT user_id, full_name, username, display_name FROM users ORDER BY user_id;")
    users = cur.fetchall()
    cur.close()
    conn.close()
    return users

def set_display_name(user_id, new_name):
    conn = get_db()
    cur = conn.cursor()
    cur.execute("UPDATE users SET display_name=%s WHERE user_id=%s;", (new_name, user_id))
    conn.commit()
    cur.close()
    conn.close()

# -------------- زمان ایران بدون pytz --------------
def get_iran_now():
    iran_tz = datetime.timezone(datetime.timedelta(hours=3, minutes=30))
    return datetime.datetime.now(iran_tz)

def to_shamsi(dt: datetime.datetime):
    # تبدیل تاریخ میلادی به شمسی
    jdt = jdatetime.datetime.fromgregorian(datetime=dt)
    return jdt.strftime("%Y/%m/%d"), jdt.strftime("%H:%M:%S")

# -------------- ثبت ورود/خروج به ازای یک بار در روز --------------
def already_registered(user_id, status):
    now = get_iran_now()
    start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    end = now.replace(hour=23, minute=59, second=59, microsecond=999999)
    conn = get_db()
    cur = conn.cursor()
    cur.execute(
        "SELECT 1 FROM attendance WHERE user_id=%s AND status=%s AND timestamp BETWEEN %s AND %s;",
        (user_id, status, start, end)
    )
    exists = cur.fetchone() is not None
    cur.close()
    conn.close()
    return exists

def save_attendance(user_id, status):
    if already_registered(user_id, status):
        return False
    now = get_iran_now()
    conn = get_db()
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO attendance (user_id, status, timestamp) VALUES (%s, %s, %s);",
        (user_id, status, now)
    )
    conn.commit()
    cur.close()
    conn.close()
    return True

def fetch_attendance(user_id=None, start=None, end=None):
    conn = get_db()
    cur = conn.cursor()
    q = "SELECT user_id, status, timestamp FROM attendance"
    params = []
    cond = []
    if user_id:
        cond.append("user_id=%s")
        params.append(user_id)
    if start:
        cond.append("timestamp>=%s")
        params.append(start)
    if end:
        cond.append("timestamp<=%s")
        params.append(end)
    if cond:
        q += " WHERE " + " AND ".join(cond)
    q += " ORDER BY timestamp"
    cur.execute(q, tuple(params))
    rows = cur.fetchall()
    cur.close()
    conn.close()
    return rows

# -------------- تعریف کیبورد‌ها --------------
def user_keyboard():
    # دکمه‌های منوی کاربر (مانند ورود، خروج، گزارش‌ها و ادمین)
    keyboard = [
        [KeyboardButton("ثبت ورود"), KeyboardButton("ثبت خروج")],
        [KeyboardButton("گزارش روزانه"), KeyboardButton("گزارش ماهانه")],
        [KeyboardButton("ادمین")],
    ]
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True)

def admin_keyboard():
    # منوی ادمین با گزینه‌ی بازگشت
    keyboard = [
        [KeyboardButton("لیست کاربران"), KeyboardButton("تعیین نام نمایشی")],
        [KeyboardButton("گزارش روزانه همه"), KeyboardButton("گزارش ماهانه کاربر")],
        [KeyboardButton("دریافت بکاپ")],
        [KeyboardButton("بازگشت")],
    ]
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True)

# -------------- هندلر دستورات --------------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    ensure_user(update.effective_user)
    await update.message.reply_text(
        "سلام! برای ثبت حضور یا دریافت گزارش‌ها از منوی زیر استفاده کنید:", reply_markup=user_keyboard()
    )

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    user_id = update.effective_user.id

    # ------------- منوی عادی ----------------
    if text == "ثبت ورود":
        if save_attendance(user_id, "enter"):
            await update.message.reply_text("✅ ورود شما ثبت شد.", reply_markup=user_keyboard())
        else:
            await update.message.reply_text("❌ ورود شما برای امروز قبلاً ثبت شده است.", reply_markup=user_keyboard())
        return

    if text == "ثبت خروج":
        if save_attendance(user_id, "exit"):
            await update.message.reply_text("✅ خروج شما ثبت شد.", reply_markup=user_keyboard())
        else:
            await update.message.reply_text("❌ خروج شما برای امروز قبلاً ثبت شده است.", reply_markup=user_keyboard())
        return

    if text in ("گزارش روزانه", "گزارش ماهانه"):
        # تعریف بازه زمانی
        now = get_iran_now()
        if text == "گزارش روزانه":
            start_time = now.replace(hour=0, minute=0, second=0, microsecond=0)
            end_time   = now.replace(hour=23, minute=59, second=59, microsecond=999999)
            title = "گزارش روزانه"
        else:
            start_time = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
            end_time   = now.replace(hour=23, minute=59, second=59, microsecond=999999)
            title = "گزارش ماهانه"

        items = fetch_attendance(user_id=user_id, start=start_time, end=end_time)
        if not items:
            await update.message.reply_text(f"{title}\n📋 موردی ثبت نشده است.", reply_markup=user_keyboard())
            return

        lines = [f"{title} ({update.effective_user.first_name}):"]
        for uid, status, ts in items:
            sh_date, sh_time = to_shamsi(ts)
            typ = "ورود" if status == "enter" else "خروج"
            lines.append(f"{sh_date} – {sh_time} | {typ}")
        await update.message.reply_text("\n".join(lines), reply_markup=user_keyboard())
        return

    if text == "ادمین":
        # اگر کاربر غیر ادمین است، پیام خطا بده
        if not is_admin(user_id):
            await update.message.reply_text("❌ شما ادمین نیستید.", reply_markup=user_keyboard())
        else:
            await update.message.reply_text("❇️ خوش آمدید به پنل ادمین:", reply_markup=admin_keyboard())
        return

    # ------------- منوی ادمین ----------------
    if text == "لیست کاربران":
        if not is_admin(user_id):
            await update.message.reply_text("دسترسی فقط برای ادمین‌ها.", reply_markup=user_keyboard())
            return
        users = list_users()
        lines = ["لیست کاربران:"]
        for u in users:
            uid, full, uname, disp = u
            lines.append(f"{uid}\t{disp or full}\t@{uname}")
        await update.message.reply_text("\n".join(lines), reply_markup=admin_keyboard())
        return

    if text == "تعیین نام نمایشی":
        if not is_admin(user_id):
            await update.message.reply_text("دسترسی فقط برای ادمین‌ها.", reply_markup=user_keyboard())
            return
        await update.message.reply_text("برای تغییر نام نمایشی از دستور زیر استفاده کنید:\n\n/setname <user_id> <نام جدید>", reply_markup=admin_keyboard())
        return

    if text == "گزارش روزانه همه":
        if not is_admin(user_id):
            await update.message.reply_text("دسترسی فقط برای ادمین‌ها.", reply_markup=user_keyboard())
            return
        now = get_iran_now()
        start_time = now.replace(hour=0, minute=0, second=0, microsecond=0)
        end_time   = now.replace(hour=23, minute=59, second=59, microsecond=999999)
        items = fetch_attendance(start=start_time, end=end_time)
        if not items:
            await update.message.reply_text("امروز هیچ گزارشی ثبت نشده.", reply_markup=admin_keyboard())
            return
        user_dict = {u[0]: u for u in list_users()}
        out_lines = []
        for uid, status, ts in items:
            name = user_dict[uid][3] or user_dict[uid][1]
            sh_date, sh_time = to_shamsi(ts)
            out_lines.append(f"{name}: {sh_date} {sh_time} | {'ورود' if status=='enter' else 'خروج'}")
        await update.message.reply_text("\n".join(out_lines), reply_markup=admin_keyboard())
        return

    if text == "گزارش ماهانه کاربر":
        if not is_admin(user_id):
            await update.message.reply_text("دسترسی فقط برای ادمین‌ها.", reply_markup=user_keyboard())
            return
        await update.message.reply_text("برای گزارش ماهانه یک کاربر از دستور زیر استفاده کنید:\n\n/report_month <user_id>", reply_markup=admin_keyboard())
        return

    if text == "دریافت بکاپ":
        if not is_admin(user_id):
            await update.message.reply_text("دسترسی فقط برای ادمین‌ها.", reply_markup=user_keyboard())
            return
        await backup_command(update, context)
        return

    if text == "بازگشت":
        # دکمه برگشت از منوی ادمین به منوی کاربر
        await update.message.reply_text("بازگشت به منوی اصلی...", reply_markup=user_keyboard())
        return

    # ------------- دستورات متنی ناشناخته -------------
    await update.message.reply_text("متوجه نشدم 😕\nلطفاً از منوی زیر استفاده کنید.", reply_markup=user_keyboard())

# -------------- دستورات ادمین (دستورات متنی) --------------
async def setname_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("دسترسی فقط برای ادمین‌ها.", reply_markup=user_keyboard())
        return
    try:
        user_id = int(context.args[0])
        new_name = " ".join(context.args[1:])
        set_display_name(user_id, new_name)
        await update.message.reply_text("✅ نام نمایشی تغییر کرد.", reply_markup=admin_keyboard())
    except Exception as e:
        await update.message.reply_text("فرمت دستور صحیح نیست:\n/setname <user_id> <نام جدید>", reply_markup=admin_keyboard())

async def report_month_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("دسترسی فقط برای ادمین‌ها.", reply_markup=user_keyboard())
        return
    try:
        user_id = int(context.args[0])
        now = get_iran_now()
        start_time = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        end_time   = now.replace(hour=23, minute=59, second=59, microsecond=999999)
        items = fetch_attendance(user_id=user_id, start=start_time, end=end_time)
        if not items:
            await update.message.reply_text("❗️ این کاربر هیچ ورودی/خروجی ثبت نکرده.", reply_markup=admin_keyboard())
            return
        lines = [f"گزارش ماه جاری ({user_id}):"]
        for _, status, ts in items:
            sh_date, sh_time = to_shamsi(ts)
            lines.append(f"{sh_date} {sh_time} | {'ورود' if status=='enter' else 'خروج'}")
        await update.message.reply_text("\n".join(lines), reply_markup=admin_keyboard())
    except Exception as e:
        await update.message.reply_text("فرمت دستور صحیح نیست:\n/report_month <user_id>", reply_markup=admin_keyboard())

def create_total_attendance_excel():
    filename = "/tmp/all_attendance.xlsx"
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(['user_id', 'display_name', 'full_name', 'status', 'تاریخ', 'ساعت'])
    user_dict = {u[0]: u for u in list_users()}
    all_items = fetch_attendance()
    for uid, status, ts in all_items:
        disp = user_dict[uid][3] or '-'
        full = user_dict[uid][1] or '-'
        sh_date, sh_time = to_shamsi(ts)
        ws.append([uid, disp, full, 'ورود' if status=='enter' else 'خروج', sh_date, sh_time])
    wb.save(filename)
    return filename

def create_users_admins_txt():
    filename = "/tmp/users_admins.txt"
    with open(filename, "w", encoding="utf8") as f:
        f.write("ادمین‌ها:\n")
        conn = get_db()
        cur = conn.cursor()
        cur.execute("SELECT user_id FROM admins;")
        for row in cur.fetchall():
            f.write(f"{row[0]}\n")
        cur.close()
        conn.close()
        f.write("\nکاربران:\n")
        for u in list_users():
            f.write(f"{u[0]}\t{u[3] or u[1]}\t@{u[2]}\n")
    return filename

async def backup_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("دسترسی فقط برای ادمین‌ها.", reply_markup=user_keyboard())
        return
    xlsx = create_total_attendance_excel()
    txt = create_users_admins_txt()
    await update.message.reply_document(document=InputFile(xlsx), filename="all_attendance.xlsx")
    await update.message.reply_document(document=InputFile(txt), filename="users_admins.txt")
    await update.message.reply_text("✅ بکاپ ارسال شد.", reply_markup=admin_keyboard())

# -------------- ستاپ و اجرای بات --------------
def main():
    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("setname", setname_command))
    app.add_handler(CommandHandler("report_month", report_month_command))
    app.add_handler(CommandHandler("backup", backup_command))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

    logging.info("Bot Started ...")
    app.run_polling()

if __name__ == "__main__":
    main()
