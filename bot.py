import asyncio
import logging
import sqlite3
from aiogram import Bot, Dispatcher, F
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton, ReplyKeyboardMarkup, KeyboardButton
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup

logging.basicConfig(level=logging.INFO)

# === ТВОИ ДАННЫЕ ===
BOT_TOKEN = "8684957172:AAHhJAfdLnbmAw-AAAYuvNI0j8q0dz9IBYA"
SENIOR_CHAT_ID = "6516986078"

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

# === БАЗА ДАННЫХ (SQLITE) ===
def init_db():
    """Создает файл базы данных и таблицу, если их еще нет."""
    with sqlite3.connect("tickets.db") as conn:
        cursor = conn.cursor()
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS tickets (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ticket_number TEXT,
                client_name TEXT,
                comment TEXT,
                urgency TEXT,
                creator TEXT,
                assignee TEXT,
                status TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        conn.commit()

def execute_query(query, params=()):
    """Функция для изменения данных (INSERT, UPDATE, DELETE)"""
    with sqlite3.connect("tickets.db") as conn:
        cursor = conn.cursor()
        cursor.execute(query, params)
        conn.commit()
        return cursor.lastrowid

def fetch_query(query, params=()):
    """Функция для получения данных (SELECT)"""
    with sqlite3.connect("tickets.db") as conn:
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute(query, params)
        return [dict(row) for row in cursor.fetchall()]

# === СОСТОЯНИЯ FSM ===
class EscalateForm(StatesGroup):
    ticket_number = State()
    client_name = State()
    comment = State()

# === КЛАВИАТУРЫ ===
main_menu_kb = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="📝 Создать заявку")],
        [KeyboardButton(text="📋 Пул заявок (Новые)"), KeyboardButton(text="💼 Мои задачи")],
        [KeyboardButton(text="ℹ️ Как пользоваться?")]
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

# === ИНСТРУКЦИЯ И СТАРТ ===
@dp.message(Command("start"))
@dp.message(F.text == "ℹ️ Как пользоваться?")
async def cmd_help(message: Message, state: FSMContext):
    await state.clear()
    help_text = (
        "🤖 <b>Добро пожаловать в CRM систему эскалаций!</b>\n\n"
        "<b>Маршрут работы:</b>\n"
        "1️⃣ <b>Создание:</b> Жми «📝 Создать заявку». После заполнения она улетит в общий пул.\n"
        "2️⃣ <b>Взятие в работу:</b> В разделе «📋 Пул заявок» лежат все свободные задачи. Нажми «Взять в работу», чтобы закрепить её за собой.\n"
        "3️⃣ <b>Управление:</b> В разделе «💼 Мои задачи» ты можешь <b>✅ Завершить</b> заявку или <b>🔄 Вернуть в пул</b>, если не справляешься.\n"
        "4️⃣ <b>Удаление:</b> Ошибся при создании? В Пуле под твоей заявкой будет кнопка <b>❌ Удалить</b>.\n\n"
        "<i>Все заявки надежно сохраняются в базе данных.</i>"
    )
    await message.answer(help_text, reply_markup=main_menu_kb, parse_mode="HTML")

# === СОЗДАНИЕ ЗАЯВКИ ===
@dp.message(F.text == "📝 Создать заявку")
@dp.message(Command("escalate"))
async def start_form(message: Message, state: FSMContext):
    await state.clear()
    await message.answer("📝 Введите **номер заявки** (или ID):", reply_markup=main_menu_kb)
    await state.set_state(EscalateForm.ticket_number)

@dp.message(EscalateForm.ticket_number)
async def process_ticket(message: Message, state: FSMContext):
    await state.update_data(ticket_number=message.text)
    await message.answer("👤 Введите **данные клиента**:")
    await state.set_state(EscalateForm.client_name)

@dp.message(EscalateForm.client_name)
async def process_client(message: Message, state: FSMContext):
    await state.update_data(client_name=message.text)
    await message.answer("💬 Введите **комментарий** к заявке:")
    await state.set_state(EscalateForm.comment)

@dp.message(EscalateForm.comment)
async def process_comment(message: Message, state: FSMContext):
    await state.update_data(comment=message.text)
    await message.answer("⚠️ Выберите **срочность заявки**:", reply_markup=urgency_kb)

@dp.callback_query(F.data.startswith("urgency_"))
async def process_urgency(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    urgency_map = {"urgency_low": "🟢 Низкая", "urgency_mid": "🟡 Средняя", "urgency_high": "🔴 Высокая"}
    
    author_name = get_user_name(callback.from_user)
    urgency_text = urgency_map.get(callback.data, "🟢 Низкая")
    
    # Сохраняем в Базу Данных
    ticket_id = execute_query(
        "INSERT INTO tickets (ticket_number, client_name, comment, urgency, creator, status) VALUES (?, ?, ?, ?, ?, ?)",
        (data.get('ticket_number'), data.get('client_name'), data.get('comment'), urgency_text, author_name, "🔴 НОВАЯ")
    )

    ticket_msg = (
        f"🆕 <b>НОВАЯ ЗАЯВКА В ПУЛЕ</b>\n\n"
        f"📌 <b>Номер:</b> {data.get('ticket_number')}\n"
        f"👤 <b>Клиент:</b> {data.get('client_name')}\n"
        f"⚡ <b>Срочность:</b> {urgency_text}\n"
        f"💬 <b>Комментарий:</b> {data.get('comment')}\n"
        f"📝 <b>Создатель:</b> {author_name}"
    )
    
    # Уведомляем старших в чат
    try:
        await bot.send_message(chat_id=SENIOR_CHAT_ID, text=ticket_msg, parse_mode="HTML")
    except Exception:
        pass
    
    await callback.message.edit_text("✅ Заявка успешно сохранена в базу! Она доступна в разделе «📋 Пул заявок».")
    await state.clear()

# === 1. ПУЛ ЗАЯВОК ===
@dp.message(F.text == "📋 Пул заявок (Новые)")
async def show_pool_tickets(message: Message):
    free_tickets = fetch_query("SELECT * FROM tickets WHERE status = '🔴 НОВАЯ'")

    if not free_tickets:
        await message.answer("📭 В пуле сейчас нет новых заявок. Отличная работа!", reply_markup=main_menu_kb)
        return

    await message.answer("📋 <b>Свободные заявки (выберите, чтобы взять):</b>", parse_mode="HTML")

    current_user = get_user_name(message.from_user)

    for t in free_tickets:
        text = (
            f"📌 <b>Заявка №{t['ticket_number']}</b>\n"
            f"👤 Клиент: {t['client_name']}\n"
            f"⚡ Срочность: {t['urgency']}\n"
            f"💬 Комментарий: {t['comment']}\n"
            f"📝 Создатель: {t['creator']}"
        )
        
        buttons = [[InlineKeyboardButton(text="✋ Взять в работу", callback_data=f"take_{t['id']}")]]
        if t['creator'] == current_user:
            buttons.append([InlineKeyboardButton(text="❌ Удалить мою заявку", callback_data=f"delete_{t['id']}")])
            
        kb = InlineKeyboardMarkup(inline_keyboard=buttons)
        await message.answer(text, parse_mode="HTML", reply_markup=kb)

# === 2. МОИ ЗАДАЧИ ===
@dp.message(F.text == "💼 Мои задачи")
async def show_my_tasks(message: Message):
    current_user_name = get_user_name(message.from_user)
    my_tickets = fetch_query("SELECT * FROM tickets WHERE status = '🟡 В РАБОТЕ' AND assignee = ?", (current_user_name,))

    if not my_tickets:
        await message.answer("💼 У вас пока нет задач в работе. Загляните в «📋 Пул заявок»!", reply_markup=main_menu_kb)
        return

    await message.answer("💼 <b>Ваши текущие задачи:</b>", parse_mode="HTML")

    for t in my_tickets:
        text = (
            f"🟡 <b>В РАБОТЕ</b>\n"
            f"📌 <b>Номер:</b> {t['ticket_number']}\n"
            f"👤 <b>Клиент:</b> {t['client_name']}\n"
            f"💬 <b>Комментарий:</b> {t['comment']}\n"
            f"⚡ <b>Срочность:</b> {t['urgency']}"
        )
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="✅ Завершить заявку", callback_data=f"close_{t['id']}")],
            [InlineKeyboardButton(text="🔄 Вернуть в пул", callback_data=f"return_{t['id']}")]
        ])
        await message.answer(text, parse_mode="HTML", reply_markup=kb)

# === ОБРАБОТЧИКИ КНОПОК УПРАВЛЕНИЯ ===

# Взять задачу
@dp.callback_query(F.data.startswith("take_"))
async def handle_take_task(callback: CallbackQuery):
    ticket_id = int(callback.data.split("_")[1])
    ticket_data = fetch_query("SELECT * FROM tickets WHERE id = ?", (ticket_id,))
    
    if not ticket_data or ticket_data[0]["status"] != "🔴 НОВАЯ":
        await callback.answer("⚠️ Кто-то другой уже забрал эту заявку или она удалена!", show_alert=True)
        await callback.message.delete()
        return

    ticket = ticket_data[0]
    assignee_name = get_user_name(callback.from_user)
    
    # Обновляем в БД
    execute_query("UPDATE tickets SET status = ?, assignee = ? WHERE id = ?", ("🟡 В РАБОТЕ", assignee_name, ticket_id))

    updated_text = (
        f"🟡 <b>ЗАЯВКА В РАБОТЕ</b>\n\n"
        f"📌 <b>Номер:</b> {ticket['ticket_number']}\n"
        f"👤 <b>Клиент:</b> {ticket['client_name']}\n"
        f"⚡ <b>Срочность:</b> {ticket['urgency']}\n"
        f"💬 <b>Комментарий:</b> {ticket['comment']}\n"
        f"📝 <b>Создатель:</b> {ticket['creator']}\n"
        f"🛠 <b>Исполнитель:</b> {assignee_name} (Вы)"
    )

    action_kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Завершить заявку", callback_data=f"close_{ticket_id}")],
        [InlineKeyboardButton(text="🔄 Вернуть в пул", callback_data=f"return_{ticket_id}")]
    ])

    await callback.message.edit_text(updated_text, reply_markup=action_kb, parse_mode="HTML")
    await callback.answer("✅ Заявка добавлена в «Мои задачи»!", show_alert=True)

# Вернуть в пул
@dp.callback_query(F.data.startswith("return_"))
async def return_task(callback: CallbackQuery):
    ticket_id = int(callback.data.split("_")[1])
    ticket_data = fetch_query("SELECT * FROM tickets WHERE id = ?", (ticket_id,))
    current_user_name = get_user_name(callback.from_user)
    
    if not ticket_data or ticket_data[0]["assignee"] != current_user_name:
        await callback.answer("⚠️ Ошибка: Это не ваша заявка!", show_alert=True)
        return

    execute_query("UPDATE tickets SET status = ?, assignee = NULL WHERE id = ?", ("🔴 НОВАЯ", ticket_id))

    await callback.message.edit_text("🔄 <i>Заявка снята с вас и возвращена в общий пул.</i>", parse_mode="HTML")
    await callback.answer("Заявка возвращена в пул.", show_alert=True)

# Завершить заявку
@dp.callback_query(F.data.startswith("close_"))
async def close_task(callback: CallbackQuery):
    ticket_id = int(callback.data.split("_")[1])
    ticket_data = fetch_query("SELECT * FROM tickets WHERE id = ?", (ticket_id,))
    current_user_name = get_user_name(callback.from_user)
    
    if not ticket_data or ticket_data[0]["assignee"] != current_user_name:
        await callback.answer("⚠️ Вы не можете закрыть эту заявку!", show_alert=True)
        return

    execute_query("UPDATE tickets SET status = ? WHERE id = ?", ("✅ РЕШЕНА", ticket_id))
    ticket = ticket_data[0]
    
    updated_text = (
        f"✅ <b>ЗАЯВКА УСПЕШНО РЕШЕНА</b>\n\n"
        f"📌 <b>Номер:</b> {ticket['ticket_number']}\n"
        f"📝 <b>Создатель:</b> {ticket['creator']}\n"
        f"🛠 <b>Решил:</b> {current_user_name}"
    )

    await callback.message.edit_text(updated_text, parse_mode="HTML")
    await callback.answer("Супер! Задача закрыта и ушла в архив.", show_alert=True)

# Удалить заявку из пула (только для автора)
@dp.callback_query(F.data.startswith("delete_"))
async def delete_task(callback: CallbackQuery):
    ticket_id = int(callback.data.split("_")[1])
    ticket_data = fetch_query("SELECT * FROM tickets WHERE id = ?", (ticket_id,))
    current_user_name = get_user_name(callback.from_user)
    
    if not ticket_data or ticket_data[0]["creator"] != current_user_name:
        await callback.answer("⚠️ Только создатель может удалить эту заявку!", show_alert=True)
        return

    execute_query("UPDATE tickets SET status = ? WHERE id = ?", ("❌ УДАЛЕНА", ticket_id))
    
    await callback.message.edit_text("❌ <i>Эта заявка была отменена и удалена создателем.</i>", parse_mode="HTML")
    await callback.answer("Заявка удалена из пула.", show_alert=True)


async def main():
    init_db()  # Обязательно инициализируем базу данных при запуске
    print("Бот запущен с SQLite БД и готов к работе!")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
