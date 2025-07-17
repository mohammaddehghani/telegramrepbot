import os
import psycopg2
from telegram import Update, ReplyKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes

# ------ پیکربندی ------
BOT_TOKEN = os.environ.get("BOT_TOKEN")  # توکن نباید مقدار پیش‌فرض داشته باشد!
DATABASE_URL = os.environ.get("DATABASE_URL")
ADMIN_IDS = [125886032]  # لیست ادمین‌ها (int)

def is_admin(user_id):
    return user_id in ADMIN_IDS

# ساخت کیبورد پویا بر اساس ادمین بودن
def build_keyboard(is_user_admin=False):
    rows = [
        ["ثبت ورود ⏱️", "ثبت خروج 🕓"],
        ["گزارش امروز 📃", "گزارش کلی 📊"],
        ["راهنما ℹ️"]
    ]
    if is_user_admin:
        rows[-1].append("گزارش مدیریتی 👑")
    return ReplyKeyboardMarkup(rows, resize_keyboard=True)

def escape_markdown(text):
    # متن را برای MarkdownV2 ایمن کن
    escape_chars = r"\_`*[]()~>#+-=|{}.!"
    return "".join(f"\\{c}" if c in escape_chars else c for c in str(text))

# ------ اتصال دیتابیس ------
def get_conn():
    try:
        return psycopg2.connect(DATABASE_URL)
    except Exception as e:
        print("DB Connection Error:", e)
        return None

def save_entry(user_id, name, action):
    conn = get_conn()
    if not conn:
        return False
    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO attendance (user_id, name, action, time) VALUES (%s, %s, %s, current_timestamp)",
                    (user_id, name, action)
                )
        return True
    except Exception as e:
        print("DB Insert Error:", e)
        return False
    finally:
        conn.close()

def get_today_status(user_id):
    conn = get_conn()
    if not conn:
        return None
    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT action, time FROM attendance
                    WHERE user_id=%s AND DATE(time)=CURRENT_DATE
                    ORDER BY time ASC
                    """, (user_id,))
                data = cur.fetchall()
                return [
                    f'{escape_markdown(act)} در {tm.strftime("%H:%M")}' for act, tm in data
                ]
    except Exception as e:
        print("DB Fetch Error (today):", e)
        return None
    finally:
        conn.close()

def get_all_today_status():
    conn = get_conn()
    if not conn:
        return None
    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT name, action, time FROM attendance
                    WHERE DATE(time)=CURRENT_DATE
                    ORDER BY name, time
                    """)
                data = cur.fetchall()
                user_states = {}
                for name, act, tm in data:
                    safe_name = escape_markdown(name)
                    if safe_name not in user_states:
                        user_states[safe_name] = []
                    user_states[safe_name].append(f'{escape_markdown(act)} در {tm.strftime("%H:%M")}')
                return user_states
    except Exception as e:
        print("DB Fetch Error (admin):", e)
        return None
    finally:
        conn.close()

# ------ هندلرها -------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    is_user_admin = is_admin(user.id)
    await update.message.reply_text(
        f"سلام {escape_markdown(user.full_name)}!\nخوش آمدید 🌱\nآیدی عددی شما: `{user.id}`\nبرای شروع یکی از گزینه‌ها را انتخاب کنید.",
        reply_markup=build_keyboard(is_user_admin),
        parse_mode='MarkdownV2'
    )

async def help_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    await update.message.reply_text(
        "📄 راهنمای ربات:\n"
        "- 'ثبت ورود' برای ثبت ساعت ورود\n"
        "- 'ثبت خروج' برای ثبت ساعت خروج\n"
        "- 'گزارش امروز' برای وضعیت امروز\n"
        "- 'گزارش کلی' برای تاریخچه خود\n"
        "- مدیران: دکمه گزارش مدیریتی\n"
        "- ارتباط با پشتیبانی: @dehghani96",
        reply_markup=build_keyboard(is_admin(user.id))
    )

async def enter(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    result = save_entry(user.id, user.full_name, "ورود")
    if result:
        await update.message.reply_text("⏱️ ورود شما ثبت شد.", reply_markup=build_keyboard(is_admin(user.id)))
    else:
        await update.message.reply_text("خطا در ثبت ورود! لطفاً بعداً تلاش کنید.", reply_markup=build_keyboard(is_admin(user.id)))

async def exit_(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    result = save_entry(user.id, user.full_name, "خروج")
    if result:
        await update.message.reply_text("🕓 خروج شما ثبت شد.", reply_markup=build_keyboard(is_admin(user.id)))
    else:
        await update.message.reply_text("خطا در ثبت خروج! لطفاً بعداً تلاش کنید.", reply_markup=build_keyboard(is_admin(user.id)))

async def today_report(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    states = get_today_status(user.id)
    if states is None:
        msg = "متاسفانه دسترسی به دیتابیس ممکن نیست."
    elif not states:
        msg = "امروز هیچ رکوردی ثبت نشده است."
    else:
        msg = "\n".join(states)
    response = f"📃 *گزارش امروز:*\n{msg}"
    await update.message.reply_text(response, parse_mode='MarkdownV2', reply_markup=build_keyboard(is_admin(user.id)))

async def full_report(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    conn = get_conn()
    if not conn:
        msg = "متاسفانه دسترسی به دیتابیس ممکن نیست."
    else:
        try:
            with conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT action, time FROM attendance WHERE user_id=%s ORDER BY time DESC LIMIT 10", (user.id,))
                    data = cur.fetchall()
            if not data:
                msg = "هیچ رکوردی برای شما ثبت نشده است."
            else:
                msg = '\n'.join([f'{escape_markdown(act)} - {tm.strftime("%Y/%m/%d %H:%M")}' for act, tm in data])
        except Exception as e:
            print("DB Fetch Error (full):", e)
            msg = "خطا در دریافت گزارش!"
        finally:
            conn.close()
    await update.message.reply_text(
        f"📊 *گزارش کلی ده رکورد اخیر:*\n\n{msg}",
        parse_mode='MarkdownV2',
        reply_markup=build_keyboard(is_admin(user.id))
    )

async def admin_report(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not is_admin(user.id):
        await update.message.reply_text("شما دسترسی ادمین ندارید.", reply_markup=build_keyboard(False))
        return
    states = get_all_today_status()
    if states is None:
        msg = "متاسفانه دسترسی به دیتابیس ممکن نیست."
    elif not states:
        msg = "امروز هیچ رکوردی ثبت نشده."
    else:
        msg = '\n\n'.join(
            [f'👤 {name}\n' + '\n'.join(actions) for name, actions in states.items()]
        )
    response = f"""👑 *گزارش مدیریت امروز*\n\n{msg}"""
    await update.message.reply_text(response, parse_mode='MarkdownV2', reply_markup=build_keyboard(True))

async def keyboard_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    text = update.message.text
    if "ورود" in text:
        return await enter(update, context)
    elif "خروج" in text:
        return await exit_(update, context)
    elif "امروز" in text:
        return await today_report(update, context)
    elif "کلی" in text:
        return await full_report(update, context)
    elif "راهنما" in text:
        return await help_handler(update, context)
    elif "مدیریتی" in text:
        return await admin_report(update, context)
    else:
        await help_handler(update, context)

# ------ راه‌‌اندازی اصلی -------
def main():
    if not BOT_TOKEN or not DATABASE_URL:
        raise ValueError("لطفاً BOT_TOKEN و DATABASE_URL را در متغیرهای محیطی تعریف کنید.")
    application = Application.builder().token(BOT_TOKEN).build()
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_handler))
    application.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), keyboard_handler))
    # ادمین کامند مستقل
    application.add_handler(CommandHandler("admin_report", admin_report))
    application.run_polling()

if __name__ == "__main__":
    main()
