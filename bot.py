import asyncio
import logging
from aiogram import Bot, Dispatcher, F
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton, ReplyKeyboardMarkup, KeyboardButton
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup

logging.basicConfig(level=logging.INFO)

# === ВСТАВЬ СВОИ ДАННЫЕ ===
BOT_TOKEN = "8684957172:AAHhJAfdLnbmAw-AAAYuvNI0j8q0dz9IBYA"
SENIOR_CHAT_ID = "6516986078"

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

# База данных заявок (храним в памяти)
# Каждая заявка будет иметь уникальный id (индекс)
ACTIVE_TICKETS = []

class EscalateForm(StatesGroup):
    ticket_number = State()
    client_name = State()
    comment = State()

# Постоянная клавиатура внизу экрана
main_menu_kb = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="📝 Создать заявку")],
        [KeyboardButton(text="📋 Активные заявки")]
    ],
    resize_keyboard=True
)

# Инлайн-кнопки выбора срочности
urgency_kb = InlineKeyboardMarkup(inline_keyboard=[
    [InlineKeyboardButton(text="🟢 Низкая", callback_data="urgency_low")],
    [InlineKeyboardButton(text="🟡 Средняя", callback_data="urgency_mid")],
    [InlineKeyboardButton(text="🔴 Высокая", callback_data="urgency_high")]
])

@dp.message(Command("start"))
async def cmd_start(message: Message, state: FSMContext):
    await state.clear()
    await message.answer(
        "Привет! Используй кнопки меню внизу экрана для работы с заявками.",
        reply_markup=main_menu_kb
    )

# Создание заявки через меню
@dp.message(F.text == "📝 Создать заявку")
async def start_form_btn(message: Message, state: FSMContext):
    await state.clear()
    await message.answer("📝 Введите **номер заявки**:", reply_markup=main_menu_kb)
    await state.set_state(EscalateForm.ticket_number)

@dp.message(Command("escalate"))
async def start_form_cmd(message: Message, state: FSMContext):
    await state.clear()
    await message.answer("📝 Введите **номер заявки**:", reply_markup=main_menu_kb)
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

# Сохранение заявки
@dp.callback_query(F.data.startswith("urgency_"))
async def process_urgency(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    
    urgency_dict = {
        "urgency_low": "🟢 Низкая",
        "urgency_mid": "🟡 Средняя",
        "urgency_high": "🔴 Высокая"
    }
    urgency_text = urgency_dict[callback.data]

    # Создаем новую задачу в общей базе
    ticket_id = len(ACTIVE_TICKETS)
    ticket_info = {
        "id": ticket_id,
        "ticket": data['ticket_number'],
        "client": data['client_name'],
        "comment": data['comment'],
        "urgency": urgency_text,
        "author": callback.from_user.username,
        "status": "🔴 НОВАЯ",
        "assignee": None
    }
    ACTIVE_TICKETS.append(ticket_info)

    # Дублируем уведомление в чат эскалаций
    ticket_msg = (
        f"🔴 <b>НОВАЯ ЭСКАЛАЦИЯ</b>\n"
        f"От: @{ticket_info['author']}\n\n"
        f"📌 <b>Номер заявки:</b> {ticket_info['ticket']}\n"
        f"👤 <b>Клиент:</b> {ticket_info['client']}\n"
        f"💬 <b>Комментарий:</b> {ticket_info['comment']}\n"
        f"⚡ <b>Срочность:</b> {ticket_info['urgency']}"
    )
    
    # Кнопка прямого взятия задачи из чата сеньоров тоже доступна
    chat_take_kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✋ Взять себе", callback_data=f"take_{ticket_id}")]
    ])

    await bot.send_message(
        chat_id=SENIOR_CHAT_ID,
        text=ticket_msg,
        reply_markup=chat_take_kb,
        parse_mode="HTML"
    )
    
    await callback.message.edit_text("✅ Заявка успешно создана и добавлена в общую базу!")
    await state.clear()

# === ПРОСМОТР ПУЛА ЗАЯВОК И ВЫБОР СВОИХ ===
@dp.message(F.text == "📋 Активные заявки")
async def show_active_tickets(message: Message):
    # Фильтруем только те, что еще не взяты в работу
    free_tickets = [t for t in ACTIVE_TICKETS if t["status"] == "🔴 НОВАЯ"]

    if not free_tickets:
        await message.answer("📭 Свободных активных заявок нет.", reply_markup=main_menu_kb)
        return

    await message.answer("📋 <b>Выберите заявку, которую хотите взять в работу:</b>", parse_mode="HTML", reply_markup=main_menu_kb)

    # Выводим каждую заявку отдельным сообщением с кнопкой "Взять"
    for t in free_tickets:
        text = (
            f"📌 <b>Заявка №{t['ticket']}</b>\n"
            f"👤 Клиент: {t['client']}\n"
            f"⚡ Срочность: {t['urgency']}\n"
            f"💬 Комментарий: {t['comment']}\n"
            f"👤 Автор: @{t['author']}"
        )
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="✋ Взять эту заявку", callback_data=f"take_{t['id']}")]
        ])
        await message.answer(text, parse_mode="HTML", reply_markup=kb)

# === ОБРАБОТКА ВЗЯТИЯ ЗАЯВКИ ЛЮБЫМ ПОЛЬЗОВАТЕЛЕМ ===
@dp.callback_query(F.data.startswith("take_"))
async def handle_take_custom_task(callback: CallbackQuery):
    # Достаем ID заявки из callback_data (например, "take_0" -> 0)
    ticket_id = int(callback.data.split("_")[1])
    
    ticket = ACTIVE_TICKETS[ticket_id]
    
    if ticket["status"] == "🟡 В РАБОТЕ":
        await callback.answer("⚠️ Эту заявку уже кто-то взял!", show_alert=True)
        return

    # Меняем статус
    ticket["status"] = "🟡 В РАБОТЕ"
    ticket["assignee"] = callback.from_user.username

    # Обновляем текст сообщения
    new_text = (
        f"🟡 <b>В РАБОТЕ (Взял: @{ticket['assignee']})</b>\n\n"
        f"📌 <b>Номер заявки:</b> {ticket['ticket']}\n"
        f"👤 <b>Клиент:</b> {ticket['client']}\n"
        f"💬 <b>Комментарий:</b> {ticket['comment']}\n"
        f"⚡ <b>Срочность:</b> {ticket['urgency']}"
    )

    await callback.message.edit_text(new_text, parse_mode="HTML")
    await callback.answer("Вы успешно взяли заявку в работу!", show_alert=True)

async def main():
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
