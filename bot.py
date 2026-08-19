import asyncio
import logging
from aiogram import Bot, Dispatcher, F
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.filters import Command

logging.basicConfig(level=logging.INFO)

# Твои данные
BOT_TOKEN = "8684957172:AAHhJAfdLnbmAw-AAAYuvNI0j8q0dz9IBYA"
SENIOR_CHAT_ID = "6516986078"

# Бот без всяких прокси!
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

take_task_kb = InlineKeyboardMarkup(inline_keyboard=[
    [InlineKeyboardButton(text="✋ Взять себе", callback_data="take_task")]
])

@dp.message(Command("start"))
async def cmd_start(message: Message):
    await message.answer("Привет! Для эскалации напиши: /escalate [текст проблемы]")

@dp.message(Command("escalate"))
async def cmd_escalate(message: Message):
    task_text = message.text.replace("/escalate", "").strip()
    if not task_text:
        await message.answer("Укажи суть проблемы!")
        return

    await bot.send_message(
        chat_id=SENIOR_CHAT_ID,
        text=f"🔴 <b>НОВАЯ ЭСКАЛАЦИЯ</b>\nОт: @{message.from_user.username}\nСуть: {task_text}",
        reply_markup=take_task_kb,
        parse_mode="HTML"
    )
    await message.answer("✅ Передано старшим!")

@dp.callback_query(F.data == "take_task")
async def handle_take_task(callback: CallbackQuery):
    senior_username = callback.from_user.username
    new_text = callback.message.text.replace("🔴 НОВАЯ ЭСКАЛАЦИЯ", f"🟡 В РАБОТЕ (Взял: @{senior_username})")
    await callback.message.edit_text(new_text)
    await callback.answer("Ты взял задачу!", show_alert=True)

async def main():
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
