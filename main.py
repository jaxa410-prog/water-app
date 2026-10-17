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
ADMIN_CHAT_ID = int(os.getenv("ADMIN_CHAT_ID", "0"))

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

def get_or_create_client(client_id: str, client_name: str = ""):
    """Получает или создает клиента и возвращает его текущий баланс тары"""
    if not supabase:
        return {"bottle_balance": 0}
    try:
        res = supabase.table("clients").select("*").eq("client_id", str(client_id)).execute()
        if res.data:
            return res.data[0]
        else:
            new_client = {
                "client_id": str(client_id),
                "name": client_name,
                "bottle_balance": 0
            }
            supabase.table("clients").insert(new_client).execute()
            return new_client
    except Exception as e:
        logging.error(f"Ошибка при работе с клиентом: {e}")
        return {"bottle_balance": 0}

def get_courier_route(courier_id: int):
    """Получает список задач курьера на сегодня из таблицы daily_routes"""
    if not supabase:
        return []
    try:
        today_str = datetime.now(UZ_TIMEZONE).strftime("%Y-%m-%d")
        response = supabase.table("daily_routes") \
            .select("*") \
            .eq("courier_id", str(courier_id)) \
            .eq("date", today_str) \
            .eq("status", "pending") \
            .execute()
        return response.data
    except Exception as e:
        logging.error(f"Ошибка получения маршрута: {e}")
        return []

def save_report_to_db_with_balance(data):
    """
    Сохраняет отчет с проверкой баланса тары (защита от махинаций),
    обновляет таблицу clients, фиксирует лог и закрывает задачу в маршруте.
    """
    if not supabase:
        return False, "Supabase не подключен"
    
    client_id = str(data.get("client_id"))
    delivered = int(data.get("delivered", 0))
    returned = int(data.get("returned", 0))
    courier_id = str(data.get("user_id"))
    task_type = data.get("task_type", "delivery")

    # 1. Получаем текущий баланс тары клиента
    client_info = get_or_create_client(client_id)
    current_balance = client_info["bottle_balance"]

    # 2. ЗАЩИТА: Курьер не может вернуть больше, чем есть на руках у клиента + доставлено в этот раз
    max_return_allowed = current_balance + delivered
    if returned > max_return_allowed:
        return False, f"Ошибка! У клиента на руках всего {current_balance} шт. Нельзя вернуть {returned} (максимум: {max_return_allowed})."

    # 3. Считаем новый баланс тары
    new_balance = current_balance + delivered - returned

    try:
        # 4. Запись отчета в reports
        res = supabase.table("reports").insert(data).execute()
        report_id = res.data[0]["id"] if res.data and "id" in res.data[0] else None

        # 5. Обновляем баланс в таблице clients
        supabase.table("clients").update({"bottle_balance": new_balance}).eq("client_id", client_id).execute()

        # 6. Пишем аудит-лог в report_logs
        supabase.table("report_logs").insert({
            "report_id": report_id,
            "client_id": client_id,
            "courier_id": courier_id,
            "action": "create",
            "new_delivered": delivered,
            "new_returned": returned
        }).execute()

        # 7. Закрываем задачу в daily_routes (если она там была)
        supabase.table("daily_routes").update({"status": "completed"})\
            .eq("courier_id", courier_id)\
            .eq("client_id", client_id)\
            .execute()

        return True, new_balance
    except Exception as e:
        logging.error(f"Ошибка записи отчета и баланса в Supabase: {e}")
        return False, str(e)


# HTML Mini App с отображением маршрута на сегодня
HTML_CONTENT = """
<!DOCTYPE html>
<html lang="ru">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Маршрут курьера / Kuryer marshruti</title>
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
            max-width: 420px;
            margin: 0 auto;
        }
        h2 {
            text-align: center;
            font-size: 20px;
            margin-bottom: 15px;
        }
        .task-card {
            background: var(--tg-theme-secondary-bg-color, #ffffff);
            padding: 14px;
            border-radius: 10px;
            margin-bottom: 10px;
            box-shadow: 0 2px 6px rgba(0,0,0,0.05);
            border-left: 5px solid #2481cc;
            cursor: pointer;
        }
        .task-card.completed {
            border-left-color: #16a34a;
            opacity: 0.6;
        }
        .client-name {
            font-weight: 600;
            font-size: 16px;
            margin-bottom: 4px;
        }
        .client-info {
            font-size: 13px;
            color: #6b7280;
            margin-bottom: 2px;
        }
        .task-badge {
            display: inline-block;
            padding: 2px 8px;
            font-size: 11px;
            font-weight: 600;
            border-radius: 4px;
            margin-top: 6px;
            background: #e0f2fe;
            color: #0369a1;
        }
        #formModal {
            display: none;
            position: fixed;
            top: 0; left: 0; width: 100%; height: 100%;
            background: rgba(0,0,0,0.5);
            justify-content: center;
            align-items: center;
            padding: 16px;
            box-sizing: border-box;
        }
        .modal-content {
            background: var(--tg-theme-bg-color, #fff);
            padding: 20px;
            border-radius: 12px;
            width: 100%;
            max-width: 380px;
            max-height: 90vh;
            overflow-y: auto;
        }
        .form-group { margin-bottom: 12px; }
        label { display: block; font-size: 13px; margin-bottom: 4px; font-weight: 500; }
        input, select {
            width: 100%; padding: 10px; box-sizing: border-box;
            border: 1px solid #ccc; border-radius: 8px; font-size: 15px;
            background: var(--tg-theme-secondary-bg-color, #fff);
            color: var(--tg-theme-text-color, #000);
        }
        button {
            width: 100%; background-color: var(--tg-theme-button-color, #2481cc);
            color: var(--tg-theme-button-text-color, #fff); border: none;
            padding: 12px; font-size: 15px; font-weight: 600; border-radius: 8px;
            cursor: pointer; margin-top: 10px;
        }
        .close-btn { background-color: #6b7280; margin-top: 6px; }
    </style>
</head>
<body>
    <div class="container">
        <h2>📍 Ваш маршрут на сегодня</h2>
        <div id="routeList">Загрузка маршрута...</div>
    </div>

    <!-- Модальное окно заполнения отчета по точке -->
    <div id="formModal">
        <div class="modal-content">
            <h3 id="modalTitle" style="margin-top:0; font-size:18px;">Сдача точки</h3>
            <form id="reportForm">
                <input type="hidden" id="modalClientId">
                <input type="hidden" id="modalTaskType">
                <div class="form-group">
                    <label id="lblDelivered">Сдал полных (19л):</label>
                    <input type="number" id="delivered" min="0" required>
                </div>
                <div class="form-group">
                    <label id="lblReturned">Принял пустых (19л):</label>
                    <input type="number" id="returned" min="0" required>
                </div>
                <div class="form-group">
                    <label>Тип оплаты:</label>
                    <select id="paymentType">
                        <option value="Наличные">Наличные</option>
                        <option value="Click/Payme">Click/Payme</option>
                        <option value="Перечисление">Перечисление</option>
                        <option value="Предоплата">Предоплата</option>
                    </select>
                </div>
                <div class="form-group">
                    <label>Сумма (сум):</label>
                    <input type="number" id="totalPrice" min="0" required>
                </div>
                <button type="submit" id="submitBtn">Сохранить отчёт</button>
                <button type="button" class="close-btn" onclick="closeModal()">Отмена</button>
            </form>
        </div>
    </div>

    <script>
        const tg = window.Telegram.WebApp;
        tg.expand();
        const PRICE_PER_BOTTLE = 23000;
        const tgUser = tg.initDataUnsafe?.user;
        const courierId = tgUser ? tgUser.id : null;

        async function loadRoute() {
            const listDiv = document.getElementById('routeList');
            if (!courierId) {
                listDiv.innerHTML = "⚠️ Не удалось определить ID курьера.";
                return;
            }

            try {
                const response = await fetch(`/api/route?courier_id=${courierId}`);
                const routes = await response.json();

                if (!routes || routes.length === 0) {
                    listDiv.innerHTML = "📭 На сегодня активных задач в маршруте нет.";
                    return;
                }

                listDiv.innerHTML = "";
                routes.forEach(task => {
                    const card = document.createElement('div');
                    card.className = `task-card ${task.status === 'completed' ? 'completed' : ''}`;
                    
                    let taskTypeText = "🚚 Доставка";
                    if (task.task_type === 'termination') taskTypeText = "🔄 Расторжение";
                    if (task.task_type === 'service') taskTypeText = "🧼 Чистка кулера";

                    card.innerHTML = `
                        <div class="client-name">${task.client_name || 'Клиент #' + task.client_id}</div>
                        <div class="client-info">📍 ${task.address || 'Адрес не указан'}</div>
                        <div class="client-info">📞 ${task.phone || 'Нет телефона'}</div>
                        <div class="client-info"><b>План:</b> ${task.planned_delivered} шт. водички</div>
                        <span class="task-badge">${taskTypeText}</span>
                    `;

                    if (task.status !== 'completed') {
                        card.onclick = () => openModal(task);
                    }
                    listDiv.appendChild(card);
                });
            } catch (e) {
                listDiv.innerHTML = "⚠️ Ошибка загрузки маршрута с сервера.";
            }
        }

        function openModal(task) {
            document.getElementById('modalClientId').value = task.client_id;
            document.getElementById('modalTaskType').value = task.task_type || 'delivery';
            document.getElementById('modalTitle').innerText = `Клиент: ${task.client_name || task.client_id}`;
            document.getElementById('delivered').value = task.planned_delivered || 0;
            document.getElementById('returned').value = 0;
            document.getElementById('totalPrice').value = (task.planned_delivered || 0) * PRICE_PER_BOTTLE;
            document.getElementById('formModal').style.display = 'flex';
        }

        function closeModal() {
            document.getElementById('formModal').style.display = 'none';
        }

        document.getElementById('delivered').addEventListener('input', function() {
            const count = parseInt(this.value) || 0;
            document.getElementById('totalPrice').value = count * PRICE_PER_BOTTLE;
        });

        document.getElementById('reportForm').addEventListener('submit', async function(e) {
            e.preventDefault();
            const submitBtn = document.getElementById('submitBtn');
            submitBtn.disabled = true;
            submitBtn.innerText = "Сохранение...";

            const data = {
                user_id: courierId,
                user_name: tgUser ? [tgUser.first_name, tgUser.last_name].filter(Boolean).join(" ") : "Курьер",
                username: tgUser ? (tgUser.username || "нет_юзернейма") : "нет_юзернейма",
                client_id: document.getElementById('modalClientId').value,
                task_type: document.getElementById('modalTaskType').value,
                delivered: document.getElementById('delivered').value,
                returned: document.getElementById('returned').value,
                payment_type: document.getElementById('paymentType').value,
                total_price: document.getElementById('totalPrice').value,
                client_type: "Физ. лицо"
            };

            try {
                const response = await fetch('/api/report', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(data)
                });
                const resData = await response.json();
                
                if (response.ok && resData.status === "ok") {
                    closeModal();
                    loadRoute();
                } else {
                    alert("Ошибка: " + (resData.message || "Попробуйте снова"));
                }
            } catch (err) {
                alert("Ошибка соединения с сервером.");
            } finally {
                submitBtn.disabled = false;
                submitBtn.innerText = "Сохранить отчёт";
            }
        });

        loadRoute();
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
        task_type = str(data.get("task_type", "delivery"))
        
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
            "earnings": courier_earnings,
            "task_type": task_type
        }

        # Сохранение с проверкой лимитов возврата тары
        success, result_msg = save_report_to_db_with_balance(report_record)
        if not success:
            return web.json_response({"status": "error", "message": result_msg}, status=400)

        new_bottle_balance = result_msg
        formatted_price = f"{total_price:,}".replace(",", " ")
        formatted_earnings = f"{courier_earnings:,}".replace(",", " ")
        
        report_text = (
            f"📋 ОТЧЕТ О ДОСТАВКЕ ({task_type.upper()})\n\n"
            f"👤 Курьер: {raw_name} (@{raw_username})\n"
            f"🆔 ID / Фирма: {client_id}\n"
            f"💧 Сдал полных (19л): {delivered} шт.\n"
            f"🔄 Принял пустых (19л): {returned} шт.\n"
            f"📦 Остаток тары у клиента: {new_bottle_balance} шт.\n"
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

async def api_get_route(request):
    """API эндпоинт для Mini App: возвращает маршрут курьера на сегодня"""
    courier_id = request.rel_url.query.get("courier_id")
    if not courier_id:
        return web.json_response([], status=400)
    routes = get_courier_route(int(courier_id))
    return web.json_response(routes)

# ОБЩИЙ СВОДНЫЙ ОТЧЕТ
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

# ОТЧЕТ ПО КУРЬЕРУ ЗА ВЫБРАННЫЙ ПЕРИОД
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

        period_title = f"ЗА СЕГОДНЯ ({today_date_str})" if days == 1 else f"ЗА ПОСЛЕДНИЕ 7 ДНЕЙ"

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

        period_title = f"ЗА СЕГОДНЯ ({today_date_str})" if days == 1 else f"ЗА ПОСЛЕДНИЕ 7 ДНЕЙ"
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

# ТЕКСТОВОЕ МЕНЮ И КОМАНДЫ
@dp.message(CommandStart())
async def cmd_start(message: types.Message):
    user_id = message.from_user.id
    couriers = load_couriers()
    
    if couriers and user_id not in couriers and (not ADMIN_IDS or user_id not in ADMIN_IDS):
        await message.answer("⛔ У вас нет доступа к системе курьеров. Обратитесь к администратору.")
        return

    is_admin = ADMIN_IDS and user_id in ADMIN_IDS

    if is_admin:
        keyboard = ReplyKeyboardMarkup(
            keyboard=[
                [KeyboardButton(text="📊 Сводка за сегодня"), KeyboardButton(text="🚚 Статистика по курьерам")],
            ],
            resize_keyboard=True
        )
        await message.answer("👋 Добро пожаловать, администратор!", reply_markup=keyboard)
    else:
        web_app_url = f"{WEB_SERVER_HOST}/"
        keyboard = InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="📱 Открыть маршрут", web_app=WebAppInfo(url=web_app_url))]
            ]
        )
        await message.answer("👋 Здравствуйте! Нажмите кнопку ниже, чтобы открыть ваш ежедневный маршрут и отправить отчёт.", reply_markup=keyboard)

@dp.message(F.text == "📊 Сводка за сегодня")
async def cmd_summary(message: types.Message):
    if ADMIN_IDS and message.from_user.id not in ADMIN_IDS:
        await message.answer("❌ Нет прав.")
        return
    await send_summary_report(message)

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
        await state.set_state(CourierStatStates.waiting_for_period)

        buttons = [
            [KeyboardButton(text="📅 За сегодня"), KeyboardButton(text="📅 За неделю (7 дней)")],
            [KeyboardButton(text="🔙 Главное меню")]
        ]
        markup = ReplyKeyboardMarkup(keyboard=buttons, resize_keyboard=True)
        await message.answer(f"📅 Выберите период для курьера (ID: `{target_id}`):", reply_markup=markup, parse_mode="Markdown")
    except Exception as e:
        await message.answer(f"⚠️ Не удалось распознать курьера: {e}")

@dp.message(CourierStatStates.waiting_for_period, F.text.in_({"📅 За сегодня", "📅 За неделю (7 дней)"}))
async def handle_courier_period(message: types.Message, state: FSMContext):
    data = await state.get_data()
    target_id = data.get("selected_courier_id")
    
    if not target_id:
        await message.answer("⚠️ Курьер не выбран. Попробуйте снова.")
        await state.clear()
        return

    days = 1 if message.text == "📅 За сегодня" else 7
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

# Запуск aiohttp сервера и вебхуков
async def on_startup(bot: Bot):
    await bot.set_webhook(WEBHOOK_URL)
    logging.info(f"Вебхук установлен: {WEBHOOK_URL}")

async def main():
    app = web.Application()
    app.router.add_get("/", handle_index)
    app.router.add_get("/health", handle_health)
    app.router.add_get("/api/route", api_get_route)
    app.router.add_post(WEBHOOK_PATH, handle_webhook)
    app.router.add_post("/api/report", api_post_report)

    dp.startup.register(on_startup)

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", PORT)
    await site.start()
    logging.info(f"Веб-сервер запущен на порту {PORT}")

    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
