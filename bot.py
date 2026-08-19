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

# 1. Создаем структуру нашей анкеты
class EscalateForm(StatesGroup):
    ticket_number = State()
    client_name = State()
    comment = State()

# 2. Клавиатура для выбора срочности
urgency_kb = InlineKeyboardMarkup(inline_keyboard=[
    [InlineKeyboardButton(text="🟢 Низкая", callback_data="urgency_low")],
    [InlineKeyboardButton(text="🟡 Средняя", callback_data="urgency_mid")],
    [InlineKeyboardButton(text="🔴 Высокая", callback_data="urgency_high")]
])

# Клавиатура для сеньоров
take_task_kb = InlineKeyboardMarkup(inline_keyboard=[
    [InlineKeyboardButton(text="✋ Взять себе", callback_data="take_task")]
])

@dp.message(Command("start"))
async def cmd_start(message: Message, state: FSMContext):
    await state.clear() # Сбрасываем анкету, если она была начата
    await message.answer("Привет! Для создания заявки нажми или напиши /escalate")

# === ПОШАГОВЫЙ СБОР ДАННЫХ ===

@dp.message(Command("escalate"))
async def start_form(message: Message, state: FSMContext):
    await message.answer("Введи номер заявки:")
    await state.set_state(EscalateForm.ticket_number) # Переводим бота в режим ожидания номера

@dp.message(EscalateForm.ticket_number)
async def process_ticket(message: Message, state: FSMContext):
    await state.update_data(ticket_number=message.text) # Сохраняем номер
    await message.answer("Введите данные клиента:")
    await state.set_state(EscalateForm.client_name)

@dp.message(EscalateForm.client_name)
async def process_client(message: Message, state: FSMContext):
    await state.update_data(client_name=message.text) # Сохраняем клиента
    await message.answer("Введите комментарий к заявке:")
    await state.set_state(EscalateForm.comment)

@dp.message(EscalateForm.comment)
async def process_comment(message: Message, state: FSMContext):
    await state.update_data(comment=message.text) # Сохраняем комментарий
    # Тут текст писать не нужно, просим нажать кнопку
    await message.answer("Выберите срочность заявки:", reply_markup=urgency_kb)

# === ОБРАБОТКА ВЫБОРА СРОЧНОСТИ И ОТПРАВКА ===

@dp.callback_query(F.data.startswith("urgency_"))
async def process_urgency(callback: CallbackQuery, state: FSMContext):
    # Достаем всё, что пользователь ввел на предыдущих шагах
    data = await state.get_data()
    
    # Расшифровываем нажатую кнопку
    urgency_dict = {
        "urgency_low": "🟢 Низкая",
        "urgency_mid": "🟡 Средняя",
        "urgency_high": "🔴 Высокая"
    }
    urgency_text = urgency_dict[callback.data]

    # Собираем красивую карточку (нашу "таблицу")
    ticket_msg = (
        f"🔴 <b>НОВАЯ ЭСКАЛАЦИЯ</b>\n"
        f"От: @{callback.from_user.username}\n\n"
        f"<b>Номер заявки:</b> {data['ticket_number']}\n"
        f"<b>Клиент:</b> {data['client_name']}\n"
        f"<b>Комментарий:</b> {data['comment']}\n"
        f"<b>Срочность:</b> {urgency_text}"
    )

    # Отправляем сеньорам
    await bot.send_message(
        chat_id=SENIOR_CHAT_ID,
        text=ticket_msg,
        reply_markup=take_task_kb,
        parse_mode="HTML"
    )
    
    # Меняем сообщение с кнопками срочности на успешный статус
    await callback.message.edit_text("✅ Заявка успешно сформирована и передана старшим!")
    await state.clear() # Очищаем память

# === КНОПКА СЕНЬОРОВ ===
@dp.callback_query(F.data == "take_task")
async def handle_take_task(callback: CallbackQuery):
    senior_username = callback.from_user.username
    original_text = callback.message.text
    
    new_text = original_text.replace("🔴 НОВАЯ ЭСКАЛАЦИЯ", f"🟡 В РАБОТЕ (Взял: @{senior_username})")
    await callback.message.edit_text(new_text)
    await callback.answer("Ты взял задачу!", show_alert=True)

async def main():
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
