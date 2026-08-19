import asyncio
import logging
from aiogram import Bot, Dispatcher, F
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup

logging.basicConfig(level=logging.INFO)

# === ВСТАВЬ СВОИ ДАННЫЕ ===
BOT_TOKEN = "8684957172:AAHhJAfdLnbmAw-AAAYuvNI0j8q0dz9IBYA"
SENIOR_CHAT_ID = "6516986078"

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

# Состояния нашей анкеты
class EscalateForm(StatesGroup):
    ticket_number = State()
    client_name = State()
    comment = State()

# Кнопки выбора срочности
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
    await message.answer("Привет! Для создания заявки просто напиши /escalate")

# Шаг 0: Пользователь пишет /escalate (больше ничего писать не нужно)
@dp.message(Command("escalate"))
async def start_form(message: Message, state: FSMContext):
    await state.clear()
    await message.answer("📝 Введите **номер заявки**:")
    await state.set_state(EscalateForm.ticket_number)

# Шаг 1: Принимаем номер заявки, спрашиваем клиента
@dp.message(EscalateForm.ticket_number)
async def process_ticket(message: Message, state: FSMContext):
    await state.update_data(ticket_number=message.text)
    await message.answer("👤 Введите **данные клиента** (любая информация):")
    await state.set_state(EscalateForm.client_name)

# Шаг 2: Принимаем клиента, спрашиваем комментарий
@dp.message(EscalateForm.client_name)
async def process_client(message: Message, state: FSMContext):
    await state.update_data(client_name=message.text)
    await message.answer("💬 Введите **комментарий** к заявке:")
    await state.set_state(EscalateForm.comment)

# Шаг 3: Принимаем комментарий и просим выбрать срочность
@dp.message(EscalateForm.comment)
async def process_comment(message: Message, state: FSMContext):
    await state.update_data(comment=message.text)
    await message.answer("⚠️ Выберите **срочность заявки**:", reply_markup=urgency_kb)

# Шаг 4: Обработка нажатия на срочность и отправка тикета сеньорам
@dp.callback_query(F.data.startswith("urgency_"))
async def process_urgency(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    
    urgency_dict = {
        "urgency_low": "🟢 Низкая",
        "urgency_mid": "🟡 Средняя",
        "urgency_high": "🔴 Высокая"
    }
    urgency_text = urgency_dict[callback.data]

    # Собираем красивую карточку-таблицу
    ticket_msg = (
        f"🔴 <b>НОВАЯ ЭСКАЛАЦИЯ</b>\n"
        f"От: @{callback.from_user.username}\n\n"
        f"📌 <b>Номер заявки:</b> {data['ticket_number']}\n"
        f"👤 <b>Клиент:</b> {data['client_name']}\n"
        f"💬 <b>Комментарий:</b> {data['comment']}\n"
        f"⚡ <b>Срочность:</b> {urgency_text}"
    )

    # Отправляем в чат сеньоров
    await bot.send_message(
        chat_id=SENIOR_CHAT_ID,
        text=ticket_msg,
        reply_markup=take_task_kb,
        parse_mode="HTML"
    )
    
    await callback.message.edit_text("✅ Заявка успешно сформирована и передана старшим!")
    await state.clear()

# Кнопка сеньоров "Взять себе"
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
