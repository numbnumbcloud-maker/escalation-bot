import asyncio
import logging
import sqlite3
import html
from datetime import datetime
from contextlib import suppress

from aiogram import Bot, Dispatcher, F
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton, ReplyKeyboardMarkup, KeyboardButton
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.base import StorageKey
from aiogram.exceptions import TelegramBadRequest

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

# === КОНФИГ ===
BOT_TOKEN = "8684957172:AAHhJAfdLnbmAw-AAAYuvNI0j8q0dz9IBYA"
SENIOR_CHAT_ID = "-1004340807494" # ID рабочей группы с минусом

try:
    GROUP_ID = int(str(SENIOR_CHAT_ID).strip().replace("'", "").replace('"', ''))
except ValueError:
    GROUP_ID = SENIOR_CHAT_ID

bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher()

# === СТАТУСЫ ===
ST_NEW = "🆕 СВОБОДНА"
ST_WIP = "⚙️ В РАБОТЕ"
ST_DONE = "✅ ВЫПОЛНЕНА"
ST_DEL = "❌ ОТМЕНЕНА"

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

# === ХЕЛПЕР ВРЕМЕНИ ===
def get_elapsed_time(assigned_at_str):
    """Высчитывает, сколько времени задача находится в работе"""
    if not assigned_at_str: return "—"
    try:
        assigned_dt = datetime.strptime(assigned_at_str, '%Y-%m-%d %H:%M:%S')
        delta = datetime.utcnow() - assigned_dt
        hours, remainder = divmod(int(delta.total_seconds()), 3600)
        minutes, _ = divmod(remainder, 60)
        if hours > 0: return f"{hours}ч {minutes}м"
        if minutes > 0: return f"{minutes}м"
        return "только что"
    except Exception:
        return "—"

# === СОСТОЯНИЯ FSM ===
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
        [KeyboardButton(text="📝 Создать"), KeyboardButton(text="📋 Свободные"), KeyboardButton(text="💼 В работе")],
        [KeyboardButton(text="👥 Команда"), KeyboardButton(text="🗄 Архив")]
    ],
    resize_keyboard=True
)

urgency_kb = InlineKeyboardMarkup(inline_keyboard=[
    [InlineKeyboardButton(text="🟢 Низкий", callback_data="urgency_low"),
     InlineKeyboardButton(text="🟡 Средний", callback_data="urgency_mid"),
     InlineKeyboardButton(text="🔴 Высокий", callback_data="urgency_high")]
])

def get_user_name(user):
    return f"@{user.username}" if user.username else user.first_name

def build_card(t):
    """Строгий дизайн карточки"""
    assignee = html.escape(str(t['assignee'])) if t['assignee'] else "—"
    reason = f"\n⚠️ <b>Причина возврата:</b> <i>{html.escape(str(t['return_reason']))}</i>" if t['return_reason'] else ""
    note = f"\n💡 <b>Ремарка:</b> <i>{html.escape(str(t['senior_note']))}</i>" if t['senior_note'] else ""
    
    time_info = ""
    if t['status'] == ST_WIP and t['assigned_at']:
        time_info = f"\n⏱ <b>В работе:</b> {get_elapsed_time(t['assigned_at'])}"
    
    return (
        f"🎫 <b>Заявка</b> <code>#{html.escape(str(t['ticket_number']))}</code>\n"
        f"Статус: <b>{t['status']}</b> | Приоритет: <b>{t['urgency']}</b>\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"👤 <b>Клиент:</b> {html.escape(str(t['client_name']))}\n"
        f"💬 <b>Суть:</b> {html.escape(str(t['comment']))}{reason}{note}\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"📝 <b>Создал:</b> {html.escape(str(t['creator']))}\n"
        f"🛠 <b>Исполнитель:</b> {assignee}{time_info}"
    )

def get_action_kb(t_id, is_wip=False):
    """Генерирует нужные кнопки для карточки"""
    if not is_wip:
        return InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="✋ Взять в работу", callback_data=f"take_{t_id}")]
        ])
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Выполнено", callback_data=f"close_{t_id}")],
        [InlineKeyboardButton(text="🔄 Снять с себя", callback_data=f"return_{t_id}")]
    ])

# === СМАРТ-РЕПЛАИ (ЗАМЕТКИ В ЧАТЕ) ===
@dp.message(F.reply_to_message & F.chat.type.in_({"group", "supergroup"}))
async def handle_smart_reply(message: Message):
    if message.reply_to_message.from_user.is_bot:
        t_data = fetch_query("SELECT * FROM tickets WHERE group_message_id = ?", (message.reply_to_message.message_id,))
        if t_data:
            t = t_data[0]
            if t['status'] in [ST_DONE, ST_DEL]: return 
            
            note = f"{message.text} ({get_user_name(message.from_user)})"
            execute_query("UPDATE tickets SET senior_note = ? WHERE id = ?", (note, t['id']))
            updated_t = fetch_query("SELECT * FROM tickets WHERE id = ?", (t['id'],))[0]
            
            with suppress(Exception):
                await bot.edit_message_text(
                    chat_id=message.chat.id, message_id=t['group_message_id'], 
                    text=build_card(updated_t), reply_markup=get_action_kb(t['id'], updated_t['status'] == ST_WIP)
                )
                await message.delete()

# === ФОНОВЫЕ ТАЙМЕРЫ ===
async def monitor_tasks():
    while True:
        await asyncio.sleep(60)
        try:
            # Тревога пула (15 минут)
            alarm_tickets = fetch_query(f"SELECT * FROM tickets WHERE status = '{ST_NEW}' AND urgency = '🔴 Высокий' AND alarm_sent = 0 AND created_at <= datetime('now', '-15 minutes')")
            for t in alarm_tickets:
                try:
                    sent_msg = await bot.send_message(chat_id=GROUP_ID, text=f"🚨 <b>СЕРЬЕЗНОЕ ОЖИДАНИЕ!</b>\nНикто не берет важную задачу!\n\n{build_card(t)}", reply_markup=get_action_kb(t['id'], False))
                    execute_query("UPDATE tickets SET alarm_sent = 1, group_message_id = ? WHERE id = ?", (sent_msg.message_id, t['id']))
                    if t['group_message_id']:
                        with suppress(Exception): await bot.delete_message(chat_id=GROUP_ID, message_id=t['group_message_id'])
                except Exception: pass

            # SLA Напоминание (1 час)
            sla_tickets = fetch_query(f"SELECT * FROM tickets WHERE status = '{ST_WIP}' AND sla_reminded = 0 AND assigned_at <= datetime('now', '-1 hour')")
            for t in sla_tickets:
                if t['assignee_id']:
                    with suppress(Exception):
                        await bot.send_message(chat_id=t['assignee_id'], text=f"⏳ <b>НАРУШЕНИЕ SLA (> 1 ЧАСА)</b>\nЗадача висит слишком долго. Завершите её или снимите с себя!\n\n{build_card(t)}")
                        execute_query("UPDATE tickets SET sla_reminded = 1 WHERE id = ?", (t['id'],))
        except Exception: pass

# === БАЗОВЫЕ КОМАНДЫ ===
@dp.message(Command("start"), StateFilter('*'))
async def cmd_start(message: Message, state: FSMContext):
    await state.clear()
    await message.answer("Система маршрутизации заявок готова к работе.\nИспользуйте меню ниже 👇", reply_markup=main_kb)

@dp.message(F.text == "👥 Команда", StateFilter('*'))
async def cmd_active_users(message: Message, state: FSMContext):
    await state.clear()
    tickets = fetch_query(f"SELECT * FROM tickets WHERE status = '{ST_WIP}' ORDER BY assignee")
    if not tickets: return await message.answer("В данный момент ни у кого нет задач в работе.")
    
    users_tasks = {}
    for t in tickets: users_tasks.setdefault(t['assignee'], []).append(t)
    
    text = "👥 <b>РАБОЧИЙ МОНИТОРИНГ:</b>\n━━━━━━━━━━━━━━━━━━\n"
    for user, tasks in users_tasks.items():
        text += f"👤 <b>{html.escape(user)}</b>:\n"
        for t in tasks:
            text += f" ├ 🎫 <code>#{html.escape(str(t['ticket_number']))}</code> (⏱ {get_elapsed_time(t['assigned_at'])})\n"
        text += "\n"
    await message.answer(text)

@dp.message(F.text == "🗄 Архив", StateFilter('*'))
async def cmd_archive(message: Message, state: FSMContext):
    await state.clear()
    tickets = fetch_query(f"SELECT * FROM tickets WHERE status = '{ST_DONE}' ORDER BY id DESC LIMIT 5")
    if not tickets: return await message.answer("🗄 Архив пуст.")
    
    await message.answer("🗄 <b>Последние 5 выполненных задач:</b>")
    for t in tickets: await message.answer(build_card(t))

@dp.message(F.text == "📋 Свободные", StateFilter('*'))
async def list_pool(message: Message, state: FSMContext):
    await state.clear()
    tickets = fetch_query(f"SELECT * FROM tickets WHERE status = '{ST_NEW}' ORDER BY id DESC LIMIT 10")
    if not tickets: return await message.answer("📭 Свободных заявок нет.")

    for t in tickets:
        kb_buttons = [[InlineKeyboardButton(text="✋ Взять в работу", callback_data=f"take_{t['id']}")]]
        if t['creator_id'] == message.from_user.id:
            kb_buttons.append([InlineKeyboardButton(text="❌ Отменить заявку", callback_data=f"delete_{t['id']}")])
        await message.answer(build_card(t), reply_markup=InlineKeyboardMarkup(inline_keyboard=kb_buttons))

@dp.message(F.text == "💼 В работе", StateFilter('*'))
async def list_mine(message: Message, state: FSMContext):
    await state.clear()
    tickets = fetch_query(f"SELECT * FROM tickets WHERE status = '{ST_WIP}' AND assignee_id = ?", (message.from_user.id,))
    if not tickets: return await message.answer("💼 У вас нет активных задач.")
    
    for t in tickets: 
        await message.answer(build_card(t), reply_markup=get_action_kb(t['id'], True))

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
        "assignee": None, "return_reason": None, "senior_note": None, "assigned_at": None
    }
    
    tid = execute_query(
        "INSERT INTO tickets (ticket_number, client_name, comment, urgency, creator, creator_id, status) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (t_data['ticket_number'], t_data['client_name'], t_data['comment'], t_data['urgency'], t_data['creator'], callback.from_user.id, ST_NEW)
    )
    t_data['id'] = tid
    
    try:
        sent = await bot.send_message(chat_id=GROUP_ID, text=build_card(t_data), reply_markup=get_action_kb(tid, False))
        execute_query("UPDATE tickets SET group_message_id = ? WHERE id = ?", (sent.message_id, tid))
        res = "✅ Заявка сохранена и добавлена в Свободные."
    except Exception as e:
        res = f"✅ Сохранено.\n⚠️ <i>Ошибка отправки в чат: {e}</i>"
    
    with suppress(TelegramBadRequest): await callback.message.edit_text(res)
    await state.clear()

# === ОБРАБОТЧИКИ КНОПОК ===
@dp.callback_query(F.data.startswith("take_"))
async def act_take(callback: CallbackQuery):
    tid = int(callback.data.split("_")[1])
    t = fetch_query("SELECT * FROM tickets WHERE id = ?", (tid,))
    
    if not t: return await callback.answer("⚠️ Заявка не найдена в базе!", show_alert=True)
    
    if t[0]["status"] == ST_DEL:
        with suppress(TelegramBadRequest): await callback.message.edit_reply_markup(reply_markup=None)
        return await callback.answer("⚠️ Заявка была отменена автором!", show_alert=True)
    elif t[0]["status"] != ST_NEW:
        with suppress(TelegramBadRequest): await callback.message.edit_reply_markup(reply_markup=None)
        return await callback.answer("⚠️ Заявку уже забрал другой сотрудник!", show_alert=True)

    uname = get_user_name(callback.from_user)
    execute_query("UPDATE tickets SET status = ?, assignee = ?, assignee_id = ?, assigned_at = CURRENT_TIMESTAMP, sla_reminded = 0 WHERE id = ?", 
                  (ST_WIP, uname, callback.from_user.id, tid))
    
    upd_t = fetch_query("SELECT * FROM tickets WHERE id = ?", (tid,))[0]
    is_grp = callback.message.chat.type in ["group", "supergroup"]

    if upd_t['group_message_id']:
        with suppress(Exception): await bot.edit_message_text(chat_id=GROUP_ID, message_id=upd_t['group_message_id'], text=build_card(upd_t), reply_markup=get_action_kb(tid, True))

    if is_grp:
        await callback.answer("✅ Взято в работу. Проверьте личные сообщения.", show_alert=True)
    else:
        with suppress(TelegramBadRequest): await callback.message.edit_text(build_card(upd_t), reply_markup=get_action_kb(tid, True))
        await callback.answer("✅ Взято в работу!")

@dp.callback_query(F.data.startswith("close_"))
async def act_close(callback: CallbackQuery):
    tid = int(callback.data.split("_")[1])
    t = fetch_query("SELECT * FROM tickets WHERE id = ?", (tid,))
    
    if not t: return await callback.answer("⚠️ Заявка не найдена!", show_alert=True)
    if t[0]["assignee_id"] != callback.from_user.id: return await callback.answer("⚠️ Это не ваша заявка!", show_alert=True)

    execute_query("UPDATE tickets SET status = ? WHERE id = ?", (ST_DONE, tid))
    upd_t = fetch_query("SELECT * FROM tickets WHERE id = ?", (tid,))[0]
    is_grp = callback.message.chat.type in ["group", "supergroup"]
    
    if upd_t['group_message_id']:
        with suppress(Exception): await bot.edit_message_text(chat_id=GROUP_ID, message_id=upd_t['group_message_id'], text=build_card(upd_t), reply_markup=None)

    if not is_grp:
        with suppress(TelegramBadRequest): await callback.message.edit_text(build_card(upd_t), reply_markup=None)
    await callback.answer("✅ Задача выполнена и отправлена в Архив!", show_alert=True)

@dp.callback_query(F.data.startswith("delete_"))
async def act_delete(callback: CallbackQuery):
    tid = int(callback.data.split("_")[1])
    t = fetch_query("SELECT * FROM tickets WHERE id = ?", (tid,))
    if not t: return await callback.answer()
    
    if t[0]['creator_id'] != callback.from_user.id:
        return await callback.answer("⚠️ Только автор может отменить заявку!", show_alert=True)
        
    execute_query("UPDATE tickets SET status = ? WHERE id = ?", (ST_DEL, tid))
    
    if t[0]['group_message_id']:
        with suppress(Exception): await bot.edit_message_text(chat_id=GROUP_ID, message_id=t[0]['group_message_id'], text=f"❌ <b>ОТМЕНЕНА</b>\nЗаявка <code>#{html.escape(str(t[0]['ticket_number']))}</code> удалена автором.", reply_markup=None)

    with suppress(TelegramBadRequest): await callback.message.edit_text("❌ Заявка отменена.", reply_markup=None)
    await callback.answer()

# === МАГИЯ СНЯТИЯ ЗАДАЧИ ===
@dp.callback_query(F.data.startswith("return_"))
async def act_return(callback: CallbackQuery, state: FSMContext):
    tid = int(callback.data.split("_")[1])
    t = fetch_query("SELECT * FROM tickets WHERE id = ?", (tid,))
    if not t: return await callback.answer("⚠️ Заявка не найдена!", show_alert=True)
    if t[0]["assignee_id"] != callback.from_user.id: return await callback.answer("⚠️ Это не ваша заявка!", show_alert=True)

    is_grp = callback.message.chat.type in ["group", "supergroup"]

    if is_grp:
        user_state = FSMContext(storage=dp.storage, key=StorageKey(bot_id=bot.id, chat_id=callback.from_user.id, user_id=callback.from_user.id))
        await user_state.set_state(ReturnForm.reason)
        await user_state.update_data(ticket_id=tid)
        try:
            await bot.send_message(callback.from_user.id, f"✍️ <b>Напишите причину, по которой снимаете задачу <code>#{t[0]['ticket_number']}</code> с себя:</b>\n<i>(Она будет возвращена в 'Свободные')</i>")
            await callback.answer("Перейдите в личные сообщения с ботом для указания причины!", show_alert=True)
        except Exception:
            await callback.answer("⚠️ ОШИБКА: Сначала запустите бота в личных сообщениях!", show_alert=True)
    else:
        # Теперь не удаляем, а просто убираем кнопки, чтобы не было ошибки клиента Telegram
        with suppress(TelegramBadRequest): await callback.message.edit_reply_markup(reply_markup=None)
        await state.update_data(ticket_id=tid)
        await state.set_state(ReturnForm.reason)
        await callback.message.answer("✍️ <b>Напишите причину, по которой снимаете задачу с себя:</b>\n<i>(Она будет возвращена в 'Свободные')</i>")
        await callback.answer()

@dp.message(ReturnForm.reason)
async def act_return_save(message: Message, state: FSMContext):
    data = await state.get_data()
    tid = data.get("ticket_id")
    
    # Защита: если прислали стикер, фото и т.д.
    reason_text = message.text[:200] if message.text else "Указана медиа-причина"
    
    # Жесткий сброс всех счетчиков, возврат в пул.
    execute_query("UPDATE tickets SET status = ?, assignee = NULL, assignee_id = NULL, return_reason = ?, created_at = CURRENT_TIMESTAMP, assigned_at = NULL, alarm_sent = 0, sla_reminded = 0 WHERE id = ?", 
                  (ST_NEW, reason_text, tid))
    
    t = fetch_query("SELECT * FROM tickets WHERE id = ?", (tid,))[0]
    
    if t['group_message_id']:
        with suppress(Exception): await bot.edit_message_text(chat_id=GROUP_ID, message_id=t['group_message_id'], text=build_card(t), reply_markup=get_action_kb(tid, False))
    
    await message.answer("🔄 Заявка успешно снята и возвращена в пул.", reply_markup=main_kb)
    await state.clear()

# === ЗАПУСК ===
async def on_startup():
    init_db()
    asyncio.create_task(monitor_tasks())
    logging.info("СИСТЕМА БЕЗУПРЕЧНОГО ТРЕКИНГА ЗАПУЩЕНА")

async def main():
    dp.startup.register(on_startup)
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
