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

# Список для хранения заявок
ACTIVE_TICKETS = []

class EscalateForm(StatesGroup):
    ticket_number = State()
    client_name = State()
    comment = State()

# Постоянная клавиатура внизу экрана (Reply)
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

# Кнопка для сеньоров
take_task_kb = InlineKeyboardMarkup(inline_keyboard=[
    [InlineKeyboardButton(text="✋ Взять себе", callback_data="take_task")]
])

@dp.message(Command("start"))
async def cmd_start(message: Message, state: FSMContext):
    await state.clear()
    await message.answer(
        "Привет! Используй кнопки меню внизу экрана для работы с заявками.",
        reply_markup=main_menu_kb
    )

# Обработка нажатия на кнопку меню "Создать заявку"
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

@dp.callback_query(F.data.startswith("urgency_"))
async def process_urgency(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    
    urgency_dict = {
        "urgency_low": "🟢 Низкая",
        "urgency_mid": "🟡 Средняя",
        "urgency_high": "🔴 Высокая"
    }
    urgency_text = urgency_dict[callback.data]

    ticket_info = {
        "ticket": data['ticket_number'],
        "client": data['client_name'],
        "comment": data['comment'],
        "urgency": urgency_text,
        "author": callback.from_user.username
    }
    ACTIVE_TICKETS.append(ticket_info)

    ticket_msg = (
        f"🔴 <b>НОВАЯ ЭСКАЛАЦИЯ</b>\n"
        f"От: @{ticket_info['author']}\n\n"
        f"📌 <b>Номер заявки:</b> {ticket_info['ticket']}\n"
        f"👤 <b>Клиент:</b> {ticket_info['client']}\n"
        f"💬 <b>Комментарий:</b> {ticket_info['comment']}\n"
        f"⚡ <b>Срочность:</b> {ticket_info['urgency']}"
    )

    await bot.send_message(
        chat_id=SENIOR_CHAT_ID,
        text=ticket_msg,
        reply_markup=take_task_kb,
        parse_mode="HTML"
    )
    
    await callback.message.edit_text("✅ Заявка успешно сформирована и передана старшим!")
    await state.clear()

# Обработка кнопки меню "Активные заявки"
@dp.message(F.text == "📋 Активные заявки")
async def show_active_tickets(message: Message):
    if not ACTIVE_TICKETS:
        await message.answer("📭 На данный момент активных заявок нет.", reply_markup=main_menu_kb)
        return

    text = "📋 <b>Список активных заявок:</b>\n\n"
    for i, t in enumerate(ACTIVE_TICKETS, 1):
        text += (
            f"<b>{i}. Заявка №{t['ticket']}</b>\n"
            f"   • Клиент: {t['client']}\n"
            f"   • Срочность: {t['urgency']}\n"
            f"   • Комментарий: {t['comment']}\n"
            f"   • Автор: @{t['author']}\n\n"
        )
    
    await message.answer(text, parse_mode="HTML", reply_markup=main_menu_kb)

@dp.callback_query(F.data == "take_task")
async def handle_take_task(callback: CallbackQuery):
    senior_username = callback.from_user.username
    original_text = callback.message.text
    new_text = original_text.replace("🔴 НОВАЯ ЭСКАЛАЦИЯ", f"🟡 В РАБОТЕ (Взял: @{senior_username})")
    await callback.message.edit_text(new_text)
    await callback.answer("Ты взял задачу в работу!", show_alert=True)

async def main():
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
