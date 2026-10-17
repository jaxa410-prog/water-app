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
    waiting_for_period = State()
    waiting_for_action = State()

class CourierMyStatsStates(StatesGroup):
    waiting_for_period = State()

class AdminManageStates(StatesGroup):
    waiting_for_add_data = State()
    waiting_for_rename_data = State()
    waiting_for_remove_id = State()

# --- Функции работы с Supabase ---
def load_couriers():
    """Загружает список курьеров из базы (возвращает словарь {user_id: name})"""
    if not supabase:
        return {}
    try:
        response = supabase.table("couriers").select("user_id, name").execute()
        couriers_dict = {}
        for row in (response.data or []):
            uid = row.get("user_id")
            name = row.get("name", "Курьер")
            if uid is not None:
                couriers_dict[int(uid)] = str(name)
        return couriers_dict
    except Exception as e:
        logging.error(f"Ошибка загрузки курьеров из Supabase: {e}")
        return {}

def save_courier_to_db(user_id, name):
    """Добавляет или обновляет курьера с именем в Supabase"""
    if not supabase:
        return
    try:
        existing = supabase.table("couriers").select("user_id").eq("user_id", int(user_id)).execute()
        if existing.data and len(existing.data) > 0:
            supabase.table("couriers").update({"name": name}).eq("user_id", int(user_id)).execute()
        else:
            supabase.table("couriers").insert({"user_id": int(user_id), "name": name}).execute()
        logging.info(f"Курьер {name} (ID: {user_id}) успешно сохранен в базе!")
    except Exception as e:
        logging.error(f"Ошибка сохранения курьера в Supabase: {e}")

def remove_courier_from_db(user_id):
    """Удаляет курьера из базы"""
    if not supabase:
        return
    try:
        supabase.table("couriers").delete().eq("user_id", int(user_id)).execute()
    except Exception as e:
        logging.error(f"Ошибка удаления курьера из Supabase: {e}")

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

# ПРЕМИУМ HTML MINI APP
HTML_CONTENT = """
<!DOCTYPE html>
<html lang="ru">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no">
    <title>Отчет курьера</title>
    <script src="https://telegram.org/js/telegram-web-app.js"></script>
    <link href="https://fonts.googleapis.com/css2?family=Plus+Jakarta+Sans:wght@400;500;600;700&display=swap" rel="stylesheet">
    <style>
        :root {
            --bg-color: var(--tg-theme-bg-color, #f8fafc);
            --card-bg: var(--tg-theme-secondary-bg-color, #ffffff);
            --text-color: var(--tg-theme-text-color, #0f172a);
            --hint-color: var(--tg-theme-hint-color, #64748b);
            --primary-color: var(--tg-theme-button-color, #0284c7);
            --primary-text: var(--tg-theme-button-text-color, #ffffff);
            --border-color: #e2e8f0;
            --accent-bg: #f0f9ff;
        }

        * {
            box-sizing: border-box;
            -webkit-tap-highlight-color: transparent;
        }

        body {
            font-family: 'Plus Jakarta Sans', -apple-system, sans-serif;
            background-color: var(--bg-color);
            color: var(--text-color);
            margin: 0;
            padding: 12px;
            display: flex;
            justify-content: center;
        }

        .card {
            width: 100%;
            max-width: 420px;
            background: var(--card-bg);
            border-radius: 20px;
            padding: 20px;
            box-shadow: 0 10px 25px -5px rgba(0, 0, 0, 0.05), 0 8px 10px -6px rgba(0, 0, 0, 0.01);
            border: 1px solid rgba(226, 232, 240, 0.8);
        }

        .header {
            display: flex;
            justify-content: space-between;
            align-items: center;
            margin-bottom: 20px;
        }

        .brand {
            display: flex;
            align-items: center;
            gap: 10px;
        }

        .brand-icon {
            width: 38px;
            height: 38px;
            background: linear-gradient(135deg, #38bdf8, #0284c7);
            border-radius: 12px;
            display: flex;
            align-items: center;
            justify-content: center;
            color: white;
            font-size: 20px;
            box-shadow: 0 4px 12px rgba(2, 132, 199, 0.25);
        }

        .brand-title {
            font-size: 17px;
            font-weight: 700;
            letter-spacing: -0.3px;
        }

        .lang-select {
            background: var(--bg-color);
            border: 1px solid var(--border-color);
            border-radius: 10px;
            padding: 6px 10px;
            font-size: 13px;
            font-weight: 600;
            color: var(--text-color);
            outline: none;
        }

        .form-group {
            margin-bottom: 16px;
        }

        label {
            display: block;
            font-size: 13px;
            font-weight: 600;
            margin-bottom: 6px;
            color: var(--hint-color);
            text-transform: uppercase;
            letter-spacing: 0.5px;
        }

        input, select {
            width: 100%;
            padding: 12px 14px;
            border-radius: 12px;
            border: 1.5px solid var(--border-color);
            background: var(--card-bg);
            color: var(--text-color);
            font-size: 15px;
            font-weight: 500;
            transition: all 0.2s ease;
            outline: none;
        }

        input:focus, select:focus {
            border-color: var(--primary-color);
            box-shadow: 0 0 0 4px rgba(2, 132, 199, 0.1);
        }

        /* Быстрые кнопки количества */
        .quick-chips {
            display: flex;
            gap: 6px;
            margin-top: 8px;
        }

        .chip {
            flex: 1;
            padding: 7px 0;
            border-radius: 8px;
            border: 1px solid var(--border-color);
            background: var(--bg-color);
            color: var(--text-color);
            font-size: 13px;
            font-weight: 600;
            text-align: center;
            cursor: pointer;
            transition: all 0.15s ease;
        }

        .chip:active {
            transform: scale(0.95);
            background: var(--primary-color);
            color: var(--primary-text);
        }

        .row-grid {
            display: grid;
            grid-template-columns: 1fr 1fr;
            gap: 12px;
        }

        .summary-box {
            background: linear-gradient(135deg, #f0f9ff, #e0f2fe);
            border-radius: 14px;
            padding: 14px 16px;
            margin: 18px 0 14px 0;
            display: flex;
            justify-content: space-between;
            align-items: center;
            border: 1px solid #bae6fd;
        }

        .summary-label {
            font-size: 13px;
            font-weight: 600;
            color: #0369a1;
        }

        .summary-value {
            font-size: 20px;
            font-weight: 800;
            color: #0284c7;
        }

        .btn-submit {
            width: 100%;
            padding: 16px;
            border: none;
            border-radius: 14px;
            background: linear-gradient(135deg, #0284c7, #0369a1);
            color: white;
            font-size: 16px;
            font-weight: 700;
            cursor: pointer;
            box-shadow: 0 6px 20px rgba(2, 132, 199, 0.3);
            transition: all 0.2s ease;
        }

        .btn-submit:active {
            transform: scale(0.98);
        }

        .btn-submit:disabled {
            background: #cbd5e1;
            box-shadow: none;
            cursor: not-allowed;
        }
    </style>
</head>
<body>
    <div class="card">
        <div class="header">
            <div class="brand">
                <div class="brand-icon">💧</div>
                <div class="brand-title" id="t_title">Отчет курьера</div>
            </div>
            <select id="langSelect" class="lang-select">
                <option value="uz">🇺🇿 UZ</option>
                <option value="ru" selected>🇷🇺 RU</option>
            </select>
        </div>

        <form id="reportForm">
            <div class="form-group">
                <label id="t_clientId">ID Абонента / Фирма</label>
                <input type="text" id="clientId" placeholder="Например: 1045 или Avesta" required autocomplete="off">
            </div>

            <div class="form-group">
                <label id="t_delivered">Сдал полных (19л)</label>
                <input type="number" id="delivered" placeholder="0" min="2" required>
                <div class="quick-chips">
                    <div class="chip" onclick="setQty('delivered', 2)">2</div>
                    <div class="chip" onclick="setQty('delivered', 3)">3</div>
                    <div class="chip" onclick="setQty('delivered', 4)">4</div>
                    <div class="chip" onclick="setQty('delivered', 5)">5</div>
                    <div class="chip" onclick="setQty('delivered', 0)">Сброс</div>
                </div>
            </div>

            <div class="form-group">
                <label id="t_returned">Принял пустых (19л)</label>
                <input type="number" id="returned" placeholder="0" min="0" required>
                <div class="quick-chips">
                    <div class="chip" onclick="setQty('returned', 1)">1</div>
                    <div class="chip" onclick="setQty('returned', 2)">2</div>
                    <div class="chip" onclick="setQty('returned', 3)">3</div>
                    <div class="chip" onclick="setQty('returned', 4)">4</div>
                    <div class="chip" onclick="setQty('returned', 5)">5</div>
                    <div class="chip" onclick="setQty('returned', 0)">Сброс</div>
                </div>
            </div>

            <div class="row-grid">
                <div class="form-group">
                    <label id="t_clientType">Тип клиента</label>
                    <select id="clientType">
                        <option value="Физ. лицо" id="opt_phys">Физ. лицо</option>
                        <option value="Юр. лицо" id="opt_jur">Юр. лицо</option>
                    </select>
                </div>
                <div class="form-group">
                    <label id="t_paymentType">Тип оплаты</label>
                    <select id="paymentType">
                        <option value="Наличные" id="opt_cash">Наличные</option>
                        <option value="Click/Payme">Click/Payme</option>
                        <option value="Перечисление" id="opt_bank">Перечисление</option>
                    </select>
                </div>
            </div>

            <div class="summary-box">
                <div class="summary-label" id="t_totalLabel">К оплате:</div>
                <div class="summary-value" id="summaryDisplay">0 сум</div>
            </div>

            <button type="submit" id="submitBtn" class="btn-submit" disabled>Отправить отчет</button>
        </form>
    </div>

    <script>
        const tg = window.Telegram?.WebApp;
        if (tg) tg.expand();

        const PRICE_PER_BOTTLE = 23000;

        const translations = {
            ru: {
                title: "Отчет курьера",
                clientId: "ID Абонента / Фирма",
                clientIdPlaceholder: "Например: 1045 или Avesta",
                delivered: "Сдал полных (19л)",
                returned: "Принял пустых (19л)",
                clientType: "Тип клиента",
                jur: "Юр. лицо",
                phys: "Физ. лицо",
                paymentType: "Тип оплаты",
                cash: "Наличные",
                bank: "Перечисление",
                totalLabel: "К оплате:",
                submit: "Отправить отчет",
                sending: "Отправка..."
            },
            uz: {
                title: "Kuryer hisoboti",
                clientId: "Abonent ID / Firma",
                clientIdPlaceholder: "Masalan: 1045 yoki Avesta",
                delivered: "Topshirildi (19l)",
                returned: "Qabul qilindi (19l)",
                clientType: "Mijoz turi",
                jur: "Yuridik shaxs",
                phys: "Jismoniy shaxs",
                paymentType: "To'lov turi",
                cash: "Naqd pul",
                bank: "Pul o'tkazmasi",
                totalLabel: "Jami summa:",
                submit: "Hisobotni yuborish",
                sending: "Yuborilmoqda..."
            }
        };

        const langSelect = document.getElementById('langSelect');
        const clientIdInput = document.getElementById('clientId');
        const deliveredInput = document.getElementById('delivered');
        const returnedInput = document.getElementById('returned');
        const clientTypeSelect = document.getElementById('clientType');
        const paymentTypeSelect = document.getElementById('paymentType');
        const summaryDisplay = document.getElementById('summaryDisplay');
        const submitBtn = document.getElementById('submitBtn');

        let currentLang = 'ru';

        langSelect.addEventListener('change', (e) => {
            currentLang = e.target.value;
            setLanguage(currentLang);
        });

        function setLanguage(lang) {
            const t = translations[lang];
            document.getElementById('t_title').innerText = t.title;
            document.getElementById('t_clientId').innerText = t.clientId;
            clientIdInput.placeholder = t.clientIdPlaceholder;
            document.getElementById('t_delivered').innerText = t.delivered;
            document.getElementById('t_returned').innerText = t.returned;
            document.getElementById('t_clientType').innerText = t.clientType;
            document.getElementById('opt_jur').innerText = t.jur;
            document.getElementById('opt_phys').innerText = t.phys;
            document.getElementById('t_paymentType').innerText = t.paymentType;
            document.getElementById('opt_cash').innerText = t.cash;
            document.getElementById('opt_bank').innerText = t.bank;
            document.getElementById('t_totalLabel').innerText = t.totalLabel;
            if (!submitBtn.disabled && submitBtn.innerText !== translations[currentLang].sending) {
                submitBtn.innerText = t.submit;
            }
        }

        // Авто-определение типа клиента по тексту в ID
        clientIdInput.addEventListener('input', () => {
            const val = clientIdInput.value.trim().toLowerCase();
            if (val.length > 0 && isNaN(val)) {
                clientTypeSelect.value = "Юр. лицо";
            }
            validateForm();
        });

        function setQty(id, val) {
            const el = document.getElementById(id);
            el.value = val === 0 ? '' : val;
            updateCalc();
        }

        function updateCalc() {
            const count = parseInt(deliveredInput.value) || 0;
            const total = count * PRICE_PER_BOTTLE;
            summaryDisplay.innerText = total.toLocaleString('ru-RU') + " сум";
            validateForm();
        }

        deliveredInput.addEventListener('input', updateCalc);
        returnedInput.addEventListener('input', validateForm);

        function validateForm() {
            const isValid = clientIdInput.value.trim() !== "" && 
                            deliveredInput.value.trim() !== "" && 
                            returnedInput.value.trim() !== "";
            submitBtn.disabled = !isValid;
        }

        document.getElementById('reportForm').addEventListener('submit', async function(e) {
            e.preventDefault();
            submitBtn.disabled = true;
            submitBtn.innerText = translations[currentLang].sending;

            const tgUser = window.Telegram?.WebApp?.initDataUnsafe?.user;
            const delivCount = parseInt(deliveredInput.value) || 0;
            const calculatedTotal = delivCount * PRICE_PER_BOTTLE;

            const data = {
                user_id: tgUser ? tgUser.id : null,
                user_name: tgUser ? [tgUser.first_name, tgUser.last_name].filter(Boolean).join(" ") : "Курьер",
                username: tgUser ? (tgUser.username || "нет_юзернейма") : "нет_юзернейма",
                client_id: clientIdInput.value,
                delivered: delivCount,
                returned: parseInt(returnedInput.value) || 0,
                client_type: clientTypeSelect.value,
                payment_type: paymentTypeSelect.value,
                total_price: calculatedTotal
            };

            try {
                const response = await fetch('/api/report', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(data)
                });
                const resData = await response.json();
                if (response.ok && resData.status === "ok") {
                    tg ? tg.close() : alert("Успешно отправлено!");
                } else {
                    alert("Ошибка: " + (resData.message || "Попробуйте снова"));
                    submitBtn.disabled = false;
                    submitBtn.innerText = translations[currentLang].submit;
                }
            } catch (err) {
                alert("Ошибка соединения с сервером.");
                submitBtn.disabled = false;
                submitBtn.innerText = translations[currentLang].submit;
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
        
        raw_name = couriers.get(sender_id, str(data.get("user_name", "Курьер")))
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

# ОБЩИЙ СВОДНЫЙ ОТЧЕТ ЗА СЕГОДНЯ
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

# ФУНКЦИЯ ЛИЧНОГО КАБИНЕТА КУРЬЕРА
async def send_courier_personal_stats(target_message: types.Message, target_id: int, days: int = 1):
    try:
        now = datetime.now(UZ_TIMEZONE)
        today_date_str = now.strftime("%Y-%m-%d")
        cutoff = now - timedelta(days=days)
        reports = load_reports()

        phys_deliv = 0
        jur_deliv = 0
        total_returned = 0
        total_earnings = 0
        orders_count = 0

        for r in reports:
            if str(r.get("courier_id", "")) == str(target_id):
                r_date_str = str(r.get("date", ""))
                try:
                    r_date = datetime.strptime(r_date_str, "%Y-%m-%d %H:%M:%S").replace(tzinfo=UZ_TIMEZONE)
                except Exception:
                    continue

                if days == 1:
                    if not r_date_str.startswith(today_date_str):
                        continue
                else:
                    if r_date < cutoff:
                        continue

                deliv = int(r.get("delivered", 0) or 0)
                ret = int(r.get("returned", 0) or 0)
                ctype = str(r.get("client_type", "Физ. лицо"))
                earn = int(r.get("earnings", 0) or 0)

                if "Юр" in ctype:
                    jur_deliv += deliv
                else:
                    phys_deliv += deliv

                total_returned += ret
                total_earnings += earn
                orders_count += 1

        if days == 1:
            period_title = f"за сегодня ({today_date_str})"
        elif days == 7:
            period_title = "за последние 7 дней"
        else:
            period_title = "за последние 15 дней"

        if orders_count == 0:
            await target_message.answer(f"📭 У вас нет выполненных заказов {period_title}.")
            return

        total_deliv = phys_deliv + jur_deliv
        earn_fmt = f"{total_earnings:,}".replace(",", " ")

        text = (
            f"👤 **ВАШ ЛИЧНЫЙ КАБИНЕТ (Моя зарплата)**\n"
            f"📅 Период: {period_title}\n\n"
            f"📦 Выполнено заказов: {orders_count}\n"
            f"👤 Физ. лицам сдал: {phys_deliv} шт.\n"
            f"🏢 Юр. лицам сдал: {jur_deliv} шт.\n"
            f"💧 Всего сдано полных (19л): **{total_deliv} шт.**\n"
            f"🔄 Всего собрано пустых: **{total_returned} шт.**\n\n"
            f"💵 **Ваш итоговый заработок: {earn_fmt} сум**"
        )
        await target_message.answer(text, parse_mode="Markdown")
    except Exception as e:
        logging.error(f"Ошибка личного кабинета курьера: {e}")
        await target_message.answer(f"⚠️ Ошибка получения статистики: {e}")

# ОТЧЕТ ПО КУРЬЕРУ (ДЛЯ АДМИНА)
async def send_single_courier_period_report(target_message: types.Message, target_id: str, days: int = 1):
    try:
        now = datetime.now(UZ_TIMEZONE)
        today_date_str = now.strftime("%Y-%m-%d")
        cutoff = now - timedelta(days=days)
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
                try:
                    r_date = datetime.strptime(r_date_str, "%Y-%m-%d %H:%M:%S").replace(tzinfo=UZ_TIMEZONE)
                except Exception:
                    continue

                if days == 1:
                    if not r_date_str.startswith(today_date_str):
                        continue
                else:
                    if r_date < cutoff:
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

        if days == 1:
            period_title = f"ЗА СЕГОДНЯ ({today_date_str})"
        elif days == 7:
            period_title = f"ЗА ПОСЛЕДНИЕ 7 ДНЕЙ"
        else:
            period_title = f"ЗА ПОСЛЕДНИЕ 15 ДНЕЙ"

        if orders_count == 0:
            await target_message.answer(f"📭 У курьера {c_name} нет заказов {period_title.lower()}.")
            return

        total_deliv = phys_deliv + jur_deliv
        rev_fmt = f"{total_revenue:,}".replace(",", " ")
        earn_fmt = f"{total_earnings:,}".replace(",", " ")

        text = (
            f"📊 СТАТИСТИКА КУРЬЕРА {period_title}\n\n"
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
        await target_message.answer(text)
    except Exception as e:
        logging.error(f"Ошибка отчета курьера: {e}")
        await target_message.answer(f"⚠️ Ошибка: {e}")

# ПОДРОБНЫЙ СПИСОК ВСЕХ ЗАКАЗОВ КУРЬЕРА ЗА ПЕРИОД
async def send_courier_detailed_orders(target_message: types.Message, target_id: str, days: int = 1):
    try:
        now = datetime.now(UZ_TIMEZONE)
        today_date_str = now.strftime("%Y-%m-%d")
        cutoff = now - timedelta(days=days)
        reports = load_reports()

        if days == 1:
            period_title = f"ЗА СЕГОДНЯ ({today_date_str})"
        elif days == 7:
            period_title = f"ЗА ПОСЛЕДНИЕ 7 ДНЕЙ"
        else:
            period_title = f"ЗА ПОСЛЕДНИЕ 15 ДНЕЙ"

        matched_orders = []

        for r in reports:
            if str(r.get("courier_id", "")) == str(target_id):
                r_date_str = str(r.get("date", ""))
                try:
                    r_date = datetime.strptime(r_date_str, "%Y-%m-%d %H:%M:%S").replace(tzinfo=UZ_TIMEZONE)
                except Exception:
                    continue

                if days == 1:
                    if not r_date_str.startswith(today_date_str):
                        continue
                else:
                    if r_date < cutoff:
                        continue
                matched_orders.append(r)

        if not matched_orders:
            await target_message.answer(f"📭 У данного курьера нет заказов {period_title.lower()}.")
            return

        c_name = matched_orders[0].get("courier_name", "Курьер")
        c_username = matched_orders[0].get("courier_username", "нет_юзернейма")

        await target_message.answer(f"📋 ДЕТАЛЬНЫЙ СПИСОК ЗАКАЗОВ КУРЬЕРА\n👤 {c_name} (@{c_username}) — {period_title}:\n")

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

    if is_courier or is_admin:
        markup_kb.append([KeyboardButton(text="💰 Моя зарплата / Статистика")])

    if is_admin:
        markup_kb.append([KeyboardButton(text="📊 Сводка за сегодня"), KeyboardButton(text="🚚 Статистика по курьерам")])
        markup_kb.append([KeyboardButton(text="⚙️ Управление курьерами")])

    markup = ReplyKeyboardMarkup(keyboard=markup_kb, resize_keyboard=True)
    await message.answer("👋 Добро пожаловать! Используйте меню ниже для отправки отчетов или просмотра статистики.", reply_markup=markup)

@dp.message(F.text == "💰 Моя зарплата / Статистика")
async def cmd_my_stats(message: types.Message, state: FSMContext):
    user_id = message.from_user.id
    couriers = load_couriers()
    
    if not ((couriers and user_id in couriers) or (ADMIN_IDS and user_id in ADMIN_IDS)):
        await message.answer("❌ У вас нет доступа к этой функции.")
        return

    await state.set_state(CourierMyStatsStates.waiting_for_period)
    buttons = [
        [KeyboardButton(text="📅 За сегодня"), KeyboardButton(text="📅 За 7 дней")],
        [KeyboardButton(text="📅 За 15 дней"), KeyboardButton(text="🔙 Главное меню")]
    ]
    markup = ReplyKeyboardMarkup(keyboard=buttons, resize_keyboard=True)
    await message.answer("📅 Выберите период для просмотра вашей статистики:", reply_markup=markup)

@dp.message(CourierMyStatsStates.waiting_for_period, F.text.in_({"📅 За сегодня", "📅 За 7 дней", "📅 За 15 дней"}))
async def handle_my_stats_period(message: types.Message, state: FSMContext):
    user_id = message.from_user.id
    if message.text == "📅 За сегодня":
        days = 1
    elif message.text == "📅 За 7 дней":
        days = 7
    else:
        days = 15

    await state.clear()
    await send_courier_personal_stats(message, user_id, days)
    await cmd_start(message)

# КНОПКА МЕНЮ УПРАВЛЕНИЯ КУРЬЕРАМИ ДЛЯ АДМИНА
@dp.message(F.text == "⚙️ Управление курьерами")
async def admin_manage_menu(message: types.Message):
    if ADMIN_IDS and message.from_user.id not in ADMIN_IDS:
        await message.answer("❌ Нет прав.")
        return
    
    buttons = [
        [KeyboardButton(text="➕ Добавить курьера"), KeyboardButton(text="✏️ Переименовать курьера")],
        [KeyboardButton(text="🗑 Удалить курьера"), KeyboardButton(text="📋 Список курьеров")],
        [KeyboardButton(text="🔙 Главное меню")]
    ]
    markup = ReplyKeyboardMarkup(keyboard=buttons, resize_keyboard=True)
    await message.answer("⚙️ Меню управления курьерами. Выберите действие:", reply_markup=markup)

@dp.message(F.text == "➕ Добавить курьера")
async def ask_add_courier(message: types.Message, state: FSMContext):
    if ADMIN_IDS and message.from_user.id not in ADMIN_IDS:
        return
    await state.set_state(AdminManageStates.waiting_for_add_data)
    await message.answer(
        "➕ Введите данные нового курьера в формате:\n`ТЕЛЕГРАМ_ID ИМЯ`\n\nПример: `120958093 Жахонгир`", 
        parse_mode="Markdown"
    )

@dp.message(AdminManageStates.waiting_for_add_data)
async def process_add_courier(message: types.Message, state: FSMContext):
    if message.text == "🔙 Главное меню":
        await back_to_main_menu(message, state)
        return

    parts = message.text.split(maxsplit=1)
    if len(parts) < 2 or not parts[0].isdigit():
        await message.answer("⚠️ Неверный формат! Введите ID и имя через пробел (например: `120958093 Жахонгир`).", parse_mode="Markdown")
        return

    new_id = int(parts[0])
    name = parts[1].strip()
    save_courier_to_db(new_id, name)
    await state.clear()
    await message.answer(f"✅ Курьер **{name}** (ID: `{new_id}`) успешно добавлен!", parse_mode="Markdown")
    await admin_manage_menu(message)

@dp.message(F.text == "✏️ Переименовать курьера")
async def ask_rename_courier(message: types.Message, state: FSMContext):
    if ADMIN_IDS and message.from_user.id not in ADMIN_IDS:
        return
    await state.set_state(AdminManageStates.waiting_for_rename_data)
    await message.answer(
        "✏️ Введите новые данные курьера в формате:\n`ТЕЛЕГРАМ_ID НОВОЕ_ИМЯ`\n\nПример: `120958093 Жахонгир Старший`", 
        parse_mode="Markdown"
    )

@dp.message(AdminManageStates.waiting_for_rename_data)
async def process_rename_courier(message: types.Message, state: FSMContext):
    if message.text == "🔙 Главное меню":
        await back_to_main_menu(message, state)
        return

    parts = message.text.split(maxsplit=1)
    if len(parts) < 2 or not parts[0].isdigit():
        await message.answer("⚠️ Неверный формат! Введите ID и новое имя через пробел.", parse_mode="Markdown")
        return

    target_id = int(parts[0])
    new_name = parts[1].strip()
    couriers = load_couriers()

    if target_id in couriers:
        save_courier_to_db(target_id, new_name)
        await state.clear()
        await message.answer(f"✏️ Курьер с ID `{target_id}` переименован в **{new_name}**.", parse_mode="Markdown")
        await admin_manage_menu(message)
    else:
        await message.answer(f"ℹ️ Курьер с ID `{target_id}` не найден в базе. Сначала добавьте его.", parse_mode="Markdown")

@dp.message(F.text == "🗑 Удалить курьера")
async def ask_remove_courier(message: types.Message, state: FSMContext):
    if ADMIN_IDS and message.from_user.id not in ADMIN_IDS:
        return
    await state.set_state(AdminManageStates.waiting_for_remove_id)
    await message.answer("🗑 Введите Telegram ID курьера, которого нужно удалить:", parse_mode="Markdown")

@dp.message(AdminManageStates.waiting_for_remove_id)
async def process_remove_courier(message: types.Message, state: FSMContext):
    if message.text == "🔙 Главное меню":
        await back_to_main_menu(message, state)
        return

    text_id = message.text.strip()
    if not text_id.isdigit():
        await message.answer("⚠️ ID должен состоять только из цифр. Попробуйте снова.")
        return

    target_id = int(text_id)
    couriers = load_couriers()
    if target_id in couriers:
        remove_courier_from_db(target_id)
        await state.clear()
        await message.answer(f"🗑 Курьер с ID `{target_id}` удален из базы.", parse_mode="Markdown")
        await admin_manage_menu(message)
    else:
        await message.answer(f"ℹ️ Курьер с ID `{target_id}` не найден в базе.")

@dp.message(F.text == "📋 Список курьеров")
async def list_couriers_button(message: types.Message):
    if ADMIN_IDS and message.from_user.id not in ADMIN_IDS:
        return
    
    couriers = load_couriers()
    if not couriers:
        await message.answer("📭 В базе пока нет зарегистрированных курьеров.")
        return

    text = "📋 **Список зарегистрированных курьеров:**\n\n"
    for idx, (cid, name) in enumerate(couriers.items(), 1):
        text += f"{idx}. **{name}** — ID: `{cid}`\n"

    await message.answer(text, parse_mode="Markdown")

# ТЕКСТОВОЕ МЕНЮ ДЛЯ АДМИНА
@dp.message(F.text == "📊 Сводка за сегодня")
async def cmd_summary(message: types.Message):
    if ADMIN_IDS and message.from_user.id not in ADMIN_IDS:
        await message.answer("❌ Нет прав.")
        return
    await send_summary_report(message)

@dp.message(F.text == "🚚 Статистика по курьерам")
async def cmd_couriers_list_menu(message: types.Message, state: FSMContext):
    if ADMIN_IDS and message.from_user.id not in ADMIN_IDS:
        await message.answer("❌ Нет прав.")
        return

    couriers = load_couriers()
    if not couriers:
        await message.answer("📭 В базе пока нет зарегистрированных курьеров.")
        return

    keyboard_buttons = []
    for cid, name in couriers.items():
        keyboard_buttons.append([KeyboardButton(text=f"👤 Курьер: {cid} | {name}")])
    
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
        await state.set_state(CourierStatStates.waiting_for_period)

        buttons = [
            [KeyboardButton(text="📅 За сегодня"), KeyboardButton(text="📅 За 7 дней")],
            [KeyboardButton(text="📅 За 15 дней"), KeyboardButton(text="🔙 Главное меню")]
        ]
        markup = ReplyKeyboardMarkup(keyboard=buttons, resize_keyboard=True)
        await message.answer(f"📅 Выберите период для курьера (ID: `{target_id}`):", reply_markup=markup, parse_mode="Markdown")
    except Exception as e:
        await message.answer(f"⚠️ Не удалось распознать курьера: {e}")

@dp.message(CourierStatStates.waiting_for_period, F.text.in_({"📅 За сегодня", "📅 За 7 дней", "📅 За 15 дней"}))
async def handle_courier_period(message: types.Message, state: FSMContext):
    data = await state.get_data()
    target_id = data.get("selected_courier_id")
    
    if not target_id:
        await message.answer("⚠️ Курьер не выбран. Попробуйте снова.")
        await state.clear()
        return

    if message.text == "📅 За сегодня":
        days = 1
    elif message.text == "📅 За 7 дней":
        days = 7
    else:
        days = 15

    await state.update_data(selected_days=days)
    await state.set_state(CourierStatStates.waiting_for_action)

    buttons = [
        [KeyboardButton(text="📈 Итоговая статистика"), KeyboardButton(text="📋 Список всех заказов")],
        [KeyboardButton(text="🔙 Главное меню")]
    ]
    markup = ReplyKeyboardMarkup(keyboard=buttons, resize_keyboard=True)
    await message.answer("📊 Как отобразить данные?", reply_markup=markup)

@dp.message(CourierStatStates.waiting_for_action, F.text.in_({"📈 Итоговая статистика", "📋 Список всех заказов"}))
async def handle_courier_action(message: types.Message, state: FSMContext):
    data = await state.get_data()
    target_id = data.get("selected_courier_id")
    days = data.get("selected_days", 1)

    if not target_id:
        await message.answer("⚠️ Сессия истекла. Начните заново.")
        await state.clear()
        return

    if message.text == "📈 Итоговая статистика":
        await send_single_courier_period_report(message, target_id, days)
    else:
        await send_courier_detailed_orders(message, target_id, days)

@dp.message(F.text == "🔙 Главное меню")
async def back_to_main_menu(message: types.Message, state: FSMContext):
    await state.clear()
    await cmd_start(message)

# Команды управления (альтернативные)
@dp.message(Command("add"))
async def cmd_add_courier(message: types.Message):
    if ADMIN_IDS and message.from_user.id not in ADMIN_IDS:
        return
    args = message.text.split(maxsplit=2)
    if len(args) < 3 or not args[1].isdigit():
        await message.answer("⚠️ Использование: `/add ТЕЛЕГРАМ_ID ИМЯ`", parse_mode="Markdown")
        return
    save_courier_to_db(int(args[1]), args[2].strip())
    await message.answer(f"✅ Курьер **{args[2].strip()}** добавлен!", parse_mode="Markdown")

@dp.message(Command("remove"))
async def cmd_remove_courier(message: types.Message):
    if ADMIN_IDS and message.from_user.id not in ADMIN_IDS:
        return
    args = message.text.split()
    if len(args) < 2 or not args[1].isdigit():
        return
    remove_courier_from_db(int(args[1]))
    await message.answer(f"🗑 Курьер удален.")

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
