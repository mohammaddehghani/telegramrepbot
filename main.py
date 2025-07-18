import os
import psycopg2
from psycopg2 import sql
from telegram import (
    Update, ReplyKeyboardMarkup, KeyboardButton
)
from telegram.ext import (
    ApplicationBuilder, CommandHandler, MessageHandler, filters, ContextTypes
)
from datetime import datetime, timedelta
import jdatetime

BOT_TOKEN = os.environ['BOT_TOKEN']
DATABASE_URL = os.environ['DATABASE_URL']

# === اتصال به دیتابیس ===
def get_conn():
    return psycopg2.connect(DATABASE_URL)

# === ایجاد جداول لازم در اولین اجرا ===
def init_db():
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
            CREATE TABLE IF NOT EXISTS attendance (
                id SERIAL PRIMARY KEY,
                user_id BIGINT NOT NULL,
                username TEXT,
                full_name TEXT,
                action TEXT NOT NULL,
                at TIMESTAMP NOT NULL
            );
            """)
            cur.execute("""
            CREATE TABLE IF NOT EXISTS admins (
                user_id BIGINT PRIMARY KEY,
                name TEXT
            );
            """)
            conn.commit()
        # اضافه کردن ادمین اولیه در صورت نبود
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM admins;")
        if cur.fetchone()[0] == 0:
            cur.execute("INSERT INTO admins (user_id, name) VALUES (%s, %s)",
                        (125886032, 'Mohammad'))  # آیدی عددی و نام خودت
            conn.commit()

# === بررسی ادمین بودن ===
def is_admin(user_id):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT 1 FROM admins WHERE user_id=%s", (user_id,))
            return cur.fetchone() is not None

# === افزودن ادمین: تنها ادمین‌ها می‌توانند ===
async def addadmin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("دسترسی فقط برای ادمین.")
        return
    try:
        user_id = int(context.args[0])
        name = " ".join(context.args[1:])
        if not name:
            name = "Unknown"
    except:
        await update.message.reply_text("فرمت درست است: /addadmin [user_id] [name]")
        return
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("INSERT INTO admins (user_id, name) VALUES (%s, %s) ON CONFLICT DO NOTHING;", (user_id, name))
            conn.commit()
    await update.message.reply_text(f"ادمین جدید اضافه شد: {user_id} - {name}")

# === حذف ادمین ===
async def removeadmin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("دسترسی فقط برای ادمین.")
        return
    try:
        user_id = int(context.args[0])
    except:
        await update.message.reply_text("فرمت درست است: /removeadmin [user_id]")
        return
    if user_id == 125886032:
        await update.message.reply_text("ادمین موسس را نمی‌توان حذف کرد.")
        return
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM admins WHERE user_id=%s", (user_id,))
            conn.commit()
    await update.message.reply_text("ادمین حذف شد.")

# === لیست ادمین‌ها ===
async def listadmins(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("دسترسی فقط برای ادمین.")
        return
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT user_id, name FROM admins")
            admins = cur.fetchall()
    text = "\n".join([f"{uid} - {name}" for uid, name in admins])
    await update.message.reply_text(text or "ادمینی وجود ندارد.")

# === کیبورد کاربر ===
def get_keyboard(isadmin: bool):
    buttons = [
      [KeyboardButton('ورود 👋'), KeyboardButton('خروج 👋')],
      [KeyboardButton('گزارش امروز 📋'), KeyboardButton('وضعیت من ℹ️')]
    ]
    if isadmin:
        buttons.append([KeyboardButton('گزارش کلی امروز (ادمین)📊')])
    return ReplyKeyboardMarkup(buttons, resize_keyboard=True)

# === هندلر های ربات ===
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    isadmin = is_admin(user.id)
    text = f"سلام {user.first_name}!\nبه ربات حضور و غیاب خوش‌اومدی."
    await update.message.reply_text(text, reply_markup=get_keyboard(isadmin))

async def enter(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    now = datetime.now() + timedelta(hours=3, minutes=30)  # Iran time
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
            INSERT INTO attendance (user_id, username, full_name, action, at)
            VALUES (%s,%s,%s,%s,%s)
            """, (user.id, user.username, user.full_name, "enter", now))
            conn.commit()
    shamsi = jdatetime.datetime.fromgregorian(datetime=now)
    await update.message.reply_text(f"ورود ثبت شد 🟢\n{shamsi.strftime('%Y/%m/%d %H:%M')}")

async def exit(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    now = datetime.now() + timedelta(hours=3, minutes=30)  # Iran time
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
            INSERT INTO attendance (user_id, username, full_name, action, at)
            VALUES (%s,%s,%s,%s,%s)
            """, (user.id, user.username, user.full_name, "exit", now))
            conn.commit()
    shamsi = jdatetime.datetime.fromgregorian(datetime=now)
    await update.message.reply_text(f"خروج ثبت شد 🔴\n{shamsi.strftime('%Y/%m/%d %H:%M')}")

async def report(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    now = datetime.now() + timedelta(hours=3, minutes=30)
    today = now.date()
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
            SELECT action, at FROM attendance
            WHERE user_id=%s AND at::date=%s
            ORDER BY at
            """, (user.id, today))
            logs = cur.fetchall()
    shamsi_today = jdatetime.date.fromgregorian(date=today).strftime('%Y/%m/%d')
    lines = [f"📋 **گزارش امروز:** {shamsi_today}\n"]
    for action, at_ in logs:
        jm = jdatetime.datetime.fromgregorian(datetime=at_)
        fa_time = jm.strftime('%H:%M')
        lines.append(f"▫️ {action=='enter' and 'ورود' or 'خروج'} : {fa_time}")
    text = "\n".join(lines) if lines else "ورود و خروجی ثبت نشده است."
    await update.message.reply_text(text)

async def status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    now = datetime.now() + timedelta(hours=3, minutes=30)
    today = now.date()
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
            SELECT action, at FROM attendance
            WHERE user_id=%s AND at::date=%s
            ORDER BY at DESC LIMIT 1
            """, (user.id, today))
            log = cur.fetchone()
    if not log:
        await update.message.reply_text("هنوز ورود یا خروج را ثبت نکرده‌اید.")
        return
    action, at_ = log
    jm = jdatetime.datetime.fromgregorian(datetime=at_)
    msg = f"آخرین ثبت امروز: {action=='enter' and 'ورود' or 'خروج'} \nدر {jm.strftime('%H:%M')}"
    await update.message.reply_text(msg)

async def admin_report(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not is_admin(user.id):
        await update.message.reply_text("دسترسی فقط برای ادمین.")
        return
    now = datetime.now() + timedelta(hours=3, minutes=30)
    today = now.date()
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
            SELECT username, full_name, action, at FROM attendance
            WHERE at::date=%s
            ORDER BY at
            """, (today,))
            logs = cur.fetchall()
    shamsi_today = jdatetime.date.fromgregorian(date=today).strftime('%Y/%m/%d')
    lines = [f"📊 گزارش کلی امروز: {shamsi_today}\n"]
    for username, full_name, action, at_ in logs:
        fa_time = jdatetime.datetime.fromgregorian(datetime=at_).strftime('%H:%M')
        lines.append(f"👤{full_name} (@{username}): {'ورود' if action=='enter' else 'خروج'}\n {fa_time}")
    text = "\n".join(lines) if lines else "ورود/خروج برای امروز ثبت نشده."
    await update.message.reply_text(text)

# === دکمه‌های کیبورد دریافت و هدایت ===
async def handle_buttons(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    if text == 'ورود 👋':
        return await enter(update, context)
    elif text == 'خروج 👋':
        return await exit(update, context)
    elif text == 'گزارش امروز 📋':
        return await report(update, context)
    elif text == 'وضعیت من ℹ️':
        return await status(update, context)
    elif text.startswith('گزارش کلی'):
        return await admin_report(update, context)
    else:
        await update.message.reply_text('دستور نامعتبر است.')

# === اجرای ربات ===
def main():
    init_db()
    app = ApplicationBuilder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler('start', start))
    app.add_handler(CommandHandler('enter', enter))
    app.add_handler(CommandHandler('exit', exit))
    app.add_handler(CommandHandler('report', report))
    app.add_handler(CommandHandler('status', status))
    app.add_handler(CommandHandler('admin_report', admin_report))
    app.add_handler(CommandHandler('addadmin', addadmin))
    app.add_handler(CommandHandler('removeadmin', removeadmin))
    app.add_handler(CommandHandler('listadmins', listadmins))
    app.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), handle_buttons))
    app.run_polling()

if __name__ == '__main__':
    main()
