import asyncio
import json
import logging
import os
from aiogram import Bot, types
from aiohttp import web

# Настройка логирования
logging.basicConfig(level=logging.INFO)

TOKEN = os.getenv("TOKEN")
CHANNEL_ID = os.getenv("CHANNEL_ID")
PORT = int(os.getenv("PORT", 10000))
WEB_SERVER_HOST = os.getenv("RENDER_EXTERNAL_URL", "https://water-bot-e0ff.onrender.com")

bot = Bot(token=TOKEN)

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
        total_price = data.get("total_price", 0)
        
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

async def main():
    app = web.Application()
    app.router.add_get("/", handle_index)
    app.router.add_get("/health", handle_health)
    app.router.add_post("/api/report", handle_report)

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
