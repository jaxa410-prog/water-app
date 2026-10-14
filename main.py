import asyncio
import json
import logging
import os
from aiogram import Bot, Dispatcher, F, types
from aiogram.filters import CommandStart
from aiogram.types import WebAppInfo, InlineKeyboardMarkup, InlineKeyboardButton

# Настройка логирования
logging.basicConfig(level=logging.INFO)

# Считываем переменные окружения с Render
TOKEN = os.getenv("TOKEN")
CHANNEL_ID = os.getenv("CHANNEL_ID")

bot = Bot(token=TOKEN)
dp = Dispatcher()

# Обработка команды /start
@dp.message(CommandStart())
async def cmd_start(message: types.Message):
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
    await message.answer("Нажмите кнопку ниже, чтобы запустить форму отчета:", reply_markup=keyboard)


# Обработка отправленных данных из Mini App
@dp.message(F.web_app_data)
async def handle_web_app_data(message: types.Message):
    try:
        data = json.loads(message.web_app_data.data)
        
        client_id = data.get("client_id")
        delivered = data.get("delivered")
        returned = data.get("returned")
        client_type = data.get("client_type")
        payment_type = data.get("payment_type")
        total_price = data.get("total_price", 0)
        lang = data.get("language", "ru")
        
        # Форматирование суммы с пробелами (например, 23 000)
        formatted_price = f"{total_price:,}".replace(",", " ")
        
        report_text = (
            f"📋 **ОТЧЕТ О ДОСТАВКЕ**\n\n"
            f"👤 **Курьер:** {message.from_user.full_name} (@{message.from_user.username or 'нет_юзернейма'})\n"
            f"🆔 **ID абонента:** `{client_id}`\n"
            f"🍾 **Сдал полных:** {delivered} шт.\n"
            f"🧴 **Принял пустых:** {returned} шт.\n"
            f"👥 **Тип клиента:** {client_type}\n"
            f"💳 **Тип оплаты:** {payment_type}\n"
            f"💰 **Сумма:** {formatted_price} сум"
        )

        # Подтверждение курьеру
        await message.answer(f"✅ **Отчет принят!**\n\n{report_text}", parse_mode="Markdown")

        # Отправка в канал / группа
        if CHANNEL_ID:
            await bot.send_message(chat_id=CHANNEL_ID, text=report_text, parse_mode="Markdown")

    except Exception as e:
        logging.error(f"Ошибка обработки WebApp data: {e}")
        await message.answer("❌ Произошла ошибка при отправке отчета.")


async def main():
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
