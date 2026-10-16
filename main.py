import asyncio
import json
import logging
import os
from aiogram import Bot, Dispatcher, F, types
from aiogram.filters import Command, CommandStart
from aiogram.types import WebAppInfo, InlineKeyboardMarkup, InlineKeyboardButton, Update
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

ENV_ADMINS = os.getenv("ADMIN_IDS", "")
ADMIN_IDS = [int(i.strip()) for i in ENV_ADMINS.split(",") if i.strip().isdigit()]


def load_couriers():
    """Загрузка списка курьеров из файла"""
    if os.path.exists(COURIERS_FILE):
        try:
            with open(COURIERS_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return []
    return []


def save_couriers(couriers):
    """Сохранение списка курьеров в файл"""
    with open(COURIERS_FILE, "w", encoding="utf-8") as f:
        json.dump(couriers, f, ensure_ascii=False, indent=4)


# HTML-страница Mini App
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
            background-color: var(--tg-theme-bg-color, #f4f4f5);
            color: var(--tg-theme-text-color, #000);
            margin: 0;
            padding: 16px;
        }
        .container {
            max-width: 400px;
            margin: 0 auto;
            background: var(--tg-theme-secondary-bg-color, #ffffff);
            padding: 20px;
            border-radius: 12px;
            box-shadow: 0 4px 12px rgba(0,0,0,0.05);
        }
        h2 {
            text-align: center;
            font-size: 20px;
            margin-bottom: 20px;
        }
        .form-group {
            margin-bottom: 14px;
        }
        label {
            display: block;
            font-size: 14px;
            margin-bottom: 6px;
            font-weight: 500;
        }
        input, select {
            width: 100%;
            padding: 10px 12px;
            box-sizing: border-box;
            border: 1px solid #ccc;
            border-radius: 8px;
            font-size: 16px;
            background: var(--tg-theme-bg-color, #fff);
            color: var(--tg-theme-text-color, #000);
        }
        button {
            width: 100%;
            background-color: var(--tg-theme-button-color, #2481cc);
            color: var(--tg-theme-button-text-color, #fff);
            border: none;
            padding: 14px;
            font-size: 16px;
            font-weight: 600;
            border-radius: 8px;
            cursor: pointer;
            margin-top: 10px;
        }
        button:disabled {
            background-color: #d1d5db;
            color: #9ca3af;
            cursor: not-allowed;
        }
        button:active:not(:disabled) {
            opacity: 0.8;
        }
    </style>
</head>
<body>
    <div class="container">
        <h2>💧 Отчет по доставке</h2>
        <form id="reportForm">
            <div class="form-group">
                <label for="clientId">ID абонента / Фирма:</label>
                <input type="text" id="clientId" required placeholder="Введите ID или название">
            </div>
            <div class="form-group">
                <label for="delivered">Сдал полных (19л):</label>
                <input type="number" id="delivered" min="0" placeholder="0" required>
            </div>
            <div class="form-group">
                <label for="returned">Принял пустых (19л):</label>
                <input type="number" id="returned" min="0" placeholder="0" required>
            </div>
            <div class="form-group">
                <label for="clientType">Тип клиента:</label>
                <select id="clientType">
                    <option value="Юр. лицо">Юр. лицо</option>
                    <option value="Физ. лицо">Физ. лицо</option>
                </select>
            </div>
            <div class="form-group">
                <label for="paymentType">Тип оплаты:</label>
                <select id="paymentType">
                    <option value="Наличные">Наличные</option>
                    <option value="Перевод (Click/Payme)">Перевод</option>
                    <option value="Терминал">Терминал</option>
                    <option value="Долг">Долг</option>
                </select>
            </div>
            <div class="form-group">
                <label for="totalPrice">Сумма (сум):</label>
                <input type="number" id="totalPrice" min="0" placeholder="0" required>
            </div>
            <button type="submit" id="submitBtn" disabled>Отправить отчет</button>
        </form>
    </div>

    <script>
        const tg = window.Telegram.WebApp;
        tg.expand();

        const PRICE_PER_BOTTLE = 23000;

        const clientIdInput = document.getElementById('clientId');
        const deliveredInput = document.getElementById('delivered');
        const returnedInput = document.getElementById('returned');
        const totalPriceInput = document.getElementById('totalPrice');
        const submitBtn = document.getElementById('submitBtn');

        // Проверка заполненности обязательных полей для активации кнопки
        function validateForm() {
            const clientIdVal = clientIdInput.value.trim();
            const deliveredVal = deliveredInput.value.trim();
            const returnedVal = returnedInput.value.trim();
            
            if (clientIdVal !== "" && deliveredVal !== "" && returnedVal !== "") {
                submitBtn.removeAttribute('disabled');
            } else {
                submitBtn.setAttribute('disabled', 'true');
            }
        }

        // Авторасчет суммы по 23 000 сум и проверка полей
        deliveredInput.addEventListener('input', function() {
            const count = parseInt(deliveredInput.value) || 0;
            totalPriceInput.value = count * PRICE_PER_BOTTLE;
            validateForm();
        });

        clientIdInput.addEventListener('input', validateForm);
        returnedInput.addEventListener('input', validateForm);

        document.getElementById('reportForm').addEventListener('submit', function(e) {
            e.preventDefault();
            const user = tg.initDataUnsafe && tg.initDataUnsafe.user ? tg.initDataUnsafe.user : {};

            const data = {
                client_id: clientIdInput.value,
                delivered: deliveredInput.value,
                returned: returnedInput.value,
                client_type: document.getElementById('clientType').value,
                payment_type: document.getElementById('paymentType').value,
                total_price: totalPriceInput.value,
                courier_id: user.id || 0,
                courier_name: user.first_name ? (user.first_name + (user.last_name ? ' ' + user.last_name : '')) : 'Курьер',
                courier_username: user.username || 'нет_юзернейма'
            };

            tg.sendData(JSON.stringify(data));
            tg.close();
        });
    </script>
</body>
</html>
"""

# 1. Веб-сервер
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


# 2. Команды управления курьерами
@dp.message(Command("add"))
async def cmd_add_courier(message: types.Message):
    if ADMIN_IDS and message.from_user.id not in ADMIN_IDS:
        await message.answer("❌ У вас нет прав администратора.")
        return

    args = message.text.split()
    if len(args) < 2 or not args[1].isdigit():
        await message.answer("⚠️ Использование: `/add ТЕЛЕГРАМ_ID`\nПример: `/add 123456789`", parse_mode="Markdown")
        return

    new_id = int(args[1])
    couriers = load_couriers()

    if new_id in couriers:
        await message.answer(f"ℹ️ Курьер с ID `{new_id}` уже есть в списке.", parse_mode="Markdown")
    else:
        couriers.append(new_id)
        save_couriers(couriers)
        await message.answer(f"✅ Курьер с ID `{new_id}` успешно добавлен в систему!", parse_mode="Markdown")


@dp.message(Command("remove"))
async def cmd_remove_courier(message: types.Message):
    if ADMIN_IDS and message.from_user.id not in ADMIN_IDS:
        await message.answer("❌ У вас нет прав администратора.")
        return

    args = message.text.split()
    if len(args) < 2 or not args[1].isdigit():
        await message.answer("⚠️ Использование: `/remove ТЕЛЕГРАМ_ID`\nПример: `/remove 123456789`", parse_mode="Markdown")
        return

    target_id = int(args[1])
    couriers = load_couriers()

    if target_id in couriers:
        couriers.remove(target_id)
        save_couriers(couriers)
        await message.answer(f"🗑 Курьер с ID `{target_id}` удален из системы.", parse_mode="Markdown")
    else:
        await message.answer(f"⚠️ Курьер с ID `{target_id}` не найден в списке.", parse_mode="Markdown")


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


# 3. Основная логика
@dp.message(CommandStart())
async def cmd_start(message: types.Message):
    user_id = message.from_user.id
    couriers = load_couriers()

    if couriers and user_id not in couriers:
        if not (ADMIN_IDS and user_id in ADMIN_IDS):
            await message.answer(
                f"⛔️ У вас нет доступа к системе отчетов.\nВаш Telegram ID: `{user_id}`", 
                parse_mode="Markdown"
            )
            return

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

        client_id = data.get("client_id")
        delivered = data.get("delivered")
        returned = data.get("returned")
        client_type = data.get("client_type")
        payment_type = data.get("payment_type")
        total_price = int(data.get("total_price", 0))
        
        courier_name = data.get("courier_name") or message.from_user.full_name
        courier_username = data.get("courier_username") or message.from_user.username or 'нет_юзернейма'
        
        formatted_price = f"{total_price:,}".replace(",", " ")
        
        report_text = (
            f"📋 **ОТЧЕТ О ДОСТАВКЕ (19л)**\n\n"
            f"👤 **Курьер:** {courier_name} (@{courier_username})\n"
            f"🆔 **ID / Фирма:** `{client_id}`\n"
            f"💧 **Сдал полных (19л):** {delivered} шт.\n"
            f"🔄 **Принял пустых (19л):** {returned} шт.\n"
            f"👥 **Тип клиента:** {client_type}\n"
            f"💳 **Тип оплаты:** {payment_type}\n"
            f"💰 **Сумма:** {formatted_price} сум"
        )

        await message.answer(f"✅ **Отчет успешно отправлен!**\n\n{report_text}", parse_mode="Markdown")

        if CHANNEL_ID:
            target_chat = int(CHANNEL_ID) if CHANNEL_ID.lstrip('-').isdigit() else CHANNEL_ID
            await bot.send_message(chat_id=target_chat, text=report_text, parse_mode="Markdown")

    except Exception as e:
        logging.error(f"Ошибка обработки отчета: {e}")
        await message.answer("❌ Произошла ошибка при сохранении отчета.")


# 4. Запуск
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
        asyncio.run(main())
    finally:
        asyncio.run(bot.session.close())
