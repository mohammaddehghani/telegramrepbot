import os
import logging
import psycopg2
import datetime
import jdatetime
import openpyxl
import pytz

from dotenv import load_dotenv
from telegram import (
    Update,
    ReplyKeyboardMarkup,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InputFile,
)
from telegram.ext import (
    Application,
    ContextTypes,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ConversationHandler,
    filters,
)

# ===== تنظیمات اولیه =====
load_dotenv()
logging.basicConfig(
    format="%(asctime)s - %(levelname)s - %(message)s", level=logging.INFO
)

BOT_TOKEN    = os.getenv("BOT_TOKEN")
DATABASE_URL = os.getenv("DATABASE_URL")
SUPER_ADMIN  = int(os.getenv("SUPER_ADMIN", "0"))

# ===== حالت‌های Conversation =====
MONTH_INPUT, SCOPE_CHOICE, GET_PERSON_ID = range(3)

# ===== توابع DB =====
def get_db():
    return psycopg2.connect(DATABASE_URL, sslmode="disable")

def ensure_user(user):
    conn = get_db(); cur = conn.cursor()
    cur.execute("SELECT 1 FROM users WHERE user_id=%s;", (user.id,))
    if not cur.fetchone():
        # تولید 4 رقمی employee_id
        cur.execute("SELECT MAX(employee_id) FROM users;")
        row = cur.fetchone()[0]
        next_id = int(row) + 1 if row and row.isdigit() else 1
        emp_code = f"{next_id:04d}"
        full = f"{user.first_name or ''} {user.last_name or ''}".strip()
        disp = full or user.username or str(user.id)
        cur.execute("""
            INSERT INTO users (user_id, full_name, username, display_name, employee_id)
            VALUES (%s,%s,%s,%s,%s)
        """, (user.id, full, user.username or "", disp, emp_code))
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
    cur.execute("SELECT user_id, display_name, username, employee_id FROM users ORDER BY employee_id;")
    rows = cur.fetchall()
    cur.close(); conn.close()
    return rows

def get_user_id_by_emp(emp_code):
    conn = get_db(); cur = conn.cursor()
    cur.execute("SELECT user_id FROM users WHERE employee_id=%s;", (emp_code,))
    row = cur.fetchone()
    cur.close(); conn.close()
    return row[0] if row else None

def save_attendance(user_id, status):
    conn = get_db(); cur = conn.cursor()
    now = datetime.datetime.now(pytz.timezone("Asia/Tehran"))
    cur.execute("""
        INSERT INTO attendance (user_id, status, "timestamp")
        VALUES (%s,%s,%s)
    """, (user_id, status, now))
    conn.commit(); cur.close(); conn.close()

def fetch_attendance(user_id=None, start=None, end=None):
    conn = get_db(); cur = conn.cursor()
    q = 'SELECT user_id, status, "timestamp" FROM attendance'
    clauses, params = [], []
    if user_id is not None:
        clauses.append("user_id=%s"); params.append(user_id)
    if start:
        clauses.append('"timestamp">=%s'); params.append(start)
    if end:
        clauses.append('"timestamp"<=%s'); params.append(end)
    if clauses:
        q += " WHERE " + " AND ".join(clauses)
    q += ' ORDER BY "timestamp"'
    cur.execute(q, tuple(params))
    rows = cur.fetchall()
    cur.close(); conn.close()
    return rows

# ===== توابع کمکی تاریخ =====
def to_shamsi(dt):
    sh = jdatetime.datetime.fromgregorian(datetime=dt.astimezone(pytz.timezone("Asia/Tehran")))
    return sh.strftime("%Y/%m/%d"), sh.strftime("%H:%M:%S")

def parse_year_month(text):
    # ورودی: YYYY-MM
    y, m = map(int, text.split("-"))
    tz = pytz.timezone("Asia/Tehran")
    start = tz.localize(datetime.datetime(y, m, 1, 0, 0, 0))
    if m == 12:
        nxt = tz.localize(datetime.datetime(y+1, 1, 1, 0, 0, 0))
    else:
        nxt = tz.localize(datetime.datetime(y, m+1, 1, 0, 0, 0))
    end = nxt - datetime.timedelta(seconds=1)
    return start, end

# ===== کیبوردها =====
def main_menu(admin_view=False):
    kb = [["ثبت ورود", "ثبت خروج"], ["گزارش روزانه", "گزارش ماهانه"]]
    if admin_view:
        kb.append(["پنل ادمین"])
    return ReplyKeyboardMarkup(kb, resize_keyboard=True)

def admin_menu():
    kb = [
        ["لیست کاربران", "تعیین نام نمایشی"],
        ["گزارش روزانه همه", "گزارش ماهانه"],
        ["دریافت بکاپ اکسل", "بازگشت"]
    ]
    return ReplyKeyboardMarkup(kb, resize_keyboard=True)

def back_kb():
    return ReplyKeyboardMarkup([["لغو"]], resize_keyboard=True)

# ===== هندلرها =====
async def start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    u = update.effective_user
    ensure_user(u)
    # ثبت SuperAdmin در جدول admins
    if u.id == SUPER_ADMIN:
        conn = get_db(); cur = conn.cursor()
        cur.execute("INSERT INTO admins(user_id) VALUES(%s) ON CONFLICT DO NOTHING;", (u.id,))
        conn.commit(); cur.close(); conn.close()
    admin_view = is_admin(u.id)
    await update.message.reply_text(
        f"سلام {u.first_name}!",
        reply_markup=main_menu(admin_view)
    )

async def handle_text(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    txt = update.message.text.strip()
    u   = update.effective_user
    ensure_user(u)
    admin_view = is_admin(u.id)

    # لغو
    if txt == "لغو":
        kb = admin_menu() if admin_view else main_menu(admin_view)
        await update.message.reply_text("✅ عملیات لغو شد.", reply_markup=kb)
        return

    # ثبت ورود/خروج
    if txt == "ثبت ورود":
        save_attendance(u.id, "enter")
        await update.message.reply_text("✅ ورود ثبت شد.", reply_markup=main_menu(admin_view))
        return
    if txt == "ثبت خروج":
        save_attendance(u.id, "exit")
        await update.message.reply_text("✅ خروج ثبت شد.", reply_markup=main_menu(admin_view))
        return

    # گزارش روزانه (شخصی)
    if txt == "گزارش روزانه":
        now = datetime.datetime.now(pytz.timezone("Asia/Tehran"))
        start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        end   = now.replace(hour=23, minute=59, second=59)
        items = fetch_attendance(user_id=u.id, start=start, end=end)
        if not items:
            await update.message.reply_text("📋 موردی ثبت نشده.", reply_markup=main_menu(admin_view))
            return
        lines = [f"📅 گزارش روزانه {jdatetime.date.today().strftime('%Y/%m/%d')}"]
        for _, status, ts in items:
            date_fa, time_fa = to_shamsi(ts)
            lines.append(f"{time_fa} — {'ورود' if status=='enter' else 'خروج'}")
        await update.message.reply_text("\n".join(lines), reply_markup=main_menu(admin_view))
        return

    # پنل ادمین
    if txt == "پنل ادمین" and admin_view:
        await update.message.reply_text("📋 پنل ادمین:", reply_markup=admin_menu())
        return

    # لیست کاربران (ادمین)
    if txt == "لیست کاربران" and admin_view:
        users = list_users()
        lines = ["👥 لیست کاربران:", "ID - EmpCode - DisplayName - @username", "---"]
        for uid, disp, uname, emp in users:
            lines.append(f"{uid} - {emp} - {disp} - @{uname}")
        await update.message.reply_text("\n".join(lines), reply_markup=admin_menu())
        return

    # دریافت بکاپ اکسل (ادمین)
    if txt == "دریافت بکاپ اکسل" and admin_view:
        await update.message.reply_text("در حال آماده‌سازی بکاپ اکسل ...")
        path = "/tmp/all_backup.xlsx"
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.append(["UserID", "EmpCode", "DisplayName", "Date", "Time", "Status"])
        users_map = {u[0]:(u[3],u[1]) for u in list_users()}
        for uid, status, ts in fetch_attendance():
            emp, disp = users_map.get(uid, ("-", "-"))
            date_fa, time_fa = to_shamsi(ts)
            ws.append([uid, emp, disp, date_fa, time_fa, 'ورود' if status=='enter' else 'خروج'])
        wb.save(path)
        await update.message.reply_document(InputFile(path), filename="all_backup.xlsx")
        os.remove(path)
        await update.message.reply_text("✅ بکاپ ارسال شد.", reply_markup=admin_menu())
        return

    # تعیین نام نمایشی (ادمین) — فقط دستور /setname پشتیبانی می‌شود
    if txt == "تعیین نام نمایشی" and admin_view:
        await update.message.reply_text("برای تغییر نام نمایشی از دستور زیر استفاده کن:\n\n/setname user_id نام جدید")
        return

    # هر چیز دیگر
    await update.message.reply_text("لطفاً از دکمه‌ها استفاده کن.", reply_markup=main_menu(admin_view))

# ===== Conversation: گزارش ماهانه =====
async def monthly_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    ensure_user(update.effective_user)
    await update.message.reply_text(
        "✅ لطفاً ماه و سال را به صورت `YYYY-MM` وارد کن.",
        reply_markup=back_kb()
    )
    return MONTH_INPUT

async def monthly_get_month(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    if text == "لغو":
        return await monthly_cancel(update, ctx)
    try:
        start, end = parse_year_month(text)
        ctx.user_data['monthly'] = {"start": start, "end": end, "label": text}
        kb = ReplyKeyboardMarkup([["همه کاربران", "کاربر انتخابی"], ["لغو"]], resize_keyboard=True)
        await update.message.reply_text("✅ محدوده:\nهمه کاربران یا کاربر انتخابی؟", reply_markup=kb)
        return SCOPE_CHOICE
    except:
        await update.message.reply_text("❌ فرمت اشتباه است. مثال: `2024-08`", reply_markup=back_kb())
        return MONTH_INPUT

async def monthly_get_scope(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    txt = update.message.text.strip()
    if txt == "لغو":
        return await monthly_cancel(update, ctx)

    rep = ctx.user_data['monthly']
    start, end, label = rep["start"], rep["end"], rep["label"]

    # همه کاربران
    if txt == "همه کاربران":
        items = fetch_attendance(start=start, end=end)
        if not items:
            await update.message.reply_text("📋 موردی در این ماه ثبت نشده.", reply_markup=main_menu(is_admin(update.effective_user.id)))
            return ConversationHandler.END
        # گزارش متنی
        users_map = {u[0]:(u[3],u[1]) for u in list_users()}
        lines = [f"📅 گزارش ماهانه {label} — همه کاربران"]
        for uid, status, ts in items:
            emp, disp = users_map.get(uid, ("-", "-"))
            date_fa, time_fa = to_shamsi(ts)
            lines.append(f"{emp} ({disp}): {date_fa} {time_fa} {'ورود' if status=='enter' else 'خروج'}")
        await update.message.reply_text("\n".join(lines), reply_markup=main_menu(is_admin(update.effective_user.id)))
        # دکمه دریافت اکسل
        kb = InlineKeyboardMarkup([[InlineKeyboardButton("دریافت اکسل", callback_data="monthly_excel")]])
        await update.message.reply_text("برای دریافت اکسل کلیک کن:", reply_markup=kb)
        return ConversationHandler.END

    # کاربر انتخابی
    if txt == "کاربر انتخابی":
        await update.message.reply_text("✅ کد پرسنلی را وارد کن:", reply_markup=back_kb())
        return GET_PERSON_ID

    # اشتباه انتخاب
    await update.message.reply_text("❌ لطفاً «همه کاربران» یا «کاربر انتخابی» را انتخاب کن.", reply_markup=back_kb())
    return SCOPE_CHOICE

async def monthly_get_person_id(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    txt = update.message.text.strip()
    if txt == "لغو":
        return await monthly_cancel(update, ctx)

    uid = get_user_id_by_emp(txt)
    if not uid:
        await update.message.reply_text("❌ این کد پرسنلی وجود ندارد. دوباره تلاش کن.", reply_markup=back_kb())
        return GET_PERSON_ID

    rep = ctx.user_data['monthly']
    start, end, label = rep["start"], rep["end"], rep["label"]
    rep["target"] = uid

    items = fetch_attendance(user_id=uid, start=start, end=end)
    if not items:
        await update.message.reply_text("📋 برای این کاربر موردی ثبت نشده.", reply_markup=main_menu(is_admin(update.effective_user.id)))
        return ConversationHandler.END

    # گزارش متنی شخصی
    lines = [f"📅 گزارش ماهانه {label} — کد {txt}"]
    for _, status, ts in items:
        date_fa, time_fa = to_shamsi(ts)
        lines.append(f"{date_fa} {time_fa} {'ورود' if status=='enter' else 'خروج'}")
    await update.message.reply_text("\n".join(lines), reply_markup=main_menu(is_admin(update.effective_user.id)))
    # دکمه دریافت اکسل
    kb = InlineKeyboardMarkup([[InlineKeyboardButton("دریافت اکسل", callback_data="monthly_excel")]])
    await update.message.reply_text("برای دریافت اکسل کلیک کن:", reply_markup=kb)
    return ConversationHandler.END

async def monthly_cancel(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    kb = admin_menu() if is_admin(update.effective_user.id) else main_menu(is_admin(update.effective_user.id))
    await update.message.reply_text("✅ عملیات گزارش ماهانه لغو شد.", reply_markup=kb)
    return ConversationHandler.END

async def monthly_excel_cb(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    rep = ctx.user_data.get('monthly')
    if not rep:
        await update.callback_query.message.reply_text("❌ خطا در تولید گزارش.")
        return

    start, end, label = rep["start"], rep["end"], rep["label"]
    # مشخص کنید شخصی یا همه
    if "target" in rep:
        items = fetch_attendance(user_id=rep["target"], start=start, end=end)
    else:
        items = fetch_attendance(start=start, end=end)

    fname = f"/tmp/monthly_{label.replace('-','')}.xlsx"
    wb = openpyxl.Workbook(); ws = wb.active

    if "target" in rep:
        ws.append(["تاریخ", "ساعت", "وضعیت"])
        for _, status, ts in items:
            d, t = to_shamsi(ts)
            ws.append([d, t, 'ورود' if status=='enter' else 'خروج'])
    else:
        ws.append(["EmpCode", "DisplayName", "تاریخ", "ساعت", "وضعیت"])
        users_map = {u[0]:(u[3],u[1]) for u in list_users()}
        for uid, status, ts in items:
            emp, disp = users_map.get(uid, ("-", "-"))
            d, t = to_shamsi(ts)
            ws.append([emp, disp, d, t, 'ورود' if status=='enter' else 'خروج'])

    wb.save(fname)
    await update.callback_query.message.reply_document(InputFile(fname), filename=os.path.basename(fname))
    os.remove(fname)
    # پاکسازی session
    ctx.user_data.pop('monthly', None)

# ===== راه‌اندازی بات =====
def main():
    app = Application.builder().token(BOT_TOKEN).build()

    # ConversationHandler برای گزارش ماهانه
    monthly_conv = ConversationHandler(
        entry_points=[MessageHandler(filters.Regex("^گزارش ماهانه$"), monthly_start)],
        states={
            MONTH_INPUT:    [MessageHandler(filters.TEXT & ~filters.COMMAND, monthly_get_month)],
            SCOPE_CHOICE:   [MessageHandler(filters.TEXT & ~filters.COMMAND, monthly_get_scope)],
            GET_PERSON_ID:  [MessageHandler(filters.TEXT & ~filters.COMMAND, monthly_get_person_id)],
        },
        fallbacks=[MessageHandler(filters.Regex("^لغو$"), monthly_cancel)],
    )

    app.add_handler(CommandHandler("start", start))
    app.add_handler(monthly_conv)
    app.add_handler(CallbackQueryHandler(monthly_excel_cb, pattern="^monthly_excel$"))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

    logging.info("Bot is running...")
    app.run_polling()

if __name__ == "__main__":
    main()
