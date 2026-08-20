import asyncio
import logging
import sqlite3
from aiogram import Bot, Dispatcher, F
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton, ReplyKeyboardMarkup, KeyboardButton
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.exceptions import TelegramBadRequest

logging.basicConfig(level=logging.INFO)

# === ТВОИ ДАННЫЕ ===
BOT_TOKEN = "8684957172:AAHhJAfdLnbmAw-AAAYuvNI0j8q0dz9IBYA"
SENIOR_CHAT_ID = "6516986078"

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

# === БАЗА ДАННЫХ И МИГРАЦИИ ===
def init_db():
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
        # Авто-добавление новых колонок (если БД старая)
        columns = [
            ("creator_id", "INTEGER"), ("assignee_id", "INTEGER"), 
            ("return_reason", "TEXT"), ("assigned_at", "TIMESTAMP"), 
            ("sla_reminded", "INTEGER DEFAULT 0"), ("alarm_sent", "INTEGER DEFAULT 0")
        ]
        for col, dtype in columns:
            try:
                cursor.execute(f"ALTER TABLE tickets ADD COLUMN {col} {dtype}")
            except sqlite3.OperationalError:
                pass
        conn.commit()

def execute_query(query, params=()):
    with sqlite3.connect("tickets.db") as conn:
        cursor = conn.cursor()
        cursor.execute(query, params)
        conn.commit()
        return cursor.lastrowid

def fetch_query(query, params=()):
    with sqlite3.connect("tickets.db") as conn:
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute(query, params)
        return [dict(row) for row in cursor.fetchall()]

# === FSM СОСТОЯНИЯ ===
class EscalateForm(StatesGroup):
    ticket_number = State()
    client_name = State()
    comment = State()

class ReturnForm(StatesGroup):
    ticket_id = State()
    reason = State()

# === КЛАВИАТУРЫ ===
main_menu_kb = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="📝 Создать заявку"), KeyboardButton(text="📋 Пул заявок")],
        [KeyboardButton(text="💼 Мои задачи"), KeyboardButton(text="📊 Статистика")],
        [KeyboardButton(text="ℹ️ Помощь")]
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

# === ФОНОВЫЙ МОНИТОРИНГ (SLA И ТРЕВОГИ) ===
async def monitor_tasks(bot: Bot):
    while True:
        await asyncio.sleep(60) # Проверка каждую минуту
        try:
            # 1. Тревога по пулу: Высокая срочность лежит > 15 минут
            alarm_tickets = fetch_query("""
                SELECT id, ticket_number, creator 
                FROM tickets 
                WHERE status = '🔴 НОВАЯ' AND urgency = '🔴 Высокая' 
                AND alarm_sent = 0 AND created_at <= datetime('now', '-15 minutes')
            """)
            for t in alarm_tickets:
                msg = f"🔥 <b>АЛАРМ! ГОРИТ ЗАЯВКА!</b>\n\nЗаявка <b>№{t['ticket_number']}</b> со срочностью 🔴 Высокая висит в пуле более 15 минут!\nСоздатель: {t['creator']}\n\n<i>Коллеги, заберите в работу!</i>"
                try:
                    await bot.send_message(chat_id=SENIOR_CHAT_ID, text=msg, parse_mode="HTML")
                    execute_query("UPDATE tickets SET alarm_sent = 1 WHERE id = ?", (t['id'],))
                except Exception as e:
                    logging.error(f"Ошибка отправки аларма: {e}")

            # 2. SLA Контроль: В работе > 2 часов
            sla_tickets = fetch_query("""
                SELECT id, ticket_number, assignee_id 
                FROM tickets 
                WHERE status = '🟡 В РАБОТЕ' AND sla_reminded = 0 
                AND assigned_at <= datetime('now', '-2 hours')
            """)
            for t in sla_tickets:
                if t['assignee_id']:
                    msg = f"⏳ <b>Напоминание по SLA!</b>\n\nЗаявка <b>№{t['ticket_number']}</b> находится у вас в работе уже более 2 часов. Если нужна помощь — обратитесь к старшим специалистам, либо верните заявку в пул."
                    try:
                        await bot.send_message(chat_id=t['assignee_id'], text=msg, parse_mode="HTML")
                        execute_query("UPDATE tickets SET sla_reminded = 1 WHERE id = ?", (t['id'],))
                    except Exception as e:
                        logging.error(f"Ошибка отправки SLA: {e}")
        except Exception as e:
            logging.error(f"Ошибка мониторинга: {e}")

# === ИНСТРУКЦИЯ И ДАШБОРД ===
@dp.message(Command("start"))
@dp.message(F.text == "ℹ️ Помощь")
async def cmd_help(message: Message, state: FSMContext):
    await state.clear()
    await message.answer("🤖 Вы в главном меню CRM-системы эскалаций. Выберите действие внизу экрана.", reply_markup=main_menu_kb)

@dp.message(F.text == "📊 Статистика")
async def cmd_stats(message: Message):
    user_id = message.from_user.id
    
    total_new = fetch_query("SELECT COUNT(*) as c FROM tickets WHERE status = '🔴 НОВАЯ'")[0]['c']
    total_wip = fetch_query("SELECT COUNT(*) as c FROM tickets WHERE status = '🟡 В РАБОТЕ'")[0]['c']
    total_solved = fetch_query("SELECT COUNT(*) as c FROM tickets WHERE status = '✅ РЕШЕНА'")[0]['c']
    
    my_solved = fetch_query("SELECT COUNT(*) as c FROM tickets WHERE status = '✅ РЕШЕНА' AND assignee_id = ?", (user_id,))[0]['c']
    
    stats_text = (
        f"📊 <b>СВОДНЫЙ ДАШБОРД</b>\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"🔴 В пуле (ждут): <b>{total_new}</b>\n"
        f"🟡 В работе сейчас: <b>{total_wip}</b>\n"
        f"✅ Всего решено: <b>{total_solved}</b>\n\n"
        f"🏆 <b>Твой личный вклад:</b>\n"
        f"Решено тобой: <b>{my_solved}</b> заявок!"
    )
    await message.answer(stats_text, parse_mode="HTML")

# === 1. СОЗДАНИЕ ЗАЯВКИ ===
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
    await message.answer("💬 Введите **комментарий**:")
    await state.set_state(EscalateForm.comment)

@dp.message(EscalateForm.comment)
async def process_comment(message: Message, state: FSMContext):
    await state.update_data(comment=message.text)
    await message.answer("⚠️ Выберите **срочность заявки**:", reply_markup=urgency_kb)

@dp.callback_query(F.data.startswith("urgency_"))
async def process_urgency(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    urgency_map = {"urgency_low": "🟢 Низкая", "urgency_mid": "🟡 Средняя", "urgency_high": "🔴 Высокая"}
    urgency_text = urgency_map.get(callback.data, "🟢 Низкая")
    
    creator_name = get_user_name(callback.from_user)
    creator_id = callback.from_user.id
    
    ticket_id = execute_query(
        "INSERT INTO tickets (ticket_number, client_name, comment, urgency, creator, creator_id, status) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (data.get('ticket_number'), data.get('client_name'), data.get('comment'), urgency_text, creator_name, creator_id, "🔴 НОВАЯ")
    )

    ticket_msg = (
        f"🔔 <b>НОВЫЙ ЗАПРОС В ПУЛЕ</b>\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"📌 <b>Номер:</b> {data.get('ticket_number')}\n"
        f"👤 <b>Клиент:</b> {data.get('client_name')}\n"
        f"⚡ <b>Срочность:</b> {urgency_text}\n"
        f"📝 <b>Создатель:</b> {creator_name}\n"
        f"💬 <i>{data.get('comment')}</i>"
    )
    
    try:
        await bot.send_message(chat_id=SENIOR_CHAT_ID, text=ticket_msg, parse_mode="HTML")
    except Exception:
        pass
    
    await callback.message.edit_text("✅ Заявка создана! Коллеги уже видят её в пуле.")
    await state.clear()

# === 2. ПУЛ ЗАЯВОК ===
@dp.message(F.text == "📋 Пул заявок")
async def show_pool_tickets(message: Message):
    free_tickets = fetch_query("SELECT * FROM tickets WHERE status = '🔴 НОВАЯ' ORDER BY id DESC LIMIT 10") # Показываем 10 свежих

    if not free_tickets:
        await message.answer("📭 В пуле сейчас чисто. Отличная работа!", reply_markup=main_menu_kb)
        return

    await message.answer("📋 <b>Открытые заявки (выберите, чтобы взять):</b>", parse_mode="HTML")
    user_id = message.from_user.id

    for t in free_tickets:
        reason_text = f"\n⚠️ <b>Причина возврата:</b> {t['return_reason']}" if t['return_reason'] else ""
        
        text = (
            f"📌 <b>Заявка №{t['ticket_number']}</b>\n"
            f"👤 Клиент: {t['client_name']}\n"
            f"⚡ Срочность: {t['urgency']}\n"
            f"💬 Комментарий: {t['comment']}\n"
            f"📝 Создатель: {t['creator']}{reason_text}"
        )
        
        buttons = [[InlineKeyboardButton(text="✋ Взять в работу", callback_data=f"take_{t['id']}")]]
        if t['creator_id'] == user_id:
            buttons.append([InlineKeyboardButton(text="❌ Удалить мою заявку", callback_data=f"delete_{t['id']}")])
            
        await message.answer(text, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))

# === 3. МОИ ЗАДАЧИ ===
@dp.message(F.text == "💼 Мои задачи")
async def show_my_tasks(message: Message):
    user_id = message.from_user.id
    my_tickets = fetch_query("SELECT * FROM tickets WHERE status = '🟡 В РАБОТЕ' AND assignee_id = ?", (user_id,))

    if not my_tickets:
        await message.answer("💼 У вас нет активных задач. Загляните в «📋 Пул заявок».", reply_markup=main_menu_kb)
        return

    await message.answer("💼 <b>Ваши задачи в работе:</b>", parse_mode="HTML")

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
            [InlineKeyboardButton(text="🔄 Вернуть в пул (указать причину)", callback_data=f"return_{t['id']}")]
        ])
        await message.answer(text, parse_mode="HTML", reply_markup=kb)

# === 4. ДЕЙСТВИЯ С ЗАЯВКАМИ ===

@dp.callback_query(F.data.startswith("take_"))
async def handle_take_task(callback: CallbackQuery):
    ticket_id = int(callback.data.split("_")[1])
    ticket_data = fetch_query("SELECT * FROM tickets WHERE id = ?", (ticket_id,))
    
    if not ticket_data or ticket_data[0]["status"] != "🔴 НОВАЯ":
        await callback.answer("⚠️ Кто-то уже забрал эту заявку!", show_alert=True)
        await callback.message.delete()
        return

    assignee_name = get_user_name(callback.from_user)
    assignee_id = callback.from_user.id
    
    execute_query(
        "UPDATE tickets SET status = '🟡 В РАБОТЕ', assignee = ?, assignee_id = ?, assigned_at = CURRENT_TIMESTAMP, sla_reminded = 0 WHERE id = ?", 
        (assignee_name, assignee_id, ticket_id)
    )

    ticket = fetch_query("SELECT * FROM tickets WHERE id = ?", (ticket_id,))[0]

    updated_text = (
        f"🟡 <b>ЗАЯВКА В РАБОТЕ</b>\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"📌 <b>Номер:</b> {ticket['ticket_number']}\n"
        f"👤 <b>Клиент:</b> {ticket['client_name']}\n"
        f"⚡ <b>Срочность:</b> {ticket['urgency']}\n"
        f"💬 <b>Комментарий:</b> {ticket['comment']}\n"
        f"📝 <b>Создатель:</b> {ticket['creator']}\n"
        f"🛠 <b>Исполнитель:</b> {assignee_name}"
    )

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Завершить заявку", callback_data=f"close_{ticket_id}")],
        [InlineKeyboardButton(text="🔄 Вернуть в пул", callback_data=f"return_{ticket_id}")]
    ])
    await callback.message.edit_text(updated_text, reply_markup=kb, parse_mode="HTML")
    await callback.answer("✅ Заявка добавлена в «Мои задачи»!")

# --- ЛОГИКА ВОЗВРАТА С УКАЗАНИЕМ ПРИЧИНЫ ---
@dp.callback_query(F.data.startswith("return_"))
async def return_task_start(callback: CallbackQuery, state: FSMContext):
    ticket_id = int(callback.data.split("_")[1])
    # Убираем старую карточку чтобы она не мешалась
    await callback.message.delete()
    
    await state.update_data(ticket_id=ticket_id)
    await state.set_state(ReturnForm.reason)
    await callback.message.answer("✍️ <b>Укажите причину возврата заявки в пул:</b>\n<i>(Например: нужен доступ к биллингу)</i>", parse_mode="HTML")
    await callback.answer()

@dp.message(ReturnForm.reason)
async def process_return_reason(message: Message, state: FSMContext):
    data = await state.get_data()
    ticket_id = data.get("ticket_id")
    reason = message.text

    execute_query(
        "UPDATE tickets SET status = '🔴 НОВАЯ', assignee = NULL, assignee_id = NULL, return_reason = ?, created_at = CURRENT_TIMESTAMP, alarm_sent = 0 WHERE id = ?", 
        (reason, ticket_id)
    )
    
    await message.answer("🔄 <i>Заявка успешно возвращена в пул с указанием причины.</i>", parse_mode="HTML", reply_markup=main_menu_kb)
    await state.clear()

# Завершить заявку
@dp.callback_query(F.data.startswith("close_"))
async def close_task(callback: CallbackQuery):
    ticket_id = int(callback.data.split("_")[1])
    execute_query("UPDATE tickets SET status = '✅ РЕШЕНА' WHERE id = ?", (ticket_id,))
    ticket = fetch_query("SELECT * FROM tickets WHERE id = ?", (ticket_id,))[0]
    
    updated_text = (
        f"✅ <b>ЗАЯВКА РЕШЕНА</b>\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"📌 <b>Номер:</b> {ticket['ticket_number']}\n"
        f"📝 <b>Создатель:</b> {ticket['creator']}\n"
        f"🛠 <b>Решил:</b> {ticket['assignee']}"
    )
    await callback.message.edit_text(updated_text, parse_mode="HTML")
    await callback.answer("🚀 Поздравляю! Задача закрыта.", show_alert=True)

# Удалить заявку из пула
@dp.callback_query(F.data.startswith("delete_"))
async def delete_task(callback: CallbackQuery):
    ticket_id = int(callback.data.split("_")[1])
    execute_query("UPDATE tickets SET status = '❌ УДАЛЕНА' WHERE id = ?", (ticket_id,))
    
    await callback.message.edit_text("❌ <i>Заявка отменена и удалена создателем.</i>", parse_mode="HTML")
    await callback.answer("Удалено.", show_alert=True)

async def main():
    init_db()
    # Запускаем фоновый мониторинг (таймеры SLA и Алармы)
    asyncio.create_task(monitor_tasks(bot))
    print("Бот CRM запущен и работает как швейцарские часы!")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
