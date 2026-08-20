import asyncio
import logging
import sqlite3
import html
from datetime import datetime, timezone
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

# === НАСТРОЙКИ ЛОГИРОВАНИЯ ===
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logging.getLogger("aiogram.event").setLevel(logging.WARNING)

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

# === АСИНХРОННАЯ БАЗА ДАННЫХ (WAL MODE) ===
def init_db():
    with sqlite3.connect("tickets.db", timeout=20) as conn:
        conn.execute('PRAGMA journal_mode=WAL;')
        cursor = conn.cursor()
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS tickets (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ticket_number TEXT, client_name TEXT, comment TEXT, urgency TEXT,
                creator TEXT, creator_id INTEGER, assignee TEXT, assignee_id INTEGER,
                status TEXT, return_reason TEXT, senior_note TEXT, photo_id TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, assigned_at TIMESTAMP,
                sla_reminded INTEGER DEFAULT 0, alarm_sent INTEGER DEFAULT 0, group_message_id INTEGER
            )
        ''')
        with suppress(sqlite3.OperationalError):
            cursor.execute("ALTER TABLE tickets ADD COLUMN photo_id TEXT")
            
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_status ON tickets(status)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_group_msg ON tickets(group_message_id)")
        conn.commit()

async def execute_query(query, params=()):
    def _exec():
        with sqlite3.connect("tickets.db", timeout=20) as conn:
            cursor = conn.execute(query, params)
            conn.commit()
            return cursor.lastrowid
    return await asyncio.to_thread(_exec)

async def fetch_query(query, params=()):
    def _fetch():
        with sqlite3.connect("tickets.db", timeout=20) as conn:
            conn.row_factory = sqlite3.Row
            return [dict(row) for row in conn.execute(query, params).fetchall()]
    return await asyncio.to_thread(_fetch)

def get_elapsed_time(assigned_at_str):
    if not assigned_at_str: return "—"
    try:
        assigned_dt = datetime.strptime(assigned_at_str, '%Y-%m-%d %H:%M:%S')
        delta = datetime.now(timezone.utc).replace(tzinfo=None) - assigned_dt
        hours, remainder = divmod(int(delta.total_seconds()), 3600)
        minutes, _ = divmod(remainder, 60)
        if hours > 0: return f"{hours}ч {minutes}м"
        if minutes > 0: return f"{minutes}м"
        return "только что"
    except Exception:
        return "—"

# === FSM СОСТОЯНИЯ (ТОЛЬКО 2 ШАГА) ===
class TicketForm(StatesGroup):
    client = State()
    comment = State()

class ReturnForm(StatesGroup):
    ticket_id = State()
    reason = State()

# === UI / МЕНЮ ===
main_kb = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="📝 Создать"), KeyboardButton(text="📋 Свободные")],
        [KeyboardButton(text="💼 В работе"), KeyboardButton(text="👥 Команда")],
        [KeyboardButton(text="🗄 Архив")]
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
    if t['status'] == ST_DEL:
        return f"❌ <b>ОТМЕНЕНА</b>\nЗаявка <code>#{t['id']:04d}</code> удалена автором."

    assignee = html.escape(str(t['assignee'] or "—"))
    reason = f"\n⚠️ <b>Причина возврата:</b> <i>{html.escape(str(t['return_reason']))}</i>" if t['return_reason'] else ""
    note = f"\n💡 <b>Ремарка:</b> <i>{html.escape(str(t['senior_note']))}</i>" if t['senior_note'] else ""
    
    time_info = f"\n⏱ <b>В работе:</b> {get_elapsed_time(t['assigned_at'])}" if (t['status'] == ST_WIP and t['assigned_at']) else ""
    
    return (
        f"🎫 <b>Заявка</b> <code>#{t['id']:04d}</code>\n"
        f"Статус: <b>{t['status']}</b> | Приоритет: <b>{t['urgency']}</b>\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"👤 <b>Клиент:</b> {html.escape(str(t['client_name'] or '—'))}\n"
        f"💬 <b>Суть:</b> {html.escape(str(t['comment'] or '—'))}{reason}{note}\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"📝 <b>Создал:</b> {html.escape(str(t['creator'] or '—'))}\n"
        f"🛠 <b>Исполнитель:</b> {assignee}{time_info}"
    )

def get_action_kb(t_id, is_wip=False):
    if not is_wip:
        return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="✋ Взять в работу", callback_data=f"take_{t_id}")]])
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Выполнено", callback_data=f"close_{t_id}")],
        [InlineKeyboardButton(text="🔄 Снять с себя", callback_data=f"return_{t_id}")]
    ])

# === ЯДРО ОТПРАВКИ (ФОТО + ТЕКСТ) ===
async def send_card(chat_id, t, reply_markup=None):
    text = build_card(t)
    if t.get('photo_id'):
        return await bot.send_photo(chat_id=chat_id, photo=t['photo_id'], caption=text, reply_markup=reply_markup)
    return await bot.send_message(chat_id=chat_id, text=text, reply_markup=reply_markup)

async def edit_card(chat_id, message_id, t, reply_markup=None):
    text = build_card(t)
    with suppress(TelegramBadRequest):
        if t.get('photo_id'):
            await bot.edit_message_caption(chat_id=chat_id, message_id=message_id, caption=text, reply_markup=reply_markup)
        else:
            await bot.edit_message_text(chat_id=chat_id, message_id=message_id, text=text, reply_markup=reply_markup)

# ========================================================================
# 1. ПЕРЕХВАТЧИКИ МЕНЮ (НАХОДЯТСЯ СТРОГО ВВЕРХУ, ЧТОБЫ ОТМЕНЯТЬ ЗАВИСАНИЯ)
# ========================================================================

@dp.message(Command("start"), StateFilter('*'))
async def cmd_start(message: Message, state: FSMContext):
    await state.clear()
    await message.answer("Система маршрутизации заявок готова к работе.\nИспользуйте меню ниже 👇", reply_markup=main_kb)

@dp.message(F.text == "📝 Создать", StateFilter('*'))
async def ticket_start(message: Message, state: FSMContext):
    await state.clear() # Сбрасываем старые состояния
    await message.answer("👤 <b>Введите Имя или Данные клиента:</b>")
    await state.set_state(TicketForm.client)

@dp.message(F.text == "📋 Свободные", StateFilter('*'))
async def list_pool(message: Message, state: FSMContext):
    await state.clear()
    tickets = await fetch_query("SELECT * FROM tickets WHERE status = ? ORDER BY id DESC LIMIT 10", (ST_NEW,))
    if not tickets: return await message.answer("📭 Свободных заявок нет.")

    for t in tickets:
        kb_buttons = [[InlineKeyboardButton(text="✋ Взять в работу", callback_data=f"take_{t['id']}")]]
        if t['creator_id'] == message.from_user.id:
            kb_buttons.append([InlineKeyboardButton(text="❌ Отменить заявку", callback_data=f"delete_{t['id']}")])
        await send_card(message.chat.id, t, InlineKeyboardMarkup(inline_keyboard=kb_buttons))

@dp.message(F.text == "💼 В работе", StateFilter('*'))
async def list_mine(message: Message, state: FSMContext):
    await state.clear()
    tickets = await fetch_query("SELECT * FROM tickets WHERE status = ? AND assignee_id = ?", (ST_WIP, message.from_user.id))
    if not tickets: return await message.answer("💼 У вас нет активных задач.")
    for t in tickets: await send_card(message.chat.id, t, get_action_kb(t['id'], True))

@dp.message(F.text == "👥 Команда", StateFilter('*'))
async def cmd_active_users(message: Message, state: FSMContext):
    await state.clear()
    tickets = await fetch_query("SELECT * FROM tickets WHERE status = ? ORDER BY assignee", (ST_WIP,))
    if not tickets: return await message.answer("В данный момент ни у кого нет задач в работе.")
    
    users_tasks = {}
    for t in tickets: users_tasks.setdefault(t['assignee'], []).append(t)
    
    text = "👥 <b>РАБОЧИЙ МОНИТОРИНГ:</b>\n━━━━━━━━━━━━━━━━━━\n"
    for user, tasks in users_tasks.items():
        text += f"👤 <b>{html.escape(user)}</b>:\n"
        for t in tasks: text += f" ├ 🎫 <code>#{t['id']:04d}</code> (⏱ {get_elapsed_time(t['assigned_at'])})\n"
        text += "\n"
    await message.answer(text)

@dp.message(F.text == "🗄 Архив", StateFilter('*'))
async def cmd_archive(message: Message, state: FSMContext):
    await state.clear()
    tickets = await fetch_query("SELECT * FROM tickets WHERE status = ? ORDER BY id DESC LIMIT 5", (ST_DONE,))
    if not tickets: return await message.answer("🗄 Архив пуст.")
    
    await message.answer("🗄 <b>Последние 5 выполненных задач:</b>")
    for t in tickets: await send_card(message.chat.id, t)

# ========================================================================
# 2. ШАГИ СОЗДАНИЯ ЗАЯВКИ (ИДУТ ПОСЛЕ МЕНЮ)
# ========================================================================

@dp.message(TicketForm.client)
async def ticket_comment(message: Message, state: FSMContext):
    raw_text = message.text or message.caption or "Без имени"
    await state.update_data(client=raw_text[:100])
    await message.answer("💬 <b>Опишите суть проблемы (можно прикрепить фото):</b>")
    await state.set_state(TicketForm.comment)

@dp.message(TicketForm.comment)
async def ticket_urgency(message: Message, state: FSMContext):
    photo_id = message.photo[-1].file_id if message.photo else None
    raw_text = message.text or message.caption or "Без описания"
    
    await state.update_data(comment=raw_text[:600], photo_id=photo_id)
    await message.answer("⚡ <b>Выберите приоритет заявки:</b>", reply_markup=urgency_kb)

@dp.callback_query(F.data.startswith("urgency_"))
async def ticket_save(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    urg_map = {"urgency_low": "🟢 Низкий", "urgency_mid": "🟡 Средний", "urgency_high": "🔴 Высокий"}
    urg_val = urg_map.get(callback.data, "🟢 Низкий")
    creator_name = get_user_name(callback.from_user)
    
    # Записываем в базу и МГНОВЕННО получаем ID. Больше никакого ручного номера.
    tid = await execute_query(
        "INSERT INTO tickets (client_name, comment, urgency, creator, creator_id, status, photo_id) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (data.get('client', '—'), data.get('comment', '—'), urg_val, creator_name, callback.from_user.id, ST_NEW, data.get('photo_id'))
    )
    
    t_data = (await fetch_query("SELECT * FROM tickets WHERE id = ?", (tid,)))[0]
    
    try:
        sent = await send_card(GROUP_ID, t_data, get_action_kb(tid, False))
        await execute_query("UPDATE tickets SET group_message_id = ? WHERE id = ?", (sent.message_id, tid))
        res = f"✅ Заявка <b>#{tid:04d}</b> сохранена и отправлена."
    except Exception as e:
        res = f"✅ Заявка <b>#{tid:04d}</b> сохранена.\n⚠️ <i>Ошибка чата: {e}</i>"
    
    with suppress(TelegramBadRequest): await callback.message.edit_text(res)
    await state.clear()

# ========================================================================
# 3. ОБРАБОТЧИКИ КНОПОК И СМАРТ-РЕПЛАИ
# ========================================================================

@dp.message(F.reply_to_message & F.chat.type.in_({"group", "supergroup"}))
async def handle_smart_reply(message: Message):
    if message.reply_to_message.from_user.is_bot:
        t_data = await fetch_query("SELECT * FROM tickets WHERE group_message_id = ?", (message.reply_to_message.message_id,))
        if t_data and t_data[0]['status'] not in [ST_DONE, ST_DEL]:
            t = t_data[0]
            raw_text = message.text or message.caption or "Медиа-ответ"
            note = f"{raw_text[:100]} ({get_user_name(message.from_user)})"
            
            await execute_query("UPDATE tickets SET senior_note = ? WHERE id = ?", (note, t['id']))
            updated_t = (await fetch_query("SELECT * FROM tickets WHERE id = ?", (t['id'],)))[0]
            
            await edit_card(message.chat.id, t['group_message_id'], updated_t, get_action_kb(t['id'], updated_t['status'] == ST_WIP))
            with suppress(TelegramBadRequest): await message.delete()

@dp.callback_query(F.data.startswith("take_"))
async def act_take(callback: CallbackQuery):
    tid = int(callback.data.split("_")[1])
    t = await fetch_query("SELECT * FROM tickets WHERE id = ?", (tid,))
    
    if not t: return await callback.answer("⚠️ Заявка не найдена в базе!", show_alert=True)
    if t[0]["status"] != ST_NEW:
        with suppress(TelegramBadRequest): await callback.message.edit_reply_markup(reply_markup=None)
        return await callback.answer("⚠️ Статус изменен (Отменена или взята)!", show_alert=True)

    uname = get_user_name(callback.from_user)
    await execute_query("UPDATE tickets SET status = ?, assignee = ?, assignee_id = ?, assigned_at = CURRENT_TIMESTAMP, sla_reminded = 0 WHERE id = ?", 
                        (ST_WIP, uname, callback.from_user.id, tid))
    
    upd_t = (await fetch_query("SELECT * FROM tickets WHERE id = ?", (tid,)))[0]
    is_grp = callback.message.chat.type in ["group", "supergroup"]

    if upd_t['group_message_id']:
        await edit_card(GROUP_ID, upd_t['group_message_id'], upd_t, get_action_kb(tid, True))

    if is_grp:
        await callback.answer("✅ Взято в работу. Управление доступно в личке.", show_alert=True)
    else:
        await edit_card(callback.message.chat.id, callback.message.message_id, upd_t, get_action_kb(tid, True))
        await callback.answer("✅ Взято в работу!")

@dp.callback_query(F.data.startswith("close_"))
async def act_close(callback: CallbackQuery):
    tid = int(callback.data.split("_")[1])
    t = await fetch_query("SELECT * FROM tickets WHERE id = ?", (tid,))
    
    if not t: return await callback.answer("⚠️ Заявка не найдена!", show_alert=True)
    if t[0]["assignee_id"] != callback.from_user.id: return await callback.answer("⚠️ Это не ваша заявка!", show_alert=True)

    await execute_query("UPDATE tickets SET status = ? WHERE id = ?", (ST_DONE, tid))
    upd_t = (await fetch_query("SELECT * FROM tickets WHERE id = ?", (tid,)))[0]
    is_grp = callback.message.chat.type in ["group", "supergroup"]
    
    if upd_t['group_message_id']:
        await edit_card(GROUP_ID, upd_t['group_message_id'], upd_t, None)

    if not is_grp:
        await edit_card(callback.message.chat.id, callback.message.message_id, upd_t, None)
    await callback.answer("✅ Выполнено!", show_alert=True)

@dp.callback_query(F.data.startswith("delete_"))
async def act_delete(callback: CallbackQuery):
    tid = int(callback.data.split("_")[1])
    t = await fetch_query("SELECT * FROM tickets WHERE id = ?", (tid,))
    if not t: return await callback.answer()
    
    if t[0]['creator_id'] != callback.from_user.id:
        return await callback.answer("⚠️ Только автор может отменить!", show_alert=True)
        
    await execute_query("UPDATE tickets SET status = ? WHERE id = ?", (ST_DEL, tid))
    upd_t = (await fetch_query("SELECT * FROM tickets WHERE id = ?", (tid,)))[0]
    
    if upd_t['group_message_id']:
        await edit_card(GROUP_ID, upd_t['group_message_id'], upd_t, None)

    await edit_card(callback.message.chat.id, callback.message.message_id, upd_t, None)
    await callback.answer("Удалено.")

@dp.callback_query(F.data.startswith("return_"))
async def act_return(callback: CallbackQuery, state: FSMContext):
    tid = int(callback.data.split("_")[1])
    t = await fetch_query("SELECT * FROM tickets WHERE id = ?", (tid,))
    if not t: return await callback.answer("⚠️ Не найдено!", show_alert=True)
    if t[0]["assignee_id"] != callback.from_user.id: return await callback.answer("⚠️ Это не ваша заявка!", show_alert=True)

    is_grp = callback.message.chat.type in ["group", "supergroup"]

    if is_grp:
        user_state = FSMContext(storage=dp.storage, key=StorageKey(bot_id=bot.id, chat_id=callback.from_user.id, user_id=callback.from_user.id))
        await user_state.set_state(ReturnForm.reason)
        await user_state.update_data(ticket_id=tid)
        try:
            await bot.send_message(callback.from_user.id, f"✍️ <b>Причина снятия задачи <code>#{tid:04d}</code>:</b>\n<i>(Используйте меню для отмены)</i>")
            await callback.answer("Напишите причину в личных сообщениях бота!", show_alert=True)
        except Exception:
            await callback.answer("⚠️ ОШИБКА: Запустите бота в личке!", show_alert=True)
    else:
        with suppress(TelegramBadRequest): await callback.message.edit_reply_markup(reply_markup=None)
        await state.update_data(ticket_id=tid)
        await state.set_state(ReturnForm.reason)
        await callback.message.answer("✍️ <b>Причина снятия с себя:</b>")
        await callback.answer()

@dp.message(ReturnForm.reason)
async def act_return_save(message: Message, state: FSMContext):
    data = await state.get_data()
    tid = data.get("ticket_id")
    reason_text = (message.text or message.caption or "Медиа-причина")[:100]
    
    await execute_query("UPDATE tickets SET status = ?, assignee = NULL, assignee_id = NULL, return_reason = ?, created_at = CURRENT_TIMESTAMP, assigned_at = NULL, alarm_sent = 0, sla_reminded = 0 WHERE id = ?", 
                        (ST_NEW, reason_text, tid))
    
    t = (await fetch_query("SELECT * FROM tickets WHERE id = ?", (tid,)))[0]
    
    if t['group_message_id']:
        await edit_card(GROUP_ID, t['group_message_id'], t, get_action_kb(tid, False))
    
    await message.answer("🔄 Заявка возвращена в пул.", reply_markup=main_kb)
    await state.clear()

# ========================================================================
# 4. ФОНОВЫЕ ЗАДАЧИ И ЗАПУСК БОТА
# ========================================================================

async def monitor_tasks():
    while True:
        await asyncio.sleep(60)
        try:
            alarm_tickets = await fetch_query("SELECT * FROM tickets WHERE status = ? AND urgency = '🔴 Высокий' AND alarm_sent = 0 AND created_at <= datetime('now', '-15 minutes')", (ST_NEW,))
            for t in alarm_tickets:
                try:
                    sent_msg = await send_card(GROUP_ID, t, get_action_kb(t['id'], False))
                    await execute_query("UPDATE tickets SET alarm_sent = 1, group_message_id = ? WHERE id = ?", (sent_msg.message_id, t['id']))
                    if t['group_message_id']:
                        with suppress(TelegramBadRequest): await bot.delete_message(chat_id=GROUP_ID, message_id=t['group_message_id'])
                except Exception: pass

            sla_tickets = await fetch_query("SELECT * FROM tickets WHERE status = ? AND sla_reminded = 0 AND assigned_at <= datetime('now', '-1 hour')", (ST_WIP,))
            for t in sla_tickets:
                if t['assignee_id']:
                    with suppress(TelegramBadRequest):
                        await bot.send_message(chat_id=t['assignee_id'], text=f"⏳ <b>НАРУШЕНИЕ SLA (> 1 ЧАСА)</b>\nПожалуйста, завершите задачу или снимите её с себя!")
                        await send_card(t['assignee_id'], t)
                        await execute_query("UPDATE tickets SET sla_reminded = 1 WHERE id = ?", (t['id'],))
        except Exception: pass

async def on_startup():
    init_db()
    asyncio.create_task(monitor_tasks())
    logging.info("🚀 СИСТЕМА УЛЬТИМАТИВНО ЗАПУЩЕНА")

async def main():
    dp.startup.register(on_startup)
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
