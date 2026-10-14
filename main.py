import asyncio
import json
import logging
import os
from aiogram import Bot, Dispatcher, types
from aiogram.filters import CommandStart
from aiogram.types import WebAppInfo, InlineKeyboardMarkup, InlineKeyboardButton
from aiohttp import web

# Настройка логирования
logging.basicConfig(level=logging.INFO)

TOKEN = os.getenv("TOKEN")
CHANNEL_ID = os.getenv("CHANNEL_ID")
PORT = int(os.getenv("PORT", 10000))
WEB_SERVER_HOST = os.getenv("RENDER_EXTERNAL_URL", "https://water-bot-e0ff.onrender.com")

bot = Bot(token=TOKEN)
dp = Dispatcher()

# Обработка команды /start
@dp.message(CommandStart())
async def cmd_start(message: types.Message):
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="📦 Открыть отчет курьера",
                    web_app=WebAppInfo(url=WEB_SERVER_HOST)
                )
            ]
        ]
    )
    await message.answer("Нажмите кнопку ниже для заполнения отчета:", reply_markup=keyboard)

async def handle_index(request):
    if os.path.exists("index.html"):
        return web.FileResponse("index.html")
    return web.Response(text="index.html not found", status=404)

async def handle_health(request):
    return web.Response(text="OK", status=200)

# Прямой прием данных из веб-приложения через fetch
async def handle_report(request):
    try:
        data = await request.json()
        logging.info(f"🔥 ПОЛУЧЕНЫ ДАННЫЕ НАПРЯМУЮ: {data}")
        
        client_id = data.get("client_id")
        delivered = data.get("delivered")
        returned = data.get("returned")
        client_type = data.get("client_type")
        payment_type = data.get("payment_type")
        
        # Надежное преобразование суммы в число без ошибок форматирования
        try:
            total_price = int(float(str(data.get("total_price", 0)).replace(" ", "").replace(",", ".")))
        except (ValueError, TypeError):
            total_price = 0
        
        formatted_price = f"{total_price:,}".replace(",", " ")
        
        report_text = (
            f"📋 **ОТЧЕТ О ДОСТАВКЕ (19л)**\n\n"
            f"🆔 **ID абонента:** `{client_id}`\n"
            f"💧 **Сдал полных (19л):** {delivered} шт.\n"
            f"🔄 **Принял пустых (19л):** {returned} шт.\n"
            f"👥 **Тип клиента:** {client_type}\n"
            f"💳 **Тип оплаты:** {payment_type}\n"
            f"💰 **Сумма:** {formatted_price} сум"
        )

        # Отправляем в Telegram-канал
        if CHANNEL_ID:
            target_chat = int(CHANNEL_ID) if CHANNEL_ID.lstrip('-').isdigit() else CHANNEL_ID
            await bot.send_message(chat_id=target_chat, text=report_text, parse_mode="Markdown")
            logging.info(f"Отчет успешно отправлен в канал: {target_chat}")
            return web.json_response({"status": "ok"})
        else:
            logging.warning("CHANNEL_ID не задан в переменных окружения!")
            return web.json_response({"status": "error", "message": "CHANNEL_ID not set"}, status=500)

    except Exception as e:
        logging.error(f"❌ Ошибка обработки отчета: {e}")
        return web.json_response({"status": "error", "message": str(e)}, status=500)

# Прием входящих обновлений от Telegram (для команды /start)
async def handle_webhook(request):
    try:
        url_token = request.match_info.get("token")
        if url_token != TOKEN:
            return web.Response(status=403)
        
        data = await request.json()
        telegram_update = types.Update.model_validate(data, context={"bot": bot})
        await dp.feed_update(bot, telegram_update)
        return web.Response(text="OK")
    except Exception as e:
        logging.error(f"Ошибка обработки вебхука: {e}")
        return web.Response(status=500)

async def main():
    app = web.Application()
    app.router.add_get("/", handle_index)
    app.router.add_get("/health", handle_health)
    app.router.add_post("/api/report", handle_report)
    app.router.add_post(f"/webhook/{TOKEN}", handle_webhook)

    # Установка вебхука для бота
    webhook_url = f"{WEB_SERVER_HOST}/webhook/{TOKEN}"
    await bot.set_webhook(url=webhook_url, drop_pending_updates=True)
    logging.info(f"Вебхук установлен: {webhook_url}")

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", PORT)
    await site.start()
    logging.info(f"Сервер успешно запущен на порту {PORT}")

    await asyncio.Event().wait()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    finally:
        asyncio.run(bot.session.close())
