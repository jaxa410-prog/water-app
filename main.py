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

def load_couriers():
    if not supabase: return {}
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

def load_reports():
    if not supabase: return []
    try:
        response = supabase.table("reports").select("*").execute()
        return response.data or []
    except Exception as e:
        logging.error(f"Ошибка чтения отчетов из Supabase: {e}")
        return []

def save_report_to_db(report_data):
    if not supabase: return
    try:
        supabase.table("reports").insert(report_data).execute()
        logging.info("Отчет успешно сохранен в Supabase!")
    except Exception as e:
        logging.error(f"Ошибка записи отчета в Supabase: {e}")

def remove_point_from_route(courier_id, client_name):
    if not supabase or not courier_id: return
    try:
        today_date = datetime.now(UZ_TIMEZONE).strftime("%Y-%m-%d")
        supabase.table("daily_routes").delete().eq("date", today_date).eq("courier_id", int(courier_id)).eq("client_name", client_name).execute()
        logging.info(f"Точка '{client_name}' удалена из сегодняшнего маршрута курьера {courier_id}")
    except Exception as e:
        logging.error(f"Ошибка удаления точки из маршрута: {e}")

def save_rescheduled_order(courier_id, courier_name, client_name, target_date):
    if not supabase: return
    try:
        data = {
            "courier_id": courier_id,
            "courier_name": courier_name,
            "client_name": client_name,
            "target_date": target_date,
            "status": "pending"
        }
        supabase.table("rescheduled_orders").insert(data).execute()
        logging.info(f"Перенос заказа '{client_name}' успешно сохранен в Supabase!")
    except Exception as e:
        logging.error(f"Ошибка сохранения переноса заказа в Supabase: {e}")

# ==================== ПРЕМИУМ HTML MINI APP В СТИЛЕ UBER / DELIVERY ====================
HTML_CONTENT = """
<!DOCTYPE html>
<html lang="ru">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no">
    <title>Hydro Control Courier</title>
    <script src="https://telegram.org/js/telegram-web-app.js"></script>
    <link href="https://fonts.googleapis.com/css2?family=Plus+Jakarta+Sans:wght@400;500;600;700;800&display=swap" rel="stylesheet">
    <style>
        :root {
            --bg-color: #f1f5f9;
            --card-bg: #ffffff;
            --text-main: #0f172a;
            --text-secondary: #64748b;
            --primary: #10b981;
            --primary-gradient: linear-gradient(135deg, #34d399, #059669);
            --accent-blue: #0284c7;
            --border-color: #e2e8f0;
            --danger: #ef4444;
        }

        * {
            box-sizing: border-box;
            -webkit-tap-highlight-color: transparent;
            font-family: 'Plus Jakarta Sans', sans-serif;
        }

        body {
            background-color: var(--bg-color);
            color: var(--text-main);
            margin: 0;
            padding: 0;
            padding-bottom: 90px;
        }

        .top-nav-container {
            padding: 16px 16px 8px 16px;
            background: var(--card-bg);
            position: sticky;
            top: 0;
            z-index: 10;
            box-shadow: 0 2px 10px rgba(0,0,0,0.03);
        }

        .tabs-header {
            display: flex;
            background: var(--bg-color);
            border-radius: 14px;
            padding: 4px;
        }

        .tab-btn {
            flex: 1;
            padding: 10px;
            text-align: center;
            font-size: 14px;
            font-weight: 700;
            color: var(--text-secondary);
            border-radius: 10px;
            cursor: pointer;
            transition: all 0.2s ease;
        }

        .tab-btn.active {
            background: var(--card-bg);
            color: var(--text-main);
            box-shadow: 0 2px 8px rgba(0,0,0,0.06);
        }

        .tab-content {
            display: none;
            padding: 16px;
        }

        .tab-content.active {
            display: block;
        }

        .order-card {
            background: var(--card-bg);
            border-radius: 20px;
            padding: 18px;
            margin-bottom: 14px;
            box-shadow: 0 4px 15px rgba(0,0,0,0.03);
            border: 1px solid var(--border-color);
            cursor: pointer;
            transition: transform 0.1s ease;
        }

        .order-card:active {
            transform: scale(0.98);
        }

        .order-header-row {
            display: flex;
            justify-content: space-between;
            align-items: center;
            margin-bottom: 10px;
        }

        .order-id {
            font-size: 15px;
            font-weight: 700;
            color: var(--text-main);
        }

        .status-badge {
            background: #d1fae5;
            color: #065f46;
            padding: 5px 12px;
            border-radius: 20px;
            font-size: 11px;
            font-weight: 700;
        }

        .client-name {
            font-size: 17px;
            font-weight: 800;
            color: var(--text-main);
            margin-bottom: 4px;
        }

        .client-address {
            font-size: 13px;
            color: var(--text-secondary);
            font-weight: 500;
            line-height: 1.4;
            margin-bottom: 14px;
        }

        .order-footer-row {
            display: flex;
            justify-content: space-between;
            align-items: center;
            border-top: 1px solid var(--border-color);
            padding-top: 12px;
        }

        .info-pill {
            font-size: 13px;
            font-weight: 600;
            color: var(--text-secondary);
            display: flex;
            align-items: center;
            gap: 5px;
        }

        .price-pill {
            font-size: 15px;
            font-weight: 800;
            color: #059669;
            background: #ecfdf5;
            padding: 6px 12px;
            border-radius: 12px;
        }

        .modal-overlay {
            position: fixed;
            top: 0; left: 0; right: 0; bottom: 0;
            background: rgba(15, 23, 42, 0.6);
            backdrop-filter: blur(4px);
            z-index: 100;
            display: none;
            justify-content: center;
            align-items: flex-end;
        }

        .modal-card {
            background: var(--card-bg);
            width: 100%;
            max-width: 500px;
            border-top-left-radius: 28px;
            border-top-right-radius: 28px;
            padding: 24px;
            max-height: 90vh;
            overflow-y: auto;
            animation: slideUp 0.3s cubic-bezier(0.16, 1, 0.3, 1);
        }

        @keyframes slideUp {
            from { transform: translateY(100%); }
            to { transform: translateY(0); }
        }

        .modal-title {
            font-size: 18px;
            font-weight: 800;
            margin-bottom: 16px;
        }

        .form-group {
            margin-bottom: 14px;
        }

        label {
            display: block;
            font-size: 11px;
            font-weight: 700;
            color: var(--text-secondary);
            text-transform: uppercase;
            margin-bottom: 6px;
            letter-spacing: 0.5px;
        }

        input, select {
            width: 100%;
            padding: 12px 14px;
            border-radius: 14px;
            border: 1.5px solid var(--border-color);
            background: var(--bg-color);
            color: var(--text-main);
            font-size: 15px;
            font-weight: 600;
            outline: none;
        }

        .quick-chips {
            display: flex;
            gap: 6px;
            margin-top: 6px;
        }

        .chip {
            flex: 1;
            padding: 8px 0;
            border-radius: 10px;
            border: 1px solid var(--border-color);
            background: var(--card-bg);
            text-align: center;
            font-weight: 700;
            font-size: 13px;
            cursor: pointer;
        }

        .reschedule-box {
            background: #fff1f2;
            border: 1.5px dashed #f43f5e;
            border-radius: 14px;
            padding: 12px;
            margin: 14px 0;
        }

        .btn-main {
            width: 100%;
            padding: 16px;
            border: none;
            border-radius: 16px;
            background: var(--primary-gradient);
            color: white;
            font-size: 16px;
            font-weight: 800;
            cursor: pointer;
            box-shadow: 0 4px 15px rgba(16, 185, 129, 0.3);
            margin-top: 10px;
        }

        .btn-main.danger {
            background: linear-gradient(135deg, #f43f5e, #be123c);
            box-shadow: 0 4px 15px rgba(244, 63, 94, 0.3);
        }

        .bottom-nav {
            position: fixed;
            bottom: 0; left: 0; right: 0;
            background: var(--card-bg);
            display: flex;
            justify-content: space-around;
            padding: 12px 0;
            border-top: 1px solid var(--border-color);
            z-index: 50;
            box-shadow: 0 -4px 20px rgba(0,0,0,0.03);
        }

        .nav-item {
            text-align: center;
            cursor: pointer;
            color: var(--text-secondary);
            font-size: 11px;
            font-weight: 700;
            flex: 1;
        }

        .nav-item.active {
            color: var(--accent-blue);
        }

        .nav-icon {
            font-size: 20px;
            margin-bottom: 2px;
        }

        .empty-state {
            text-align: center;
            padding: 50px 20px;
            color: var(--text-secondary);
            font-weight: 600;
        }
    </style>
</head>
<body>

    <div class="top-nav-container">
        <div class="tabs-header">
            <div class="tab-btn active" onclick="switchTab('orders')" id="tabOrders">📦 Заказы на сегодня</div>
            <div class="tab-btn" onclick="switchTab('profile')" id="tabProfile">👤 Профиль</div>
        </div>
    </div>

    <div id="contentOrders" class="tab-content active">
        <div id="ordersList">
            <div class="empty-state">Загрузка маршрута...</div>
        </div>
    </div>

    <div id="contentProfile" class="tab-content">
        <div class="order-card">
            <div class="client-name" id="profileName">Курьер</div>
            <div class="client-address" id="profileId">Telegram ID: ...</div>
            <hr style="border:0; border-top:1px solid var(--border-color); margin: 12px 0;">
            <div style="font-size: 14px; font-weight: 700; color: var(--text-secondary);">Статус системы: <span style="color: var(--primary);">● Онлайн</span></div>
        </div>
    </div>

    <div id="orderModal" class="modal-overlay">
        <div class="modal-card">
            <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 15px;">
                <div class="modal-title" id="modalClientTitle">Обработка заказа</div>
                <div onclick="closeModal()" style="font-size: 22px; font-weight: bold; cursor: pointer; color: var(--text-secondary);">✕</div>
            </div>

            <form id="reportForm" onsubmit="submitReport(event)">
                <div class="reschedule-box">
                    <label style="display: flex; align-items: center; gap: 8px; cursor: pointer; color: #be123c;">
                        <input type="checkbox" id="isReschedule" onchange="toggleReschedule(this.checked)" style="width: 18px; height: 18px; accent-color: #f43f5e;">
                        <span>📅 Перенести заказ на другой день</span>
                    </label>
                    <div id="rescheduleBlock" style="display: none; margin-top: 10px;">
                        <label>Когда привезти:</label>
                        <select id="rescheduleDate">
                            <option value="Завтра">Завтра</option>
                            <option value="Послезавтра">Послезавтра</option>
                            <option value="Через 3 дня">Через 3 дня</option>
                            <option value="Уточнить по звонку">Уточнить по звонку</option>
                        </select>
                    </div>
                </div>

                <div id="deliveryFields">
                    <div class="form-group">
                        <label>Сдал полных (19л)</label>
                        <input type="number" id="delivered" placeholder="0" min="0">
                        <div class="quick-chips">
                            <div class="chip" onclick="setVal('delivered', 1)">1</div>
                            <div class="chip" onclick="setVal('delivered', 2)">2</div>
                            <div class="chip" onclick="setVal('delivered', 3)">3</div>
                            <div class="chip" onclick="setVal('delivered', 5)">5</div>
                            <div class="chip" onclick="setVal('delivered', 0)">Сброс</div>
                        </div>
                    </div>

                    <div class="form-group">
                        <label>Принял пустых (19л)</label>
                        <input type="number" id="returned" placeholder="0" min="0">
                        <div class="quick-chips">
                            <div class="chip" onclick="setVal('returned', 1)">1</div>
                            <div class="chip" onclick="setVal('returned', 2)">2</div>
                            <div class="chip" onclick="setVal('returned', 3)">3</div>
                            <div class="chip" onclick="setVal('returned', 5)">5</div>
                            <div class="chip" onclick="setVal('returned', 0)">Сброс</div>
                        </div>
                    </div>

                    <div style="display: grid; grid-template-columns: 1fr 1fr; gap: 10px;">
                        <div class="form-group">
                            <label>Тип клиента</label>
                            <select id="clientType">
                                <option value="Физ. лицо">Физ. лицо</option>
                                <option value="Юр. лицо">Юр. лицо</option>
                            </select>
                        </div>
                        <div class="form-group">
                            <label>Оплата</label>
                            <select id="paymentType">
                                <option value="Наличные">Наличные</option>
                                <option value="Click/Payme">Click/Payme</option>
                                <option value="Перечисление">Перечисление</option>
                            </select>
                        </div>
                    </div>

                    <div style="background: #f0fdf4; border-radius: 14px; padding: 12px; display: flex; justify-content: space-between; align-items: center; margin: 10px 0;">
                        <span style="font-weight: 700; color: #065f46; font-size: 13px;">Сумма к оплате:</span>
                        <span id="calcTotal" style="font-size: 18px; font-weight: 800; color: #059669;">0 сум</span>
                    </div>
                </div>

                <button type="submit" id="submitBtn" class="btn-main">✅ Подтвердить доставку</button>
            </form>
        </div>
    </div>

    <div class="bottom-nav">
        <div class="nav-item active" onclick="switchTab('orders')">
            <div class="nav-icon">📦</div>
            <div>Заказы</div>
        </div>
        <div class="nav-item" onclick="switchTab('profile')">
            <div class="nav-icon">👤</div>
            <div>Профиль</div>
        </div>
    </div>

    <script>
        const tg = window.Telegram?.WebApp;
        if (tg) tg.expand();

        const PRICE_PER_BOTTLE = 23000;
        let routePoints = [];
        let selectedClient = "";

        function switchTab(tab) {
            document.getElementById('contentOrders').classList.remove('active');
            document.getElementById('contentProfile').classList.remove('active');
            document.getElementById('tabOrders').classList.remove('active');
            document.getElementById('tabProfile').classList.remove('active');

            if (tab === 'orders') {
                document.getElementById('contentOrders').classList.add('active');
                document.getElementById('tabOrders').classList.add('active');
            } else {
                document.getElementById('contentProfile').classList.add('active');
                document.getElementById('tabProfile').classList.add('active');
            }
        }

        async function loadData() {
            const user = tg?.initDataUnsafe?.user;
            if (user) {
                document.getElementById('profileName').innerText = [user.first_name, user.last_name].filter(Boolean).join(" ");
                document.getElementById('profileId').innerText = "Telegram ID: " + user.id;
            }

            const userId = user ? user.id : 120958093;
            try {
                const res = await fetch(`/api/route?user_id=${userId}`);
                const data = await res.json();
                
                if (data.status === "ok" && data.points && data.points.length > 0) {
                    routePoints = data.points;
                    renderOrdersList();
                } else {
                    document.getElementById('ordersList').innerHTML = '<div class="empty-state">🎉 На сегодня все заказы выполнены!</div>';
                }
            } catch (e) {
                document.getElementById('ordersList').innerHTML = '<div class="empty-state">Ошибка загрузки маршрута</div>';
            }
        }

        function renderOrdersList() {
            const container = document.getElementById('ordersList');
            container.innerHTML = "";

            routePoints.forEach((point, index) => {
                const card = document.createElement('div');
                card.className = 'order-card';
                card.onclick = () => openModal(point);

                card.innerHTML = `
                    <div class="order-header-row">
                        <div class="order-id">Заказ #${173200 + index}</div>
                        <div class="status-badge">Активный</div>
                    </div>
                    <div class="client-name">${point}</div>
                    <div class="client-address">Точка маршрута №${index + 1}</div>
                    <div class="order-footer-row">
                        <div class="info-pill">💧 Вода 19л</div>
                        <div class="price-pill">23 000 сум / бут</div>
                    </div>
                `;
                container.appendChild(card);
            });
        }

        function openModal(clientName) {
            selectedClient = clientName;
            document.getElementById('modalClientTitle').innerText = clientName;
            document.getElementById('orderModal').style.display = 'flex';
            document.getElementById('delivered').value = '';
            document.getElementById('returned').value = '';
            document.getElementById('isReschedule').checked = false;
            toggleReschedule(false);
            updateCalc();
        }

        function closeModal() {
            document.getElementById('orderModal').style.display = 'none';
        }

        function toggleReschedule(isResch) {
            const fields = document.getElementById('deliveryFields');
            const block = document.getElementById('rescheduleBlock');
            const btn = document.getElementById('submitBtn');

            if (isResch) {
                fields.style.display = 'none';
                block.style.display = 'block';
                btn.innerText = "⚠️ Подтвердить перенос";
                btn.className = "btn-main danger";
            } else {
                fields.style.display = 'block';
                block.style.display = 'none';
                btn.innerText = "✅ Подтвердить доставку";
                btn.className = "btn-main";
            }
        }

        function setVal(id, val) {
            document.getElementById(id).value = val === 0 ? '' : val;
            updateCalc();
        }

        function updateCalc() {
            const count = parseInt(document.getElementById('delivered').value) || 0;
            const total = count * PRICE_PER_BOTTLE;
            document.getElementById('calcTotal').innerText = total.toLocaleString('ru-RU') + " сум";
        }

        document.getElementById('delivered').addEventListener('input', updateCalc);

        async function submitReport(e) {
            e.preventDefault();
            const user = tg?.initDataUnsafe?.user;
            const isResch = document.getElementById('isReschedule').checked;

            const payload = {
                user_id: user ? user.id : null,
                user_name: user ? [user.first_name, user.last_name].filter(Boolean).join(" ") : "Курьер",
                username: user ? (user.username || "нет") : "нет",
                client_id: selectedClient,
                is_reschedule: isResch,
                reschedule_date: isResch ? document.getElementById('rescheduleDate').value : null,
                delivered: isResch ? 0 : (parseInt(document.getElementById('delivered').value) || 0),
                returned: isResch ? 0 : (parseInt(document.getElementById('returned').value) || 0),
                client_type: isResch ? "-" : document.getElementById('clientType').value,
                payment_type: isResch ? "-" : document.getElementById('paymentType').value,
                total_price: isResch ? 0 : ((parseInt(document.getElementById('delivered').value) || 0) * PRICE_PER_BOTTLE)
            };

            try {
                const res = await fetch('/api/report', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify(payload)
                });
                if (res.ok) {
                    closeModal();
                    loadData();
                } else {
                    alert("Ошибка отправки отчета");
                }
            } catch (err) {
                alert("Ошибка соединения с сервером");
            }
        }

        document.addEventListener("DOMContentLoaded", loadData);
    </script>
</body>
</html>
"""

# --- ТЕЛЕГРАМ ХЕНДЛЕРЫ ---

@dp.message(CommandStart())
async def cmd_start(message: types.Message):
    user_id = message.from_user.id
    
    couriers = load_couriers()
    is_courier = user_id in couriers
    is_admin = user_id in ADMIN_IDS

    if not is_courier and not is_admin:
        await message.answer("❌ Извините, этот бот предназначен только для авторизованных курьеров и диспетчеров службы доставки воды.")
        return

    web_app_url = f"{WEB_SERVER_HOST}/"
    
    keyboard_buttons = [
        [KeyboardButton(text="📝 Открыть Mini App", web_app=WebAppInfo(url=web_app_url))],
        [KeyboardButton(text="💰 Моя зарплата")]
    ]
    if is_admin:
        keyboard_buttons.append([KeyboardButton(text="📊 Сводка за сегодня")])

    markup = ReplyKeyboardMarkup(keyboard=keyboard_buttons, resize_keyboard=True)
    await message.answer(
        "👋 Добро пожаловать в систему управления доставкой воды!\n\n"
        "Используйте кнопку ниже для открытия панели курьера.",
        reply_markup=markup
    )

@dp.message(F.text == "💰 Моя зарплата")
async def btn_my_salary(message: types.Message):
    await send_courier_personal_stats(message, message.from_user.id, days=1)

@dp.message(F.text == "📊 Сводка за сегодня")
async def btn_daily_summary(message: types.Message):
    if message.from_user.id not in ADMIN_IDS:
        await message.answer("❌ У вас нет прав администратора.")
        return
    await send_summary_report(message)

async def send_courier_personal_stats(target_message: types.Message, target_id: int, days: int = 1):
    try:
        now = datetime.now(UZ_TIMEZONE)
        today_date_str = now.strftime("%Y-%m-%d")
        reports = load_reports()

        phys_deliv, jur_deliv, total_returned, total_earnings, orders_count = 0, 0, 0, 0, 0

        for r in reports:
            if str(r.get("courier_id", "")) == str(target_id):
                r_date_str = str(r.get("date", ""))
                if not r_date_str.startswith(today_date_str): continue

                deliv = int(r.get("delivered", 0) or 0)
                ret = int(r.get("returned", 0) or 0)
                ctype = str(r.get("client_type", "Физ. лицо"))
                earn = int(r.get("earnings", 0) or 0)

                if "Юр" in ctype: jur_deliv += deliv
                else: phys_deliv += deliv

                total_returned += ret
                total_earnings += earn
                orders_count += 1

        if orders_count == 0:
            await target_message.answer("📭 У вас нет выполненных заказов за сегодня.")
            return

        total_deliv = phys_deliv + jur_deliv
        earn_fmt = f"{total_earnings:,}".replace(",", " ")

        text = (
            f"👤 ВАШ ЛИЧНЫЙ КАБИНЕТ\n"
            f"📅 Дата: {today_date_str}\n\n"
            f"📦 Выполнено заказов: {orders_count}\n"
            f"💧 Всего сдано (19л): {total_deliv} шт.\n"
            f"🔄 Собрано пустых: {total_returned} шт.\n\n"
            f"💵 Итого заработок: {earn_fmt} сум"
        )
        await target_message.answer(text)
    except Exception as e:
        logging.error(f"Ошибка: {e}")

async def send_summary_report(target_message: types.Message):
    try:
        today_date = datetime.now(UZ_TIMEZONE).strftime("%Y-%m-%d")
        reports = load_reports()
        if not reports:
            await target_message.answer("📭 База отчетов пуста.")
            return

        courier_stats = {}
        matched = 0

        for r in reports:
            if str(r.get("date", "")).startswith(today_date):
                matched += 1
                cid = str(r.get("courier_id", "неизвестно"))
                cname = str(r.get("courier_name", "Курьер"))
                client_id = str(r.get("client_id", "Не указан"))

                if cid not in courier_stats:
                    courier_stats[cid] = {"name": cname, "deliv": 0, "ret": 0, "rev": 0, "earn": 0, "orders": 0, "clients": []}

                deliv = int(r.get("delivered", 0) or 0)
                ret = int(r.get("returned", 0) or 0)
                rev = int(r.get("total_price", 0) or 0)
                earn = int(r.get("earnings", 0) or 0)

                courier_stats[cid]["deliv"] += deliv
                courier_stats[cid]["ret"] += ret
                courier_stats[cid]["rev"] += rev
                courier_stats[cid]["earn"] += earn
                courier_stats[cid]["orders"] += 1
                courier_stats[cid]["clients"].append(f"• {client_id} (сдал: {deliv}, пустых: {ret})")

        if matched == 0:
            await target_message.answer(f"📭 За сегодня отчетов нет.")
            return

        text = f"📊 СВОДНЫЙ ОТЧЕТ ЗА СЕГОДНЯ ({today_date}):\n\n"
        tot_deliv, tot_ret, tot_rev = 0, 0, 0

        for cid, s in courier_stats.items():
            tot_deliv += s["deliv"]
            tot_ret += s["ret"]
            tot_rev += s["rev"]
            
            rev_formatted = f"{s['rev']:,}".replace(",", " ")
            
            text += f"🚚 Курьер: {s['name']}\n"
            text += f"   ▫️ Выполнено заказов: {s['orders']}\n"
            text += f"   ▫️ Сдано воды: {s['deliv']} шт | Пустых: {s['ret']} шт\n"
            text += f"   ▫️ Выручка: {rev_formatted} сум\n"
            text += "   📍 Точки доставки:\n"
            for c_line in s["clients"]:
                text += f"      {c_line}\n"
            text += "\n"

        tot_rev_formatted = f"{tot_rev:,}".replace(",", " ")
        text += f"💧 Всего сдали воды: {tot_deliv} шт\n"
        text += f"🔄 Всего собрали пустых: {tot_ret} шт\n"
        text += f"💰 Общая выручка: {tot_rev_formatted} сум"
        
        await target_message.answer(text)
    except Exception as e:
        await target_message.answer(f"⚠️ Ошибка: {e}")

# --- WEB-СЕРВЕР ---

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
        return web.Response(text=str(e), status=500)

async def handle_get_route(request):
    try:
        user_id = request.query.get("user_id")
        if not user_id: return web.json_response({"status": "error", "message": "No user_id"}, status=400)
        today_date = datetime.now(UZ_TIMEZONE).strftime("%Y-%m-%d")
        if not supabase: return web.json_response({"status": "ok", "points": []})

        response = supabase.table("daily_routes").select("client_name").eq("date", today_date).eq("courier_id", int(user_id)).execute()
        points = [row["client_name"] for row in (response.data or [])]
        return web.json_response({"status": "ok", "points": points})
    except Exception as e:
        return web.json_response({"status": "error", "message": str(e)}, status=500)

async def handle_api_report(request):
    try:
        data = await request.json()
        sender_id = data.get("user_id")
        if sender_id is not None:
            try: sender_id = int(sender_id)
            except ValueError: pass

        couriers = load_couriers()
        client_id = str(data.get("client_id", "Не указан"))
        is_reschedule = bool(data.get("is_reschedule", False))
        raw_name = couriers.get(sender_id, str(data.get("user_name", "Курьер")))
        raw_username = str(data.get("username", "нет"))

        if is_reschedule:
            reschedule_date = str(data.get("reschedule_date", "Завтра"))
            save_rescheduled_order(sender_id, raw_name, client_id, reschedule_date)
            remove_point_from_route(sender_id, client_id)
            return web.json_response({"status": "ok"})

        delivered = int(data.get("delivered", 0))
        returned = int(data.get("returned", 0))
        client_type = str(data.get("client_type", "Физ. лицо"))
        payment_type = str(data.get("payment_type", "Наличные"))
        total_price = int(data.get("total_price", 0))
        
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
        remove_point_from_route(sender_id, client_id)

        # Отправка уведомления в Telegram-канал
        if CHANNEL_ID:
            try:
                chan_text = (
                    f"📦 Новый отчет о доставке!\n\n"
                    f"🚚 Курьер: {raw_name}\n"
                    f"📍 Клиент: {client_id}\n"
                    f"💧 Сдал (19л): {delivered} шт.\n"
                    f"🔄 Пустых забрал: {returned} шт.\n"
                    f"💳 Оплата: {payment_type} ({total_price:,} сум)".replace(",", " ")
                )
                await bot.send_message(CHANNEL_ID, chan_text)
            except Exception as ex:
                logging.error(f"Не удалось отправить уведомление в канал: {ex}")

        return web.json_response({"status": "ok"})
    except Exception as e:
        return web.json_response({"status": "error", "message": str(e)}, status=500)

async def main():
    if TOKEN:
        try:
            await bot.set_webhook(url=WEBHOOK_URL)
            logging.info(f"Вебхук установлен: {WEBHOOK_URL}")
        except Exception as e:
            logging.error(f"Ошибка вебхука: {e}")

    app = web.Application()
    app.router.add_get("/", handle_index)
    app.router.add_get("/health", handle_health)
    app.router.add_post(WEBHOOK_PATH, handle_webhook)
    app.router.add_get("/api/route", handle_get_route)
    app.router.add_post("/api/report", handle_api_report)

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", PORT)
    await site.start()
    await asyncio.Event().wait()

if __name__ == "__main__":
    asyncio.run(main())
