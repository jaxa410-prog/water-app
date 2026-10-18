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

# --- Функции работы с Supabase ---
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
    """Удаляет выполненную или перенесенную точку из сегодняшнего маршрута"""
    if not supabase or not courier_id: return
    try:
        today_date = datetime.now(UZ_TIMEZONE).strftime("%Y-%m-%d")
        supabase.table("daily_routes").delete().eq("date", today_date).eq("courier_id", int(courier_id)).eq("client_name", client_name).execute()
        logging.info(f"Точка '{client_name}' удалена из сегодняшнего маршрута курьера {courier_id}")
    except Exception as e:
        logging.error(f"Ошибка удаления точки из маршрута: {e}")

# ПРЕМИУМ HTML MINI APP С ПОДДЕРЖКОЙ ПЕРЕНОСА ЗАКАЗА
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
            --danger-color: #ef4444;
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
            box-shadow: 0 10px 25px -5px rgba(0, 0, 0, 0.05);
            border: 1px solid rgba(226, 232, 240, 0.8);
        }

        .header {
            display: flex;
            justify-content: space-between;
            align-items: center;
            margin-bottom: 16px;
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
            margin-bottom: 14px;
        }

        label {
            display: block;
            font-size: 12px;
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
            outline: none;
        }

        /* Блок выбора переноса */
        .reschedule-box {
            background: #fff1f2;
            border: 1.5px dashed #f43f5e;
            border-radius: 14px;
            padding: 12px 14px;
            margin-bottom: 16px;
            display: flex;
            flex-direction: column;
            gap: 8px;
        }

        .reschedule-toggle {
            display: flex;
            align-items: center;
            gap: 10px;
            cursor: pointer;
            font-weight: 700;
            font-size: 14px;
            color: #be123c;
        }

        .reschedule-toggle input[type="checkbox"] {
            width: 20px;
            height: 20px;
            accent-color: #f43f5e;
            cursor: pointer;
        }

        .quick-chips {
            display: flex;
            gap: 6px;
            margin-top: 6px;
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
            margin: 16px 0 14px 0;
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
        }

        .btn-submit.reschedule-btn {
            background: linear-gradient(135deg, #e11d48, #be123c);
            box-shadow: 0 6px 20px rgba(225, 29, 72, 0.3);
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
            <!-- Динамический выбор клиента -->
            <div class="form-group">
                <label id="t_clientId">Клиент / Точка маршрута</label>
                <select id="clientIdSelect" required onchange="checkCustomClient(this)">
                    <option value="" disabled selected>Загрузка маршрута...</option>
                </select>
            </div>

            <!-- Ручной ввод -->
            <div class="form-group" id="customClientGroup" style="display: none;">
                <label id="t_customClient">Введите название клиента / ID</label>
                <input type="text" id="customClientInput" placeholder="Например: ООО Навруз или 1045">
            </div>

            <!-- ЧЕКБОКС ПЕРЕНОСА ЗАКАЗА -->
            <div class="reschedule-box">
                <label class="reschedule-toggle">
                    <input type="checkbox" id="isReschedule" onchange="toggleRescheduleMode(this.checked)">
                    <span id="t_rescheduleLabel">📅 Перенести заказ на другой день</span>
                </label>
                <div id="rescheduleDateField" style="display: none; margin-top: 6px;">
                    <label style="color: #9f1239; font-size: 11px;" id="t_rescheduleDateLabel">Укажите когда привезти:</label>
                    <select id="rescheduleTargetDate">
                        <option value="Завтра">Завтра</option>
                        <option value="Послезавтра">Послезавтра</option>
                        <option value="Через 3 дня">Через 3 дня</option>
                        <option value="Уточнить по звонку">Уточнить по звонку</option>
                    </select>
                </div>
            </div>

            <!-- БЛОК ВВОДА БУТЫЛЕЙ (Скрывается при переносе) -->
            <div id="deliveryFieldsBlock">
                <div class="form-group">
                    <label id="t_delivered">Сдал полных (19л)</label>
                    <input type="number" id="delivered" placeholder="0" min="0">
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
                    <input type="number" id="returned" placeholder="0" min="0">
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
                clientId: "Клиент / Точка маршрута",
                customClient: "Введите название клиента / ID",
                rescheduleLabel: "📅 Перенести заказ на другой день",
                rescheduleDateLabel: "Укажите когда привезти:",
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
                submitReschedule: "⚠️ Перенести и убрать из списка",
                sending: "Отправка..."
            },
            uz: {
                title: "Kuryer hisoboti",
                clientId: "Mijoz / Marshrut nuqtasi",
                customClient: "Mijoz nomini kiriting / ID",
                rescheduleLabel: "📅 Boshqa kunga ko'chirish",
                rescheduleDateLabel: "Qachon olib borilsin:",
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
                submitReschedule: "⚠️ Ko'chirish va ro'yxatdan o'chirish",
                sending: "Yuborilmoqda..."
            }
        };

        const langSelect = document.getElementById('langSelect');
        const clientIdSelect = document.getElementById('clientIdSelect');
        const customClientGroup = document.getElementById('customClientGroup');
        const customClientInput = document.getElementById('customClientInput');
        const isRescheduleCheckbox = document.getElementById('isReschedule');
        const rescheduleDateField = document.getElementById('rescheduleDateField');
        const deliveryFieldsBlock = document.getElementById('deliveryFieldsBlock');
        const deliveredInput = document.getElementById('delivered');
        const returnedInput = document.getElementById('returned');
        const clientTypeSelect = document.getElementById('clientType');
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
            document.getElementById('t_customClient').innerText = t.customClient;
            document.getElementById('t_rescheduleLabel').innerText = t.rescheduleLabel;
            document.getElementById('t_rescheduleDateLabel').innerText = t.rescheduleDateLabel;
            document.getElementById('t_delivered').innerText = t.delivered;
            document.getElementById('t_returned').innerText = t.returned;
            document.getElementById('t_clientType').innerText = t.clientType;
            document.getElementById('opt_jur').innerText = t.jur;
            document.getElementById('opt_phys').innerText = t.phys;
            document.getElementById('t_paymentType').innerText = t.paymentType;
            document.getElementById('opt_cash').innerText = t.cash;
            document.getElementById('opt_bank').innerText = t.bank;
            document.getElementById('t_totalLabel').innerText = t.totalLabel;
            
            updateSubmitButtonText();
        }

        function toggleRescheduleMode(isReschedule) {
            if (isReschedule) {
                deliveryFieldsBlock.style.display = 'none';
                rescheduleDateField.style.display = 'block';
                submitBtn.classList.add('reschedule-btn');
            } else {
                deliveryFieldsBlock.style.display = 'block';
                rescheduleDateField.style.display = 'none';
                submitBtn.classList.remove('reschedule-btn');
            }
            updateSubmitButtonText();
            validateForm();
        }

        function updateSubmitButtonText() {
            const t = translations[currentLang];
            if (isRescheduleCheckbox.checked) {
                submitBtn.innerText = t.submitReschedule;
            } else {
                submitBtn.innerText = t.submit;
            }
        }

        async function loadCourierRoute() {
            const tgUser = tg?.initDataUnsafe?.user;
            if (!tgUser || !tgUser.id) {
                clientIdSelect.innerHTML = '<option value="CUSTOM">⚠️ Откройте через Telegram</option>';
                showCustomInput(true);
                return;
            }

            try {
                const response = await fetch(`/api/route?user_id=${tgUser.id}`);
                const data = await response.json();
                
                if (data.status === "ok" && data.points && data.points.length > 0) {
                    clientIdSelect.innerHTML = '<option value="" disabled selected>-- Выберите точку из маршрута --</option>';
                    
                    data.points.forEach(point => {
                        const option = document.createElement('option');
                        option.value = point;
                        option.textContent = point;
                        clientIdSelect.appendChild(option);
                    });

                    const customOption = document.createElement('option');
                    customOption.value = "CUSTOM_INPUT";
                    customOption.textContent = "➕ Другой клиент (ввести вручную)";
                    clientIdSelect.appendChild(customOption);
                } else {
                    clientIdSelect.innerHTML = '<option value="CUSTOM">Нет маршрута на сегодня (введите вручную)</option>';
                    showCustomInput(true);
                }
            } catch (err) {
                clientIdSelect.innerHTML = '<option value="CUSTOM">Ошибка связи (введите вручную)</option>';
                showCustomInput(true);
            }
        }

        function checkCustomClient(selectObj) {
            if (selectObj.value === "CUSTOM_INPUT" || selectObj.value === "CUSTOM") {
                showCustomInput(true);
            } else {
                showCustomInput(false);
            }
            validateForm();
        }

        function showCustomInput(show) {
            customClientGroup.style.display = show ? 'block' : 'none';
        }

        customClientInput.addEventListener('input', () => {
            const val = customClientInput.value.trim().toLowerCase();
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
            let clientVal = clientIdSelect.value;
            let hasClient = false;
            if (clientVal === "CUSTOM_INPUT" || clientVal === "CUSTOM") {
                hasClient = customClientInput.value.trim() !== "";
            } else {
                hasClient = clientVal !== "";
            }

            if (isRescheduleCheckbox.checked) {
                submitBtn.disabled = !hasClient;
            } else {
                const isValid = hasClient && 
                                deliveredInput.value.trim() !== "" && 
                                returnedInput.value.trim() !== "";
                submitBtn.disabled = !isValid;
            }
        }

        document.getElementById('reportForm').addEventListener('submit', async function(e) {
            e.preventDefault();
            submitBtn.disabled = true;
            submitBtn.innerText = translations[currentLang].sending;

            const tgUser = window.Telegram?.WebApp?.initDataUnsafe?.user;
            let finalClient = clientIdSelect.value;
            if (finalClient === "CUSTOM_INPUT" || finalClient === "CUSTOM" || !finalClient) {
                finalClient = customClientInput.value.trim();
            }

            const isReschedule = isRescheduleCheckbox.checked;

            const data = {
                user_id: tgUser ? tgUser.id : null,
                user_name: tgUser ? [tgUser.first_name, tgUser.last_name].filter(Boolean).join(" ") : "Курьер",
                username: tgUser ? (tgUser.username || "нет_юзернейма") : "нет_юзернейма",
                client_id: finalClient,
                is_reschedule: isReschedule,
                reschedule_date: isReschedule ? document.getElementById('rescheduleTargetDate').value : null,
                delivered: isReschedule ? 0 : (parseInt(deliveredInput.value) || 0),
                returned: isReschedule ? 0 : (parseInt(returnedInput.value) || 0),
                client_type: isReschedule ? "-" : clientTypeSelect.value,
                payment_type: isReschedule ? "-" : document.getElementById('paymentType').value,
                total_price: isReschedule ? 0 : ((parseInt(deliveredInput.value) || 0) * PRICE_PER_BOTTLE)
            };

            try {
                const response = await fetch('/api/report', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(data)
                });
                const resData = await response.json();
                if (response.ok && resData.status === "ok") {
                    tg ? tg.close() : alert("Успешно!");
                } else {
                    alert("Ошибка: " + (resData.message || "Попробуйте снова"));
                    submitBtn.disabled = false;
                    updateSubmitButtonText();
                }
            } catch (err) {
                alert("Ошибка соединения с сервером.");
                submitBtn.disabled = false;
                updateSubmitButtonText();
            }
        });

        document.addEventListener("DOMContentLoaded", loadCourierRoute);
    </script>
</body>
</html>
"""

# --- ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ БОТА ---

async def send_courier_personal_stats(target_message: types.Message, target_id: int, days: int = 1):
    try:
        now = datetime.now(UZ_TIMEZONE)
        today_date_str = now.strftime("%Y-%m-%d")
        cutoff = now - timedelta(days=days)
        reports = load_reports()

        phys_deliv, jur_deliv, total_returned, total_earnings, orders_count = 0, 0, 0, 0, 0

        for r in reports:
            if str(r.get("courier_id", "")) == str(target_id):
                r_date_str = str(r.get("date", ""))
                try:
                    r_date = datetime.strptime(r_date_str, "%Y-%m-%d %H:%M:%S").replace(tzinfo=UZ_TIMEZONE)
                except Exception: continue

                if days == 1:
                    if not r_date_str.startswith(today_date_str): continue
                else:
                    if r_date < cutoff: continue

                deliv = int(r.get("delivered", 0) or 0)
                ret = int(r.get("returned", 0) or 0)
                ctype = str(r.get("client_type", "Физ. лицо"))
                earn = int(r.get("earnings", 0) or 0)

                if "Юр" in ctype: jur_deliv += deliv
                else: phys_deliv += deliv

                total_returned += ret
                total_earnings += earn
                orders_count += 1

        period_title = f"за сегодня ({today_date_str})" if days == 1 else f"за последние {days} дней"

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

                if "Юр" in ctype: courier_stats[cid]["jur_delivered"] += deliv
                else: courier_stats[cid]["phys_delivered"] += deliv

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
        logging.error(f"Ошибка сводки: {e}")
        await target_message.answer(f"⚠️ Ошибка: {e}")

# --- ТЕЛЕГРАМ ХЕНДЛЕРЫ ---

@dp.message(CommandStart())
async def cmd_start(message: types.Message):
    user_id = message.from_user.id
    web_app_url = f"{WEB_SERVER_HOST}/"
    
    keyboard_buttons = [
        [KeyboardButton(text="📝 Открыть форму отчета", web_app=WebAppInfo(url=web_app_url))],
        [KeyboardButton(text="💰 Моя зарплата")]
    ]
    if user_id in ADMIN_IDS:
        keyboard_buttons.append([KeyboardButton(text="📊 Сводка за сегодня")])

    markup = ReplyKeyboardMarkup(keyboard=keyboard_buttons, resize_keyboard=True)
    await message.answer(
        "👋 Добро пожаловать в систему управления доставкой воды!\n\n"
        "Используйте кнопки ниже для отправки отчетов и проверки баланса.",
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

@dp.message()
async def fallback_handler(message: types.Message):
    await message.answer("ℹ️ Пожалуйста, используйте кнопки меню ниже для работы с системой.")

# --- WEB-СЕРВЕР AIOHTTP И МАРШРУТИЗАЦИЯ ---

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

async def handle_get_route(request):
    try:
        user_id = request.query.get("user_id")
        if not user_id:
            return web.json_response({"status": "error", "message": "No user_id"}, status=400)

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
        if couriers and sender_id and (sender_id not in couriers):
            if not (ADMIN_IDS and sender_id in ADMIN_IDS):
                return web.json_response({"status": "error", "message": "У вас нет доступа"}, status=403)

        client_id = str(data.get("client_id", "Не указан"))
        is_reschedule = bool(data.get("is_reschedule", False))
        raw_name = couriers.get(sender_id, str(data.get("user_name", "Курьер")))
        raw_username = str(data.get("username", "нет_юзернейма"))

        # 1. Если это ПЕРЕНОС ЗАКАЗА
        if is_reschedule:
            reschedule_date = str(data.get("reschedule_date", "Завтра"))
            
            # Автоматически удаляем эту точку из маршрута на сегодня
            remove_point_from_route(sender_id, client_id)

            report_text = (
                f"📅 **ПЕРЕНОС ЗАКАЗА**\n\n"
                f"🚚 Курьер: {raw_name} (@{raw_username})\n"
                f"🆔 Клиент / Точка: **{client_id}**\n"
                f"🗓 Когда привезти: **{reschedule_date}**\n"
                f"❌ Точка удалена из текущего маршрута."
            )

            if sender_id:
                try:
                    await bot.send_message(chat_id=sender_id, text=f"✅ Заказ перенесен!\n\n{report_text}", parse_mode="Markdown")
                except Exception as e: logging.error(f"Ошибка отправки курьеру: {e}")

            if CHANNEL_ID:
                try:
                    target_chat = int(CHANNEL_ID) if CHANNEL_ID.lstrip('-').isdigit() else CHANNEL_ID
                    await bot.send_message(chat_id=target_chat, text=report_text, parse_mode="Markdown")
                except Exception as channel_err: logging.error(f"Ошибка отправки в канал: {channel_err}")

            return web.json_response({"status": "ok"})

        # 2. Если это ОБЫЧНЫЙ ОТЧЕТ О ДОСТАВКЕ
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

        # Автоматически удаляем выполненную точку из списка маршрута на сегодня
        remove_point_from_route(sender_id, client_id)

        formatted_price = f"{total_price:,}".replace(",", " ")
        formatted_earnings = f"{courier_earnings:,}".replace(",", " ")
        
        report_text = (
            f"📋 ОТЧЕТ О ДОСТАВКЕ (19л)\n\n"
            f"👤 Курьер: {raw_name} (@{raw_username})\n"
            f"🆔 Клиент / Точка: {client_id}\n"
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
            except Exception as e: logging.error(f"Ошибка отправки курьеру: {e}")

        if CHANNEL_ID:
            try:
                target_chat = int(CHANNEL_ID) if CHANNEL_ID.lstrip('-').isdigit() else CHANNEL_ID
                await bot.send_message(chat_id=target_chat, text=report_text)
            except Exception as channel_err: logging.error(f"Ошибка отправки в канал: {channel_err}")

        return web.json_response({"status": "ok"})
    except Exception as e:
        logging.error(f"Ошибка сохранения отчета: {e}")
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
