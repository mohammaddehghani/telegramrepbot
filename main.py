import os
import logging
import psycopg2
import datetime
import jdatetime
import openpyxl
from telegram import (
    Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardRemove, InputFile,
)
from telegram.ext import (
    Application, ContextTypes, CommandHandler, CallbackQueryHandler,
)
import pytz

logging.basicConfig(level=logging.INFO)

# خواندن پارامترها از محیط (نیازی به dotenv نیست)
BOT_TOKEN = os.environ.get("BOT_TOKEN")
DATABASE_URL = os.environ.get("DATABASE_URL")
SUPER_ADMIN = os.environ.get("SUPER_ADMIN")

if not BOT_TOKEN or not DATABASE_URL or not SUPER_ADMIN:
    raise Exception(
        "متغیرهای محیطی BOT_TOKEN و DATABASE_URL و SUPER_ADMIN را در پنل لیارا تعریف کنید."
    )

SUPER_ADMIN = int(SUPER_ADMIN)

####=== DATABASE ===####
def get_db():
    return psycopg2.connect(DATABASE_URL, sslmode="require")

def ensure_user(user):
    conn = get_db(); cur = conn.cursor()
    cur.execute("INSERT INTO users (user_id, full_name, username, display_name) VALUES (%s, %s, %s, %s) ON CONFLICT (user_id) DO NOTHING;",
                (user.id, f"{user.first_name or ''} {user.last_name or ''}".strip(), user.username or '', f"{user.first_name or ''} {user.last_name or ''}".strip()))
    conn.commit()
    cur.close(); conn.close()

def add_admin(user_id):
    conn = get_db(); cur = conn.cursor()
    cur.execute("INSERT INTO admins (user_id) VALUES (%s) ON CONFLICT DO NOTHING;", (user_id,))
    conn.commit()
    cur.close(); conn.close()

def is_admin(user_id):
    conn = get_db(); cur = conn.cursor()
    cur.execute("SELECT 1 FROM admins WHERE user_id = %s;", (user_id,))
    res = cur.fetchone()
    cur.close(); conn.close()
    return user_id == SUPER_ADMIN or (res is not None)

def list_admins():
    conn = get_db(); cur = conn.cursor()
    cur.execute("SELECT user_id FROM admins;")
    result = [row[0] for row in cur.fetchall()]
    cur.close(); conn.close()
    return result

def list_users():
    conn = get_db(); cur = conn.cursor()
    cur.execute("SELECT user_id, full_name, username, display_name FROM users ORDER BY user_id;")
    users = cur.fetchall()
    cur.close(); conn.close()
    return users

def set_display_name(user_id, new_name):
    conn = get_db(); cur = conn.cursor()
    cur.execute("UPDATE users SET display_name=%s WHERE user_id=%s;", (new_name, user_id))
    conn.commit()
    cur.close(); conn.close()

def save_attendance(user_id, status):
    conn = get_db(); cur = conn.cursor()
    now = get_iran_now()
    cur.execute("INSERT INTO attendance (user_id, status, timestamp) VALUES (%s, %s, %s);", (user_id, status, now))
    conn.commit()
    cur.close(); conn.close()

def fetch_attendance(user_id=None, start=None, end=None):
    conn = get_db(); cur = conn.cursor()
    q = "SELECT user_id, status, timestamp FROM attendance"
    p = []
    if user_id is not None or start or end:
        q += " WHERE"
    if user_id is not None:
        q += " user_id=%s"
        p.append(user_id)
    if start:
        if p: q += " AND"
        q += " timestamp >= %s"
        p.append(start)
    if end:
        if p: q += " AND"
        q += " timestamp <= %s"
        p.append(end)
    q += " ORDER BY timestamp"
    cur.execute(q, tuple(p))
    result = cur.fetchall()
    cur.close(); conn.close()
    return result

####=== JALALI & TEHRAN TIME ===####
def get_iran_now():
    return datetime.datetime.now(pytz.timezone('Asia/Tehran'))

def to_shamsi(dateobj):
    s = jdatetime.datetime.fromgregorian(datetime=dateobj.astimezone(pytz.timezone('Asia/Tehran')))
    return s.strftime('%Y/%m/%d'), s.strftime('%H:%M:%S')

def get_display_name(user_id):
    users = list_users()
    for u in users:
        if u[0] == user_id:
            return u[3] or u[1] or str(user_id)
    return str(user_id)

####=== COMMANDS ===####
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    ensure_user(update.effective_user)
    add_admin(SUPER_ADMIN)
    await update.message.reply_text('سلام! برای ثبت حضور یا دریافت گزارش‌ها از دکمه‌های زیر استفاده کنید.', reply_markup=main_menu())

def main_menu():
    keyboard = [
        [InlineKeyboardButton("ثبت ورود", callback_data='enter')],
        [InlineKeyboardButton("ثبت خروج", callback_data='exit')],
        [InlineKeyboardButton("گزارش روزانه", callback_data='my_daily')],
        [InlineKeyboardButton("گزارش ماهانه", callback_data='my_monthly')],
    ]
    return InlineKeyboardMarkup(keyboard)

async def handle_buttons(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    ensure_user(query.from_user)
    if query.data == 'enter':
        save_attendance(query.from_user.id, 'enter')
        await query.delete_message()
        await query.message.reply_text("✅ | ورود ثبت شد.")
    elif query.data == 'exit':
        save_attendance(query.from_user.id, 'exit')
        await query.delete_message()
        await query.message.reply_text("✅ | خروج ثبت شد.")
    elif query.data == 'my_daily':
        await query.delete_message()
        await send_report(query, context, period='day', user_id=query.from_user.id)
    elif query.data == 'my_monthly':
        await query.delete_message()
        await send_report(query, context, period='month', user_id=query.from_user.id)
    elif query.data == 'admin':
        if is_admin(query.from_user.id):
            await query.delete_message()
            await admin_menu(query, context)
        else:
            await query.answer("دسترسی فقط برای ادمین.")

async def send_report(query, context, period, user_id):
    now = get_iran_now()
    start, end = None, None
    if period == 'day':
        start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        end = now.replace(hour=23, minute=59, second=59)
        title = "گزارش روزانه"
    elif period == 'month':
        start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        end = now.replace(hour=23, minute=59, second=59)
        title = "گزارش ماه جاری"
    items = fetch_attendance(user_id=user_id, start=start, end=end)
    if not items:
        await query.message.reply_text(f"{title}\n📋 موردی ثبت نشده است.")
        return
    out = f"{title} ({get_display_name(user_id)})\n\n"
    for it in items:
        shdate, shtime = to_shamsi(it[2])
        out += f"{shdate} - {shtime} | {'ورود' if it[1]=='enter' else 'خروج'}\n"
    await query.message.reply_text(out)

### ADMIN PANEL HANDLER ###
def admin_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("لیست کاربران", callback_data='admin_users')],
        [InlineKeyboardButton("تعیین نام نمایشی", callback_data='admin_setname')],
        [InlineKeyboardButton("گزارش روزانه همه", callback_data='admin_daily_all')],
        [InlineKeyboardButton("گزارش ماهانه کاربر", callback_data='admin_month_one')],
        [InlineKeyboardButton("دریافت اکسل بکاپ", callback_data='admin_backup')],
    ])

async def admin_menu(query, context):
    await query.message.reply_text("پنل ادمین:", reply_markup=admin_keyboard())

async def admin_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("دسترسی فقط برای ادمین‌ها.")
        return
    await update.message.reply_text("پنل ادمین:", reply_markup=admin_keyboard())

async def admin_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if not is_admin(query.from_user.id):
        await query.answer("دسترسی فقط برای ادمین")
        return
    if query.data == 'admin_users':
        users = list_users()
        users_txt = ["لیست کاربران:\n"]
        for u in users:
            users_txt.append(f"{u[0]}\t{u[3] or u[1]}\t@{u[2]}")
        await query.message.reply_text("\n".join(users_txt))
    elif query.data == 'admin_setname':
        await query.message.reply_text("فرمت:\n /setname user_id نام جدید ")
    elif query.data == 'admin_daily_all':
        now = get_iran_now()
        start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        end = now.replace(hour=23, minute=59, second=59)
        allitems = fetch_attendance(start=start, end=end)
        users = {u[0]: u for u in list_users()}
        if not allitems:
            await query.message.reply_text("گزارشی ثبت نشده.")
            return
        out = []
        for it in allitems:
            name = users[it[0]][3] or users[it[0]][1]
            shdate, shtime = to_shamsi(it[2])
            out.append(f"{name}: {shdate} {shtime} {'ورود' if it[1]=='enter' else 'خروج'}")
        await query.message.reply_text('\n'.join(out))
    elif query.data == 'admin_month_one':
        await query.message.reply_text("فرمت:\n/report_month user_id")
    elif query.data == 'admin_backup':
        await query.message.reply_text("دریافت کل خروجی اکسل و فایل متنی ادمین‌ها/کاربران.\nفرمت:\n/backup")

async def setname_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("دسترسی فقط برای ادمین‌ها.")
        return
    try:
        user_id = int(context.args[0])
        new_name = ' '.join(context.args[1:])
        set_display_name(user_id, new_name)
        await update.message.reply_text("نام داخلی کاربر تغییر کرد ✅")
    except Exception as e:
        await update.message.reply_text("فرمت صحیح: /setname user_id نام جدید")

async def report_month_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("دسترسی فقط برای ادمین‌ها.")
        return
    try:
        user_id = int(context.args[0])
        now = get_iran_now()
        start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        end = now.replace(hour=23, minute=59, second=59)
        items = fetch_attendance(user_id=user_id, start=start, end=end)
        if not items:
            await update.message.reply_text("بدون داده.")
            return
        out = [f"گزارش ماه جاری ({get_display_name(user_id)}):"]
        for it in items:
            shdate, shtime = to_shamsi(it[2])
            out.append(f"{shdate} {shtime} {'ورود' if it[1]=='enter' else 'خروج'}")
        await update.message.reply_text('\n'.join(out))
    except Exception as e:
        await update.message.reply_text("فرمت صحیح: /report_month user_id")

### BACKUP ###
def create_total_attendance_excel():
    filename = "/tmp/all_attendance.xlsx"
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(['user_id', 'نام داخلی', 'full_name', 'status', 'تاریخ شمسی', 'ساعت'])
    allusers = {u[0]: u for u in list_users()}
    allitems = fetch_attendance()
    for it in allitems:
        user = allusers.get(it[0])
        display_name = user[3] if user else '-'
        full_name = user[1] if user else '-'
        shdate, shtime = to_shamsi(it[2])
        ws.append([it[0], display_name, full_name, 'ورود' if it[1]=='enter' else 'خروج', shdate, shtime])
    wb.save(filename)
    return filename

def create_users_admins_txt():
    filename = "/tmp/users_admins.txt"
    with open(filename, "w", encoding="utf8") as f:
        f.write("ادمین‌ها:\n")
        for admin in list_admins():
            f.write(str(admin) + "\n")
        f.write("\nکاربران:\n")
        for u in list_users():
            f.write(f"{u[0]}\t{u[3] or u[1]}\t@{u[2]}\n")
    return filename

async def backup_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("دسترسی فقط برای ادمین‌ها.")
        return
    xlsx = create_total_attendance_excel()
    txt = create_users_admins_txt()
    await update.message.reply_document(document=InputFile(xlsx), filename="all_attendance.xlsx")
    await update.message.reply_document(document=InputFile(txt), filename="users_admins.txt")
    await update.message.reply_text("بکاپ کامل ارسال شد ✅")

####=== SETUP APP ===####
def main():
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("admin", admin_command))
    app.add_handler(CommandHandler("setname", setname_command))
    app.add_handler(CommandHandler("report_month", report_month_command))
    app.add_handler(CommandHandler("backup", backup_command))
    app.add_handler(CallbackQueryHandler(handle_buttons, pattern='^(enter|exit|my_daily|my_monthly|admin)$'))
    app.add_handler(CallbackQueryHandler(admin_handler, pattern='^admin_'))
    logging.info("Bot Started ...")
    app.run_polling()

if __name__ == "__main__":
    main()
