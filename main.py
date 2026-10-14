import asyncio
import json
import logging
import os
from aiogram import Bot, Dispatcher, types
from aiogram.filters import CommandStart
from aiogram.types import WebAppInfo, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.webhook.aiohttp_server import SimpleRequestHandler, setup_application
from aiohttp import web

# Логирование
logging.basicConfig(level=logging.INFO)

TOKEN = os.getenv("TOKEN")
CHANNEL_ID = os.getenv("CHANNEL_ID")
PORT = int(os.getenv("PORT", 8080))

WEB_SERVER_HOST = os.getenv("RENDER_EXTERNAL_URL", "https://water-bot-e0ff.onrender.com")
WEBHOOK_PATH = f"/webhook/{TOKEN}"
WEBHOOK_URL = f"{WEB_SERVER_HOST}{WEBHOOK_PATH}"

bot = Bot(token=TOKEN)
dp = Dispatcher()

async def handle_index(request):
    try:
        if os.path.exists("index.html"):
            return web.FileResponse("index.html")
        return web.Response(text="index.html not found", status=404)
    except Exception as e:
        return web.Response(text=str(e), status=500)

async def handle_health(request):
    return web.Response(text="OK", status=200)

@dp.message(CommandStart())
async def cmd_start(message: types.Message):
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="📦 Открыть отчет курьера",
                    web_app=WebAppInfo(url=f"{WEB_SERVER_HOST}/?v=3")
                )
            ]
        ]
    )
    await message.answer("Нажмите кнопку ниже для заполнения отчета:", reply_markup=keyboard)

# Ловим любые сообщения, включая данные из Web App
@dp.message()
async def catch_all_messages(message: types.Message):
    logging.info(f"Получено сообщение от пользователя ID: {message.from_user.id}")
    logging.info(f"Содержимое message.web_app_data: {message.web_app_data}")
    
    if message.web_app_data:
        raw_data = message.web_app_data.data
        logging.info(f"🔥 ПОЙМАНЫ ДАННЫЕ ИЗ WEB APP: {raw_data}")
        
        try:
            data = json.loads(raw_data)
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

            # Отправляем подтверждение пользователю
            await message.answer(f"✅ **Отчет успешно отправлен!**\n\n{report_text}", parse_mode="Markdown")
            logging.info("Ответ пользователю в чат отправлен.")

            # Отправляем в канал, если задан CHANNEL_ID
            if CHANNEL_ID:
                target_chat = int(CHANNEL_ID) if CHANNEL_ID.lstrip('-').isdigit() else CHANNEL_ID
                await bot.send_message(chat_id=target_chat, text=report_text, parse_mode="Markdown")
                logging.info(f"Отчет успешно отправлен в канал: {target_chat}")
            else:
                logging.warning("CHANNEL_ID не задан в переменных окружения Render!")

        except Exception as e:
            logging.error(f"❌ Ошибка при разборе JSON или отправке: {e}")
            await message.answer(f"❌ Ошибка обработки отчета: {e}")
    else:
        if message.text and not message.text.startswith("/"):
            await message.answer("Пожалуйста, используйте кнопку «📦 Открыть отчет курьера» для отправки данных.")

async def main():
    app = web.Application()
    app.router.add_get("/", handle_index)
    app.router.add_get("/health", handle_health)

    # Регистрация стандартного обработчика вебхуков aiogram
    webhook_requests_handler = SimpleRequestHandler(
        dispatcher=dp,
        bot=bot,
    )
    webhook_requests_handler.register(app, path=WEBHOOK_PATH)
    setup_application(app, dp, bot=bot)

    await bot.set_webhook(url=WEBHOOK_URL, drop_pending_updates=True)
    logging.info(f"Вебхук установлен: {WEBHOOK_URL}")

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", PORT)
    await site.start()
    logging.info(f"Веб-сервер запущен на порту {PORT}")

    await asyncio.Event().wait()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    finally:
        asyncio.run(bot.session.close())
