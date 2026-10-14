import asyncio
import json
import logging
import os
from aiogram import Bot, Dispatcher, F, types
from aiogram.filters import CommandStart
from aiogram.types import WebAppInfo, InlineKeyboardMarkup, InlineKeyboardButton
from aiohttp import web

# Логирование
logging.basicConfig(level=logging.INFO)

TOKEN = os.getenv("TOKEN")
CHANNEL_ID = os.getenv("CHANNEL_ID")
PORT = int(os.getenv("PORT", 8080))  # Render передает порт через эту переменную

bot = Bot(token=TOKEN)
dp = Dispatcher()

# 1. Раздача index.html через aiohttp
async def handle_index(request):
    try:
        if os.path.exists("index.html"):
            return web.FileResponse("index.html")
        return web.Response(text="index.html not found", status=404)
    except Exception as e:
        return web.Response(text=str(e), status=500)

async def handle_health(request):
    return web.Response(text="OK", status=200)

# 2. Обработчики Aiogram
@dp.message(CommandStart())
async def cmd_start(message: types.Message):
    # Берем адрес приложения из публичной ссылки Render или задаем вручную
    web_app_url = os.getenv("RENDER_EXTERNAL_URL", "https://water-bot-e0ff.onrender.com")
    
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
    await message.answer("Нажмите кнопку ниже для заполнения отчета:", reply_markup=keyboard)


@dp.message(F.web_app_data)
async def handle_web_app_data(message: types.Message):
    logging.info(f"Получены данные из Mini App: {message.web_app_data.data}")
    
    try:
        data = json.loads(message.web_app_data.data)
        
        client_id = data.get("client_id")
        delivered = data.get("delivered")
        returned = data.get("returned")
        client_type = data.get("client_type")
        payment_type = data.get("payment_type")
        total_price = data.get("total_price", 0)
        
        formatted_price = f"{total_price:,}".replace(",", " ")
        
        report_text = (
            f"📋 **ОТЧЕТ О ДОСТАВКЕ (19л)**\n\n"
            f"👤 **Курьер:** {message.from_user.full_name} (@{message.from_user.username or 'нет_юзернейма'})\n"
            f"🆔 **ID абонента:** `{client_id}`\n"
            f"💧 **Сдал полных (19л):** {delivered} шт.\n"
            f"🔄 **Принял пустых (19л):** {returned} шт.\n"
            f"👥 **Тип клиента:** {client_type}\n"
            f"💳 **Тип оплаты:** {payment_type}\n"
            f"💰 **Сумма:** {formatted_price} сум"
        )

        # Подтверждение курьеру
        await message.answer(f"✅ **Отчет успешно отправлен!**\n\n{report_text}", parse_mode="Markdown")

        # Отправка в канал
        if CHANNEL_ID:
            target_chat = int(CHANNEL_ID) if CHANNEL_ID.lstrip('-').isdigit() else CHANNEL_ID
            await bot.send_message(chat_id=target_chat, text=report_text, parse_mode="Markdown")
            logging.info(f"Отчет отправлен в канал/чат: {target_chat}")

    except Exception as e:
        logging.error(f"Ошибка обработки отчета: {e}")
        await message.answer("❌ Произошла ошибка при сохранении отчета.")


# 3. Единый запуск веб-сервера и бота
async def main():
    app = web.Application()
    app.router.add_get("/", handle_index)
    app.router.add_get("/health", handle_health)

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", PORT)
    await site.start()
    logging.info(f"Веб-сервер запущен на порту {PORT}")

    # Запускаем поллинг бота
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
