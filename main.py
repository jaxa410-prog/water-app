import asyncio
import json
import logging
import os
from datetime import datetime, timedelta, timezone
import aiohttp
from aiogram import Bot, Dispatcher, F, types
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    WebAppInfo, 
    InlineKeyboardMarkup, 
    InlineKeyboardButton, 
    ReplyKeyboardMarkup, 
    KeyboardButton, 
    Update
)
from aiohttp import web
from supabase import create_client, Client

logging.basicConfig(level=logging.INFO)

TOKEN = os.getenv("TOKEN")
CHANNEL_ID = os.getenv("CHANNEL_ID")
PORT = int(os.getenv("PORT", 10000))

# Подключение к Supabase
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")

supabase: Client = None
if SUPABASE_URL and SUPABASE_KEY:
    try:
        supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
        logging.info("Успешное подключение к Supabase!")
    except Exception as e:
        logging.error(f"Ошибка подключения к Supabase: {e}")

WEB_SERVER_HOST = os.getenv("RENDER_EXTERNAL_URL", "https://water-bot-e0ff.onrender.com").rstrip("/")
WEBHOOK_PATH = f"/webhook/{TOKEN}"
WEBHOOK_URL = f"{WEB_SERVER_HOST}{WEBHOOK_PATH}"

bot = Bot(token=TOKEN)
dp = Dispatcher()

ENV_ADMINS = os.getenv("ADMIN_IDS", "")
ADMIN_IDS = [int(i.strip()) for i in ENV_ADMINS.split(",") if i.strip().isdigit()]

BONUS_PHYSICAL = 3500
BONUS_JURIDICAL = 2000
UZ_TIMEZONE = timezone(timedelta(hours=5))

# Состояния FSM
class CourierStatStates(StatesGroup):
    waiting_for_date = State()
    waiting_for_action = State()

# --- Функции работы с Supabase ---
def load_couriers():
    if not supabase:
        return []
    try:
        response = supabase.table("couriers").select("user_id").execute()
        return [row["user_id"] for row in response.data]
    except Exception as e:
        logging.error(f"Ошибка загрузки курьеров из Supabase: {e}")
        return []

def save_couriers_to_db(couriers_list):
    if not supabase:
        return
    try:
        supabase.table("couriers").delete().neq("user_id", 0).execute()
        if couriers_list:
            data_to_insert = [{"user_id": int(cid)} for cid in couriers_list]
            supabase.table("couriers").insert(data_to_insert).execute()
    except Exception as e:
        logging.error(f"Ошибка сохранения курьеров в Supabase: {e}")

def load_reports():
    if not supabase:
        return []
    try:
        response = supabase.table("reports").select("*").execute()
        return response.data
    except Exception as e:
        logging.error(f"Ошибка чтения отчетов из Supabase: {e}")
        return []

def save_report_to_db(report_data):
    if not supabase:
        logging.error("Supabase не подключен, отчет не сохранен в БД!")
        return
    try:
        supabase.table("reports").insert(report_data).execute()
        logging.info("Отчет успешно сохранен в Supabase!")
    except Exception as e:
        logging.error(f"Ошибка записи отчета в Supabase: {e}")

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

        langSelect.addEventListener('change', function() {
            setLanguage(this.value);
        });

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

        function validateForm() {
            if (clientIdInput.value.trim() !== "" && deliveredInput.value.trim() !== "" && returnedInput.value.trim() !== "") {
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
                return web.json_response({"status": "error", "message": "У вас нет доступа"}, status=403)

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
        current_datetime = datetime.now(UZ_TIMEZONE).strftime("%Y-%m-%d %H:%M:%S")

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
                logging.error(f"Ошибка отправки в канал ({CHANNEL_ID}): {channel_err}")

        return web.json_response({"status": "ok"})
    except Exception as e:
        logging.error(f"Ошибка сохранения отчета: {e}", exc_info=True)
        return web.json_response({"status": "error", "message": str(e)}, status=500)

# ГЕНЕРАТОР ИНЛАЙН-КАЛЕНДАРЯ
def generate_calendar_keyboard(year: int, month: int):
    markup_inline = []
    
    months_names = ["Январь", "Февраль", "Март", "Апрель", "Май", "Июнь", 
                    "Июль", "Август", "Сентябрь", "Октябрь", "Ноябрь", "Декабрь"]
    
    # Шапка месяца и года
    markup_inline.append([
        InlineKeyboardButton(text="◀️", callback_data=f"cal_prev_{year}_{month}"),
        InlineKeyboardButton(text=f"{months_names[month-1]} {year}", callback_data="cal_ignore"),
        InlineKeyboardButton(text="▶️", callback_data=f"cal_next_{year}_{month}")
    ])
    
    # Дни недели
    days_week = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]
    markup_inline.append([InlineKeyboardButton(text=day, callback_data="cal_ignore") for day in days_week])
    
    # Расчет дней месяца
    first_day_weekday = datetime(year, month, 1).weekday() # 0 - Пн, 6 - Вс
    if month == 12:
        next_month_first_day = datetime(year + 1, 1, 1)
    else:
        next_month_first_day = datetime(year, month + 1, 1)
    
    total_days = (next_month_first_day - timedelta(days=1)).day
    
    week = []
    # Пустые ячейки для сдвига недели
    for _ in range(first_day_weekday):
        week.append(InlineKeyboardButton(text=" ", callback_data="cal_ignore"))
        
    for day in range(1, total_days + 1):
        date_str = f"{year}-{month:02d}-{day:02d}"
        week.append(InlineKeyboardButton(text=str(day), callback_data=f"cal_select_{date_str}"))
        if len(week) == 7:
            markup_inline.append(week)
            week = []
            
    if week:
        while len(week) < 7:
            week.append(InlineKeyboardButton(text=" ", callback_data="cal_ignore"))
        markup_inline.append(week)
        
    return InlineKeyboardMarkup(inline_keyboard=markup_inline)

# ОТЧЕТ ПО КУРЬЕРУ ЗА КОНКРЕТНУЮ ДАТУ
async def send_single_courier_date_report(target_message: types.Message, target_id: str, selected_date: str):
    try:
        reports = load_reports()

        phys_deliv = 0
        jur_deliv = 0
        total_returned = 0
        total_revenue = 0
        total_earnings = 0
        orders_count = 0
        c_name = "Курьер"
        c_username = "нет_юзернейма"

        for r in reports:
            if str(r.get("courier_id", "")) == str(target_id):
                r_date_str = str(r.get("date", ""))
                if not r_date_str.startswith(selected_date):
                    continue

                c_name = r.get("courier_name", c_name)
                c_username = r.get("courier_username", c_username)
                deliv = int(r.get("delivered", 0) or 0)
                ret = int(r.get("returned", 0) or 0)
                ctype = str(r.get("client_type", "Физ. лицо"))
                rev = int(r.get("total_price", 0) or 0)
                earn = int(r.get("earnings", 0) or 0)

                if "Юр" in ctype:
                    jur_deliv += deliv
                else:
                    phys_deliv += deliv

                total_returned += ret
                total_revenue += rev
                total_earnings += earn
                orders_count += 1

        if orders_count == 0:
            await target_message.answer(f"📭 У курьера {c_name} нет заказов за дату `{selected_date}`.", parse_mode="Markdown")
            return

        total_deliv = phys_deliv + jur_deliv
        rev_fmt = f"{total_revenue:,}".replace(",", " ")
        earn_fmt = f"{total_earnings:,}".replace(",", " ")

        text = (
            f"📊 СТАТИСТИКА КУРЬЕРА ЗА ДАТУ: `{selected_date}`\n\n"
            f"🚚 Курьер: {c_name} (@{c_username})\n"
            f"🆔 ID: {target_id}\n\n"
            f"📦 Выполнено заказов: {orders_count}\n"
            f"👤 Физ. лицам сдал: {phys_deliv} шт.\n"
            f"🏢 Юр. лицам сдал: {jur_deliv} шт.\n"
            f"💧 Всего сдано полных: {total_deliv} шт.\n"
            f"🔄 Всего собрано пустых: {total_returned} шт.\n"
            f"💰 Выручка компании: {rev_fmt} сум\n\n"
            f"💵 Итого заработок: {earn_fmt} сум"
        )
        await target_message.answer(text, parse_mode="Markdown")
    except Exception as e:
        logging.error(f"Ошибка отчета курьера: {e}")
        await target_message.answer(f"⚠️ Ошибка: {e}")

# ПОДРОБНЫЙ СПИСОК ВСЕХ ЗАКАЗОВ КУРЬЕРА ЗА ДАТУ
async def send_courier_detailed_orders_date(target_message: types.Message, target_id: str, selected_date: str):
    try:
        reports = load_reports()
        matched_orders = []

        for r in reports:
            if str(r.get("courier_id", "")) == str(target_id):
                r_date_str = str(r.get("date", ""))
                if r_date_str.startswith(selected_date):
                    matched_orders.append(r)

        if not matched_orders:
            await target_message.answer(f"📭 У данного курьера нет заказов за `{selected_date}`.", parse_mode="Markdown")
            return

        c_name = matched_orders[0].get("courier_name", "Курьер")
        c_username = matched_orders[0].get("courier_username", "нет_юзернейма")

        await target_message.answer(f"📋 ДЕТАЛЬНЫЙ СПИСОК ЗАКАЗОВ КУРЬЕРА\n👤 {c_name} (@{c_username}) за `{selected_date}`:\n", parse_mode="Markdown")

        for idx, r in enumerate(matched_orders, 1):
            date_time = r.get("date", "")
            client_id = r.get("client_id", "Не указан")
            delivered = r.get("delivered", 0)
            returned = r.get("returned", 0)
            client_type = r.get("client_type", "Физ. лицо")
            payment_type = r.get("payment_type", "Наличные")
            total_price = int(r.get("total_price", 0) or 0)
            earnings = int(r.get("earnings", 0) or 0)

            price_fmt = f"{total_price:,}".replace(",", " ")
            earn_fmt = f"{earnings:,}".replace(",", " ")

            order_text = (
                f"🔹 **Заказ #{idx}** от `{date_time}`\n"
                f"🆔 Клиент / Фирма: `{client_id}`\n"
                f"👥 Тип клиента: {client_type}\n"
                f"💧 Сдал полных: **{delivered} шт.**\n"
                f"🔄 Принял пустых: **{returned} шт.**\n"
                f"💳 Оплата: {payment_type}\n"
                f"💰 Сумма: **{price_fmt} сум**\n"
                f"💵 ЗП за заказ: **{earn_fmt} сум**\n"
                f"────────────────────────"
            )
            await target_message.answer(order_text, parse_mode="Markdown")

    except Exception as e:
        logging.error(f"Ошибка детального списка заказов: {e}")
        await target_message.answer(f"⚠️ Ошибка: {e}")

# Команда /start
@dp.message(CommandStart())
async def cmd_start(message: types.Message):
    user_id = message.from_user.id
    couriers = load_couriers()
    is_admin = ADMIN_IDS and user_id in ADMIN_IDS
    is_courier = couriers and user_id in couriers

    if not is_admin and not is_courier and couriers:
        await message.answer("❌ У вас нет доступа к этому боту.")
        return

    markup_kb = []
    web_app_url = f"{WEB_SERVER_HOST}/"
    markup_kb.append([KeyboardButton(text="💧 Отправить отчет (Mini App)", web_app=WebAppInfo(url=web_app_url))])

    if is_admin:
        markup_kb.append([KeyboardButton(text="📊 Сводка за сегодня"), KeyboardButton(text="🚚 Статистика по курьерам")])

    markup = ReplyKeyboardMarkup(keyboard=markup_kb, resize_keyboard=True)
    await message.answer("👋 Добро пожаловать! Используйте меню ниже для отправки отчетов или просмотра статистики.", reply_markup=markup)

# ТЕКСТОВОЕ МЕНЮ
@dp.message(F.text == "📊 Сводка за сегодня")
async def cmd_summary(message: types.Message):
    if ADMIN_IDS and message.from_user.id not in ADMIN_IDS:
        await message.answer("❌ Нет прав.")
        return
    
    # Для сводки за сегодня используем встроенную логику
    from datetime import datetime
    today_date = datetime.now(UZ_TIMEZONE).strftime("%Y-%m-%d")
    
    # Вы можете использовать вашу существующую функцию send_summary_report
    await send_summary_report(message)

# Сводная функция для сегодняшней сводки (переиспользована из прошлого кода)
async def send_summary_report(target_message: types.Message):
    try:
        today_date = datetime.now(UZ_TIMEZONE).strftime("%Y-%m-%d")
        reports = load_reports()
        if not reports:
            await target_message.answer("📭 База данных отчетов пуста.")
            return

        courier_stats = {}
        matched_reports_count = 0

        for r in reports:
            if not isinstance(r, dict):
                continue
            if str(r.get("date", "")).startswith(today_date):
                matched_reports_count += 1
                cid = str(r.get("courier_id", "неизвестно"))
                cname = str(r.get("courier_name", "Курьер"))
                cuser = str(r.get("courier_username", "нет_юзернейма"))

                if cid not in courier_stats:
                    courier_stats[cid] = {
                        "name": cname, "username": cuser,
                        "phys_delivered": 0, "jur_delivered": 0,
                        "returned": 0, "revenue": 0, "earnings": 0, "orders": 0
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

        if matched_reports_count == 0:
            await target_message.answer(f"📭 За сегодня (`{today_date}`) отчетов не найдено.", parse_mode="Markdown")
            return

        text = f"📊 ОБЩИЙ СВОДНЫЙ ОТЧЕТ ЗА СЕГОДНЯ ({today_date}):\n\n"
        grand_deliv, grand_ret, grand_rev, grand_earn = 0, 0, 0, 0

        for cid, s in courier_stats.items():
            total_c_deliv = s["phys_delivered"] + s["jur_delivered"]
            grand_deliv += total_c_deliv
            grand_ret += s["returned"]
            grand_rev += s["revenue"]
            grand_earn += s["earnings"]

            rev_fmt = f"{s['revenue']:,}".replace(",", " ")
            earn_fmt = f"{s['earnings']:,}".replace(",", " ")

            text += (
                f"🚚 {s['name']} (@{s['username']})\n"
                f"📦 Заказов: {s['orders']} | Сдано: {total_c_deliv} шт.\n"
                f"👤 Физ. лицам: {s['phys_delivered']} шт. | 🏢 Юр. лицам: {s['jur_delivered']} шт.\n"
                f"🔄 Пустых: {s['returned']} шт. | Выручка: {rev_fmt} сум | ЗП: {earn_fmt} сум\n"
                f"───────────────────\n"
            )

        tot_rev_fmt = f"{grand_rev:,}".replace(",", " ")
        tot_earn_fmt = f"{grand_earn:,}".replace(",", " ")

        text += (
            f"📊 ИТОГО ПО КОМПАНИИ:\n"
            f"💧 Сдано всего полных: {grand_deliv} шт.\n"
            f"🔄 Собрано всего пустых: {grand_ret} шт.\n"
            f"💰 Общая выручка: {tot_rev_fmt} сум\n"
            f"💵 Всего курьерских: {tot_earn_fmt} сум"
        )
        await target_message.answer(text)
    except Exception as e:
        logging.error(f"Ошибка сводки: {e}", exc_info=True)
        await target_message.answer(f"⚠️ Ошибка: {e}")

@dp.message(F.text == "🚚 Статистика по курьерам")
async def cmd_couriers_list(message: types.Message, state: FSMContext):
    if ADMIN_IDS and message.from_user.id not in ADMIN_IDS:
        await message.answer("❌ Нет прав.")
        return

    reports = load_reports()
    courier_map = {}
    for r in reports:
        cid = r.get("courier_id")
        if cid is not None:
            cname = r.get("courier_name", "Курьер")
            cuser = r.get("courier_username", "нет_юзернейма")
            courier_map[str(cid)] = f"{cname} (@{cuser})"

    if not courier_map:
        await message.answer("📭 В базе пока нет ни одного отчета.")
        return

    keyboard_buttons = []
    for cid, label in courier_map.items():
        keyboard_buttons.append([KeyboardButton(text=f"👤 Курьер: {cid} | {label[:20]}")])
    
    keyboard_buttons.append([KeyboardButton(text="🔙 Главное меню")])
    markup = ReplyKeyboardMarkup(keyboard=keyboard_buttons, resize_keyboard=True)
    await message.answer("👤 Выберите курьера:", reply_markup=markup)

@dp.message(F.text.startswith("👤 Курьер: "))
async def handle_selected_courier(message: types.Message, state: FSMContext):
    if ADMIN_IDS and message.from_user.id not in ADMIN_IDS:
        await message.answer("❌ Нет прав.")
        return
    
    try:
        target_id = message.text.split(" | ")[0].replace("👤 Курьер: ", "").strip()
        await state.update_data(selected_courier_id=target_id)
        await state.set_state(CourierStatStates.waiting_for_date)

        now = datetime.now(UZ_TIMEZONE)
        markup = generate_calendar_keyboard(now.year, now.month)
        
        # Убираем обычную клавиатуру и выводим инлайн-календарь
        await message.answer(
            f"📅 Выберите дату для курьера (ID: `{target_id}`):", 
            reply_markup=markup, 
            parse_mode="Markdown"
        )
    except Exception as e:
        await message.answer(f"⚠️ Не удалось распознать курьера: {e}")

# ОБРАБОТЧИК КАЛЕНДАРЯ (CALLBACK)
@dp.callback_query(CourierStatStates.waiting_for_date, F.data.startswith("cal_"))
async def process_calendar_callback(callback: types.CallbackQuery, state: FSMContext):
    data_parts = callback.data.split("_")
    action = data_parts[1]

    if action == "ignore":
        await callback.answer()
        return

    if action in ["prev", "next"]:
        year = int(data_parts[2])
        month = int(data_parts[3])
        
        if action == "prev":
            month -= 1
            if month < 1:
                month = 12
                year -= 1
        else:
            month += 1
            if month > 12:
                month = 1
                year += 1
                
        markup = generate_calendar_keyboard(year, month)
        await callback.message.edit_reply_markup(reply_markup=markup)
        await callback.answer()
        return

    if action == "select":
        selected_date = data_parts[2] # ГГГГ-ММ-ДД
        await state.update_data(selected_date=selected_date)
        await state.set_state(CourierStatStates.waiting_for_action)
        
        await callback.message.delete()
        
        buttons = [
            [KeyboardButton(text="📈 Итоговая статистика"), KeyboardButton(text="📋 Список всех заказов")],
            [KeyboardButton(text="🔙 Главное меню")]
        ]
        markup = ReplyKeyboardMarkup(keyboard=buttons, resize_keyboard=True)
        await callback.message.answer(f"✅ Выбрана дата: `{selected_date}`\n📊 Как отобразить данные?", reply_markup=markup, parse_mode="Markdown")
        await callback.answer()

@dp.message(CourierStatStates.waiting_for_action, F.text.in_({"📈 Итоговая статистика", "📋 Список всех заказов"}))
async def handle_courier_action(message: types.Message, state: FSMContext):
    data = await state.get_data()
    target_id = data.get("selected_courier_id")
    selected_date = data.get("selected_date")

    if not target_id or not selected_date:
        await message.answer("⚠️ Сессия истекла. Начните заново.")
        await state.clear()
        return

    if message.text == "📈 Итоговая статистика":
        await send_single_courier_date_report(message, target_id, selected_date)
    else:
        await send_courier_detailed_orders_date(message, target_id, selected_date)

@dp.message(F.text == "🔙 Главное меню")
async def back_to_main_menu(message: types.Message, state: FSMContext):
    await state.clear()
    await cmd_start(message)

# Команды управления
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
        await message.answer(f"ℹ️ Курьер с ID `{new_id}` уже есть.", parse_mode="Markdown")
    else:
        couriers.append(new_id)
        save_couriers_to_db(couriers)
        await message.answer(f"✅ Курьер с ID `{new_id}` добавлен в базу Supabase!", parse_mode="Markdown")

@dp.message(Command("remove"))
async def cmd_remove_courier(message: types.Message):
    if ADMIN_IDS and message.from_user.id not in ADMIN_IDS:
        await message.answer("❌ У вас нет прав администратора.")
        return
    args = message.text.split()
    if len(args) < 2 or not args[1].isdigit():
        await message.answer("⚠️ Использование: `/remove ТЕЛЕГРАМ_ID`", parse_mode="Markdown")
        return
    target_id = int(args[1])
    couriers = load_couriers()
    if target_id in couriers:
        couriers.remove(target_id)
        save_couriers_to_db(couriers)
        await message.answer(f"🗑 Курьер с ID `{target_id}` удален из базы.", parse_mode="Markdown")
    else:
        await message.answer(f"ℹ️ Курьер с ID `{target_id}` не найден в базе.", parse_mode="Markdown")

# Запуск aiohttp сервера и вебхука
async def on_startup(app):
    webhook_info = await bot.get_webhook_info()
    if webhook_info.url != WEBHOOK_URL:
        await bot.set_webhook(url=WEBHOOK_URL)
        logging.info(f"Вебхук установлен: {WEBHOOK_URL}")

async def main():
    app = web.Application()
    app.router.add_get("/", handle_index)
    app.router.add_get("/health", handle_health)
    app.router.add_post(WEBHOOK_PATH, handle_webhook)
    app.router.add_post("/api/report", handle_api_report)

    app.on_startup.append(on_startup)

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", PORT)
    await site.start()
    logging.info(f"Сервер запущен на порту {PORT}")

    await asyncio.Event().wait()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logging.info("Бот остановлен.")
