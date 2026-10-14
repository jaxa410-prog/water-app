import asyncio
import json
import logging
import os
from aiogram import Bot, Dispatcher, F, types
from aiogram.filters import CommandStart
from aiogram.types import WebAppInfo, InlineKeyboardMarkup, InlineKeyboardButton

# Включаем логирование
logging.basicConfig(level=logging.INFO)

# Получаем переменные окружения из Render
TOKEN = os.getenv("TOKEN")
CHANNEL_ID = os.getenv("CHANNEL_ID")  # ID канала или чата (например, -1001234567890 или @channel_name)

bot = Bot(token=TOKEN)
dp = Dispatcher()

# Команда /start
@dp.message(CommandStart())
async def cmd_start(message: types.Message):
    # Укажите ссылку на ваше Mini App на Render
    web_app_url = "https://water-bot-e0ff.onrender.com" 
    
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="📦 Открыть отчет курьера",
                    web_app=WebAppInfo(url=web_app_url)
                )
            ]
        ]
    )
    await message.answer("Нажмите кнопку ниже, чтобы заполнить отчет:", reply_markup=keyboard)


# Обработчик данных, поступающих из Web App (Mini App)
@dp.message(F.web_app_data)
async def handle_web_app_data(message: types.Message):
    try:
        # Распаковываем JSON от Web App
        data = json.loads(message.web_app_data.data)
        
        client_id = data.get("client_id")
        delivered = data.get("delivered")
        returned = data.get("returned")
        client_type = data.get("client_type")
        payment_type = data.get("payment_type")
        total_price = data.get("total_price", 0)
        
        # Форматируем красивый текст отчета
        report_text = (
            f"📋 **ОТЧЕТ О ДОСТАВКЕ**\n\n"
            f"👤 **Курьер:** {message.from_user.full_name} (@{message.from_user.username or 'без_юзернейма'})\n"
            f"🆔 **ID абонента:** `{client_id}`\n"
            f"🍾 **Сдал полных:** {delivered} шт.\n"
            f"🧴 **Принял пустых:** {returned} шт.\n"
            f"👥 **Тип клиента:** {client_type}\n"
            f"💳 **Тип оплаты:** {payment_type}\n"
            f"💰 **Сумма:** {total_price:,} сум\n".replace(",", " ")
        )

        # 1. Отправляем подтверждение самому курьеру
        await message.answer(f"✅ **Отчет успешно отправлен!**\n\n{report_text}", parse_mode="Markdown")

        # 2. Пересылаем отчет в рабочий канал / чат (если указан CHANNEL_ID)
        if CHANNEL_ID:
            await bot.send_message(chat_id=CHANNEL_ID, text=report_text, parse_mode="Markdown")

    except Exception as e:
        logging.error(f"Ошибка при обработке данных Web App: {e}")
        await message.answer("❌ Произошла ошибка при сохранении отчета.")


async def main():
    # Запуск бота
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
