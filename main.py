import asyncio
import hashlib
import hmac
import json
import logging
import os
import re
import time
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qsl

from aiogram import Bot, Dispatcher, F, types
from aiogram.filters import CommandStart
from aiogram.types import KeyboardButton, ReplyKeyboardMarkup, Update, WebAppInfo
from aiohttp import web
from supabase import Client, create_client

logging.basicConfig(level=logging.INFO)

TOKEN = os.getenv("TOKEN")
if not TOKEN:
    raise SystemExit("Не задана переменная окружения TOKEN")
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

# Секрет вебхука не должен совпадать с токеном бота: путь попадает в логи сервера.
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET") or hashlib.sha256(("webhook:" + TOKEN).encode()).hexdigest()[:48]
WEBHOOK_PATH = f"/webhook/{WEBHOOK_SECRET}"
WEBHOOK_URL = f"{WEB_SERVER_HOST}{WEBHOOK_PATH}"

bot = Bot(token=TOKEN)
dp = Dispatcher()

ENV_ADMINS = os.getenv("ADMIN_IDS", "")
ADMIN_IDS = [int(i.strip()) for i in ENV_ADMINS.split(",") if i.strip().isdigit()]

BONUS_PHYSICAL = 3500
BONUS_JURIDICAL = 2000
PRICE_PER_BOTTLE = 23000
UZ_TIMEZONE = timezone(timedelta(hours=5))
PAGE_SIZE = 1000                      # Supabase отдаёт не больше 1000 строк за запрос
INIT_DATA_MAX_AGE = 24 * 3600         # сколько живёт подпись Mini App, секунд
MAX_BOTTLES = 1000

CLIENT_TYPES = {"Физ. лицо", "Юр. лицо"}
PAYMENT_TYPES = {"Наличные", "Click/Payme", "Перечисление"}
RESCHEDULE_CHOICES = {"Завтра", "Послезавтра", "Через 3 дня", "Уточнить по звонку"}


# ==================== ПРОВЕРКА ПОДПИСИ MINI APP ====================
def verify_init_data(init_data, token=None, max_age=INIT_DATA_MAX_AGE, now=None):
    """Проверяет подпись Telegram Mini App. Возвращает словарь пользователя или None.

    id курьера нельзя брать из тела запроса: его легко подделать. Подписанный
    initData подделать нельзя, не зная токена бота.
    """
    if not init_data:
        return None
    try:
        pairs = dict(parse_qsl(init_data, keep_blank_values=True))
    except ValueError:
        return None
    received_hash = pairs.pop("hash", None)
    if not received_hash:
        return None

    check_string = "\n".join(f"{k}={v}" for k, v in sorted(pairs.items()))
    secret_key = hmac.new(b"WebAppData", (token or TOKEN).encode(), hashlib.sha256).digest()
    expected = hmac.new(secret_key, check_string.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, received_hash):
        return None

    try:
        auth_date = int(pairs.get("auth_date", "0"))
        user = json.loads(pairs.get("user", ""))
    except (ValueError, TypeError):
        return None
    if (now or time.time()) - auth_date > max_age:
        return None
    if not isinstance(user, dict) or "id" not in user:
        return None
    return user


def get_auth_user(request):
    header = request.headers.get("Authorization", "")
    if not header.startswith("tma "):
        return None
    return verify_init_data(header[4:].strip())


# ==================== ИМЕНА ТОЧЕК ====================
NUM_HEAD_RE = re.compile(r"^\s*\d+[.)]\s*")
QTY_TAIL_RE = re.compile(r"\s+\d+\s*[кКkK]\s*$")


def clean_point_name(text):
    text = NUM_HEAD_RE.sub("", str(text or ""))
    text = QTY_TAIL_RE.sub("", text)
    return " ".join(text.split())


def location_key(text):
    return clean_point_name(text).casefold()


def normalize_phone(text):
    """+998901234567 или None. Телефон уходит в ссылку tel:, поэтому отдаём только проверенный вид."""
    raw = str(text or "").strip()
    if re.search(r"[^\d\s+\-()./]", raw):                # буквы и прочий мусор: не номер
        return None
    digits = re.sub(r"\D", "", raw)
    if digits.startswith("998") and len(digits) != 12:   # узбекский номер ровно из 12 цифр
        return None
    if len(digits) == 9:
        digits = "998" + digits
    elif len(digits) == 10 and digits.startswith("8"):
        digits = "998" + digits[1:]
    if len(digits) == 12 and digits.startswith("998"):
        return "+" + digits
    if raw.startswith("+") and 10 <= len(digits) <= 15:
        return "+" + digits
    return None


def today_str():
    return datetime.now(UZ_TIMEZONE).strftime("%Y-%m-%d")


# ==================== БАЗА ДАННЫХ (синхронные функции, вызываются через to_thread) ====================
def fetch_all(table, columns="*", apply=None, order_by="id"):
    """Читает таблицу страницами по 1000 строк, иначе данные после 1000-й строки теряются."""
    rows = []
    start = 0
    ordered = bool(order_by)
    while True:
        query = supabase.table(table).select(columns)
        if apply:
            query = apply(query)
        if ordered:
            query = query.order(order_by)
        try:
            batch = query.range(start, start + PAGE_SIZE - 1).execute().data or []
        except Exception:
            if ordered and start == 0:
                ordered = False
                continue
            raise
        rows.extend(batch)
        if len(batch) < PAGE_SIZE:
            return rows
        start += PAGE_SIZE


def load_couriers():
    if not supabase:
        return {}
    try:
        couriers_dict = {}
        for row in fetch_all("couriers", "user_id, name", order_by=None):
            uid = row.get("user_id")
            if uid is not None:
                couriers_dict[int(uid)] = str(row.get("name") or "Курьер")
        return couriers_dict
    except Exception:
        logging.exception("Ошибка загрузки курьеров из Supabase")
        return {}


def load_reports_for_day(day, courier_id=None):
    """Отчёты за один день. Фильтр на стороне базы, а не скачивание всей таблицы."""
    next_day = (datetime.strptime(day, "%Y-%m-%d") + timedelta(days=1)).strftime("%Y-%m-%d")

    def apply(query):
        query = query.gte("date", day).lt("date", next_day)
        if courier_id is not None:
            query = query.eq("courier_id", int(courier_id))
        return query

    return fetch_all("reports", apply=apply)


def load_route(courier_id, day):
    return fetch_all(
        "daily_routes", "id, client_name",
        apply=lambda q: q.eq("date", day).eq("courier_id", int(courier_id)),
    )


def get_route_point(point_id, courier_id):
    resp = (
        supabase.table("daily_routes").select("id, client_name")
        .eq("id", int(point_id)).eq("courier_id", int(courier_id)).eq("date", today_str())
        .limit(1).execute()
    )
    rows = resp.data or []
    return rows[0] if rows else None


def delete_route_point(point_id):
    try:
        supabase.table("daily_routes").delete().eq("id", int(point_id)).execute()
        return True
    except Exception:
        logging.exception("Ошибка удаления точки из маршрута")
        return False


def load_locations():
    """{ключ_клиента: (широта, долгота, адрес)}. Без таблицы client_locations просто пусто."""
    if not supabase:
        return {}
    try:
        result = {}
        for r in fetch_all("client_locations", order_by="client_key"):
            try:
                result[r["client_key"]] = (float(r["latitude"]), float(r["longitude"]), r.get("address") or "")
            except (KeyError, TypeError, ValueError):
                continue
        return result
    except Exception:
        logging.warning("Геолокации недоступны (нет таблицы client_locations?)")
        return {}


def load_phones():
    """{ключ_клиента: телефон}. Без таблицы client_phones кнопки «Позвонить» просто не показываются."""
    if not supabase:
        return {}
    try:
        result = {}
        for r in fetch_all("client_phones", order_by="client_key"):
            phone = normalize_phone(r.get("phone"))
            if r.get("client_key") and phone:
                result[r["client_key"]] = phone
        return result
    except Exception:
        logging.warning("Телефоны недоступны (нет таблицы client_phones?)")
        return {}


def save_report_to_db(report_data):
    """True только если запись реально сохранилась."""
    if not supabase:
        return False
    try:
        supabase.table("reports").insert(report_data).execute()
        return True
    except Exception:
        logging.exception("Ошибка записи отчета в Supabase")
        return False


def save_rescheduled_order(courier_id, courier_name, client_name, target_date):
    if not supabase:
        return False
    try:
        supabase.table("rescheduled_orders").insert({
            "courier_id": courier_id,
            "courier_name": courier_name,
            "client_name": client_name,
            "target_date": target_date,
            "status": "pending",
        }).execute()
        return True
    except Exception:
        logging.exception("Ошибка сохранения переноса заказа в Supabase")
        return False


# ==================== MINI APP ====================
HTML_CONTENT = r"""
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
        * { box-sizing: border-box; -webkit-tap-highlight-color: transparent; font-family: 'Plus Jakarta Sans', sans-serif; }
        body { background-color: var(--bg-color); color: var(--text-main); margin: 0; padding: 0; padding-bottom: 90px; }
        .top-nav-container { padding: 16px 16px 8px 16px; background: var(--card-bg); position: sticky; top: 0; z-index: 10; box-shadow: 0 2px 10px rgba(0,0,0,0.03); }
        .tabs-header { display: flex; background: var(--bg-color); border-radius: 14px; padding: 4px; }
        .tab-btn { flex: 1; padding: 10px; text-align: center; font-size: 14px; font-weight: 700; color: var(--text-secondary); border-radius: 10px; cursor: pointer; transition: all 0.2s ease; }
        .tab-btn.active { background: var(--card-bg); color: var(--text-main); box-shadow: 0 2px 8px rgba(0,0,0,0.06); }
        .tab-content { display: none; padding: 16px; }
        .tab-content.active { display: block; }
        .order-card { background: var(--card-bg); border-radius: 20px; padding: 18px; margin-bottom: 14px; box-shadow: 0 4px 15px rgba(0,0,0,0.03); border: 1px solid var(--border-color); cursor: pointer; transition: transform 0.1s ease; }
        .order-card:active { transform: scale(0.98); }
        .order-header-row { display: flex; justify-content: space-between; align-items: center; margin-bottom: 10px; }
        .order-id { font-size: 15px; font-weight: 700; color: var(--text-main); }
        .status-badge { background: #d1fae5; color: #065f46; padding: 5px 12px; border-radius: 20px; font-size: 11px; font-weight: 700; }
        .client-name { font-size: 17px; font-weight: 800; color: var(--text-main); margin-bottom: 4px; word-break: break-word; }
        .client-address { font-size: 13px; color: var(--text-secondary); font-weight: 500; line-height: 1.4; margin-bottom: 12px; }
        .map-row { display: flex; gap: 8px; margin-bottom: 12px; }
        .map-btn { flex: 1; text-align: center; padding: 9px 0; border-radius: 12px; background: #e0f2fe; color: #0369a1; font-size: 13px; font-weight: 700; }
        .order-footer-row { display: flex; justify-content: space-between; align-items: center; border-top: 1px solid var(--border-color); padding-top: 12px; }
        .info-pill { font-size: 13px; font-weight: 600; color: var(--text-secondary); display: flex; align-items: center; gap: 5px; }
        .price-pill { font-size: 15px; font-weight: 800; color: #059669; background: #ecfdf5; padding: 6px 12px; border-radius: 12px; }
        .modal-overlay { position: fixed; top: 0; left: 0; right: 0; bottom: 0; background: rgba(15, 23, 42, 0.6); backdrop-filter: blur(4px); z-index: 100; display: none; justify-content: center; align-items: flex-end; }
        .modal-card { background: var(--card-bg); width: 100%; max-width: 500px; border-top-left-radius: 28px; border-top-right-radius: 28px; padding: 24px; max-height: 90vh; overflow-y: auto; animation: slideUp 0.3s cubic-bezier(0.16, 1, 0.3, 1); }
        @keyframes slideUp { from { transform: translateY(100%); } to { transform: translateY(0); } }
        .modal-title { font-size: 18px; font-weight: 800; margin-bottom: 16px; }
        .form-group { margin-bottom: 14px; }
        label { display: block; font-size: 11px; font-weight: 700; color: var(--text-secondary); text-transform: uppercase; margin-bottom: 6px; letter-spacing: 0.5px; }
        input, select { width: 100%; padding: 12px 14px; border-radius: 14px; border: 1.5px solid var(--border-color); background: var(--bg-color); color: var(--text-main); font-size: 15px; font-weight: 600; outline: none; }
        .quick-chips { display: flex; gap: 6px; margin-top: 6px; }
        .chip { flex: 1; padding: 8px 0; border-radius: 10px; border: 1px solid var(--border-color); background: var(--card-bg); text-align: center; font-weight: 700; font-size: 13px; cursor: pointer; }
        .reschedule-box { background: #fff1f2; border: 1.5px dashed #f43f5e; border-radius: 14px; padding: 12px; margin: 14px 0; }
        .btn-main { width: 100%; padding: 16px; border: none; border-radius: 16px; background: var(--primary-gradient); color: white; font-size: 16px; font-weight: 800; cursor: pointer; box-shadow: 0 4px 15px rgba(16, 185, 129, 0.3); margin-top: 10px; }
        .btn-main:disabled { opacity: 0.6; }
        .btn-main.danger { background: linear-gradient(135deg, #f43f5e, #be123c); box-shadow: 0 4px 15px rgba(244, 63, 94, 0.3); }
        .bottom-nav { position: fixed; bottom: 0; left: 0; right: 0; background: var(--card-bg); display: flex; justify-content: space-around; padding: 12px 0; border-top: 1px solid var(--border-color); z-index: 50; box-shadow: 0 -4px 20px rgba(0,0,0,0.03); }
        .nav-item { text-align: center; cursor: pointer; color: var(--text-secondary); font-size: 11px; font-weight: 700; flex: 1; }
        .nav-item.active { color: var(--accent-blue); }
        .nav-icon { font-size: 20px; margin-bottom: 2px; }
        .empty-state { text-align: center; padding: 50px 20px; color: var(--text-secondary); font-weight: 600; }
        a { text-decoration: none; }
        .call-btn { display: block; text-align: center; padding: 12px 0; border-radius: 12px; background: #dcfce7; color: #166534; font-size: 14px; font-weight: 800; }
        .call-btn-lg { display: block; text-align: center; padding: 14px 0; border-radius: 14px; background: #0284c7; color: #ffffff; font-size: 15px; font-weight: 800; margin-bottom: 14px; }
        .pay-chips { display: grid; grid-template-columns: 1fr; gap: 8px; border-radius: 16px; }
        .pay-chip { padding: 13px; border-radius: 14px; border: 1.5px solid var(--border-color); background: var(--card-bg); text-align: center; font-weight: 700; font-size: 14px; cursor: pointer; }
        .pay-chip.selected { background: #d1fae5; border-color: #10b981; color: #065f46; }
        .pay-chips.error .pay-chip { border-color: var(--danger); background: #fef2f2; }
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
        <div class="order-card" style="cursor: default;">
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

            <a id="modalCall" class="call-btn-lg" style="display: none;"></a>

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
                        <input type="number" id="delivered" placeholder="0" min="0" step="1" inputmode="numeric">
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
                        <input type="number" id="returned" placeholder="0" min="0" step="1" inputmode="numeric">
                        <div class="quick-chips">
                            <div class="chip" onclick="setVal('returned', 1)">1</div>
                            <div class="chip" onclick="setVal('returned', 2)">2</div>
                            <div class="chip" onclick="setVal('returned', 3)">3</div>
                            <div class="chip" onclick="setVal('returned', 5)">5</div>
                            <div class="chip" onclick="setVal('returned', 0)">Сброс</div>
                        </div>
                    </div>

                    <div class="form-group">
                        <label>Тип клиента</label>
                        <select id="clientType">
                            <option value="Физ. лицо">Физ. лицо</option>
                            <option value="Юр. лицо">Юр. лицо</option>
                        </select>
                    </div>

                    <div class="form-group">
                        <label>Способ оплаты (обязательно)</label>
                        <div class="pay-chips" id="paymentChips">
                            <div class="pay-chip" data-pay="Наличные" onclick="selectPayment(this)">💵 Наличные</div>
                            <div class="pay-chip" data-pay="Click/Payme" onclick="selectPayment(this)">💳 Click / Payme</div>
                            <div class="pay-chip" data-pay="Перечисление" onclick="selectPayment(this)">🏛 Перечисление</div>
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
        if (tg) { tg.ready(); tg.expand(); }

        // Подписанные данные Telegram: по ним сервер узнаёт, кто курьер. id из тела запроса не принимается.
        const initData = tg?.initData || "";

        let pricePerBottle = 23000;
        let routePoints = [];
        let selected = null;
        let sending = false;
        let selectedPayment = null;

        function isValidPhone(p) {
            return /^\+\d{9,15}$/.test(p || "");
        }

        function el(tag, className, text) {
            const node = document.createElement(tag);
            if (className) node.className = className;
            if (text !== undefined) node.textContent = text;   // textContent: названия клиентов не исполняются как HTML
            return node;
        }

        function notify(message) {
            if (tg?.showAlert) tg.showAlert(message); else alert(message);
        }

        function askConfirm(message) {
            return new Promise(resolve => {
                if (tg?.showConfirm) tg.showConfirm(message, resolve); else resolve(confirm(message));
            });
        }

        function openLink(url) {
            if (tg?.openLink) tg.openLink(url); else window.open(url, '_blank');
        }

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

        function showMessage(text) {
            const box = el('div', 'empty-state', text);
            const list = document.getElementById('ordersList');
            list.innerHTML = "";
            list.appendChild(box);
        }

        async function loadData() {
            const user = tg?.initDataUnsafe?.user;
            if (user) {
                document.getElementById('profileName').innerText = [user.first_name, user.last_name].filter(Boolean).join(" ");
                document.getElementById('profileId').innerText = "Telegram ID: " + user.id;
            }
            if (!initData) {
                showMessage("⚠️ Откройте приложение через Telegram");
                return;
            }
            try {
                const res = await fetch('/api/route', { headers: { 'Authorization': 'tma ' + initData } });
                const data = await res.json().catch(() => ({}));
                if (res.status === 401) { showMessage("⚠️ Сессия устарела. Закройте и откройте приложение заново"); return; }
                if (res.status === 403) { showMessage("⛔ Вы не зарегистрированы как курьер"); return; }
                if (!res.ok || data.status !== "ok") { showMessage("Ошибка загрузки маршрута"); return; }

                pricePerBottle = data.price || pricePerBottle;
                routePoints = data.points || [];
                if (routePoints.length > 0) {
                    renderOrdersList();
                } else {
                    showMessage("🎉 На сегодня все заказы выполнены!");
                }
            } catch (e) {
                showMessage("Ошибка загрузки маршрута");
            }
        }

        function mapButton(label, url) {
            const btn = el('div', 'map-btn', label);
            btn.onclick = (event) => { event.stopPropagation(); openLink(url); };
            return btn;
        }

        function renderOrdersList() {
            const container = document.getElementById('ordersList');
            container.innerHTML = "";

            routePoints.forEach((point, index) => {
                const card = el('div', 'order-card');
                card.onclick = () => openModal(point);

                const header = el('div', 'order-header-row');
                header.appendChild(el('div', 'order-id', 'Точка №' + (index + 1)));
                header.appendChild(el('div', 'status-badge', 'Активный'));
                card.appendChild(header);

                card.appendChild(el('div', 'client-name', point.name));
                card.appendChild(el('div', 'client-address', point.address || ''));

                if (isValidPhone(point.phone)) {
                    const call = el('a', 'call-btn', '📞 Позвонить: ' + point.phone);
                    call.href = 'tel:' + point.phone;
                    call.style.marginBottom = '10px';
                    call.onclick = (event) => event.stopPropagation();
                    card.appendChild(call);
                }

                if (point.lat != null && point.lon != null) {
                    const row = el('div', 'map-row');
                    row.appendChild(mapButton('🗺 Google Maps', `https://www.google.com/maps/dir/?api=1&destination=${point.lat},${point.lon}`));
                    row.appendChild(mapButton('🧭 Яндекс Карты', `https://yandex.com/maps/?rtext=~${point.lat},${point.lon}&rtt=auto`));
                    card.appendChild(row);
                }

                const footer = el('div', 'order-footer-row');
                footer.appendChild(el('div', 'info-pill', '💧 Вода 19л'));
                footer.appendChild(el('div', 'price-pill', pricePerBottle.toLocaleString('ru-RU') + ' сум / бут'));
                card.appendChild(footer);

                container.appendChild(card);
            });
        }

        function openModal(point) {
            selected = point;
            document.getElementById('modalClientTitle').innerText = point.name;
            document.getElementById('orderModal').style.display = 'flex';
            document.getElementById('delivered').value = '';
            document.getElementById('returned').value = '';
            document.getElementById('isReschedule').checked = false;
            toggleReschedule(false);
            resetPayment();

            const modalCall = document.getElementById('modalCall');
            if (isValidPhone(point.phone)) {
                modalCall.href = 'tel:' + point.phone;
                modalCall.textContent = '📞 Позвонить клиенту: ' + point.phone;
                modalCall.style.display = 'block';
            } else {
                modalCall.style.display = 'none';
            }
            updateCalc();
        }

        function resetPayment() {
            selectedPayment = null;
            document.querySelectorAll('.pay-chip').forEach(chip => chip.classList.remove('selected'));
            document.getElementById('paymentChips').classList.remove('error');
        }

        function selectPayment(node) {
            selectedPayment = node.dataset.pay;
            document.querySelectorAll('.pay-chip').forEach(chip => chip.classList.remove('selected'));
            node.classList.add('selected');
            document.getElementById('paymentChips').classList.remove('error');
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

        // Пустое поле = 0. Возвращает null, если введено не целое число от 0.
        function readCount(id) {
            const raw = document.getElementById(id).value.trim();
            if (raw === "") return 0;
            const n = Number(raw);
            return Number.isInteger(n) && n >= 0 && n <= 1000 ? n : null;
        }

        function updateCalc() {
            const count = readCount('delivered') || 0;
            document.getElementById('calcTotal').innerText = (count * pricePerBottle).toLocaleString('ru-RU') + " сум";
        }

        document.getElementById('delivered').addEventListener('input', updateCalc);

        async function submitReport(e) {
            e.preventDefault();
            if (sending || !selected) return;

            const isResch = document.getElementById('isReschedule').checked;
            const delivered = readCount('delivered');
            const returned = readCount('returned');

            const rescheduleDate = document.getElementById('rescheduleDate').value;
            let paymentType = "-";

            if (!isResch) {
                if (delivered === null || returned === null) {
                    notify("Введите целые числа от 0 до 1000");
                    return;
                }
                if (delivered > 0) {
                    if (!selectedPayment) {
                        document.getElementById('paymentChips').classList.add('error');
                        notify("Выберите способ оплаты: наличные, Click/Payme или перечисление");
                        return;
                    }
                    paymentType = selectedPayment;
                }
                const total = (delivered * pricePerBottle).toLocaleString('ru-RU');
                let message = `Сдал: ${delivered}, пустых: ${returned}\nОплата: ${delivered > 0 ? paymentType : '—'}\nСумма: ${total} сум\n\nОтправить отчёт?`;
                if (delivered === 0 && returned === 0) message = "⚠️ Сдано 0 и принято 0.\n\n" + message;
                if (!(await askConfirm(message))) return;
            } else {
                if (!(await askConfirm("Перенести заказ: " + rescheduleDate + "?"))) return;
            }

            const payload = {
                route_point_id: selected.id,
                is_reschedule: isResch,
                reschedule_date: isResch ? rescheduleDate : null,
                delivered: isResch ? 0 : delivered,
                returned: isResch ? 0 : returned,
                client_type: document.getElementById('clientType').value,
                payment_type: paymentType
            };

            const btn = document.getElementById('submitBtn');
            sending = true;
            btn.disabled = true;
            try {
                const res = await fetch('/api/report', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json', 'Authorization': 'tma ' + initData },
                    body: JSON.stringify(payload)
                });
                const data = await res.json().catch(() => ({}));
                if (res.ok && data.status === "ok") {
                    closeModal();
                    await loadData();
                } else {
                    notify(data.message || "Ошибка отправки отчета");
                    if (res.status === 409) { closeModal(); await loadData(); }
                }
            } catch (err) {
                notify("Ошибка соединения с сервером");
            } finally {
                sending = false;
                btn.disabled = false;
            }
        }

        document.addEventListener("DOMContentLoaded", loadData);
    </script>
</body>
</html>
"""


# ==================== ТЕЛЕГРАМ ХЕНДЛЕРЫ ====================
async def answer_long(message, text):
    """Telegram не принимает сообщения длиннее 4096 символов: режем по строкам."""
    chunk = ""
    for line in text.split("\n"):
        if len(chunk) + len(line) + 1 > 4000:
            await message.answer(chunk)
            chunk = ""
        chunk += line + "\n"
    if chunk.strip():
        await message.answer(chunk)


@dp.message(CommandStart())
async def cmd_start(message: types.Message):
    user_id = message.from_user.id

    couriers = await asyncio.to_thread(load_couriers)
    is_courier = user_id in couriers
    is_admin = user_id in ADMIN_IDS

    if not is_courier and not is_admin:
        await message.answer("❌ Извините, этот бот предназначен только для авторизованных курьеров и диспетчеров службы доставки воды.")
        return

    keyboard_buttons = [
        [KeyboardButton(text="📝 Открыть Mini App", web_app=WebAppInfo(url=f"{WEB_SERVER_HOST}/"))],
        [KeyboardButton(text="💰 Моя зарплата")],
    ]
    if is_admin:
        keyboard_buttons.append([KeyboardButton(text="📊 Сводка за сегодня")])

    markup = ReplyKeyboardMarkup(keyboard=keyboard_buttons, resize_keyboard=True)
    await message.answer(
        "👋 Добро пожаловать в систему управления доставкой воды!\n\n"
        "Используйте кнопку ниже для открытия панели курьера.",
        reply_markup=markup,
    )


@dp.message(F.text == "💰 Моя зарплата")
async def btn_my_salary(message: types.Message):
    await send_courier_personal_stats(message, message.from_user.id)


@dp.message(F.text == "📊 Сводка за сегодня")
async def btn_daily_summary(message: types.Message):
    if message.from_user.id not in ADMIN_IDS:
        await message.answer("❌ У вас нет прав администратора.")
        return
    await send_summary_report(message)


async def send_courier_personal_stats(target_message: types.Message, target_id: int):
    try:
        today = today_str()
        reports = await asyncio.to_thread(load_reports_for_day, today, target_id)

        phys_deliv, jur_deliv, total_returned, total_earnings = 0, 0, 0, 0
        for r in reports:
            deliv = int(r.get("delivered", 0) or 0)
            if "Юр" in str(r.get("client_type") or ""):
                jur_deliv += deliv
            else:
                phys_deliv += deliv
            total_returned += int(r.get("returned", 0) or 0)
            total_earnings += int(r.get("earnings", 0) or 0)

        if not reports:
            await target_message.answer("📭 У вас нет выполненных заказов за сегодня.")
            return

        earn_fmt = f"{total_earnings:,}".replace(",", " ")
        await target_message.answer(
            f"👤 ВАШ ЛИЧНЫЙ КАБИНЕТ\n"
            f"📅 Дата: {today}\n\n"
            f"📦 Выполнено заказов: {len(reports)}\n"
            f"💧 Всего сдано (19л): {phys_deliv + jur_deliv} шт.\n"
            f"🔄 Собрано пустых: {total_returned} шт.\n\n"
            f"💵 Итого заработок: {earn_fmt} сум"
        )
    except Exception:
        logging.exception("Ошибка личной статистики")
        await target_message.answer("⚠️ Не удалось получить данные. Попробуйте позже.")


async def send_summary_report(target_message: types.Message):
    try:
        today = today_str()
        reports = await asyncio.to_thread(load_reports_for_day, today)
        if not reports:
            await target_message.answer("📭 За сегодня отчетов нет.")
            return

        courier_stats = {}
        for r in reports:
            cid = str(r.get("courier_id") or "неизвестно")
            stats = courier_stats.setdefault(
                cid, {"name": str(r.get("courier_name") or "Курьер"), "deliv": 0, "ret": 0, "rev": 0, "orders": 0, "clients": []}
            )
            deliv = int(r.get("delivered", 0) or 0)
            ret = int(r.get("returned", 0) or 0)
            stats["deliv"] += deliv
            stats["ret"] += ret
            stats["rev"] += int(r.get("total_price", 0) or 0)
            stats["orders"] += 1
            stats["clients"].append(f"• {r.get('client_id') or 'Не указан'} (сдал: {deliv}, пустых: {ret})")

        text = f"📊 СВОДНЫЙ ОТЧЕТ ЗА СЕГОДНЯ ({today}):\n\n"
        tot_deliv, tot_ret, tot_rev = 0, 0, 0
        for s in courier_stats.values():
            tot_deliv += s["deliv"]
            tot_ret += s["ret"]
            tot_rev += s["rev"]
            rev_formatted = f"{s['rev']:,}".replace(",", " ")
            text += f"🚚 Курьер: {s['name']}\n"
            text += f"   ▫️ Выполнено заказов: {s['orders']}\n"
            text += f"   ▫️ Сдано воды: {s['deliv']} шт | Пустых: {s['ret']} шт\n"
            text += f"   ▫️ Выручка: {rev_formatted} сум\n"
            text += "   📍 Точки доставки:\n"
            for line in s["clients"]:
                text += f"      {line}\n"
            text += "\n"

        text += f"💧 Всего сдали воды: {tot_deliv} шт\n"
        text += f"🔄 Всего собрали пустых: {tot_ret} шт\n"
        text += f"💰 Общая выручка: {f'{tot_rev:,}'.replace(',', ' ')} сум"
        await answer_long(target_message, text)
    except Exception:
        logging.exception("Ошибка сводки")
        await target_message.answer("⚠️ Не удалось собрать сводку. Попробуйте позже.")


# ==================== WEB-СЕРВЕР ====================
def api_error(status, message):
    return web.json_response({"status": "error", "message": message}, status=status)


async def handle_index(request):
    return web.Response(text=HTML_CONTENT, content_type="text/html", headers={"Cache-Control": "no-store"})


async def handle_health(request):
    return web.Response(text="OK", status=200)


async def handle_webhook(request):
    # Telegram присылает секрет в заголовке. Чужие запросы отбрасываем.
    received = request.headers.get("X-Telegram-Bot-Api-Secret-Token", "")
    if not hmac.compare_digest(received, WEBHOOK_SECRET):
        return web.Response(status=403)
    try:
        data = await request.json()
        update = Update.model_validate(data, context={"bot": bot})
        await dp.feed_update(bot, update)
    except Exception:
        # Всегда отвечаем 200, иначе Telegram будет бесконечно повторять тот же апдейт
        logging.exception("Ошибка обработки апдейта")
    return web.Response(text="OK", status=200)


async def handle_get_route(request):
    user = get_auth_user(request)
    if not user:
        return api_error(401, "Не удалось подтвердить пользователя Telegram")
    courier_id = int(user["id"])
    if not supabase:
        return web.json_response({"status": "ok", "points": [], "price": PRICE_PER_BOTTLE})
    try:
        couriers = await asyncio.to_thread(load_couriers)
        if courier_id not in couriers and courier_id not in ADMIN_IDS:
            return api_error(403, "Вы не зарегистрированы как курьер")

        rows, locations, phones = await asyncio.gather(
            asyncio.to_thread(load_route, courier_id, today_str()),
            asyncio.to_thread(load_locations),
            asyncio.to_thread(load_phones),
        )
        points = []
        for row in rows:
            name = str(row.get("client_name") or "")
            point = {"id": row["id"], "name": name}
            loc = locations.get(location_key(name))
            if loc:
                point.update({"lat": loc[0], "lon": loc[1], "address": loc[2]})
            phone = phones.get(location_key(name))
            if phone:
                point["phone"] = phone
            points.append(point)
        return web.json_response({"status": "ok", "points": points, "price": PRICE_PER_BOTTLE})
    except Exception:
        logging.exception("Ошибка загрузки маршрута")
        return api_error(500, "Ошибка сервера")


def parse_count(value):
    n = int(value or 0)
    if n < 0 or n > MAX_BOTTLES:
        raise ValueError("Количество должно быть от 0 до 1000")
    return n


async def handle_api_report(request):
    user = get_auth_user(request)
    if not user:
        return api_error(401, "Не удалось подтвердить пользователя Telegram")
    sender_id = int(user["id"])
    if not supabase:
        return api_error(503, "База данных недоступна")

    try:
        data = await request.json()
    except Exception:
        return api_error(400, "Некорректный запрос")

    try:
        couriers = await asyncio.to_thread(load_couriers)
        if sender_id not in couriers:
            return api_error(403, "Вы не зарегистрированы как курьер")
        courier_name = couriers[sender_id]
        username = str(user.get("username") or "нет")

        try:
            point_id = int(data.get("route_point_id"))
        except (TypeError, ValueError):
            return api_error(400, "Не указана точка маршрута")

        # Точка должна быть в маршруте именно этого курьера. Повторная отправка
        # или чужая точка отсекаются здесь.
        point = await asyncio.to_thread(get_route_point, point_id, sender_id)
        if not point:
            return api_error(409, "Эта точка уже выполнена или удалена из маршрута")
        client_id = str(point.get("client_name") or "Не указан")

        if bool(data.get("is_reschedule", False)):
            reschedule_date = str(data.get("reschedule_date") or "")
            if reschedule_date not in RESCHEDULE_CHOICES:
                return api_error(400, "Некорректная дата переноса")
            if not await asyncio.to_thread(save_rescheduled_order, sender_id, courier_name, client_id, reschedule_date):
                return api_error(500, "Не удалось сохранить перенос. Попробуйте ещё раз")
            await asyncio.to_thread(delete_route_point, point_id)
            return web.json_response({"status": "ok"})

        try:
            delivered = parse_count(data.get("delivered"))
            returned = parse_count(data.get("returned"))
        except (TypeError, ValueError) as e:
            return api_error(400, str(e))

        client_type = str(data.get("client_type") or "")
        payment_type = str(data.get("payment_type") or "")
        if client_type not in CLIENT_TYPES:
            return api_error(400, "Некорректный тип клиента")
        if delivered > 0:
            # Способ оплаты выбирает курьер сам, значения по умолчанию нет
            if payment_type not in PAYMENT_TYPES:
                return api_error(400, "Выберите способ оплаты")
        else:
            payment_type = "-"   # воду не сдавали, оплаты нет

        total_price = delivered * PRICE_PER_BOTTLE   # сумму считает сервер, а не телефон
        rate = BONUS_JURIDICAL if "Юр" in client_type else BONUS_PHYSICAL

        report_record = {
            "date": datetime.now(UZ_TIMEZONE).strftime("%Y-%m-%d %H:%M:%S"),
            "courier_id": sender_id,
            "courier_name": courier_name,
            "courier_username": username,
            "client_id": client_id,
            "delivered": delivered,
            "returned": returned,
            "client_type": client_type,
            "payment_type": payment_type,
            "total_price": total_price,
            "earnings": delivered * rate,
        }
        # Точку убираем из маршрута только после того, как отчёт точно записан
        if not await asyncio.to_thread(save_report_to_db, report_record):
            return api_error(500, "Не удалось сохранить отчёт. Попробуйте ещё раз")
        await asyncio.to_thread(delete_route_point, point_id)

        if CHANNEL_ID:
            try:
                price_fmt = f"{total_price:,}".replace(",", " ")
                await bot.send_message(
                    CHANNEL_ID,
                    f"📦 Новый отчет о доставке!\n\n"
                    f"🚚 Курьер: {courier_name}\n"
                    f"📍 Клиент: {client_id}\n"
                    f"💧 Сдал (19л): {delivered} шт.\n"
                    f"🔄 Пустых забрал: {returned} шт.\n"
                    f"💳 Оплата: {payment_type} ({price_fmt} сум)",
                )
            except Exception:
                logging.exception("Не удалось отправить уведомление в канал")

        return web.json_response({"status": "ok"})
    except Exception:
        logging.exception("Ошибка обработки отчёта")
        return api_error(500, "Ошибка сервера")


async def main():
    try:
        await bot.set_webhook(url=WEBHOOK_URL, secret_token=WEBHOOK_SECRET)
        logging.info("Вебхук установлен")
    except Exception:
        logging.exception("Ошибка вебхука")

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
