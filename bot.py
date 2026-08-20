import asyncio
import logging
import sqlite3
import html
from contextlib import suppress

from aiogram import Bot, Dispatcher, F
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton, ReplyKeyboardMarkup, KeyboardButton
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.exceptions import TelegramBadRequest

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

# === ТВОИ ДАННЫЕ ===
BOT_TOKEN = "8684957172:AAHhJAfdLnbmAw-AAAYuvNI0j8q0dz9IBYA"
SENIOR_CHAT_ID = -1004340807494  # Строго без кавычек, число с минусом

bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher()

# === БАЗА ДАННЫХ ===
def init_db():
    with sqlite3.connect("tickets.db", timeout=20) as conn:
        cursor = conn.cursor()
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS tickets (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ticket_number TEXT,
                client_name TEXT,
                comment TEXT,
                urgency TEXT,
                creator TEXT,
                creator_id INTEGER,
                assignee TEXT,
                assignee_id INTEGER,
                status TEXT,
                return_reason TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                assigned_at TIMESTAMP,
                sla_reminded INTEGER DEFAULT 0,
                alarm_sent INTEGER DEFAULT 0
            )
        ''')
        columns = [
            ("creator_id", "INTEGER"), ("assignee_id", "INTEGER"), 
            ("return_reason", "TEXT"), ("assigned_at", "TIMESTAMP"), 
            ("sla_reminded", "INTEGER DEFAULT 0"), ("alarm_sent", "INTEGER DEFAULT 0")
        ]
        for col, dtype in columns:
            with suppress(sqlite3.OperationalError):
                cursor.execute(f"ALTER TABLE tickets ADD COLUMN {col} {dtype}")
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

# === СОСТОЯНИЯ FSM ===
class EscalateForm(StatesGroup):
    ticket_number = State()
    client_name = State()
    comment = State()

class ReturnForm(StatesGroup):
    ticket_id = State()
    reason = State()

# === МЕНЮ И КЛАВИАТУРЫ ===
main_menu_kb = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="📝 Создать"), KeyboardButton(text="📋 Пул")],
        [KeyboardButton(text="💼 В работе"), KeyboardButton(text="📊 Стат")],
        [KeyboardButton(text="ℹ️ Инфо")]
    ],
    resize_keyboard=True
)

urgency_kb = InlineKeyboardMarkup(inline_keyboard=[
    [InlineKeyboardButton(text="🟢 Низкая", callback_data="urgency_low")],
    [InlineKeyboardButton(text="🟡 Средняя", callback_data="urgency_mid")],
    [InlineKeyboardButton(text="🔴 Высокая", callback_data="urgency_high")]
])

def get_user_name(user):
    return f"@{user.username}" if user.username else user.first_name

def build_card(t):
    status_icon = "🆕" if t['status'] == "🔴 НОВАЯ" else "⏳" if t['status'] == "🟡 В РАБОТЕ" else "✅"
    assignee = html.escape(str(t['assignee'])) if t['assignee'] else "⏳ Ожидает"
    
    reason_text = html.escape(str(t['return_reason'])) if t['return_reason'] else ""
    reason_block = f"\n⚠️ <b>Причина возврата:</b> <i>{reason_text}</i>" if reason_text else ""
    
    return (
        f"{status_icon} <b>{t['status']}</b>\n"
        f"➖➖➖➖➖➖➖➖\n"
        f"🆔 <b>№:</b> {html.escape(str(t['ticket_number']))}\n"
        f"👤 <b>Клиент:</b> {html.escape(str(t['client_name']))}\n"
        f"⚡ <b>Срочность:</b> {t['urgency']}\n"
        f"💬 <b>Суть:</b> {html.escape(str(t['comment']))}{reason_block}\n"
        f"➖➖➖➖➖➖➖➖\n"
        f"✍️ <b>Автор:</b> {html.escape(str(t['creator']))}\n"
        f"🛠 <b>Взял:</b> {assignee}"
    )

# === СИСТЕМА ИНТЕРАКТИВНЫХ УВЕДОМЛЕНИЙ В ГРУППУ ===
async def notify_group(text, reply_markup=None):
    try:
        await bot.send_message(chat_id=SENIOR_CHAT_ID, text=text, reply_markup=reply_markup)
        return True, ""
    except Exception as e:
        logging.error(f"Ошибка уведомления: {e}")
        return False, str(e)

# === ФОНОВЫЕ ПРОЦЕССЫ (SLA И АЛАРМЫ) ===
async def monitor_tasks():
    while True:
        await asyncio.sleep(60)
        try:
            # Аларм: Важные задачи лежат более 15 минут
            alarm_tickets = fetch_query("SELECT * FROM tickets WHERE status = '🔴 НОВАЯ' AND urgency = '🔴 Высокая' AND alarm_sent = 0 AND created_at <= datetime('now', '-15 minutes')")
            for t in alarm_tickets:
                # Кнопка прямо в аларме!
                chat_kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="✋ Спасти задачу (Взять)", callback_data=f"take_{t['id']}")]])
                await notify_group(f"🔥 <b>АЛАРМ! ЗАЯВКА ГОРИТ!</b>\nНикто не берет важную задачу!\n\n{build_card(t)}", reply_markup=chat_kb)
                execute_query("UPDATE tickets SET alarm_sent = 1 WHERE id = ?", (t['id'],))

            # SLA: Задачи в работе более 2 часов
            sla_tickets = fetch_query("SELECT * FROM tickets WHERE status = '🟡 В РАБОТЕ' AND sla_reminded = 0 AND assigned_at <= datetime('now', '-2 hours')")
            for t in sla_tickets:
                if t['assignee_id']:
                    with suppress(Exception):
                        await bot.send_message(chat_id=t['assignee_id'], text=f"⏱ <b>SLA НАПОМИНАНИЕ!</b>\nЗадача у вас более 2 часов. Нужна помощь?\n\n{build_card(t)}")
                        execute_query("UPDATE tickets SET sla_reminded = 1 WHERE id = ?", (t['id'],))
        except Exception as e:
            logging.error(f"Ошибка монитора SLA: {e}")

# === ИНФО И СТАТИСТИКА ===
@dp.message(Command("start"), StateFilter('*'))
@dp.message(F.text == "ℹ️ Инфо", StateFilter('*'))
async def cmd_help(message: Message, state: FSMContext):
    await state.clear()
    text = (
        "🤖 <b>CRM Эскалаций</b>\n\n"
        "📝 <b>Создать:</b> Завести тикет.\n"
        "📋 <b>Пул:</b> Взять свободные задачи.\n"
        "💼 <b>В работе:</b> Управление своими задачами (завершить/вернуть).\n"
        "📊 <b>Стат:</b> Твои успехи.\n\n"
        "<i>Используй меню ниже 👇</i>"
    )
    await message.answer(text, reply_markup=main_menu_kb)

@dp.message(F.text == "📊 Стат", StateFilter('*'))
async def cmd_stats(message: Message, state: FSMContext):
    await state.clear()
    user_id = message.from_user.id
    
    t_new = fetch_query("SELECT COUNT(*) as c FROM tickets WHERE status = '🔴 НОВАЯ'")[0]['c']
    t_wip = fetch_query("SELECT COUNT(*) as c FROM tickets WHERE status = '🟡 В РАБОТЕ'")[0]['c']
    t_solved = fetch_query("SELECT COUNT(*) as c FROM tickets WHERE status = '✅ РЕШЕНА'")[0]['c']
    my_solved = fetch_query("SELECT COUNT(*) as c FROM tickets WHERE status = '✅ РЕШЕНА' AND assignee_id = ?", (user_id,))[0]['c']
    
    text = (
        f"📊 <b>ДАШБОРД CRM</b>\n"
        f"➖➖➖➖➖➖➖➖\n"
        f"🆕 В пуле: <b>{t_new}</b>\n"
        f"⏳ В работе: <b>{t_wip}</b>\n"
        f"✅ Решено всего: <b>{t_solved}</b>\n\n"
        f"🏆 <b>Твой вклад:</b> <b>{my_solved}</b> заявок!"
    )
    await message.answer(text)

# === 1. СОЗДАНИЕ ЗАЯВКИ ===
@dp.message(F.text == "📝 Создать", StateFilter('*'))
@dp.message(Command("escalate"), StateFilter('*'))
async def start_form(message: Message, state: FSMContext):
    await state.clear()
    await message.answer("🆔 Введите **номер заявки** (или ID):", reply_markup=main_menu_kb)
    await state.set_state(EscalateForm.ticket_number)

@dp.message(EscalateForm.ticket_number)
async def process_ticket(message: Message, state: FSMContext):
    await state.update_data(ticket_number=message.text)
    await message.answer("👤 Введите **данные клиента**:")
    await state.set_state(EscalateForm.client_name)

@dp.message(EscalateForm.client_name)
async def process_client(message: Message, state: FSMContext):
    await state.update_data(client_name=message.text)
    await message.answer("💬 Введите **суть/комментарий**:")
    await state.set_state(EscalateForm.comment)

@dp.message(EscalateForm.comment)
async def process_comment(message: Message, state: FSMContext):
    await state.update_data(comment=message.text)
    await message.answer("⚡ Выберите **срочность**:", reply_markup=urgency_kb)

@dp.callback_query(F.data.startswith("urgency_"))
async def process_urgency(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    urgency_map = {"urgency_low": "🟢 Низкая", "urgency_mid": "🟡 Средняя", "urgency_high": "🔴 Высокая"}
    
    t_data = {
        "ticket_number": data.get('ticket_number', '-'),
        "client_name": data.get('client_name', '-'),
        "comment": data.get('comment', '-'),
        "urgency": urgency_map.get(callback.data, "🟢 Низкая"),
        "creator": get_user_name(callback.from_user),
        "status": "🔴 НОВАЯ",
        "assignee": None,
        "return_reason": None
    }
    
    # Сохраняем в БД и получаем ID новой задачи
    ticket_id = execute_query(
        "INSERT INTO tickets (ticket_number, client_name, comment, urgency, creator, creator_id, status) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (t_data['ticket_number'], t_data['client_name'], t_data['comment'], t_data['urgency'], t_data['creator'], callback.from_user.id, t_data['status'])
    )

    t_data['id'] = ticket_id # Добавляем ID в словарь для карточки

    # Формируем кнопку для отправки в группу
    group_kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✋ Взять в работу", callback_data=f"take_{ticket_id}")]
    ])

    success, err = await notify_group(f"🔔 <b>НОВАЯ ЗАЯВКА В ПУЛЕ</b>\n\n{build_card(t_data)}", reply_markup=group_kb)
    
    res_text = "✅ Заявка улетела в пул!" if success else f"✅ Заявка создана!\n⚠️ <i>Ошибка отправки в группу: {err}</i>"
    
    with suppress(TelegramBadRequest):
        await callback.message.edit_text(res_text)
    await state.clear()

# === 2. ПУЛ ЗАЯВОК ===
@dp.message(F.text == "📋 Пул", StateFilter('*'))
async def show_pool_tickets(message: Message, state: FSMContext):
    await state.clear()
    free_tickets = fetch_query("SELECT * FROM tickets WHERE status = '🔴 НОВАЯ' ORDER BY id DESC LIMIT 10")

    if not free_tickets:
        return await message.answer("📭 Пул пуст. Отдыхаем!", reply_markup=main_menu_kb)

    await message.answer("📋 <b>Свободные задачи:</b>")
    user_id = message.from_user.id

    for t in free_tickets:
        buttons = [[InlineKeyboardButton(text="✋ Взять в работу", callback_data=f"take_{t['id']}")]]
        if t['creator_id'] == user_id:
            buttons.append([InlineKeyboardButton(text="❌ Удалить", callback_data=f"delete_{t['id']}")])
            
        await message.answer(build_card(t), reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))

# === 3. МОИ ЗАДАЧИ ===
@dp.message(F.text == "💼 В работе", StateFilter('*'))
async def show_my_tasks(message: Message, state: FSMContext):
    await state.clear()
    user_id = message.from_user.id
    my_tickets = fetch_query("SELECT * FROM tickets WHERE status = '🟡 В РАБОТЕ' AND assignee_id = ?", (user_id,))

    if not my_tickets:
        return await message.answer("💼 У вас нет задач. Возьмите их из пула!", reply_markup=main_menu_kb)

    await message.answer("💼 <b>Ваши задачи:</b>")

    for t in my_tickets:
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="✅ Завершить", callback_data=f"close_{t['id']}")],
            [InlineKeyboardButton(text="🔄 Вернуть в пул", callback_data=f"return_{t['id']}")]
        ])
        await message.answer(build_card(t), reply_markup=kb)

# === ДЕЙСТВИЯ (ИНТЕРАКТИВНЫЕ КНОПКИ) ===
@dp.callback_query(F.data.startswith("take_"))
async def handle_take_task(callback: CallbackQuery):
    ticket_id = int(callback.data.split("_")[1])
    t_data = fetch_query("SELECT * FROM tickets WHERE id = ?", (ticket_id,))
    
    if not t_data or t_data[0]["status"] != "🔴 НОВАЯ":
        with suppress(TelegramBadRequest):
            await callback.message.delete()
        return await callback.answer("⚠️ Кто-то уже забрал заявку!", show_alert=True)

    user_name = get_user_name(callback.from_user)
    execute_query("UPDATE tickets SET status = '🟡 В РАБОТЕ', assignee = ?, assignee_id = ?, assigned_at = CURRENT_TIMESTAMP, sla_reminded = 0 WHERE id = ?", 
                  (user_name, callback.from_user.id, ticket_id))

    updated_t = fetch_query("SELECT * FROM tickets WHERE id = ?", (ticket_id,))[0]
    
    # ПРОВЕРЯЕМ, ГДЕ БЫЛА НАЖАТА КНОПКА (В ГРУППЕ ИЛИ В ЛИЧКЕ)
    is_group = callback.message.chat.type in ["group", "supergroup"]

    if is_group:
        # Если нажали в группе -> Обновляем сообщение в группе + кнопка "Завершить"
        group_kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="✅ Завершить", callback_data=f"close_{ticket_id}")]
        ])
        with suppress(TelegramBadRequest):
            await callback.message.edit_text(f"🟡 <b>ЗАДАЧА В РАБОТЕ</b>\n\n{build_card(updated_t)}", reply_markup=group_kb)
        
        await callback.answer("✅ Вы взяли задачу! Можете управлять ей в личке с ботом.", show_alert=True)
        
        # Дублируем уведомление в личку специалисту
        with suppress(Exception):
            await bot.send_message(chat_id=callback.from_user.id, text=f"✅ Вы забрали заявку <b>№{html.escape(str(updated_t['ticket_number']))}</b> из чата!\nОна добавлена в ваши задачи.")
    else:
        # Если нажали в личке (Пул) -> Обновляем личку + уведомляем группу (без кнопок)
        await notify_group(f"🟡 <b>ЗАДАЧА В РАБОТЕ</b>\nЗаявку <b>№{html.escape(str(updated_t['ticket_number']))}</b> взял {user_name}\n\n{build_card(updated_t)}")
        
        private_kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="✅ Завершить", callback_data=f"close_{ticket_id}")],
            [InlineKeyboardButton(text="🔄 Вернуть в пул", callback_data=f"return_{ticket_id}")]
        ])
        with suppress(TelegramBadRequest):
            await callback.message.edit_text(build_card(updated_t), reply_markup=private_kb)
        await callback.answer("✅ Взято в работу!")

@dp.callback_query(F.data.startswith("close_"))
async def close_task(callback: CallbackQuery):
    ticket_id = int(callback.data.split("_")[1])
    t_data = fetch_query("SELECT * FROM tickets WHERE id = ?", (ticket_id,))
    
    if not t_data or t_data[0]["assignee_id"] != callback.from_user.id:
        return await callback.answer("⚠️ Вы не можете закрыть чужую заявку!", show_alert=True)

    execute_query("UPDATE tickets SET status = '✅ РЕШЕНА' WHERE id = ?", (ticket_id,))
    updated_t = fetch_query("SELECT * FROM tickets WHERE id = ?", (ticket_id,))[0]
    
    is_group = callback.message.chat.type in ["group", "supergroup"]
    
    if is_group:
        # Если закрыли прямо из группы
        with suppress(TelegramBadRequest):
            await callback.message.edit_text(f"✅ <b>ЗАДАЧА РЕШЕНА</b>\n\n{build_card(updated_t)}")
        await callback.answer("🚀 Задача успешно решена!", show_alert=True)
    else:
        # Если закрыли из лички
        await notify_group(f"✅ <b>ЗАДАЧА РЕШЕНА</b>\nЗаявку <b>№{html.escape(str(updated_t['ticket_number']))}</b> закрыл {updated_t['assignee']}\n\n{build_card(updated_t)}")
        with suppress(TelegramBadRequest):
            await callback.message.edit_text(build_card(updated_t))
        await callback.answer("🚀 Задача решена!", show_alert=True)

@dp.callback_query(F.data.startswith("delete_"))
async def delete_task(callback: CallbackQuery):
    ticket_id = int(callback.data.split("_")[1])
    t_data = fetch_query("SELECT * FROM tickets WHERE id = ?", (ticket_id,))
    
    execute_query("UPDATE tickets SET status = '❌ УДАЛЕНА' WHERE id = ?", (ticket_id,))
    
    if t_data:
        await notify_group(f"❌ <b>ЗАЯВКА УДАЛЕНА</b>\nЗаявка <b>№{html.escape(str(t_data[0]['ticket_number']))}</b> отменена создателем.")

    with suppress(TelegramBadRequest):
        await callback.message.edit_text("❌ <i>Заявка удалена.</i>")
    await callback.answer("Удалено.")

# === ВОЗВРАТ В ПУЛ С ПРИЧИНОЙ ===
@dp.callback_query(F.data.startswith("return_"))
async def return_task_start(callback: CallbackQuery, state: FSMContext):
    ticket_id = int(callback.data.split("_")[1])
    with suppress(TelegramBadRequest):
        await callback.message.delete()
    
    await state.update_data(ticket_id=ticket_id)
    await state.set_state(ReturnForm.reason)
    await callback.message.answer("✍️ <b>Напишите причину возврата:</b>\n<i>(Или нажмите любую кнопку меню для отмены)</i>")
    await callback.answer()

@dp.message(ReturnForm.reason)
async def process_return_reason(message: Message, state: FSMContext):
    data = await state.get_data()
    ticket_id = data.get("ticket_id")
    t_data = fetch_query("SELECT * FROM tickets WHERE id = ?", (ticket_id,))
    
    execute_query(
        "UPDATE tickets SET status = '🔴 НОВАЯ', assignee = NULL, assignee_id = NULL, return_reason = ?, created_at = CURRENT_TIMESTAMP, alarm_sent = 0 WHERE id = ?", 
        (message.text, ticket_id)
    )
    
    if t_data:
        # Уведомляем группу о возврате + снова вешаем кнопку "Взять"
        return_kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="✋ Взять в работу", callback_data=f"take_{ticket_id}")]])
        msg_text = f"🔄 <b>ЗАДАЧА ВЕРНУЛАСЬ В ПУЛ</b>\nЗаявку <b>№{html.escape(str(t_data[0]['ticket_number']))}</b> вернули.\n⚠️ Причина: <i>{html.escape(str(message.text))}</i>\n\n{build_card(fetch_query('SELECT * FROM tickets WHERE id = ?', (ticket_id,))[0])}"
        await notify_group(msg_text, reply_markup=return_kb)
    
    await message.answer("🔄 <i>Заявка возвращена в пул.</i>", reply_markup=main_menu_kb)
    await state.clear()

# === ЖИЗНЕННЫЙ ЦИКЛ БОТА ===
async def on_startup():
    init_db()
    asyncio.create_task(monitor_tasks())
    logging.info("CRM Бот успешно запущен!")

async def main():
    dp.startup.register(on_startup)
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
