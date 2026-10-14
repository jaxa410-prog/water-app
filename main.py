import os
import logging
import json
import sqlite3
from aiogram import Bot, Dispatcher, F, types
from aiogram.filters import Command
from aiogram.types import WebAppInfo, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.fsm.storage.memory import MemoryStorage

# Настройка логирования
logging.basicConfig(level=logging.INFO)

# Получаем токен и ID канала из безопасного окружения Render
TOKEN = os.getenv("TOKEN")
CHANNEL_ID = int(os.getenv("CHANNEL_ID", "0"))

if not TOKEN or CHANNEL_ID == 0:
    logging.error("Не заданы TOKEN или CHANNEL_ID в переменных окружения!")

bot = Bot(token=TOKEN)
dp = Dispatcher(storage=MemoryStorage())

# Инициализация базы данных SQLite для сохранения истории
def init_db():
    conn = sqlite3.connect("water_reports.db")
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS reports (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            username TEXT,
            address TEXT,
            count INTEGER,
            date TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.commit()
    conn.close()

init_db()

# Команда /start: отправляет курьеру кнопку для открытия формы
@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    web_app_url = "https://jaxa410-prog.github.io/water-app/"
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="📦 Открыть форму отчета",
                    web_app=WebAppInfo(url=web_app_url)
                )
            ]
        ]
    )
    await message.answer(
        "Привет! Нажми на кнопку ниже, чтобы заполнить и отправить отчет о доставке воды:",
        reply_markup=keyboard
    )

# Обработка данных, пришедших из Mini App (когда курьер нажимает "Отправить отчет")
@dp.message(F.web_app_data)
async def handle_web_app_data(message: types.Message):
    try:
        data = json.loads(message.web_app_data.data)
        address = data.get("address")
        count = data.get("count")
        
        user_id = message.from_user.id
        username = message.from_user.username or message.from_user.first_name

        # Сохраняем отчет в базу данных SQLite
        conn = sqlite3.connect("water_reports.db")
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO reports (user_id, username, address, count) VALUES (?, ?, ?, ?)",
            (user_id, username, address, count)
        )
        conn.commit()
        conn.close()

        # Формируем красивый отчет для вашего закрытого канала
        report_text = (
            f"📥 **Новый отчет о доставке!**\n\n"
            f"👤 **Курьер:** {username} (ID: {user_id})\n"
            f"📍 **Адрес:** {address}\n"
            f"🍾 **Количество бутылей:** {count}"
        )

        # Отправляем сообщение в ваш закрытый канал
        await bot.send_message(chat_id=CHANNEL_ID, text=report_text, parse_mode="Markdown")

        # Подтверждение курьеру в чате с ботом, что всё ушло успешно
        await message.answer("✅ Ваш отчет успешно отправлен и сохранен!")

    except Exception as e:
        logging.error(f"Ошибка при обработке данных из Web App: {e}")
        await message.answer("❌ Произошла ошибка при отправке отчета. Попробуйте еще раз.")

if __name__ == "__main__":
    import asyncio
    asyncio.run(dp.start_polling(bot))
