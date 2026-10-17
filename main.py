import asyncio
import json
import logging
import os
from datetime import datetime, timedelta
from aiogram import Bot, Dispatcher, F, types
from aiogram.filters import Command, CommandStart
from aiogram.types import (
    WebAppInfo, 
    InlineKeyboardMarkup, 
    InlineKeyboardButton, 
    ReplyKeyboardMarkup, 
    KeyboardButton, 
    Update
)
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

COURIERS_FILE = "couriers.json"
REPORTS_FILE = "reports.json"

ENV_ADMINS = os.getenv("ADMIN_IDS", "")
ADMIN_IDS = [int(i.strip()) for i in ENV_ADMINS.split(",") if i.strip().isdigit()]

# Ставки заработка курьера за 1 бутыль (в сумах)
BONUS_PHYSICAL = 2000
BONUS_JURIDICAL = 3500


def load_couriers():
    if os.path.exists(COURIERS_FILE):
        try:
            with open(COURIERS_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return []
    return []


def save_couriers(couriers):
    with open(COURIERS_FILE, "w", encoding="utf-8") as f:
        json.dump(couriers, f, ensure_ascii=False, indent=4)


def load_reports():
    if os.path.exists(REPORTS_FILE):
        try:
            with open(REPORTS_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return []
    return []


def save_report_to_db(report_data):
    reports = load_reports()
    reports.append(report_data)
    with open(REPORTS_FILE, "w", encoding="utf-8") as f:
        json.dump(reports, f, ensure_ascii=False, indent=4)


# HTML-код Mini App с исправленной отправкой через tg.sendData
HTML_CONTENT = """
<!DOCTYPE html>
<html lang="ru">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Отчет курьера</title>
    <script src="https://telegram.org/js/telegram-web-app.js"></script>
    <style>
        body {
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif;
            background-color: var(--tg-theme-bg-color, #f4f4f9);
            color: var(--tg-theme-text-color, #333);
            margin: 0;
            padding: 16px;
            box-sizing: border-box;
        }
        .container {
            max-width: 400px;
            margin: 0 auto;
            background: var(--tg-theme-secondary-bg-color, #ffffff);
            padding: 20px;
            border-radius: 14px;
            box-shadow: 0 4px 12px rgba(0,0,0,0.08);
        }
        h2 {
            margin: 0 0 20px 0;
            font-size: 20px;
            text-align: center;
        }
        .form-group {
            margin-bottom: 15px;
        }
        label {
            display: block;
            margin-bottom: 5px;
            font-weight: 500;
            font-size: 14px;
        }
        input, select {
            width: 100%;
            padding: 10px;
            border: 1px solid #ccc;
            border-radius: 8px;
            box-sizing: border-box;
            font-size: 16px;
            background: var(--tg-theme-bg-color, #fff);
            color: var(--tg-theme-text-color, #000);
        }
        button {
            width: 100%;
            padding: 12px;
            background-color: var(--tg-theme-button-color, #2481cc);
            color: var(--tg-theme-button-text-color, #ffffff);
            border: none;
            border-radius: 8px;
            font-size: 16px;
            font-weight: 600;
            cursor: pointer;
            margin-top: 10px;
        }
    </style>
</head>
<body>
    <div class="container">
        <h2>💧 Отчет о доставке</h2>
        
        <div class="form-group">
            <label for="clientId">ID абонента / Фирма:</label>
            <input type="text" id="clientId" placeholder="Введите ID или название">
        </div>

        <div class="form-group">
            <label for="deliveredCount">Сдал полных (19л):</label>
            <input type="number" id="deliveredCount" value="0" min="0" oninput="calculateTotal()">
        </div>

        <div class="form-group">
            <label for="returnedCount">Принял пустых (19л):</label>
            <input type="number" id="returnedCount" value="0" min="0">
        </div>

        <div class="form-group">
            <label for="clientType">Тип клиента:</label>
            <select id="clientType">
                <option value="Физ. лицо">Физ. лицо</option>
                <option value="Юр. лицо">Юр. лицо</option>
            </select>
        </div>

        <div class="form-group">
            <label for="paymentType">Тип оплаты:</label>
            <select id="paymentType">
                <option value="Наличные">Наличные</option>
                <option value="Click / Payme">Click / Payme</option>
                <option value="Перечисление">Перечисление</option>
            </select>
        </div>

        <div class="form-group">
            <label for="totalPrice">Сумма (сум):</label>
            <input type="number" id="totalPrice" value="0" min="0">
        </div>

        <button onclick="sendReport()">Отправить отчет</button>
    </div>

    <script>
        const tg = window.Telegram.WebApp;
        tg.expand();

        function calculateTotal() {
            const delivered = parseInt(document.getElementById('deliveredCount').value) || 0;
            const pricePerBottle = 23000;
            document.getElementById('totalPrice').value = delivered * pricePerBottle;
        }

        function sendReport() {
            const clientId = document.getElementById('clientId').value.trim();
            if (!clientId) {
                alert('Пожалуйста, введите ID или название клиента!');
                return;
            }

            const data = {
                client_id: clientId,
                delivered: document.getElementById('deliveredCount').value,
                returned: document.getElementById('returnedCount').value,
                client_type: document.getElementById('clientType').value,
                payment_type: document.getElementById('paymentType').value,
                total_price: document.getElementById('totalPrice').value
            };

            // Передаем данные боту через официальный метод Telegram WebApp
            tg.sendData(JSON.stringify(data));
            tg.close();
        }
    </script>
</body>
</html>
"""

async def handle_index(request):
    return web.Response(text=HTML_CONTENT, content_type="text/html")

async def handle_health(request):
    return web.Response(text="OK", status=200)

async def handle_webhook(request):
    try:
        data = await request.json()
        telegram_update = Update.model_validate(data, context={"bot": bot})
        await dp.feed_update(bot, telegram_update)
        return web.Response(text="OK", status=200)
    except Exception as e:
        logging.error(f"Ошибка вебхука: {e}")
        return web.Response(text=str(e), status=500)


@dp.message(Command("add"))
async def cmd_add_courier(message: types.Message):
    if ADMIN_IDS and message.from_user.id not in ADMIN_IDS:
        await message.answer("❌ У вас нет прав администратора.")
        return
    args = message.text.split()
    if len(args) < 2 or not args[1].isdigit():
        await message.answer("⚠️ Использование: `/add ТЕЛЕГРАМ_ID`", parse_mode="Markdown")
        return
    new_id = int(args[1])
    couriers = load_couriers()
    if new_id in couriers:
        await message.answer(f"ℹ️ Курьер с ID `{new_id}` уже есть в списке.", parse_mode="Markdown")
    else:
        couriers.append(new_id)
        save_couriers(couriers)
        await message.answer(f"✅ Курьер с ID `{new_id}` успешно добавлен!", parse_mode="Markdown")


@dp.message(Command("list"))
async def cmd_list_couriers(message: types.Message):
    if ADMIN_IDS and message.from_user.id not in ADMIN_IDS:
        await message.answer("❌ У вас нет прав администратора.")
        return
    couriers = load_couriers()
    if not couriers:
        await message.answer("📭 Список курьеров пуст.")
    else:
        text = "📋 **Список разрешенных курьеров (ID):**\n\n" + "\n".join([f"• `{cid}`" for cid in couriers])
        await message.answer(text, parse_mode="Markdown")


@dp.message(CommandStart())
async def cmd_start(message: types.Message):
    user_id = message.from_user.id
    couriers = load_couriers()

    if couriers and user_id not in couriers:
        if not (ADMIN_IDS and user_id in ADMIN_IDS):
            await message.answer(f"⛔️ У вас нет доступа.\nВаш Telegram ID: `{user_id}`", parse_mode="Markdown")
            return

    buttons = [
        [KeyboardButton(text="📦 Открыть отчет / Hisobotni ochish")]
    ]
    if not ADMIN_IDS or user_id in ADMIN_IDS:
        buttons.insert(0, [KeyboardButton(text="📊 Статистика админа")])

    markup = ReplyKeyboardMarkup(keyboard=buttons, resize_keyboard=True)
    
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="📦 Заполнить отчет (Mini App)", web_app=WebAppInfo(url=WEB_SERVER_HOST))]
        ]
    )
    
    await message.answer("👋 Добро пожаловать! Используйте меню ниже:", reply_markup=markup)
    await message.answer("Форма отчета:", reply_markup=keyboard)


@dp.message(F.text == "📦 Открыть отчет / Hisobotni ochish")
async def open_report_btn(message: types.Message):
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="📦 Заполнить отчет (Mini App)", web_app=WebAppInfo(url=WEB_SERVER_HOST))]
        ]
    )
    await message.answer("Нажмите на кнопку ниже, чтобы открыть форму:", reply_markup=keyboard)


# Обработка данных, поступающих из WebApp
@dp.message(F.web_app_data)
async def handle_web_app_data(message: types.Message):
    try:
        data = json.loads(message.web_app_data.data)
        sender_id = message.from_user.id
        couriers = load_couriers()

        if couriers and sender_id not in couriers:
            if not (ADMIN_IDS and sender_id in ADMIN_IDS):
                await message.answer("❌ Ошибка безопасности: ваш ID не найден в списке курьеров.")
                return

        client_id = str(data.get("client_id", "Не указан"))
        delivered = int(data.get("delivered", 0))
        returned = int(data.get("returned", 0))
        client_type = str(data.get("client_type", "Физ. лицо"))
        payment_type = str(data.get("payment_type", "Наличные"))
        total_price = int(data.get("total_price", 0))
        
        courier_name = message.from_user.full_name
        courier_username = message.from_user.username or 'нет_юзернейма'
        
        rate = BONUS_JURIDICAL if client_type == "Юр. лицо" else BONUS_PHYSICAL
        courier_earnings = delivered * rate
        
        current_datetime = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        report_record = {
            "date": current_datetime,
            "courier_id": sender_id,
            "courier_name": courier_name,
            "courier_username": courier_username,
            "client_id": client_id,
            "delivered": delivered,
            "returned": returned,
            "client_type": client_type,
            "payment_type": payment_type,
            "total_price": total_price,
            "earnings": courier_earnings
        }
        save_report_to_db(report_record)

        formatted_price = f"{total_price:,}".replace(",", " ")
        formatted_earnings = f"{courier_earnings:,}".replace(",", " ")
        
        report_text = (
            f"📋 **ОТЧЕТ О ДОСТАВКЕ (19л)**\n\n"
            f"👤 **Курьер:** {courier_name} (@{courier_username})\n"
            f"🆔 **ID / Фирма:** `{client_id}`\n"
            f"💧 **Сдал полных (19л):** {delivered} шт.\n"
            f"🔄 **Принял пустых (19л):** {returned} шт.\n"
            f"👥 **Тип клиента:** {client_type}\n"
            f"💳 **Тип оплаты:** {payment_type}\n"
            f"💰 **Сумма заказа:** {formatted_price} сум\n"
            f"💵 **Заработок курьера:** `{formatted_earnings} сум`"
        )

        await message.answer(f"✅ **Отчет успешно отправлен!**\n\n{report_text}", parse_mode="Markdown")

        if CHANNEL_ID:
            try:
                target_chat = int(CHANNEL_ID) if CHANNEL_ID.lstrip('-').isdigit() else CHANNEL_ID
                await bot.send_message(chat_id=target_chat, text=report_text, parse_mode="Markdown")
            except Exception as channel_err:
                logging.error(f"Ошибка при отправке в канал CHANNEL_ID: {channel_err}")

    except Exception as e:
        logging.error(f"Ошибка обработки отчета: {e}")
        await message.answer(f"❌ Произошла ошибка при сохранении отчета: {e}")


async def main():
    app = web.Application()
    app.router.add_get("/", handle_index)
    app.router.add_get("/health", handle_health)
    app.router.add_post(WEBHOOK_PATH, handle_webhook)

    await bot.set_webhook(url=WEBHOOK_URL, drop_pending_updates=True)
    logging.info(f"Вебхук установлен: {WEBHOOK_URL}")

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", PORT)
    await site.start()
    logging.info(f"Веб-сервер запущен на порту {PORT}")

    # Пинг-цикл для предотвращения засыпания на Render
    async def ping_loop():
        await asyncio.sleep(10)
        import aiohttp
        async with aiohttp.ClientSession() as session:
            while True:
                try:
                    async with session.get(f"{WEB_SERVER_HOST}/health") as resp:
                        pass
                except Exception:
                    pass
                await asyncio.sleep(600)

    asyncio.create_task(ping_loop())
    await asyncio.Event().wait()

if __name__ == "__main__":
    try:
        async asyncio.run(main())
    finally:
        asyncio.run(bot.session.close())
