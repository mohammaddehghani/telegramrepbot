import os
import psycopg2
from datetime import datetime, timedelta
import jdatetime
from telegram import (
    InlineKeyboardButton, InlineKeyboardMarkup, Update, InputFile
)
from telegram.ext import (
    ApplicationBuilder, CommandHandler, CallbackQueryHandler, MessageHandler, ContextTypes, filters
)
import openpyxl

BOT_TOKEN = os.environ['BOT_TOKEN']
DATABASE_URL = os.environ['DATABASE_URL']
FOUNDER_ID = 125886032  # ایدی محمد به عنوان موسس

#=================== اتصال به دیتابیس ======================
def get_conn():
    return psycopg2.connect(DATABASE_URL)

def init_db():
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
            CREATE TABLE IF NOT EXISTS attendance (
                id SERIAL PRIMARY KEY,
                user_id BIGINT,
                username TEXT,
                full_name TEXT,
                action TEXT,
                at TIMESTAMP
            );
            """)
            cur.execute("""
            CREATE TABLE IF NOT EXISTS admins (
                user_id BIGINT PRIMARY KEY,
                name TEXT
            );
            """)
            cur.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id BIGINT PRIMARY KEY,
                username TEXT,
                full_name TEXT,
                display_name TEXT
            );
            """)
            conn.commit()
            # ادمین اصلی اگر وجود ندارد اضافه شود
            cur.execute("SELECT COUNT(*) FROM admins;")
            if cur.fetchone()[0] == 0:
                cur.execute("INSERT INTO admins (user_id, name) VALUES (%s, %s) ON CONFLICT DO NOTHING;", (FOUNDER_ID, 'Mohammad'))
                conn.commit()

def record_user(user):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
            INSERT INTO users (user_id, username, full_name, display_name)
            VALUES (%s, %s, %s, %s)
            ON CONFLICT (user_id) DO UPDATE 
              SET username=EXCLUDED.username, full_name=EXCLUDED.full_name;
            """, (user.id, user.username, user.full_name, user.full_name))
            conn.commit()

def is_admin(user_id):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT 1 FROM admins WHERE user_id=%s", (user_id,))
            return cur.fetchone() is not None

def get_iran_now():
    return datetime.utcnow() + timedelta(hours=3, minutes=30)

def to_shamsi(dt):
    return jdatetime.datetime.fromgregorian(datetime=dt)

def get_display_name(user_id):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT display_name FROM users WHERE user_id=%s", (user_id,))
            x = cur.fetchone()
    return x[0] if x else ''

async def delete_message(msg):
    try:
        await msg.delete()
    except:
        pass

#================== بخش کاربری ============================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    record_user(user)
    await show_main_menu(update, context)

async def show_main_menu(update, context):
    keyboard = [
        [InlineKeyboardButton("ثبت ورود 👋", callback_data="enter"),
         InlineKeyboardButton("ثبت خروج 👋", callback_data="exit")],
        [InlineKeyboardButton("گزارش روزانه 📅", callback_data="my_daily_report"),
         InlineKeyboardButton("گزارش ماهانه 📆", callback_data="my_monthly_report")]
    ]
    msg = await update.message.reply_text(
        "لطفاً گزینه خود را انتخاب کنید:",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

async def main_menu_btn(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await show_main_menu(update, context)
    await delete_message(update.message)

async def button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    record_user(user)
    query = update.callback_query
    action = query.data

    if action == "enter":
        await save_action(user, "enter", query)
    elif action == "exit":
        await save_action(user, "exit", query)
    elif action == "my_daily_report":
        await daily_report(query, user.id)
    elif action == "my_monthly_report":
        await monthly_report(query, user.id)
    elif action.startswith("admin_"):
        await handle_admin_actions(query, context)
    elif action.startswith("show_day_"):
        user_id, date_str = action.replace("show_day_","").split("_")
        await specific_user_daily_report(query, int(user_id), date_str)
    elif action == "admin_backup_confirm":
        await handle_backup(query, context)
    await query.answer()

async def save_action(user, action, query):
    now = get_iran_now()
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
            INSERT INTO attendance (user_id, username, full_name, action, at)
            VALUES (%s, %s, %s, %s, %s)
            """, (user.id, user.username, user.full_name, action, now))
            conn.commit()
    msg = f"ثبت {'ورود' if action=='enter' else 'خروج'} انجام شد✅ \n{to_shamsi(now).strftime('%Y/%m/%d ساعت %H:%M')}\n"
    # نمایش لیست ورود/خروج امروز
    recs = daily_action_list(user.id)
    msg += "\n".join([f"▫️{('ورود' if r[0]=='enter' else 'خروج')} {to_shamsi(r[1]).strftime('%H:%M')}" for r in recs])
    await query.message.delete()
    await query.message.reply_text(msg, reply_markup=main_menu_keyboard_inline(user.id))

def daily_action_list(user_id):
    today = get_iran_now().date()
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
            SELECT action, at FROM attendance
            WHERE user_id=%s AND at::date=%s
            ORDER BY at
            """, (user_id, today))
            return cur.fetchall()

def main_menu_keyboard_inline(user_id):
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("ثبت ورود 👋", callback_data="enter"),
             InlineKeyboardButton("ثبت خروج 👋", callback_data="exit")],
            [InlineKeyboardButton("گزارش روزانه 📅", callback_data="my_daily_report"),
             InlineKeyboardButton("گزارش ماهانه 📆", callback_data="my_monthly_report")]
        ]
    )

async def daily_report(query, user_id):
    today = get_iran_now().date()
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
            SELECT action, at FROM attendance
            WHERE user_id=%s AND at::date=%s
            ORDER BY at
            """, (user_id, today))
            logs = cur.fetchall()
    shamsi = jdatetime.date.fromgregorian(date=today).strftime('%Y/%m/%d')
    name = get_display_name(user_id)
    lines = [f"🗓️ گزارش امروز {shamsi}\n"]
    for action, at_ in logs:
        jm = jdatetime.datetime.fromgregorian(datetime=at_)
        lines.append(f"▫️{'ورود' if action=='enter' else 'خروج'} ساعت {jm.strftime('%H:%M')}")
    text = "\n".join(lines) if logs else "امروز ورود یا خروج ثبت نشده."
    await query.message.delete()
    await query.message.reply_text(text, reply_markup=main_menu_keyboard_inline(user_id))

async def monthly_report(query, user_id):
    now = get_iran_now()
    month = jdatetime.datetime.fromgregorian(datetime=now).month
    year = jdatetime.datetime.fromgregorian(datetime=now).year
    first_gregorian = jdatetime.date(year, month, 1).togregorian()
    if month<12:
        next_gregorian = jdatetime.date(year, month+1, 1).togregorian()
    else:
        next_gregorian = jdatetime.date(year+1, 1, 1).togregorian()
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
            SELECT action, at FROM attendance
            WHERE user_id=%s AND at >= %s AND at < %s
            ORDER BY at
            """, (user_id, first_gregorian, next_gregorian))
            logs = cur.fetchall()
    per_day = {}
    for action, at_ in logs:
        shamsi = jdatetime.date.fromgregorian(date=at_.date())
        time_str = jdatetime.datetime.fromgregorian(datetime=at_).strftime('%H:%M')
        if shamsi not in per_day:
            per_day[shamsi] = []
        per_day[shamsi].append((action, time_str))
    text = f"📆 گزارش ماه {year}/{month}:\n"
    if logs:
        for day in sorted(per_day.keys()):
            text += f"\n-- {day.strftime('%Y/%m/%d')}\n"
            for action, t in per_day[day]:
                text += f"  ▫️ {'ورود' if action=='enter' else 'خروج'} {t}\n"
    else:
        text += "\nدر این ماه ورود/خروج ثبت نشده است."
    await query.message.delete()
    await query.message.reply_text(text, reply_markup=main_menu_keyboard_inline(user_id))

#================== مدیریت ویژه ادمین ===================

async def admin_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not is_admin(user.id):
        await update.message.reply_text("دسترسی فقط برای ادمین.")
        return
    keyboard = [
        [InlineKeyboardButton("لیست کاربران 👥", callback_data="admin_list_users")],
        [InlineKeyboardButton("گزارش روز کلی 👁", callback_data="admin_all_today")],
        [InlineKeyboardButton("گزارش روز هر فرد 🔎", callback_data="admin_day_person")],
        [InlineKeyboardButton("گزارش ماه هر فرد 📊", callback_data="admin_month_person")],
        [InlineKeyboardButton("بکاپ دیتابیس و لیست ادمین‌ها/کاربرها 🛡", callback_data="admin_backup_confirm")]
    ]
    await update.message.reply_text("پنل ادمین:", reply_markup=InlineKeyboardMarkup(keyboard))

async def handle_admin_actions(query, context):
    user = query.from_user
    if not is_admin(user.id):
        await query.message.reply_text("شما ادمین نیستید.")
        await query.message.delete()
        return
    act = query.data
    if act == "admin_list_users":
        await query.message.delete()
        await list_users(query)
    elif act == "admin_all_today":
        await query.message.delete()
        await admin_report_all_today(query)
    elif act == "admin_day_person":
        await query.message.delete()
        await admin_select_user_for_daily(query)
    elif act == "admin_month_person":
        await query.message.delete()
        await admin_select_user_for_monthly(query)
    elif act == "admin_backup_confirm":
        await query.message.delete()
        await confirm_backup(query, context)

async def list_users(query):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT user_id, display_name FROM users ORDER BY user_id;")
            all_users = cur.fetchall()
    msg = "لیست کاربران ثبت شده:\n"
    msg += "\n".join([f"{uid}: {dname}" for uid, dname in all_users])
    await query.message.reply_text(msg)

async def admin_report_all_today(query):
    today = get_iran_now().date()
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
            SELECT u.display_name, a.user_id, a.action, a.at
            FROM attendance a JOIN users u ON a.user_id=u.user_id
            WHERE a.at::date=%s
            ORDER BY a.at
            """, (today,))
            logs = cur.fetchall()
    if not logs:
        await query.message.reply_text("امروز ورود و خروجی ثبت نشده.")
        return
    msg = "گزارش کلی امروز:\n"
    for dname, uid, act, at_ in logs:
        shamsi_time = to_shamsi(at_)
        msg += f"{dname} ({uid}): {'ورود' if act=='enter' else 'خروج'} ساعت {shamsi_time.strftime('%H:%M')}\n"
    await query.message.reply_text(msg)

async def admin_select_user_for_daily(query):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT user_id, display_name FROM users")
            users = cur.fetchall()
    kb = [[InlineKeyboardButton(f"{name}", callback_data=f"admin_day_{uid}")] for uid,name in users]
    markup = InlineKeyboardMarkup(kb)
    await query.message.reply_text("انتخاب کاربر برای گزارش روز:", reply_markup=markup)

async def admin_select_user_for_monthly(query):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT user_id, display_name FROM users")
            users = cur.fetchall()
    kb = [[InlineKeyboardButton(f"{name}", callback_data=f"admin_month_{uid}")] for uid,name in users]
    markup = InlineKeyboardMarkup(kb)
    await query.message.reply_text("انتخاب کاربر برای گزارش ماه:", reply_markup=markup)

# ==== نمایش گزارش انتخابات ===
async def specific_user_daily_report(query, user_id, date_shamsi):
    y,m,d = map(int, date_shamsi.split('-'))
    dt = jdatetime.date(y,m,d).togregorian()
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
            SELECT action, at FROM attendance
            WHERE user_id=%s AND at::date=%s
            ORDER BY at
            """, (user_id, dt))
            logs = cur.fetchall()
    dname = get_display_name(user_id)
    text = f"گزارش روز {date_shamsi} برای {dname}:\n"
    if logs:
        for action, at_ in logs:
            jm = jdatetime.datetime.fromgregorian(datetime=at_)
            text += f"{'ورود' if action=='enter' else 'خروج'} {jm.strftime('%H:%M')}\n"
    else:
        text += "ورود یا خروجی ثبت نشده."
    await query.message.reply_text(text)

# ==== ادمین: گزارش روز و ماه هر فرد ===
async def admin_report_day_person(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # فعلاً فقط با اینلاین دکمه بالا پیاده‌سازی می‌شود
    await update.message.reply_text("از منوی ادمین و دکمه مربوط استفاده کنید.")

async def admin_report_month_person(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("از منوی ادمین و دکمه مربوط استفاده کنید.")

# ==== کاملترین اکسل و بکاپ =======
async def confirm_backup(query, context):
    admin_id = query.from_user.id
    backup_msg = await query.message.reply_text(
        "آیا مایل به تهیه بکاپ کامل دیتابیس هستید؟",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("بله، بکاپ بگیر", callback_data="admin_backup_confirm")],
            [InlineKeyboardButton("خیر", callback_data="ignore")]
        ])
    )

async def handle_backup(query, context):
    admin_id = query.from_user.id
    # اکسل کامل تمام حضور و غیاب
    xlsx_name = "[/mnt/data/all_attendance.xlsx"](https://gapgpt.app/media/code_interpreter/574d73a4-c1d1-4445-8bab-7827033b9575/all_attendance.xlsx%22)
    txt_name = "[/mnt/data/users_and_admins.txt"](https://gapgpt.app/media/code_interpreter/574d73a4-c1d1-4445-8bab-7827033b9575/users_and_admins.txt%22)
    await create_total_attendance_excel(xlsx_name)
    await create_users_admins_txt(txt_name)

    await query.message.reply_text("فایل‌های بکاپ آماده است:")
    await context.bot.send_document(admin_id, document=InputFile(xlsx_name))
    await context.bot.send_document(admin_id, document=InputFile(txt_name))
    await query.message.reply_text("تمام داده‌ها ارسال شد.")

def make_shamsi_excel(ws, logs):
    ws.append(["آیدی", "نام نمایشی", "نام تلگرام", "نوع", "تاریخ (شمسی)", "ساعت"])
    for uid, dname, uname, action, at_ in logs:
        shamsi = to_shamsi(at_)
        ws.append([
            uid, dname, uname, 
            "ورود" if action == 'enter' else "خروج",
            shamsi.strftime('%Y/%m/%d'),
            shamsi.strftime('%H:%M')
        ])

async def create_total_attendance_excel(file_name):
    wb = openpyxl.Workbook()
    ws = wb.active
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
            SELECT a.user_id, u.display_name, a.username, a.action, a.at
            FROM attendance a JOIN users u ON a.user_id=u.user_id
            ORDER BY a.at
            """)
            logs = cur.fetchall()
    make_shamsi_excel(ws, logs)
    wb.save(file_name)

async def create_users_admins_txt(file_name):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT user_id, display_name, username FROM users ORDER BY user_id")
            users = cur.fetchall()
            cur.execute("SELECT user_id, name FROM admins ORDER BY user_id")
            admins = cur.fetchall()
    with open(file_name, "w", encoding="utf-8") as f:
        f.write("🟩 کاربران:\n")
        for uid, dname, uname in users:
            f.write(f"{uid}, {dname}, @{uname}\n")
        f.write("\n🟦 ادمین‌ها:\n")
        for uid, name in admins:
            f.write(f"{uid}, {name}\n")

# ==== معرفی و اسم‌گذاری کاربر توسط ادمین ====
async def set_display_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not is_admin(user.id):
        await update.message.reply_text("فقط برای ادمین مجاز است.")
        return
    try:
        uid = int(context.args[0])
        name = " ".join(context.args[1:])
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("UPDATE users SET display_name=%s WHERE user_id=%s", (name, uid))
                conn.commit()
        await update.message.reply_text(f"نام کاربری به {name} تغییر یافت.")
    except:
        await update.message.reply_text("/setname user_id name")

#================== هندلرهای اصلی و اجرای برنامه ===========

def main():
    init_db()
    app = ApplicationBuilder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler('start', start))
    app.add_handler(CommandHandler('menu', main_menu_btn))
    app.add_handler(CommandHandler('admin', admin_menu))
    app.add_handler(CommandHandler('setname', set_display_name))
    app.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), main_menu_btn))
    app.add_handler(CallbackQueryHandler(button))
    app.run_polling()

if __name__ == '__main__':
    main()
