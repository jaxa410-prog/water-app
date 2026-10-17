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
PORT = int(os.getenv("PORT", 10000))

WEB_SERVER_HOST = os.getenv("RENDER_EXTERNAL_URL", "https://water-bot-e0ff.onrender.com").rstrip("/")
WEBHOOK_PATH = f"/webhook/{TOKEN}"
WEBHOOK_URL = f"{WEB_SERVER_HOST}{WEBHOOK_PATH}"

bot = Bot(token=TOKEN)
dp = Dispatcher()

# Абсолютные пути для стабильной работы файлов на Render
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
COURIERS_FILE = os.path.join(BASE_DIR, "couriers.json")
REPORTS_FILE = os.path.join(BASE_DIR, "reports.json")

ENV_ADMINS = os.getenv("ADMIN_IDS", "")
ADMIN_IDS = [int(i.strip()) for i in ENV_ADMINS.split(",") if i.strip().isdigit()]

# Актуальные ставки заработка курьера за 1 бутыль (в сумах)
BONUS_PHYSICAL = 3500   # За физ. лицо — 3500 сум
BONUS_JURIDICAL = 2000  # За юр. лицо — 2000 сум


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
                data = json.load(f)
                if isinstance(data, list):
                    return data
        except Exception as e:
            logging.error(f"Ошибка чтения reports.json: {e}")
            return []
    return []


def save_report_to_db(report_data):
    reports = load_reports()
    reports.append(report_data)
    try:
        with open(REPORTS_FILE, "w", encoding="utf-8") as f:
            json.dump(reports, f, ensure_ascii=False, indent=4)
        logging.info(f"Отчет успешно сохранен. Всего записей в базе: {len(reports)}")
    except Exception as e:
        logging.error(f"Ошибка записи в reports.json: {e}")


# HTML Mini App
HTML_CONTENT = """
<!DOCTYPE html>
<html lang="ru">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Отчет курьера / Kuryer hisoboti</title>
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
        .lang-switcher {
            display: flex;
            justify-content: flex-end;
            margin-bottom: 15px;
        }
        .lang-switcher select {
            width: auto;
            padding: 4px 8px;
            font-size: 14px;
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
    </style>
</head>
<body>
    <div class="container">
        <div class="lang-switcher">
            <select id="langSelect">
                <option value="uz">🇺🇿 O'zbekcha</option>
                <option value="ru" selected>🇷🇺 Русский</option>
            </select>
        </div>
        
        <h2 id="t_title">💧 Отчет по доставке</h2>
        <form id="reportForm">
            <div class="form-group">
                <label id="t_clientId" for="clientId">ID абонента / Фирма:</label>
                <input type="text" id="clientId" required placeholder="Введите ID или название">
            </div>
            <div class="form-group">
                <label id="t_delivered" for="delivered">Сдал полных (19л):</label>
                <input type="number" id="delivered" min="0" placeholder="0" required>
            </div>
            <div class="form-group">
                <label id="t_returned" for="returned">Принял пустых (19л):</label>
                <input type="number" id="returned" min="0" placeholder="0" required>
            </div>
            <div class="form-group">
                <label id="t_clientType" for="clientType">Тип клиента:</label>
                <select id="clientType">
                    <option value="Физ. лицо" id="opt_phys">Физ. лицо</option>
                    <option value="Юр. лицо" id="opt_jur">Юр. лицо</option>
                </select>
            </div>
            <div class="form-group">
                <label id="t_paymentType" for="paymentType">Тип оплаты:</label>
                <select id="paymentType">
                    <option value="Наличные" id="opt_cash">Наличные</option>
                    <option value="Click/Payme">Click/Payme</option>
                    <option value="Перечисление" id="opt_bank">Перечисление</option>
                </select>
            </div>
            <div class="form-group">
                <label id="t_totalPrice" for="totalPrice">Сумма (сум):</label>
                <input type="number" id="totalPrice" min="0" placeholder="0" required>
            </div>
            <button type="submit" id="submitBtn" disabled>Отправить отчет</button>
        </form>
    </div>

    <script>
        const tg = window.Telegram.WebApp;
        tg.expand();

        const PRICE_PER_BOTTLE = 23000;

        const translations = {
            ru: {
                title: "💧 Отчет по доставке",
                clientId: "ID абонента / Фирма:",
                clientIdPlaceholder: "Введите ID или название",
                delivered: "Сдал полных (19л):",
                returned: "Принял пустых (19л):",
                clientType: "Тип клиента:",
                jur: "Юр. лицо",
                phys: "Физ. лицо",
                paymentType: "Тип оплаты:",
                cash: "Наличные",
                bank: "Перечисление",
                totalPrice: "Сумма (сум):",
                submit: "Отправить отчет"
            },
            uz: {
                title: "💧 Yetkazib berish hisoboti",
                clientId: "Abonent ID / Firma:",
                clientIdPlaceholder: "ID yoki nomini kiriting",
                delivered: "To'liq topshirildi (19l):",
                returned: "Bo'sh qabul qilindi (19l):",
                clientType: "Mijoz turi:",
                jur: "Yuridik shaxs",
                phys: "Jismoniy shaxs",
                paymentType: "To'lov turi:",
                cash: "Naqd pul",
                bank: "Pul o'tkazmasi",
                totalPrice: "Summa (so'm):",
                submit: "Hisobotni yuborish"
            }
        };

        const langSelect = document.getElementById('langSelect');
        const t_title = document.getElementById('t_title');
        const t_clientId = document.getElementById('t_clientId');
        const clientIdInput = document.getElementById('clientId');
        const t_delivered = document.getElementById('t_delivered');
        const t_returned = document.getElementById('t_returned');
        const t_clientType = document.getElementById('t_clientType');
        const opt_jur = document.getElementById('opt_jur');
        const opt_phys = document.getElementById('opt_phys');
        const t_paymentType = document.getElementById('t_paymentType');
        const opt_cash = document.getElementById('opt_cash');
        const opt_bank = document.getElementById('opt_bank');
        const t_totalPrice = document.getElementById('t_totalPrice');
        const deliveredInput = document.getElementById('delivered');
        const returnedInput = document.getElementById('returned');
        const totalPriceInput = document.getElementById('totalPrice');
        const submitBtn = document.getElementById('submitBtn');

        function setLanguage(lang) {
            const t = translations[lang];
            t_title.innerText = t.title;
            t_clientId.innerText = t.clientId;
            clientIdInput.placeholder = t.clientIdPlaceholder;
            t_delivered.innerText = t.delivered;
            t_returned.innerText = t.returned;
            t_clientType.innerText = t.clientType;
            opt_jur.innerText = t.jur;
            opt_phys.innerText = t.phys;
            t_paymentType.innerText = t.paymentType;
            opt_cash.innerText = t.cash;
            opt_bank.innerText = t.bank;
            t_totalPrice.innerText = t.totalPrice;
            submitBtn.innerText = t.submit;
        }

        langSelect.addEventListener('change', function() {
            setLanguage(this.value);
        });

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

        deliveredInput.addEventListener('input', function() {
            const count = parseInt(deliveredInput.value) || 0;
            totalPriceInput.value = count * PRICE_PER_BOTTLE;
            validateForm();
        });

        clientIdInput.addEventListener('input', validateForm);
        returnedInput.addEventListener('input', validateForm);

        document.getElementById('reportForm').addEventListener('submit', async function(e) {
            e.preventDefault();
            submitBtn.disabled = true;
            submitBtn.innerText = "Отправка...";

            const tgUser = window.Telegram?.WebApp?.initDataUnsafe?.user;

            const data = {
                user_id: tgUser ? tgUser.id : null,
                user_name: tgUser ? [tgUser.first_name, tgUser.last_name].filter(Boolean).join(" ") : "Курьер",
                username: tgUser ? (tgUser.username || "нет_юзернейма") : "нет_юзернейма",
                client_id: clientIdInput.value,
                delivered: deliveredInput.value,
                returned: returnedInput.value,
                client_type: document.getElementById('clientType').value,
                payment_type: document.getElementById('paymentType').value,
                total_price: totalPriceInput.value
            };

            try {
                const response = await fetch('/api/report', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(data)
                });

                const resData = await response.json();

                if (response.ok && resData.status === "ok") {
                    tg.close();
                } else {
                    alert("Ошибка отправки: " + (resData.message || "Попробуйте снова"));
                    submitBtn.disabled = false;
                    submitBtn.innerText = "Отправить отчет";
                }
            } catch (err) {
                alert("Ошибка соединения с сервером.");
                submitBtn.disabled = false;
                submitBtn.innerText = "Отправить отчет";
            }
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

# Обработчик API запроса из формы Mini App
async def handle_api_report(request):
    try:
        data = await request.json()
        sender_id = data.get("user_id")
        
        if sender_id is not None:
            try:
                sender_id = int(sender_id)
            except ValueError:
                pass

        couriers = load_couriers()
        if couriers and sender_id and (sender_id not in couriers):
            if not (ADMIN_IDS and sender_id in ADMIN_IDS):
                return web.json_response({"status": "error", "message": "У вас нет доступа (ID не найден в списке курьеров)"}, status=403)

        client_id = str(data.get("client_id", "Не указан"))
        delivered = int(data.get("delivered", 0))
        returned = int(data.get("returned", 0))
        client_type = str(data.get("client_type", "Физ. лицо"))
        payment_type = str(data.get("payment_type", "Наличные"))
        total_price = int(data.get("total_price", 0))
        
        raw_name = str(data.get("user_name", "Курьер"))
        raw_username = str(data.get("username", "нет_юзернейма"))
        
        rate = BONUS_JURIDICAL if "Юр" in client_type else BONUS_PHYSICAL
        courier_earnings = delivered * rate
        
        current_datetime = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        report_record = {
            "date": current_datetime,
            "courier_id": sender_id,
            "courier_name": raw_name,
            "courier_username": raw_username,
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
            f"📋 ОТЧЕТ О ДОСТАВКЕ (19л)\n\n"
            f"👤 Курьер: {raw_name} (@{raw_username})\n"
            f"🆔 ID / Фирма: {client_id}\n"
            f"💧 Сдал полных (19л): {delivered} шт.\n"
            f"🔄 Принял пустых (19л): {returned} шт.\n"
            f"👥 Тип клиента: {client_type}\n"
            f"💳 Тип оплаты: {payment_type}\n"
            f"💰 Сумма заказа: {formatted_price} сум\n"
            f"💵 Заработок курьера: {formatted_earnings} сум"
        )

        if sender_id:
            try:
                await bot.send_message(chat_id=sender_id, text=f"✅ Отчет успешно отправлен!\n\n{report_text}")
            except Exception as e:
                logging.error(f"Не удалось отправить сообщение курьеру {sender_id}: {e}")

        if CHANNEL_ID:
            try:
                target_chat = int(CHANNEL_ID) if CHANNEL_ID.lstrip('-').isdigit() else CHANNEL_ID
                await bot.send_message(chat_id=target_chat, text=report_text)
            except Exception as channel_err:
                logging.error(f"Ошибка при отправке в канал CHANNEL_ID ({CHANNEL_ID}): {channel_err}")

        return web.json_response({"status": "ok"})
    except Exception as e:
        logging.error(f"Ошибка сохранения отчета: {e}", exc_info=True)
        return web.json_response({"status": "error", "message": str(e)}, status=500)


# 2. Команды админа
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


# 3. Интерактивная Панель Админа
@dp.message(Command("stats"))
@dp.message(F.text == "📊 Статистика админа")
async def admin_panel_select_courier(message: types.Message):
    if ADMIN_IDS and message.from_user.id not in ADMIN_IDS:
        await message.answer("❌ У вас нет прав администратора.")
        return

    reports = load_reports()
    courier_map = {}

    for r in reports:
        cid = r.get("courier_id")
        if cid:
            try:
                cid = int(cid)
            except ValueError:
                pass
            cname = r.get("courier_name", "Курьер")
            cuser = r.get("courier_username", "нет_юзернейма")
            courier_map[cid] = f"{cname} (@{cuser})"

    inline_keyboard = []
    
    inline_keyboard.append([
        InlineKeyboardButton(text="🏢 Общий сводный отчет (За сегодня)", callback_data="admin_stat_today_all")
    ])

    for cid, label in courier_map.items():
        inline_keyboard.append([
            InlineKeyboardButton(text=f"🚚 {label}", callback_data=f"sel_cour_{cid}")
        ])

    if not courier_map:
        await message.answer("📭 В системе пока нет ни одного опубликованного отчета.")
        return

    kb = InlineKeyboardMarkup(inline_keyboard=inline_keyboard)
    await message.answer("👥 Панель администратора:\nВыберите курьера для просмотра статистики:", reply_markup=kb)


@dp.callback_query(F.data.startswith("sel_cour_"))
async def admin_select_period(callback: types.CallbackQuery):
    await callback.answer()
    if ADMIN_IDS and callback.from_user.id not in ADMIN_IDS:
        await callback.message.answer("❌ Нет доступа")
        return

    courier_id = callback.data.split("_")[2]

    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="📅 За сегодня", callback_data=f"rep_{courier_id}_1"),
                InlineKeyboardButton(text="📅 За неделю (7д)", callback_data=f"rep_{courier_id}_7")
            ],
            [
                InlineKeyboardButton(text="📅 За 15 дней", callback_data=f"rep_{courier_id}_15"),
                InlineKeyboardButton(text="📅 За месяц (30д)", callback_data=f"rep_{courier_id}_30")
            ],
            [
                InlineKeyboardButton(text="📅 За всё время", callback_data=f"rep_{courier_id}_all")
            ]
        ]
    )

    await callback.message.edit_text(
        f"👤 Выбран курьер ID: {courier_id}\n📅 Выберите период для расчета:",
        reply_markup=kb
    )


@dp.callback_query(F.data.startswith("rep_"))
async def admin_show_courier_report(callback: types.CallbackQuery):
    await callback.answer()
    if ADMIN_IDS and callback.from_user.id not in ADMIN_IDS:
        await callback.message.answer("❌ Нет доступа")
        return

    parts = callback.data.split("_")
    target_id = parts[1]
    period = parts[2]

    days_limit = int(period) if period.isdigit() else None
    reports = load_reports()
    now = datetime.now()

    phys_deliv = 0
    jur_deliv = 0
    total_returned = 0
    total_revenue = 0
    total_earnings = 0
    orders_count = 0
    courier_name = "Курьер"
    courier_username = "нет_юзернейма"

    for r in reports:
        r_cid = str(r.get("courier_id", ""))
        if r_cid == str(target_id):
            r_date_str = r.get("date", "")
            try:
                r_date = datetime.strptime(r_date_str, "%Y-%m-%d %H:%M:%S")
            except Exception:
                continue

            if days_limit:
                cutoff = now - timedelta(days=days_limit)
                if r_date < cutoff:
                    continue

            courier_name = r.get("courier_name", courier_name)
            courier_username = r.get("courier_username", courier_username)

            deliv = int(r.get("delivered", 0))
            ret = int(r.get("returned", 0))
            ctype = str(r.get("client_type", "Физ. лицо"))
            rev = int(r.get("total_price", 0))
            earn = int(r.get("earnings", 0))

            if "Юр" in ctype:
                jur_deliv += deliv
            else:
                phys_deliv += deliv

            total_returned += ret
            total_revenue += rev
            total_earnings += earn
            orders_count += 1

    period_titles = {
        "1": "ЗА СЕГОДНЯ",
        "7": "ЗА НЕДЕЛЮ (7 ДНЕЙ)",
        "15": "ЗА 15 ДНЕЙ",
        "30": "ЗА МЕСЯЦ (30 ДНЕЙ)",
        "all": "ЗА ВСЁ ВРЕМЯ"
    }
    header_title = period_titles.get(period, "ЗА ПЕРИОД")

    if orders_count == 0:
        await callback.message.edit_text(
            f"📭 У курьера {courier_name} нет заказов {header_title.lower()}."
        )
        return

    total_deliv = phys_deliv + jur_deliv
    rev_fmt = f"{total_revenue:,}".replace(",", " ")
    earn_fmt = f"{total_earnings:,}".replace(",", " ")

    text = (
        f"📊 СТАТИСТИКА КУРЬЕРА {header_title}\n\n"
        f"🚚 Курьер: {courier_name} (@{courier_username})\n"
        f"🆔 ID: {target_id}\n\n"
        f"📦 Выполнено заказов: {orders_count}\n"
        f"👤 Физ. лицам сдал: {phys_deliv} шт.\n"
        f"🏢 Юр. лицам сдал: {jur_deliv} шт.\n"
        f"💧 Всего сдано полных: {total_deliv} шт.\n"
        f"🔄 Всего собрано пустых: {total_returned} шт.\n"
        f"💰 Выручка компании: {rev_fmt} сум\n\n"
        f"💵 Итого заработок: {earn_fmt} сум"
    )

    await callback.message.edit_text(text)


# ОБЩИЙ СВОДНЫЙ ОТЧЕТ (ЗАЩИЩЕННЫЙ ОБРАБОТЧИК)
@dp.callback_query(F.data == "admin_stat_today_all")
async def admin_stat_today_all_handler(callback: types.CallbackQuery):
    await callback.answer("Загрузка сводки...")
    if ADMIN_IDS and callback.from_user.id not in ADMIN_IDS:
        await callback.message.answer("❌ Нет доступа")
        return

    try:
        today_date = datetime.now().strftime("%Y-%m-%d")
        reports = load_reports()
        courier_stats = {}

        for r in reports:
            if not isinstance(r, dict):
                continue
            r_date = str(r.get("date", ""))
            if today_date in r_date:
                cid = str(r.get("courier_id", "неизвестно"))
                cname = str(r.get("courier_name", "Курьер"))
                cuser = str(r.get("courier_username", "нет_юзернейма"))

                if cid not in courier_stats:
                    courier_stats[cid] = {
                        "name": cname,
                        "username": cuser,
                        "phys_delivered": 0,
                        "jur_delivered": 0,
                        "returned": 0,
                        "revenue": 0,
                        "earnings": 0,
                        "orders": 0
                    }

                deliv = int(r.get("delivered", 0) or 0)
                ret = int(r.get("returned", 0) or 0)
                rev = int(r.get("total_price", 0) or 0)
                earn = int(r.get("earnings", 0) or 0)
                ctype = str(r.get("client_type", "Физ. лицо"))

                if "Юр" in ctype:
                    courier_stats[cid]["jur_delivered"] += deliv
                else:
                    courier_stats[cid]["phys_delivered"] += deliv

                courier_stats[cid]["returned"] += ret
                courier_stats[cid]["revenue"] += rev
                courier_stats[cid]["earnings"] += earn
                courier_stats[cid]["orders"] += 1

        if not courier_stats:
            await callback.message.edit_text(f"📭 За сегодня ({today_date}) отчетов еще не поступало.")
            return

        text = f"📊 ОБЩИЙ СВОДНЫЙ ОТЧЕТ ЗА СЕГОДНЯ ({today_date}):\n\n"
        
        grand_delivered = 0
        grand_returned = 0
        grand_revenue = 0
        grand_earnings = 0

        for cid, s in courier_stats.items():
            total_courier_deliv = s["phys_delivered"] + s["jur_delivered"]
            grand_delivered += total_courier_deliv
            grand_returned += s["returned"]
            grand_revenue += s["revenue"]
            grand_earnings += s["earnings"]

            rev_fmt = f"{s['revenue']:,}".replace(",", " ")
            earn_fmt = f"{s['earnings']:,}".replace(",", " ")

            text += (
                f"🚚 {s['name']} (@{s['username']})\n"
                f"📦 Заказов: {s['orders']} | Сдано: {total_courier_deliv} шт. (👤 {s['phys_delivered']} / 🏢 {s['jur_delivered']})\n"
                f"🔄 Пустых: {s['returned']} шт. | Выручка: {rev_fmt} сум | ЗП: {earn_fmt} сум\n"
                f"───────────────────\n"
            )

        tot_rev_fmt = f"{grand_revenue:,}".replace(",", " ")
        tot_earn_fmt = f"{grand_earnings:,}".replace(",", " ")

        text += (
            f"📊 ИТОГО ПО КОМПАНИИ:\n"
            f"💧 Сдано всего полных: {grand_delivered} шт.\n"
            f"🔄 Собрано всего пустых: {grand_returned} шт.\n"
            f"💰 Общая выручка: {tot_rev_fmt} сум\n"
            f"💵 Всего курьерских: {tot_earn_fmt} сум"
        )

        await callback.message.edit_text(text)
    except Exception as e:
        logging.error(f"Ошибка в общем сводном отчете: {e}", exc_info=True)
        await callback.message.edit_text(f"⚠️ Ошибка формирования отчета: {e}")


# 4. Основная логика бота
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

    buttons = [
        [KeyboardButton(text="💰 Мой заработок за сегодня")],
        [KeyboardButton(text="📦 Открыть отчет / Hisobotni ochish")]
    ]
    
    if not ADMIN_IDS or user_id in ADMIN_IDS:
        buttons.insert(0, [KeyboardButton(text="📊 Статистика админа")])

    markup = ReplyKeyboardMarkup(keyboard=buttons, resize_keyboard=True)

    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="📦 Заполнить отчет (Mini App)",
                    web_app=WebAppInfo(url=WEB_SERVER_HOST)
                )
            ]
        ]
    )
    
    await message.answer(
        "👋 Добро пожаловать!\nИспользуйте кнопку ниже для отправки отчета или меню для проверки статистики.", 
        reply_markup=keyboard
    )
    await message.answer("Меню управления:", reply_markup=markup)


@dp.message(F.text == "📦 Открыть отчет / Hisobotni ochish")
async def open_report_btn(message: types.Message):
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="📦 Заполнить отчет (Mini App)",
                    web_app=WebAppInfo(url=WEB_SERVER_HOST)
                )
            ]
        ]
    )
    await message.answer("Нажмите на кнопку ниже, чтобы открыть форму отчета:", reply_markup=keyboard)


@dp.message(F.text == "💰 Мой заработок за сегодня")
async def check_today_earnings(message: types.Message):
    user_id = message.from_user.id
    today_date = datetime.now().strftime("%Y-%m-%d")
    
    reports = load_reports()
    total_earned = 0
    total_delivered = 0
    orders_count = 0

    for r in reports:
        r_cid = str(r.get("courier_id", ""))
        if r_cid == str(user_id) and str(r.get("date", "")).startswith(today_date):
            total_earned += int(r.get("earnings", 0) or 0)
            total_delivered += int(r.get("delivered", 0) or 0)
            orders_count += 1

    formatted_earnings = f"{total_earned:,}".replace(",", " ")

    await message.answer(
        f"📊 **Ваша статистика за сегодня ({today_date}):**\n\n"
        f"📦 Успешных заказов: **{orders_count}**\n"
        f"💧 Доставлено бутылей (19л): **{total_delivered} шт.**\n"
        f"💵 **Заработано за сегодня:** `{formatted_earnings} сум`",
        parse_mode="Markdown"
    )


# 5. Запуск
async def main():
    app = web.Application()
    app.router.add_get("/", handle_index)
    app.router.add_get("/health", handle_health)
    app.router.add_post("/api/report", handle_api_report)
    app.router.add_post(WEBHOOK_PATH, handle_webhook)

    try:
        await bot.delete_webhook(drop_pending_updates=True)
        await bot.set_webhook(url=WEBHOOK_URL)
        logging.info(f"Вебхук успешно установлен на URL: {WEBHOOK_URL}")
    except Exception as e:
        logging.error(f"Ошибка при установке вебхука: {e}")

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
        pass
