import asyncio
import logging
import sqlite3
import html
import csv
import io
from contextlib import suppress

from aiogram import Bot, Dispatcher, F
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton, ReplyKeyboardMarkup, KeyboardButton, BufferedInputFile
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.exceptions import TelegramBadRequest

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

# === КОНФИГ ===
BOT_TOKEN = "8684957172:AAHhJAfdLnbmAw-AAAYuvNI0j8q0dz9IBYA"
SENIOR_CHAT_ID = "-1004340807494" # Замени на свой ID чата с минусом

try:
    GROUP_ID = int(str(SENIOR_CHAT_ID).strip().replace("'", "").replace('"', ''))
except ValueError:
    GROUP_ID = SENIOR_CHAT_ID

bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher()

# === СТАТУСЫ ===
ST_NEW = "🆕 НОВАЯ"
ST_WIP = "⚙️ В РАБОТЕ"
ST_DONE = "✅ РЕШЕНА"
ST_DEL = "❌ УДАЛЕНА"

# === БАЗА ДАННЫХ ===
def init_db():
    with sqlite3.connect("tickets.db", timeout=20) as conn:
        cursor = conn.cursor()
        cursor.execute(f'''
            CREATE TABLE IF NOT EXISTS tickets (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ticket_number TEXT, client_name TEXT, comment TEXT, urgency TEXT,
                creator TEXT, creator_id INTEGER, assignee TEXT, assignee_id INTEGER,
                status TEXT, return_reason TEXT, senior_note TEXT, 
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, assigned_at TIMESTAMP,
                sla_reminded INTEGER DEFAULT 0, alarm_sent INTEGER DEFAULT 0, group_message_id INTEGER
            )
        ''')
        conn.commit()

def execute_query(query, params=()):
    with sqlite3.connect("tickets.db", timeout=20) as conn:
        cursor = conn.cursor()
        cursor.execute(query, params)
        conn.commit()
        return cursor.lastrowid

def fetch_query(query, params=()):
    with sqlite3.connect("tickets.db", timeout=20) as conn:
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute(query, params)
        return [dict(row) for row in cursor.fetchall()]

# === СОСТОЯНИЯ ===
class TicketForm(StatesGroup):
    number = State()
    client = State()
    comment = State()

class ReturnForm(StatesGroup):
    ticket_id = State()
    reason = State()

# === UI / МЕНЮ ===
main_kb = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="📝 Создать"), KeyboardButton(text="📋 Пул"), KeyboardButton(text="💼 Мои")],
        [KeyboardButton(text="📊 Статистика"), KeyboardButton(text="📥 Отчет CSV")]
    ],
    resize_keyboard=True
)

urgency_kb = InlineKeyboardMarkup(inline_keyboard=[
    [InlineKeyboardButton(text="Низкий", callback_data="urgency_low"),
     InlineKeyboardButton(text="Средний", callback_data="urgency_mid"),
     InlineKeyboardButton(text="Высокий", callback_data="urgency_high")]
])

def get_user_name(user):
    return f"@{user.username}" if user.username else user.first_name

def build_card(t):
    """Строгий корпоративный дизайн карточки"""
    assignee = html.escape(str(t['assignee'])) if t['assignee'] else "—"
    reason = f"\n⚠️ <b>Возврат:</b> <i>{html.escape(str(t['return_reason']))}</i>" if t['return_reason'] else ""
    note = f"\n💡 <b>Ремарка:</b> <i>{html.escape(str(t['senior_note']))}</i>" if t['senior_note'] else ""
    
    return (
        f"🎫 <b>Заявка</b> <code>#{html.escape(str(t['ticket_number']))}</code>\n"
        f"Статус: <b>{t['status']}</b> | Приоритет: {t['urgency']}\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"👤 <b>Клиент:</b> {html.escape(str(t['client_name']))}\n"
        f"💬 <b>Суть:</b> {html.escape(str(t['comment']))}{reason}{note}\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"📝 <b>Автор:</b> {html.escape(str(t['creator']))}\n"
        f"⚙️ <b>Взял:</b> {assignee}"
    )

# === СМАРТ-РЕПЛАИ (ЗАМЕТКИ В ЧАТЕ) ===
@dp.message(F.reply_to_message & F.chat.type.in_({"group", "supergroup"}))
async def handle_smart_reply(message: Message):
    if message.reply_to_message.from_user.is_bot:
        t_data = fetch_query("SELECT * FROM tickets WHERE group_message_id = ?", (message.reply_to_message.message_id,))
        if t_data:
            t = t_data[0]
            note = f"{message.text} ({get_user_name(message.from_user)})"
            execute_query("UPDATE tickets SET senior_note = ? WHERE id = ?", (note, t['id']))
            updated_t = fetch_query("SELECT * FROM tickets WHERE id = ?", (t['id'],))[0]
            
            kb = None
            if updated_t['status'] == ST_NEW:
                kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="✋ Взять", callback_data=f"take_{t['id']}")]])
            elif updated_t['status'] == ST_WIP:
                kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="✅ Закрыть", callback_data=f"close_{t['id']}")]])
                
            with suppress(Exception):
                await bot.edit_message_text(chat_id=message.chat.id, message_id=t['group_message_id'], text=build_card(updated_t), reply_markup=kb)
                await message.delete()

# === ФОНОВЫЕ ТАЙМЕРЫ ===
async def monitor_tasks():
    while True:
        await asyncio.sleep(60)
        try:
            # Тревога
            alarm_tickets = fetch_query(f"SELECT * FROM tickets WHERE status = '{ST_NEW}' AND urgency = '🔴 Высокий' AND alarm_sent = 0 AND created_at <= datetime('now', '-15 minutes')")
            for t in alarm_tickets:
                chat_kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="✋ Взять срочно", callback_data=f"take_{t['id']}")]])
                try:
                    sent_msg = await bot.send_message(chat_id=GROUP_ID, text=f"🚨 <b>НАРУШЕНИЕ SLA (ОЖИДАНИЕ)</b>\n\n{build_card(t)}", reply_markup=chat_kb)
                    execute_query("UPDATE tickets SET alarm_sent = 1, group_message_id = ? WHERE id = ?", (sent_msg.message_id, t['id']))
                    if t['group_message_id']:
                        with suppress(Exception): await bot.delete_message(chat_id=GROUP_ID, message_id=t['group_message_id'])
                except Exception: pass

            # SLA Напоминание
            sla_tickets = fetch_query(f"SELECT * FROM tickets WHERE status = '{ST_WIP}' AND sla_reminded = 0 AND assigned_at <= datetime('now', '-2 hours')")
            for t in sla_tickets:
                if t['assignee_id']:
                    with suppress(Exception):
                        await bot.send_message(chat_id=t['assignee_id'], text=f"⏳ <b>НАРУШЕНИЕ SLA (В РАБОТЕ > 2Ч)</b>\n\n{build_card(t)}")
                        execute_query("UPDATE tickets SET sla_reminded = 1 WHERE id = ?", (t['id'],))
        except Exception: pass

# === БАЗОВЫЕ КОМАНДЫ И СТАТИСТИКА ===
@dp.message(Command("start"), StateFilter('*'))
async def cmd_start(message: Message, state: FSMContext):
    await state.clear()
    await message.answer("Система управления заявками активирована.", reply_markup=main_kb)

@dp.message(F.text == "📊 Статистика", StateFilter('*'))
async def cmd_stats(message: Message, state: FSMContext):
    await state.clear()
    uid = message.from_user.id
    t_new = fetch_query(f"SELECT COUNT(*) as c FROM tickets WHERE status = '{ST_NEW}'")[0]['c']
    t_wip = fetch_query(f"SELECT COUNT(*) as c FROM tickets WHERE status = '{ST_WIP}'")[0]['c']
    t_done = fetch_query(f"SELECT COUNT(*) as c FROM tickets WHERE status = '{ST_DONE}'")[0]['c']
    my_done = fetch_query(f"SELECT COUNT(*) as c FROM tickets WHERE status = '{ST_DONE}' AND assignee_id = ?", (uid,))[0]['c']
    
    top = fetch_query(f"SELECT assignee, COUNT(*) as c FROM tickets WHERE status = '{ST_DONE}' AND assignee IS NOT NULL GROUP BY assignee ORDER BY c DESC LIMIT 3")
    top_list = "\n".join([f"{i+1}. {w['assignee']} — {w['c']}" for i, w in enumerate(top)]) or "—"

    text = (
        f"📊 <b>СВОДКА</b>\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"В пуле: <b>{t_new}</b> | В работе: <b>{t_wip}</b>\n"
        f"Всего закрыто: <b>{t_done}</b>\n"
        f"Закрыто вами: <b>{my_done}</b>\n\n"
        f"🏆 <b>ЛИДЕРЫ:</b>\n<code>{top_list}</code>"
    )
    await message.answer(text)

@dp.message(F.text == "📥 Отчет CSV", StateFilter('*'))
async def export_csv(message: Message):
    tickets = fetch_query("SELECT id, ticket_number, client_name, comment, urgency, creator, assignee, status, created_at, return_reason, senior_note FROM tickets ORDER BY id DESC")
    if not tickets:
        return await message.answer("База данных пуста.")
    
    output = io.StringIO()
    writer = csv.writer(output, delimiter=';')
    writer.writerow(['ID', 'Номер', 'Клиент', 'Суть', 'Приоритет', 'Автор', 'Исполнитель', 'Статус', 'Дата', 'Причина возврата', 'Заметка'])
    for t in tickets:
        writer.writerow([t['id'], t['ticket_number'], t['client_name'], t['comment'], t['urgency'], t['creator'], t['assignee'], t['status'], t['created_at'], t['return_reason'], t['senior_note']])
    
    await message.answer_document(BufferedInputFile(output.getvalue().encode('utf-8-sig'), filename="Report.csv"))

# === ЗАВЕДЕНИЕ ЗАЯВКИ ===
@dp.message(F.text == "📝 Создать", StateFilter('*'))
async def ticket_start(message: Message, state: FSMContext):
    await state.clear()
    await message.answer("🆔 <b>Номер заявки:</b>")
    await state.set_state(TicketForm.number)

@dp.message(TicketForm.number)
async def ticket_client(message: Message, state: FSMContext):
    await state.update_data(number=message.text)
    await message.answer("👤 <b>Имя/Данные клиента:</b>")
    await state.set_state(TicketForm.client)

@dp.message(TicketForm.client)
async def ticket_comment(message: Message, state: FSMContext):
    await state.update_data(client=message.text)
    await message.answer("💬 <b>Суть проблемы:</b>")
    await state.set_state(TicketForm.comment)

@dp.message(TicketForm.comment)
async def ticket_urgency(message: Message, state: FSMContext):
    await state.update_data(comment=message.text)
    await message.answer("⚡ <b>Приоритет:</b>", reply_markup=urgency_kb)

@dp.callback_query(F.data.startswith("urgency_"))
async def ticket_save(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    urg_map = {"urgency_low": "🟢 Низкий", "urgency_mid": "🟡 Средний", "urgency_high": "🔴 Высокий"}
    
    t_data = {
        "ticket_number": data.get('number', '-'), "client_name": data.get('client', '-'),
        "comment": data.get('comment', '-'), "urgency": urg_map.get(callback.data, "🟢 Низкий"),
        "creator": get_user_name(callback.from_user), "status": ST_NEW,
        "assignee": None, "return_reason": None, "senior_note": None
    }
    
    tid = execute_query(
        "INSERT INTO tickets (ticket_number, client_name, comment, urgency, creator, creator_id, status) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (t_data['ticket_number'], t_data['client_name'], t_data['comment'], t_data['urgency'], t_data['creator'], callback.from_user.id, t_data['status'])
    )
    t_data['id'] = tid
    group_kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="✋ Взять", callback_data=f"take_{tid}")]])
    
    try:
        sent = await bot.send_message(chat_id=GROUP_ID, text=build_card(t_data), reply_markup=group_kb)
        execute_query("UPDATE tickets SET group_message_id = ? WHERE id = ?", (sent.message_id, tid))
        res = "✅ Заявка сохранена и отправлена."
    except Exception as e:
        res = f"✅ Сохранено.\n⚠️ <i>Ошибка отправки в чат: {e}</i>"
    
    with suppress(TelegramBadRequest): await callback.message.edit_text(res)
    await state.clear()

# === ПУЛ И В РАБОТЕ ===
@dp.message(F.text == "📋 Пул", StateFilter('*'))
async def list_pool(message: Message, state: FSMContext):
    await state.clear()
    tickets = fetch_query(f"SELECT * FROM tickets WHERE status = '{ST_NEW}' ORDER BY id DESC LIMIT 10")
    if not tickets: return await message.answer("📭 Пул пуст.")

    for t in tickets:
        btns = [[InlineKeyboardButton(text="✋ Взять", callback_data=f"take_{t['id']}")]]
        if t['creator_id'] == message.from_user.id:
            btns.append([InlineKeyboardButton(text="❌ Удалить", callback_data=f"delete_{t['id']}")])
        await message.answer(build_card(t), reply_markup=InlineKeyboardMarkup(inline_keyboard=btns))

@dp.message(F.text == "💼 Мои", StateFilter('*'))
async def list_mine(message: Message, state: FSMContext):
    await state.clear()
    tickets = fetch_query(f"SELECT * FROM tickets WHERE status = '{ST_WIP}' AND assignee_id = ?", (message.from_user.id,))
    if not tickets: return await message.answer("💼 У вас нет активных задач.")

    for t in tickets:
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="✅ Закрыть", callback_data=f"close_{t['id']}"),
             InlineKeyboardButton(text="🔄 Вернуть", callback_data=f"return_{t['id']}")]
        ])
        await message.answer(build_card(t), reply_markup=kb)

# === ОБРАБОТЧИКИ КНОПОК ===
@dp.callback_query(F.data.startswith("take_"))
async def act_take(callback: CallbackQuery):
    tid = int(callback.data.split("_")[1])
    t = fetch_query("SELECT * FROM tickets WHERE id = ?", (tid,))
    if not t or t[0]["status"] != ST_NEW:
        with suppress(TelegramBadRequest): await callback.message.delete()
        return await callback.answer("⚠️ Уже в работе!", show_alert=True)

    uname = get_user_name(callback.from_user)
    execute_query(f"UPDATE tickets SET status = '{ST_WIP}', assignee = ?, assignee_id = ?, assigned_at = CURRENT_TIMESTAMP, sla_reminded = 0 WHERE id = ?", 
                  (uname, callback.from_user.id, tid))
    
    upd_t = fetch_query("SELECT * FROM tickets WHERE id = ?", (tid,))[0]
    is_grp = callback.message.chat.type in ["group", "supergroup"]

    grp_kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="✅ Закрыть", callback_data=f"close_{tid}")]])
    if upd_t['group_message_id']:
        with suppress(Exception): await bot.edit_message_text(chat_id=GROUP_ID, message_id=upd_t['group_message_id'], text=build_card(upd_t), reply_markup=grp_kb)

    if is_grp:
        await callback.answer("✅ Взято в работу.", show_alert=True)
    else:
        priv_kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="✅ Закрыть", callback_data=f"close_{tid}"), InlineKeyboardButton(text="🔄 Вернуть", callback_data=f"return_{tid}")]
        ])
        with suppress(TelegramBadRequest): await callback.message.edit_text(build_card(upd_t), reply_markup=priv_kb)
        await callback.answer("✅ Взято в работу!")

@dp.callback_query(F.data.startswith("close_"))
async def act_close(callback: CallbackQuery):
    tid = int(callback.data.split("_")[1])
    t = fetch_query("SELECT * FROM tickets WHERE id = ?", (tid,))
    if not t or t[0]["assignee_id"] != callback.from_user.id: return await callback.answer("⚠️ Нет прав!", show_alert=True)

    execute_query(f"UPDATE tickets SET status = '{ST_DONE}' WHERE id = ?", (tid,))
    upd_t = fetch_query("SELECT * FROM tickets WHERE id = ?", (tid,))[0]
    is_grp = callback.message.chat.type in ["group", "supergroup"]
    
    if upd_t['group_message_id']:
        with suppress(Exception): await bot.edit_message_text(chat_id=GROUP_ID, message_id=upd_t['group_message_id'], text=build_card(upd_t), reply_markup=None)

    if not is_grp:
        with suppress(TelegramBadRequest): await callback.message.edit_text(build_card(upd_t))
    await callback.answer("🛡️ Задача закрыта.")

@dp.callback_query(F.data.startswith("delete_"))
async def act_delete(callback: CallbackQuery):
    tid = int(callback.data.split("_")[1])
    t = fetch_query("SELECT * FROM tickets WHERE id = ?", (tid,))
    execute_query(f"UPDATE tickets SET status = '{ST_DEL}' WHERE id = ?", (tid,))
    
    if t and t[0]['group_message_id']:
        with suppress(Exception): await bot.edit_message_text(chat_id=GROUP_ID, message_id=t[0]['group_message_id'], text=f"❌ <b>ОТМЕНЕНО</b>\nЗаявка <code>#{html.escape(t[0]['ticket_number'])}</code> удалена автором.", reply_markup=None)

    with suppress(TelegramBadRequest): await callback.message.edit_text("❌ Заявка удалена.")
    await callback.answer()

@dp.callback_query(F.data.startswith("return_"))
async def act_return(callback: CallbackQuery, state: FSMContext):
    tid = int(callback.data.split("_")[1])
    with suppress(TelegramBadRequest): await callback.message.delete()
    await state.update_data(ticket_id=tid)
    await state.set_state(ReturnForm.reason)
    await callback.message.answer("✍️ <b>Причина возврата:</b>")
    await callback.answer()

@dp.message(ReturnForm.reason)
async def act_return_save(message: Message, state: FSMContext):
    data = await state.get_data()
    tid = data.get("ticket_id")
    execute_query(f"UPDATE tickets SET status = '{ST_NEW}', assignee = NULL, assignee_id = NULL, return_reason = ?, created_at = CURRENT_TIMESTAMP, alarm_sent = 0 WHERE id = ?", 
                  (message.text, tid))
    
    t = fetch_query("SELECT * FROM tickets WHERE id = ?", (tid,))[0]
    if t['group_message_id']:
        kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="✋ Взять", callback_data=f"take_{tid}")]])
        with suppress(Exception): await bot.edit_message_text(chat_id=GROUP_ID, message_id=t['group_message_id'], text=build_card(t), reply_markup=kb)
    
    await message.answer("🔄 Заявка возвращена в пул.", reply_markup=main_kb)
    await state.clear()

# === ЗАПУСК ===
async def on_startup():
    init_db()
    asyncio.create_task(monitor_tasks())
    logging.info("СИСТЕМА ЗАПУЩЕНА")

async def main():
    dp.startup.register(on_startup)
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
