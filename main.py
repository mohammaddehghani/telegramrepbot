import os
import logging
import psycopg2
import datetime
import jdatetime
import openpyxl

from dotenv import load_dotenv
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
    ConversationHandler,
    filters,
)

load_dotenv()
logging.basicConfig(level=logging.INFO)

BOT_TOKEN   = os.getenv("BOT_TOKEN")
DATABASE_URL= os.getenv("DATABASE_URL")
SUPER_ADMIN = int(os.getenv("SUPER_ADMIN"))

# حالت‌های ConversationHandler
(
    USER_MONTH,
    ADMIN_MONTH_ALL,
    ADMIN_SET_NAME,
    ADMIN_ADD_REMOVE,
) = range(4)

# ==== DATABASE HELPERS ====
def get_db():
    return psycopg2.connect(DATABASE_URL, sslmode="disable")

def ensure_user(user):
    """در صورت جدید بودن کاربر در جدول users، یه employee_id اختصاص بده."""
    conn = get_db(); cur = conn.cursor()
    # ۱. ببین کاربر قبلاً هست یا نه
    cur.execute("SELECT employee_id FROM users WHERE user_id=%s;", (user.id,))
    row = cur.fetchone()
    if not row:
        # شناسه ماکسیمم فعلی را بگیر
        cur.execute("SELECT MAX(employee_id) FROM users;")
        mx = cur.fetchone()[0]
        next_id = int(mx) + 1 if mx else 1
        emp_id = f"{next_id:04d}"
        # درج رکورد جدید
        cur.execute("""
            INSERT INTO users (user_id, full_name, username, display_name, employee_id)
            VALUES (%s, %s, %s, %s, %s)
            ON CONFLICT (user_id) DO NOTHING;
        """, (
            user.id,
            f"{user.first_name or ''} {user.last_name or ''}".strip(),
            user.username or '',
            f"{user.first_name or ''}".strip(),
            emp_id
        ))
        conn.commit()
    cur.close(); conn.close()

def add_admin(user_id):
    conn = get_db(); cur = conn.cursor()
    cur.execute("INSERT INTO admins (user_id) VALUES (%s) ON CONFLICT DO NOTHING;", (user_id,))
    conn.commit(); cur.close(); conn.close()

def is_admin(user_id):
    conn = get_db(); cur = conn.cursor()
    cur.execute("SELECT 1 FROM admins WHERE user_id=%s;", (user_id,))
    ok = cur.fetchone() is not None
    cur.close(); conn.close()
    return user_id == SUPER_ADMIN or ok

def list_users():
    conn = get_db(); cur = conn.cursor()
    cur.execute("SELECT user_id, full_name, username, display_name, employee_id FROM users ORDER BY user_id;")
    rows = cur.fetchall()
    cur.close(); conn.close()
    return rows

def list_admins():
    conn = get_db(); cur = conn.cursor()
    cur.execute("SELECT user_id FROM admins;")
    rows = [r[0] for r in cur.fetchall()]
    cur.close(); conn.close()
    return rows

def set_display_name(user_id, new_name):
    conn = get_db(); cur = conn.cursor()
    cur.execute("UPDATE users SET display_name=%s WHERE user_id=%s;", (new_name, user_id))
    conn.commit(); cur.close(); conn.close()

def save_attendance(user_id, status):
    conn = get_db(); cur = conn.cursor()
    now = get_iran_now()
    cur.execute(
        "INSERT INTO attendance (user_id, status, timestamp) VALUES (%s,%s,%s);",
        (user_id, status, now)
    )
    conn.commit(); cur.close(); conn.close()

def fetch_attendance(user_id=None, start=None, end=None):
    conn = get_db(); cur = conn.cursor()
    q = "SELECT user_id, status, timestamp FROM attendance"
    params = []
    clauses = []
    if user_id is not None:
        clauses.append("user_id=%s"); params.append(user_id)
    if start is not None:
        clauses.append("timestamp>=%s"); params.append(start)
    if end is not None:
        clauses.append("timestamp<=%s"); params.append(end)
    if clauses:
        q += " WHERE " + " AND ".join(clauses)
    q += " ORDER BY timestamp"
    cur.execute(q, tuple(params))
    rows = cur.fetchall()
    cur.close(); conn.close()
    return rows

# ==== زمان ایران و تبدیل شمسی ====
import pytz
def get_iran_now():
    return datetime.datetime.now(pytz.timezone('Asia/Tehran'))

def to_shamsi(dateobj):
    s = jdatetime.datetime.fromgregorian(datetime=dateobj.astimezone(pytz.timezone('Asia/Tehran')))
    return s.strftime('%Y/%m/%d'), s.strftime('%H:%M:%S')

def get_display_name(user_id):
    for u in list_users():
        if u[0] == user_id:
            return u[3] or u[1] or u[4] or str(user_id)
    return str(user_id)

# ==== کیبوردهای دائمی ====
def main_menu_keyboard():
    kb = [
        ['ثبت ورود', 'ثبت خروج'],
        ['گزارش روزانه', 'گزارش ماهانه من'],
    ]
    return ReplyKeyboardMarkup(kb, resize_keyboard=True)

def admin_menu_keyboard():
    kb = [
        ['لیست کاربران', 'تعیین نام نمایشی'],
        ['گزارش روزانه همه', 'گزارش ماهانه همه'],
        ['دریافت بکاپ اکسل', 'بازگشت']
    ]
    return ReplyKeyboardMarkup(kb, resize_keyboard=True)

# ==== HANDLERS اصلی ====
async def start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    ensure_user(update.effective_user)
    add_admin(SUPER_ADMIN)
    await update.message.reply_text(
        'سلام! از دکمه‌های زیر استفاده کنید.',
        reply_markup=main_menu_keyboard()
    )

async def handle_text(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    txt = update.message.text
    user_id = update.effective_user.id
    ensure_user(update.effective_user)

    # --- غیر ادمین ---
    if txt == 'ثبت ورود':
        save_attendance(user_id, 'enter')
        await update.message.reply_text("✅ ورود ثبت شد.", reply_markup=main_menu_keyboard())
    elif txt == 'ثبت خروج':
        save_attendance(user_id, 'exit')
        await update.message.reply_text("✅ خروج ثبت شد.", reply_markup=main_menu_keyboard())
    elif txt == 'گزارش روزانه':
        await send_report(update, ctx, 'day', user_id)
    elif txt == 'گزارش ماهانه من':
        # شروع Conversation برای ماه کاربر
        await update.message.reply_text(
            "لطفاً ماه و سال را به صورت YYYY/MM وارد کنید یا 'بازگشت' برای انصراف.",
            reply_markup=ReplyKeyboardMarkup([['بازگشت']], resize_keyboard=True)
        )
        return USER_MONTH

    # --- ادمین ---
    elif txt == 'لیست کاربران' and is_admin(user_id):
        lines = ["ID  │  نام نمایش  │ emp_id  │ @username"]
        for u in list_users():
            lines.append(f"{u[0]} │ {u[3]} │ {u[4]} │ @{u[2]}")
        await update.message.reply_text('\n'.join(lines), reply_markup=admin_menu_keyboard())

    elif txt == 'تعیین نام نمایشی' and is_admin(user_id):
        await update.message.reply_text(
            "لطفاً ابتدا ID کاربر را وارد کنید یا 'بازگشت'.",
            reply_markup=ReplyKeyboardMarkup([['بازگشت']], resize_keyboard=True)
        )
        return ADMIN_SET_NAME

    elif txt == 'گزارش روزانه همه' and is_admin(user_id):
        await send_report(update, ctx, 'day', None, all_users=True)

    elif txt == 'گزارش ماهانه همه' and is_admin(user_id):
        await update.message.reply_text(
            "ماه و سال را به صورت YYYY/MM وارد کنید یا 'بازگشت'.",
            reply_markup=ReplyKeyboardMarkup([['بازگشت']], resize_keyboard=True)
        )
        return ADMIN_MONTH_ALL

    elif txt == 'دریافت بکاپ اکسل' and is_admin(user_id):
        # بکاپ کلی
        xlsx = create_total_attendance_excel()
        await update.message.reply_document(
            document=InputFile(xlsx), filename="all_attendance.xlsx"
        )
        await update.message.reply_text("✅ بکاپ ارسال شد.", reply_markup=admin_menu_keyboard())

    elif txt == 'بازگشت':
        # بازگشت به منوی اصلی یا ادمین
        if is_admin(user_id):
            await update.message.reply_text("پنل ادمین:", reply_markup=admin_menu_keyboard())
        else:
            await update.message.reply_text("منوی اصلی:", reply_markup=main_menu_keyboard())

    else:
        await update.message.reply_text("دستور نامعتبر. لطفاً از کیبورد استفاده کنید.")

    return ConversationHandler.END

# ==== ConversationHandler callbacks ====

async def user_month_cb(update: Update, ctx):
    txt = update.message.text.strip()
    if txt == 'بازگشت':
        await update.message.reply_text("انصراف داده شد.", reply_markup=main_menu_keyboard())
        return ConversationHandler.END
    try:
        y, m = map(int, txt.split('/'))
        start = datetime.datetime(y, m, 1, tzinfo=pytz.timezone('Asia/Tehran'))
        # روز آخر ماه: با رفتن به ماه بعد منهای یک روز
        if m == 12:
            next_month = datetime.datetime(y+1, 1, 1, tzinfo=start.tzinfo)
        else:
            next_month = datetime.datetime(y, m+1, 1, tzinfo=start.tzinfo)
        end = next_month - datetime.timedelta(seconds=1)
        # گزارش اکسل خود کاربر
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.append(['تاریخ شمسی', 'ساعت', 'وضعیت'])
        items = fetch_attendance(user_id=update.effective_user.id, start=start, end=end)
        for it in items:
            ds, ts = to_shamsi(it[2])
            ws.append([ds, ts, 'ورود' if it[1]=='enter' else 'خروج'])
        path = f"user_{update.effective_user.id}_{y}{m:02d}.xlsx"
        wb.save(path)
        await update.message.reply_document(
            document=InputFile(path), filename=f"report_{y}_{m:02d}.xlsx"
        )
    except Exception:
        await update.message.reply_text("فرمت نادرست. لطفاً دوباره YYYY/MM وارد کنید یا 'بازگشت'.")
        return USER_MONTH

    await update.message.reply_text("✅ گزارش ماهانه ارسال شد.", reply_markup=main_menu_keyboard())
    return ConversationHandler.END

async def admin_month_all_cb(update: Update, ctx):
    txt = update.message.text.strip()
    if txt == 'بازگشت':
        await update.message.reply_text("انصراف.", reply_markup=admin_menu_keyboard())
        return ConversationHandler.END
    try:
        y, m = map(int, txt.split('/'))
        start = datetime.datetime(y, m, 1, tzinfo=pytz.timezone('Asia/Tehran'))
        if m == 12:
            next_month = datetime.datetime(y+1, 1, 1, tzinfo=start.tzinfo)
        else:
            next_month = datetime.datetime(y, m+1, 1, tzinfo=start.tzinfo)
        end = next_month - datetime.timedelta(seconds=1)
        # ساخت اکسل برای همه
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.append(['user_id','emp_id','نام نمایش','تاریخ شمسی','ساعت','وضعیت'])
        users = {u[0]:u for u in list_users()}
        items = fetch_attendance(start=start, end=end)
        for it in items:
            u = users[it[0]]
            ds, ts = to_shamsi(it[2])
            ws.append([
                it[0], u[4], u[3],
                ds, ts,
                'ورود' if it[1]=='enter' else 'خروج'
            ])
        path = f"all_{y}{m:02d}.xlsx"
        wb.save(path)
        await update.message.reply_document(
            document=InputFile(path), filename=f"all_report_{y}_{m:02d}.xlsx"
        )
    except Exception:
        await update.message.reply_text("فرمت نادرست. دوباره YYYY/MM وارد کنید یا 'بازگشت'.")
        return ADMIN_MONTH_ALL

    await update.message.reply_text("✅ گزارش ماهانه همه ارسال شد.", reply_markup=admin_menu_keyboard())
    return ConversationHandler.END

async def admin_setname_cb(update: Update, ctx):
    txt = update.message.text.strip()
    if txt == 'بازگشت':
        await update.message.reply_text("انصراف.", reply_markup=admin_menu_keyboard())
        return ConversationHandler.END
    # اول پیام، user_id
    if 'awaiting_user_id' not in ctx.user_data:
        if txt.isdigit():
            ctx.user_data['awaiting_user_id'] = int(txt)
            await update.message.reply_text(
                "حالا نام جدید را وارد کنید یا 'بازگشت'.",
                reply_markup=ReplyKeyboardMarkup([['بازگشت']], resize_keyboard=True)
            )
        else:
            await update.message.reply_text("فقط عدد ID را وارد کنید یا 'بازگشت'.")
        return ADMIN_SET_NAME
    else:
        new_name = txt
        uid = ctx.user_data.pop('awaiting_user_id')
        set_display_name(uid, new_name)
        await update.message.reply_text("✅ نام تغییر کرد.", reply_markup=admin_menu_keyboard())
        return ConversationHandler.END

# ==== BACKUP کلی ====
def create_total_attendance_excel():
    fn = "all_attendance.xlsx"
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(['user_id','emp_id','نام نمایش','full_name','تاریخ شمسی','ساعت','وضعیت'])
    users = {u[0]:u for u in list_users()}
    items = fetch_attendance()
    for it in items:
        u = users[it[0]]
        ds, ts = to_shamsi(it[2])
        ws.append([
            it[0], u[4], u[3], u[1],
            ds, ts,
            'ورود' if it[1]=='enter' else 'خروج'
        ])
    wb.save(fn)
    return fn

# ==== گزارش متنی روزانه/عادی ====
async def send_report(update, ctx, period, user_id, all_users=False):
    now = get_iran_now()
    if period == 'day':
        start = now.replace(hour=0,minute=0,second=0,microsecond=0)
        end   = now.replace(hour=23,minute=59,second=59)
        title = "گزارش روزانه"
    else:
        return
    items = (
        fetch_attendance(start=start,end=end)
        if all_users else
        fetch_attendance(user_id=user_id,start=start,end=end)
    )
    if not items:
        await update.message.reply_text("📋 موردی ثبت نشده.", reply_markup=(admin_menu_keyboard() if all_users else main_menu_keyboard()))
        return
    text = title+"\n\n"
    users = {u[0]:u for u in list_users()}
    for it in items:
        name = users[it[0]][3]
        ds, ts = to_shamsi(it[2])
        text += f"{name} | {ds} {ts} | {'ورود' if it[1]=='enter' else 'خروج'}\n"
    await update.message.reply_text(text, reply_markup=(admin_menu_keyboard() if all_users else main_menu_keyboard()))


def main():
    app = Application.builder().token(BOT_TOKEN).build()

    conv = ConversationHandler(
        entry_points=[MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text)],
        states={
            USER_MONTH:       [MessageHandler(filters.TEXT & ~filters.COMMAND, user_month_cb)],
            ADMIN_MONTH_ALL:  [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_month_all_cb)],
            ADMIN_SET_NAME:   [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_setname_cb)],
        },
        fallbacks=[MessageHandler(filters.Regex('^بازگشت$'), handle_text)],
        per_user=True
    )

    app.add_handler(CommandHandler("start", start))
    app.add_handler(conv)

    logging.info("Bot Started ...")
    app.run_polling()

if __name__ == "__main__":
    main()
