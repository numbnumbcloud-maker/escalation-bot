import asyncio
import logging
from aiogram import Bot, Dispatcher, F
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton, ReplyKeyboardMarkup, KeyboardButton
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup

logging.basicConfig(level=logging.INFO)

# === ВСТАВЬ СВОИ ДАННЫЕ СЮДА ===
BOT_TOKEN = "8684957172:AAHhJAfdLnbmAw-AAAYuvNI0j8q0dz9IBYA"
SENIOR_CHAT_ID = "6516986078"

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

# Хранилище заявок в памяти
ACTIVE_TICKETS = []

class EscalateForm(StatesGroup):
    ticket_number = State()
    client_name = State()
    comment = State()

# Меню внизу экрана
main_menu_kb = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="📝 Создать заявку")],
        [KeyboardButton(text="📋 Активные заявки")]
    ],
    resize_keyboard=True
)

# Выбор срочности
urgency_kb = InlineKeyboardMarkup(inline_keyboard=[
    [InlineKeyboardButton(text="🟢 Низкая", callback_data="urgency_low")],
    [InlineKeyboardButton(text="🟡 Средняя", callback_data="urgency_mid")],
    [InlineKeyboardButton(text="🔴 Высокая", callback_data="urgency_high")]
])

@dp.message(Command("start"))
async def cmd_start(message: Message, state: FSMContext):
    await state.clear()
    await message.answer(
        "👋 Привет! Используй меню внизу для создания заявки или просмотра пула задач.",
        reply_markup=main_menu_kb
    )

# --- ШАГ 1: СОЗДАНИЕ ЗАЯВКИ ---
@dp.message(F.text == "📝 Создать заявку")
@dp.message(Command("escalate"))
async def start_form(message: Message, state: FSMContext):
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
async def process_comment(message: Message, state: FsmContext if 'FsmContext' in globals() else FSMContext):
    await state.update_data(comment=message.text)
    await message.answer("⚠️ Выберите **срочность заявки**:", reply_markup=urgency_kb)

# --- ШАГ 2: СОХРАНЕНИЕ И ОТПРАВКА ---
@dp.callback_query(F.data.startswith("urgency_"))
async def process_urgency(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    
    urgency_dict = {
        "urgency_low": "🟢 Низкая",
        "urgency_mid": "🟡 Средняя",
        "urgency_high": "🔴 Высокая"
    }
    urgency_text = urgency_dict.get(callback.data, "🟢 Низкая")

    ticket_id = len(ACTIVE_TICKETS)
    ticket_info = {
        "id": ticket_id,
        "ticket": data.get('ticket_number', 'N/A'),
        "client": data.get('client_name', 'N/A'),
        "comment": data.get('comment', 'N/A'),
        "urgency": urgency_text,
        "author": callback.from_user.username or "Без имени",
        "status": "🔴 НОВАЯ",
        "assignee": None
    }
    ACTIVE_TICKETS.append(ticket_info)

    # Текст карточки заявки
    ticket_msg = (
        f"🔴 <b>НОВАЯ ЭСКАЛАЦИЯ</b>\n"
        f"От: @{ticket_info['author']}\n\n"
        f"📌 <b>Номер заявки:</b> {ticket_info['ticket']}\n"
        f"👤 <b>Клиент:</b> {ticket_info['client']}\n"
        f"💬 <b>Комментарий:</b> {ticket_info['comment']}\n"
        f"⚡ <b>Срочность:</b> {ticket_info['urgency']}"
    )
    
    # Кнопка для чата эскалаций
    chat_take_kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✋ Взять заявку на свой аккаунт", callback_data=f"take_{ticket_id}")]
    ])

    # Отправляем в общий чат сеньоров
    try:
        await bot.send_message(
            chat_id=SENIOR_CHAT_ID,
            text=ticket_msg,
            reply_markup=chat_take_kb,
            parse_mode="HTML"
        )
    except Exception:
        pass
    
    await callback.message.edit_text("✅ Заявка успешно создана и добавлена в базу!")
    await state.clear()

# --- ШАГ 3: ПРОСМОТР И ВЫБОР ЗАЯВОК ---
@dp.message(F.text == "📋 Активные заявки")
async def show_active_tickets(message: Message):
    free_tickets = [t for t in ACTIVE_TICKETS if t["status"] == "🔴 НОВАЯ"]

    if not free_tickets:
        await message.answer("📭 Свободных активных заявок сейчас нет.", reply_markup=main_menu_kb)
        return

    await message.answer("📋 <b>Доступные заявки в системе:</b>", parse_mode="HTML", reply_markup=main_menu_kb)

    for t in free_tickets:
        text = (
            f"📌 <b>Заявка №{t['ticket']}</b>\n"
            f"👤 Клиент: {t['client']}\n"
            f"⚡ Срочность: {t['urgency']}\n"
            f"💬 Комментарий: {t['comment']}\n"
            f"👤 Автор: @{t['author']}"
        )
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="✋ Взять заявку на свой аккаунт", callback_data=f"take_{t['id']}")]
        ])
        await message.answer(text, parse_mode="HTML", reply_markup=kb)

# --- ШАГ 4: ОБРАБОТКА КЛИКА "ВЗЯТЬ НА СВОЙ АККАУНТ" ---
@dp.callback_query(F.data.startswith("take_"))
async def handle_take_task(callback: CallbackQuery):
    try:
        ticket_id = int(callback.data.split("_")[1])
    except (IndexError, ValueError):
        await callback.answer("Ошибка обработки задачи!", show_alert=True)
        return
    
    if ticket_id >= len(ACTIVE_TICKETS):
        await callback.answer("⚠️ Заявка не найдена!", show_alert=True)
        return

    ticket = ACTIVE_TICKETS[ticket_id]
    
    if ticket["status"] == "🟡 В РАБОТЕ":
        await callback.answer(f"⚠️ Эту заявку уже взял @{ticket['assignee']}!", show_alert=True)
        return

    ticket["status"] = "🟡 В РАБОТЕ"
    ticket["assignee"] = callback.from_user.username or "Специалист"

    updated_text = (
        f"🟡 <b>В РАБОТЕ (Взял: @{ticket['assignee']})</b>\n\n"
        f"📌 <b>Номер заявки:</b> {ticket['ticket']}\n"
        f"👤 <b>Клиент:</b> {ticket['client']}\n"
        f"💬 <b>Комментарий:</b> {ticket['comment']}\n"
        f"⚡ <b>Срочность:</b> {ticket['urgency']}"
    )

    try:
        await callback.message.edit_text(updated_text, parse_mode="HTML")
    except Exception:
        pass
        
    await callback.answer("✅ Вы успешно закрепили заявку за собой!", show_alert=True)

async def main():
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
