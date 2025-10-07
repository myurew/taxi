import asyncio
import sqlite3
import threading
from datetime import datetime
from flask import Flask, render_template_string, jsonify, request, session
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, ReplyKeyboardMarkup, KeyboardButton
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.context import FSMContext
from aiogram.exceptions import (
    TelegramAPIError,
    TelegramForbiddenError,
    TelegramRetryAfter
)
import os
import secrets
import queue

# === CONFIG ===
BOT_TOKEN = os.getenv("BOT_TOKEN", "8297146262:AAG72LEJM2xVds5KDEoB0dJb52iwz8W4_qw")
ORDER_TIMEOUT = 10  # минут

# === BROADCAST QUEUE ===
BROADCAST_QUEUE = queue.Queue()

# === DATABASE ===
def init_db():
    conn = sqlite3.connect('taxi.db', check_same_thread=False)
    c = conn.cursor()
    c.execute('''
        CREATE TABLE IF NOT EXISTS users (
            telegram_id INTEGER PRIMARY KEY,
            username TEXT,
            first_name TEXT,
            role TEXT DEFAULT 'passenger' CHECK(role IN ('passenger', 'driver')),
            is_banned INTEGER DEFAULT 0,
            full_name TEXT,
            car_brand TEXT,
            car_model TEXT,
            license_plate TEXT,
            car_color TEXT,
            phone_number TEXT,
            payment_number TEXT,
            bank_name TEXT,
            registration_date TEXT DEFAULT (datetime('now'))
        )
    ''')
    c.execute('''
        CREATE TABLE IF NOT EXISTS tariffs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT UNIQUE NOT NULL,
            price REAL NOT NULL
        )
    ''')
    c.execute('''
        CREATE TABLE IF NOT EXISTS trips (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            passenger_id INTEGER,
            driver_id INTEGER,
            status TEXT DEFAULT 'requested',
            pickup TEXT,
            destination TEXT,
            fare REAL,
            created_at TEXT DEFAULT (datetime('now')),
            accepted_at TEXT,
            arrived_at TEXT,
            completed_at TEXT,
            passenger_message_id INTEGER,
            driver_message_id INTEGER
        )
    ''')
    c.execute('''
        CREATE TABLE IF NOT EXISTS ratings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            trip_id INTEGER UNIQUE,
            driver_id INTEGER,
            passenger_id INTEGER,
            rating INTEGER CHECK(rating BETWEEN 1 AND 5),
            created_at TEXT DEFAULT (datetime('now'))
        )
    ''')
    c.execute("INSERT OR IGNORE INTO tariffs (name, price) VALUES ('Эконом', 100.0), ('Стандарт', 200.0), ('Премиум', 300.0)")
    conn.commit()
    return conn

DB = init_db()

# === FLASK DASHBOARD ===
DASHBOARD_HTML = '''
<!DOCTYPE html>
<html lang="ru">
<head>
    <meta charset="UTF-8">
    <title>🚕 Такси — Админка</title>
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <style>
        * { box-sizing: border-box; }
        body {
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
            margin: 0;
            padding: 20px;
            background: #f8fafc;
            color: #1e293b;
        }
        .container {
            max-width: 1200px;
            margin: 0 auto;
        }
        header {
            text-align: center;
            margin-bottom: 30px;
            padding-bottom: 15px;
            border-bottom: 1px solid #e2e8f0;
        }
        .tabs {
            display: flex;
            gap: 8px;
            margin-bottom: 25px;
            flex-wrap: wrap;
        }
        .tab {
            padding: 10px 18px;
            background: #e2e8f0;
            cursor: pointer;
            border-radius: 8px;
            font-weight: 500;
            transition: all 0.2s;
        }
        .tab:hover {
            background: #cbd5e1;
        }
        .tab.active {
            background: #3b82f6;
            color: white;
        }
        .tab-content {
            display: none;
        }
        .tab-content.active {
            display: block;
        }
        .card {
            background: white;
            padding: 20px;
            border-radius: 12px;
            margin-bottom: 20px;
            box-shadow: 0 2px 6px rgba(0,0,0,0.05);
        }
        .stats {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
            gap: 16px;
            margin-bottom: 24px;
        }
        .stat-card {
            background: white;
            padding: 16px;
            border-radius: 10px;
            text-align: center;
            box-shadow: 0 1px 4px rgba(0,0,0,0.08);
        }
        .stat-card h3 {
            margin: 8px 0 4px;
            font-size: 28px;
            color: #3b82f6;
        }
        table {
            width: 100%;
            border-collapse: collapse;
            margin: 12px 0;
        }
        th, td {
            padding: 12px 10px;
            text-align: left;
            border-bottom: 1px solid #e2e8f0;
        }
        th {
            background: #f8fafc;
            font-weight: 600;
        }
        button {
            padding: 6px 12px;
            margin: 2px;
            border: none;
            border-radius: 6px;
            cursor: pointer;
            font-size: 14px;
        }
        .btn-primary { background: #3b82f6; color: white; }
        .btn-success { background: #10b981; color: white; }
        .btn-danger { background: #ef4444; color: white; }
        .btn-warning { background: #f59e0b; color: white; }
        .form-group {
            margin: 12px 0;
        }
        input, select, textarea {
            width: 100%;
            padding: 10px;
            border: 1px solid #cbd5e1;
            border-radius: 6px;
            font-size: 15px;
        }
        .hidden { display: none !important; }
        .message {
            padding: 12px;
            margin: 12px 0;
            border-radius: 8px;
        }
        .message.success { background: #d1fae5; color: #065f46; }
        .message.error { background: #fee2e2; color: #b91c1c; }
        .actions { white-space: nowrap; }
        footer {
            text-align: center;
            margin-top: 40px;
            color: #94a3b8;
            font-size: 14px;
        }
    </style>
</head>
<body>
<div class="container">
    <header>
        <h1>🚕 Служба такси — Админ-панель</h1>
    </header>
    <!-- Экран входа -->
    <div id="auth-screen" class="card">
        <h2>🔐 Вход для администратора</h2>
        <div class="form-group">
            <input type="text" id="login-username" placeholder="Логин" value="admin">
        </div>
        <div class="form-group">
            <input type="password" id="login-password" placeholder="Пароль" value="admin123">
        </div>
        <button class="btn-primary" onclick="login()">Войти</button>
        <div id="login-message"></div>
    </div>
    <!-- Основной интерфейс -->
    <div id="main-app" class="hidden">
        <button onclick="logout()" style="float: right; margin-bottom: 10px;">🚪 Выйти</button>
        <div class="tabs">
            <div class="tab active" data-tab="dashboard">📊 Обзор</div>
            <div class="tab" data-tab="users">👥 Пассажиры</div>
            <div class="tab" data-tab="drivers">🚗 Водители</div>
            <div class="tab" data-tab="orders">📋 Заказы</div>
            <div class="tab" data-tab="tariffs">💰 Тарифы</div>
            <div class="tab" data-tab="broadcast">📢 Рассылка</div>
        </div>
        <!-- Вкладка: Обзор -->
        <div class="tab-content active" id="tab-dashboard">
            <div class="stats" id="stats-container"></div>
            <div class="card">
                <h3>🏆 Топ-5 водителей по заработку</h3>
                <table id="top-drivers-table">
                    <thead>
                        <tr>
                            <th>ID</th>
                            <th>Имя</th>
                            <th>Завершено поездок</th>
                            <th>Заработок</th>
                        </tr>
                    </thead>
                    <tbody></tbody>
                </table>
            </div>
            <div id="financial-chart" class="card"></div>
        </div>
        <!-- Вкладка: Пассажиры -->
        <div class="tab-content" id="tab-users">
            <div class="card">
                <h3>👥 Список пассажиров</h3>
                <table id="passengers-table">
                    <thead>
                        <tr>
                            <th>ID</th>
                            <th>Имя</th>
                            <th>Юзернейм</th>
                            <th>Заблокирован</th>
                            <th>Действия</th>
                        </tr>
                    </thead>
                    <tbody></tbody>
                </table>
            </div>
        </div>
        <!-- Вкладка: Водители -->
        <div class="tab-content" id="tab-drivers">
            <div class="card">
                <h3>🚗 Управление водителями</h3>
                <button class="btn-success" onclick="toggleCreateDriverForm()">+ Создать водителя</button>
                <div id="create-driver-form" class="card hidden" style="margin-top: 16px;">
                    <h4 id="form-title">Создать водителя из существующего пользователя</h4>
                    <div class="form-group">
                        <input type="number" id="driver-user-id" placeholder="Telegram ID пользователя (обязательно)" required readonly>
                    </div>
                    <div class="form-group">
                        <input type="text" id="driver-name" placeholder="ФИО водителя">
                    </div>
                    <div class="form-group">
                        <input type="text" id="driver-car-brand" placeholder="Марка автомобиля">
                    </div>
                    <div class="form-group">
                        <input type="text" id="driver-car-model" placeholder="Модель автомобиля">
                    </div>
                    <div class="form-group">
                        <input type="text" id="driver-license-plate" placeholder="Гос. номер">
                    </div>
                    <div class="form-group">
                        <input type="text" id="driver-phone" placeholder="Контактный телефон">
                    </div>
                    <div class="form-group">
                        <input type="text" id="driver-payment" placeholder="Номер для оплаты (СБП/номер карты)">
                    </div>
                    <div class="form-group">
                        <input type="text" id="driver-bank" placeholder="Банк">
                    </div>
                    <button class="btn-success" onclick="createDriver()">Сохранить данные водителя</button>
                    <button type="button" onclick="toggleCreateDriverForm()">Отмена</button>
                </div>
                <table id="drivers-table">
                    <thead>
                        <tr>
                            <th>ID</th>
                            <th>Имя</th>
                            <th>Авто</th>
                            <th>Заказов</th>
                            <th>Заработок</th>
                            <th>Рейтинг</th>
                            <th>Действия</th>
                        </tr>
                    </thead>
                    <tbody></tbody>
                </table>
            </div>
        </div>
        <!-- Вкладка: Заказы -->
        <div class="tab-content" id="tab-orders">
            <div class="card">
                <h3>📋 Последние 50 заказов</h3>
                <table id="orders-table">
                    <thead>
                        <tr>
                            <th>ID</th>
                            <th>Пассажир</th>
                            <th>Водитель</th>
                            <th>Откуда</th>
                            <th>Куда</th>
                            <th>Статус</th>
                            <th>Цена</th>
                            <th>Создан</th>
                            <th>Действия</th>
                        </tr>
                    </thead>
                    <tbody></tbody>
                </table>
            </div>
        </div>
        <!-- Вкладка: Тарифы -->
        <div class="tab-content" id="tab-tariffs">
            <div class="card">
                <h3>💰 Управление тарифами</h3>
                <div class="form-group">
                    <input type="text" id="new-tariff-name" placeholder="Название тарифа (например, 'Эконом')">
                </div>
                <div class="form-group">
                    <input type="number" step="0.01" id="new-tariff-price" placeholder="Цена в рублях">
                </div>
                <button class="btn-success" onclick="createTariff()">Добавить тариф</button>
                <table id="tariffs-table" style="margin-top: 20px;">
                    <thead>
                        <tr>
                            <th>ID</th>
                            <th>Название</th>
                            <th>Цена</th>
                            <th>Действия</th>
                        </tr>
                    </thead>
                    <tbody></tbody>
                </table>
            </div>
        </div>
        <!-- Вкладка: Рассылка -->
        <div class="tab-content" id="tab-broadcast">
            <div class="card">
                <h3>📢 Массовая рассылка</h3>
                <div class="form-group">
                    <label><input type="radio" name="broadcast-type" value="drivers" checked> Водителям</label>
                    <label><input type="radio" name="broadcast-type" value="passengers"> Пассажирам</label>
                    <label><input type="radio" name="broadcast-type" value="all"> Всем пользователям</label>
                </div>
                <div class="form-group">
                    <textarea id="broadcast-message" placeholder="Введите текст сообщения..." rows="5"></textarea>
                </div>
                <button class="btn-primary" onclick="sendBroadcast()">Отправить рассылку</button>
                <div id="broadcast-result"></div>
            </div>
        </div>
    </div>
    <footer>
        Админ-панель службы такси • Обновлено: <span id="current-date"></span>
    </footer>
</div>
<script>
    // Утилиты
    const qs = (sel) => document.querySelector(sel);
    const qsa = (sel) => document.querySelectorAll(sel);
    let currentTab = 'dashboard';
    // Переключение вкладок
    qsa('.tab').forEach(tab => {
        tab.addEventListener('click', () => {
            qsa('.tab').forEach(t => t.classList.remove('active'));
            qsa('.tab-content').forEach(t => t.classList.remove('active'));
            tab.classList.add('active');
            const target = tab.dataset.tab;
            qs(`#tab-${target}`).classList.add('active');
            currentTab = target;
            loadTabData(target);
        });
    });
    // Загрузка данных по вкладке
    function loadTabData(tabName) {
        if (tabName === 'dashboard') loadDashboard();
        else if (tabName === 'users') loadPassengers();
        else if (tabName === 'drivers') loadDrivers();
        else if (tabName === 'orders') loadOrders();
        else if (tabName === 'tariffs') loadTariffs();
    }
    // Проверка авторизации
    async function checkAuth() {
        try {
            const res = await fetch('/check_auth');
            const data = await res.json();
            if (data.logged_in) {
                qs('#auth-screen').classList.add('hidden');
                qs('#main-app').classList.remove('hidden');
                loadDashboard();
            } else {
                qs('#main-app').classList.add('hidden');
                qs('#auth-screen').classList.remove('hidden');
            }
        } catch (e) {
            console.error('Auth check failed:', e);
        }
    }
    // Вход
    async function login() {
        const msgEl = qs('#login-message');
        msgEl.className = 'message';
        try {
            const res = await fetch('/login', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    username: qs('#login-username').value,
                    password: qs('#login-password').value
                })
            });
            if (res.ok) {
                msgEl.className = 'message success';
                msgEl.textContent = '✅ Успешный вход!';
                setTimeout(checkAuth, 500);
            } else {
                msgEl.className = 'message error';
                msgEl.textContent = '❌ Неверный логин или пароль';
            }
        } catch (e) {
            msgEl.className = 'message error';
            msgEl.textContent = 'Ошибка подключения';
        }
    }
    // Выход
    function logout() {
        fetch('/logout').then(() => checkAuth());
    }
    // Универсальный API-вызов
    async function apiCall(url, options = {}) {
        const res = await fetch(url, {
            ...options,
            credentials: 'same-origin'
        });
        if (!res.ok) {
            const text = await res.text();
            throw new Error(`HTTP ${res.status}: ${text}`);
        }
        return await res.json();
    }
    // === DASHBOARD ===
    async function loadDashboard() {
        try {
            const data = await apiCall('/api/dashboard');
            const statsContainer = qs('#stats-container');
            statsContainer.innerHTML = `
                <div class="stat-card">
                    <div>Пассажиры</div>
                    <h3>${data.users.role_stats.passenger}</h3>
                </div>
                <div class="stat-card">
                    <div>Водители</div>
                    <h3>${data.users.role_stats.driver}</h3>
                </div>
                <div class="stat-card">
                    <div>Всего заказов</div>
                    <h3>${data.orders.total_stats.total_orders}</h3>
                </div>
                <div class="stat-card">
                    <div>Завершено</div>
                    <h3>${data.orders.total_stats.completed_orders}</h3>
                </div>
                <div class="stat-card">
                    <div>Отменено</div>
                    <h3>${data.orders.total_stats.canceled_orders}</h3>
                </div>
                <div class="stat-card">
                    <div>Выручка</div>
                    <h3>${(data.orders.total_stats.total_earnings || 0).toFixed(2)} ₽</h3>
                </div>
            `;
            const topDriversTbody = qs('#top-drivers-table tbody');
            topDriversTbody.innerHTML = data.financial.top_drivers.map(d => `
                <tr>
                    <td>${d.user_id}</td>
                    <td>${d.name}</td>
                    <td>${d.total_orders}</td>
                    <td>${d.total_earnings.toFixed(2)} ₽</td>
                </tr>
            `).join('');
            loadFinancialChart();
        } catch (e) {
            console.error('Ошибка загрузки дашборда:', e);
        }
    }
    async function loadFinancialChart() {
        try {
            const data = await apiCall('/api/financial');
            const container = qs('#financial-chart');
            if (!container) return;
            const earnings = data.daily_earnings;
            if (earnings.length === 0) {
                container.innerHTML = '<p>Нет данных о доходах за последние дни</p>';
                return;
            }
            earnings.sort((a, b) => a.day.localeCompare(b.day));
            const maxEarnings = Math.max(...earnings.map(e => e.earnings));
            const chartHtml = earnings.map(e => {
                const height = maxEarnings > 0 ? Math.max(10, (e.earnings / maxEarnings) * 100) : 10;
                const dateParts = e.day.split('-');
                const formattedDate = `${dateParts[2]}.${dateParts[1]}`;
                return `
                    <div style="display: flex; flex-direction: column; align-items: center; margin: 0 4px;">
                        <div style="width: 30px; height: ${height}px; background: #3b82f6; margin-bottom: 4px; border-radius: 4px;"></div>
                        <small>${formattedDate}</small>
                        <small>${e.earnings.toFixed(0)} ₽</small>
                    </div>
                `;
            }).join('');
            container.innerHTML = `
                <h3>Доход за последние ${earnings.length} дней</h3>
                <div style="display: flex; justify-content: center; align-items: flex-end; height: 120px; background: #f8fafc; padding: 10px; border-radius: 8px;">
                    ${chartHtml}
                </div>
            `;
        } catch (e) {
            console.error('Ошибка загрузки графика:', e);
        }
    }
    // === ПАССАЖИРЫ ===
    async function loadPassengers() {
        try {
            const passengers = await apiCall('/api/admin/passengers');
            const tbody = qs('#passengers-table tbody');
            tbody.innerHTML = passengers.map(p => `
                <tr>
                    <td>${p.user_id}</td>
                    <td>${p.first_name || '—'}</td>
                    <td>@${p.username || '—'}</td>
                    <td>${p.is_banned ? '✅' : '—'}</td>
                    <td class="actions">
                        ${p.is_banned ?
                            `<button class="btn-success" onclick="unbanUser(${p.user_id})">Разблокировать</button>` :
                            `<button class="btn-danger" onclick="banUser(${p.user_id})">Заблокировать</button>`
                        }
                        <button class="btn-warning" onclick="makeDriver(${p.user_id})">Сделать водителем</button>
                    </td>
                </tr>
            `).join('');
        } catch (e) {
            console.error('Ошибка загрузки пассажиров:', e);
        }
    }
    // === ВОДИТЕЛИ ===
    async function loadDrivers() {
        try {
            const drivers = await apiCall('/api/drivers');
            const tbody = qs('#drivers-table tbody');
            tbody.innerHTML = drivers.map(d => {
                const displayName = d.name || d.first_name || `ID ${d.user_id}`;
                const carInfo = [
                    d.car_brand,
                    d.car_model,
                    d.license_plate ? `(${d.license_plate})` : ''
                ].filter(Boolean).join(' ');
                return `
                    <tr>
                        <td>${d.user_id}</td>
                        <td>${displayName}</td>
                        <td>${carInfo || '—'}</td>
                        <td>${d.completed_orders}</td>
                        <td>${(d.total_earnings || 0).toFixed(2)} ₽</td>
                        <td>${d.avg_rating ? '⭐' + d.avg_rating : '—'}</td>
                        <td class="actions">
                            <button class="btn-danger" onclick="deleteDriver(${d.user_id})">Удалить</button>
                        </td>
                    </tr>
                `;
            }).join('');
        } catch (e) {
            console.error('Ошибка загрузки водителей:', e);
        }
    }
    function toggleCreateDriverForm(userId = null) {
        const form = qs('#create-driver-form');
        const title = qs('#form-title');
        if (userId !== null) {
            qs('#driver-user-id').value = userId;
            qs('#driver-user-id').readOnly = true;
            title.textContent = `Редактировать водителя ID ${userId}`;
        } else {
            qs('#driver-user-id').value = '';
            qs('#driver-user-id').readOnly = false;
            title.textContent = 'Создать водителя из существующего пользователя';
        }
        form.classList.toggle('hidden');
    }
    async function createDriver() {
        const userId = parseInt(qs('#driver-user-id').value);
        if (!userId) {
            alert('Укажите корректный Telegram ID пользователя');
            return;
        }
        try {
            await apiCall('/api/admin/create_driver', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    user_id: userId,
                    driver_data: {
                        name: qs('#driver-name').value,
                        car_brand: qs('#driver-car-brand').value,
                        car_model: qs('#driver-car-model').value,
                        license_plate: qs('#driver-license-plate').value,
                        contact_phone: qs('#driver-phone').value,
                        payment_phone: qs('#driver-payment').value,
                        bank: qs('#driver-bank').value
                    }
                })
            });
            alert('✅ Данные водителя сохранены!');
            toggleCreateDriverForm();
            loadDrivers();
            if (currentTab === 'users') loadPassengers();
        } catch (e) {
            alert('❌ Ошибка: ' + e.message);
        }
    }
    async function makeDriver(userId) {
        if (!confirm(`Сделать пользователя ID ${userId} водителем?`)) return;
        try {
            await apiCall('/api/admin/create_driver', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    user_id: userId,
                    driver_data: {
                        name: '',
                        car_brand: '',
                        car_model: '',
                        license_plate: '',
                        contact_phone: '',
                        payment_phone: '',
                        bank: ''
                    }
                })
            });
            qsa('.tab').forEach(t => t.classList.remove('active'));
            qs('.tab[data-tab="drivers"]').classList.add('active');
            qsa('.tab-content').forEach(t => t.classList.remove('active'));
            qs('#tab-drivers').classList.add('active');
            currentTab = 'drivers';
            loadDrivers();
            setTimeout(() => {
                toggleCreateDriverForm(userId);
            }, 300);
        } catch (e) {
            alert('❌ Ошибка: ' + e.message);
        }
    }
    // === ЗАКАЗЫ ===
    async function loadOrders() {
        try {
            const data = await apiCall('/api/orders');
            const tbody = qs('#orders-table tbody');
            const getStatusText = (status) => {
                switch(status) {
                    case 'requested': return 'Ожидает';
                    case 'accepted': return 'Принят';
                    case 'in_progress': return 'В пути';
                    case 'completed': return 'Завершён';
                    case 'cancelled': return 'Отменён';
                    case 'cancelled_by_passenger': return 'Отм. пассажиром';
                    case 'cancelled_by_driver': return 'Отм. водителем';
                    case 'expired': return 'Авто-отмена';
                    default: return status;
                }
            };
            tbody.innerHTML = data.recent_orders.map(o => {
                const driverDisplay = o.driver_id ?
                    (o.driver_name + (o.license_plate ? ` (${o.license_plate})` : '')) :
                    '—';
                return `
                    <tr>
                        <td>${o.order_id}</td>
                        <td>${o.passenger_id}</td>
                        <td>${driverDisplay}</td>
                        <td>${o.from_location || '—'}</td>
                        <td>${o.to_location || '—'}</td>
                        <td>${getStatusText(o.status)}</td>
                        <td>${o.price ? o.price.toFixed(2) + ' ₽' : '—'}</td>
                        <td>${new Date(o.created_at).toLocaleString('ru-RU')}</td>
                        <td class="actions">
                            ${['requested', 'accepted', 'in_progress'].includes(o.status) ?
                                `<button class="btn-danger" onclick="cancelOrder(${o.order_id})">Отменить</button>` : ''
                            }
                        </td>
                    </tr>
                `;
            }).join('');
        } catch (e) {
            console.error('Ошибка загрузки заказов:', e);
        }
    }
    async function cancelOrder(orderId) {
        if (!confirm(`Вы уверены, что хотите отменить заказ #${orderId}?`)) return;
        try {
            await apiCall('/api/admin/cancel_order', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ order_id: orderId })
            });
            alert('✅ Заказ отменён');
            loadOrders();
        } catch (e) {
            alert('❌ Ошибка: ' + e.message);
        }
    }
    // === ТАРИФЫ ===
    async function loadTariffs() {
        try {
            const tariffs = await apiCall('/api/tariffs');
            const tbody = qs('#tariffs-table tbody');
            tbody.innerHTML = tariffs.map(t => `
                <tr>
                    <td>${t.id}</td>
                    <td>${t.name}</td>
                    <td>${t.price.toFixed(2)} ₽</td>
                    <td class="actions">
                        <button class="btn-warning" onclick="editTariff(${t.id}, '${t.name}', ${t.price})">✏️</button>
                        <button class="btn-danger" onclick="deleteTariff(${t.id})">🗑️</button>
                    </td>
                </tr>
            `).join('');
        } catch (e) {
            console.error('Ошибка загрузки тарифов:', e);
        }
    }
    async function createTariff() {
        const name = qs('#new-tariff-name').value.trim();
        const price = parseFloat(qs('#new-tariff-price').value);
        if (!name || isNaN(price)) {
            alert('Заполните название и цену');
            return;
        }
        try {
            await apiCall('/api/tariffs', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ name, price })
            });
            alert('✅ Тариф добавлен');
            qs('#new-tariff-name').value = '';
            qs('#new-tariff-price').value = '';
            loadTariffs();
        } catch (e) {
            alert('❌ Ошибка: ' + e.message);
        }
    }
    async function editTariff(id, name, price) {
        const newName = prompt("Название тарифа:", name);
        const newPrice = prompt("Цена:", price);
        if (newName === null || newPrice === null) return;
        const numPrice = parseFloat(newPrice);
        if (!newName.trim() || isNaN(numPrice)) {
            alert("Некорректные данные");
            return;
        }
        try {
            await apiCall(`/api/tariffs/${id}`, {
                method: 'PUT',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ name: newName.trim(), price: numPrice })
            });
            alert('✅ Тариф обновлён');
            loadTariffs();
        } catch (e) {
            alert('❌ Ошибка: ' + e.message);
        }
    }
    async function deleteTariff(id) {
        if (!confirm('Удалить тариф? Это действие нельзя отменить.')) return;
        try {
            await apiCall(`/api/tariffs/${id}`, { method: 'DELETE' });
            alert('✅ Тариф удалён');
            loadTariffs();
        } catch (e) {
            alert('❌ Ошибка: ' + e.message);
        }
    }
    // === РАССЫЛКА ===
    async function sendBroadcast() {
        const message = qs('#broadcast-message').value.trim();
        if (!message) {
            alert('Введите текст сообщения');
            return;
        }
        const type = document.querySelector('input[name="broadcast-type"]:checked').value;
        let user_ids = [];
        try {
            if (type === 'drivers') {
                const drivers = await apiCall('/api/admin/drivers_for_messaging');
                user_ids = drivers.map(d => d.user_id);
            } else if (type === 'passengers') {
                const passengers = await apiCall('/api/admin/passengers');
                user_ids = passengers.map(p => p.user_id);
            } else {
                const users = await apiCall('/api/admin/users');
                user_ids = users.map(u => u.user_id);
            }
            if (user_ids.length === 0) {
                alert('Нет получателей для рассылки');
                return;
            }
            const result = await apiCall('/api/admin/send_message', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ user_ids, message_text: message })
            });
            const resultEl = qs('#broadcast-result');
            resultEl.className = 'message success';
            resultEl.textContent = result.message;
            qs('#broadcast-message').value = '';
        } catch (e) {
            const resultEl = qs('#broadcast-result');
            resultEl.className = 'message error';
            resultEl.textContent = '❌ Ошибка: ' + e.message;
        }
    }
    // === УПРАВЛЕНИЕ ПОЛЬЗОВАТЕЛЯМИ ===
    async function banUser(id) {
        if (!confirm('Заблокировать пользователя?')) return;
        try {
            await apiCall('/api/admin/ban', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ user_id: id })
            });
            alert('✅ Пользователь заблокирован');
            loadPassengers();
        } catch (e) {
            alert('❌ Ошибка: ' + e.message);
        }
    }
    async function unbanUser(id) {
        try {
            await apiCall('/api/admin/unban', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ user_id: id })
            });
            alert('✅ Пользователь разблокирован');
            loadPassengers();
        } catch (e) {
            alert('❌ Ошибка: ' + e.message);
        }
    }
    async function deleteDriver(id) {
        if (!confirm('Удалить водителя? Профиль станет пассажиром.')) return;
        try {
            await apiCall('/api/admin/delete_driver', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ user_id: id })
            });
            alert('✅ Водитель удалён');
            loadDrivers();
            if (currentTab === 'users') loadPassengers();
        } catch (e) {
            alert('❌ Ошибка: ' + e.message);
        }
    }
    // Инициализация
    document.getElementById('current-date').textContent = new Date().toLocaleDateString('ru-RU');
    checkAuth();
    setInterval(checkAuth, 60000);
</script>
</body>
</html>
'''

app = Flask(__name__)
app.secret_key = secrets.token_hex(16)
ADMIN_USERNAME = "admin"
ADMIN_PASSWORD = "admin123"

@app.route('/')
def dashboard():
    return render_template_string(DASHBOARD_HTML)

@app.route('/check_auth')
def check_auth():
    return jsonify({"logged_in": "user_id" in session})

@app.route('/login', methods=['POST'])
def login():
    data = request.get_json()
    username = data.get('username')
    password = data.get('password')
    if username == ADMIN_USERNAME and password == ADMIN_PASSWORD:
        session['user_id'] = 1
        return jsonify({"success": True})
    return jsonify({"success": False}), 401

@app.route('/logout')
def logout():
    session.pop('user_id', None)
    return jsonify({"success": True})

# === API ENDPOINTS ===
@app.route('/api/dashboard')
def api_dashboard():
    cur = DB.cursor()
    users = cur.execute("SELECT * FROM users").fetchall()
    orders = cur.execute("SELECT * FROM trips").fetchall()
    drivers = [u for u in users if u[3] == 'driver']
    total_stats = {
        "total_orders": len(orders),
        "completed_orders": len([o for o in orders if o[3] == 'completed']),
        "canceled_orders": len([o for o in orders if o[3] in ('cancelled', 'cancelled_by_passenger', 'cancelled_by_driver', 'expired')]),
        "total_earnings": sum(o[6] or 0 for o in orders if o[3] == 'completed')
    }
    cur.execute('''
        SELECT u.telegram_id, u.full_name, u.first_name,
               COUNT(t.id) as total_orders,
               SUM(t.fare) as total_earnings
        FROM users u
        LEFT JOIN trips t ON u.telegram_id = t.driver_id AND t.status = 'completed'
        WHERE u.role = 'driver'
        GROUP BY u.telegram_id
        ORDER BY total_earnings DESC
        LIMIT 5
    ''')
    top_drivers = cur.fetchall()
    top_drivers_list = [
        {
            "user_id": d[0],
            "name": d[1] or d[2] or f"ID {d[0]}",
            "total_orders": d[3] or 0,
            "total_earnings": d[4] or 0,
        }
        for d in top_drivers
    ]
    return jsonify({
        "users": {
            "role_stats": {
                "passenger": len([u for u in users if u[3] == 'passenger']),
                "driver": len(drivers)
            },
            "active_users": len([u for u in users if not u[4]])
        },
        "orders": {
            "total_stats": total_stats,
            "daily_stats": []
        },
        "drivers": drivers,
        "financial": {
            "daily_earnings": [],
            "top_drivers": top_drivers_list
        }
    })

@app.route('/api/admin/users')
def api_users():
    cur = DB.cursor()
    users = cur.execute("SELECT * FROM users").fetchall()
    return jsonify([{
        "user_id": u[0], "username": u[1], "first_name": u[2], "role": u[3],
        "is_banned": bool(u[4]), "registration_date": u[13]
    } for u in users])

@app.route('/api/admin/passengers')
def api_passengers():
    cur = DB.cursor()
    passengers = cur.execute("SELECT * FROM users WHERE role = 'passenger'").fetchall()
    return jsonify([{
        "user_id": p[0],
        "username": p[1],
        "first_name": p[2],
        "role": p[3],
        "is_banned": bool(p[4])
    } for p in passengers])

@app.route('/api/admin/drivers_for_messaging')
def api_drivers_for_messaging():
    cur = DB.cursor()
    drivers = cur.execute("SELECT * FROM users WHERE role = 'driver'").fetchall()
    return jsonify([{
        "user_id": d[0], "username": d[1], "first_name": d[2],
        "is_banned": bool(d[4])
    } for d in drivers])

@app.route('/api/drivers')
def api_drivers():
    cur = DB.cursor()
    cur.execute('''
        SELECT 
            u.telegram_id,
            u.full_name,
            u.first_name,
            u.username,
            u.is_banned,
            u.car_brand,
            u.car_model,
            u.license_plate,
            COUNT(t.id) as total_orders,
            SUM(CASE WHEN t.status = 'completed' THEN 1 ELSE 0 END) as completed_orders,
            SUM(CASE WHEN t.status IN ('cancelled', 'cancelled_by_passenger', 'cancelled_by_driver', 'expired') THEN 1 ELSE 0 END) as canceled_orders,
            SUM(CASE WHEN t.status = 'completed' THEN t.fare ELSE 0 END) as total_earnings,
            AVG(r.rating) as avg_rating
        FROM users u
        LEFT JOIN trips t ON u.telegram_id = t.driver_id
        LEFT JOIN ratings r ON t.id = r.trip_id
        WHERE u.role = 'driver'
        GROUP BY u.telegram_id
    ''')
    rows = cur.fetchall()
    return jsonify([{
        "user_id": row[0],
        "name": row[1],
        "first_name": row[2],
        "username": row[3],
        "is_banned": bool(row[4]),
        "car_brand": row[5],
        "car_model": row[6],
        "license_plate": row[7],
        "total_orders": row[8] or 0,
        "completed_orders": row[9] or 0,
        "canceled_orders": row[10] or 0,
        "total_earnings": row[11] or 0,
        "avg_rating": round(row[12], 1) if row[12] else None
    } for row in rows])

@app.route('/api/orders')
def api_orders():
    cur = DB.cursor()
    cur.execute('''
        SELECT 
            t.id, t.passenger_id, t.driver_id, t.status, t.pickup, t.destination, t.fare, t.created_at,
            u.full_name, u.license_plate
        FROM trips t
        LEFT JOIN users u ON t.driver_id = u.telegram_id
        ORDER BY t.created_at DESC
        LIMIT 50
    ''')
    orders = cur.fetchall()
    return jsonify({"recent_orders": [
        {
            "order_id": o[0],
            "passenger_id": o[1],
            "driver_id": o[2],
            "status": o[3],
            "from_location": o[4],
            "to_location": o[5],
            "price": o[6],
            "created_at": o[7],
            "driver_name": o[8] or f"ID {o[2]}" if o[2] else None,
            "license_plate": o[9]
        }
        for o in orders
    ]})

@app.route('/api/tariffs')
def api_tariffs():
    cur = DB.cursor()
    tariffs = cur.execute("SELECT id, name, price FROM tariffs").fetchall()
    return jsonify([{"id": t[0], "name": t[1], "price": t[2]} for t in tariffs])

@app.route('/api/tariffs', methods=['POST'])
def create_tariff():
    data = request.get_json()
    name = data.get('name')
    price = data.get('price')
    if not name or not price:
        return jsonify({"success": False, "message": "Название и цена обязательны"}), 400
    try:
        cur = DB.cursor()
        cur.execute("INSERT INTO tariffs (name, price) VALUES (?, ?)", (name, price))
        DB.commit()
        return jsonify({"success": True, "message": "Тариф добавлен"})
    except sqlite3.IntegrityError:
        return jsonify({"success": False, "message": "Тариф с таким названием уже существует"}), 400

@app.route('/api/tariffs/<int:tariff_id>', methods=['PUT'])
def update_tariff(tariff_id):
    data = request.get_json()
    name = data.get('name')
    price = data.get('price')
    if not name or price is None:
        return jsonify({"success": False, "message": "Название и цена обязательны"}), 400
    cur = DB.cursor()
    cur.execute("UPDATE tariffs SET name = ?, price = ? WHERE id = ?", (name, price, tariff_id))
    DB.commit()
    if cur.rowcount == 0:
        return jsonify({"success": False, "message": "Тариф не найден"}), 404
    return jsonify({"success": True, "message": "Тариф обновлён"})

@app.route('/api/tariffs/<int:tariff_id>', methods=['DELETE'])
def delete_tariff(tariff_id):
    cur = DB.cursor()
    cur.execute("DELETE FROM tariffs WHERE id = ?", (tariff_id,))
    DB.commit()
    if cur.rowcount == 0:
        return jsonify({"success": False, "message": "Тариф не найден"}), 404
    return jsonify({"success": True, "message": "Тариф удалён"})

@app.route('/api/financial')
def api_financial():
    cur = DB.cursor()
    cur.execute('''
        SELECT 
            date(created_at) as day,
            SUM(fare) as earnings
        FROM trips
        WHERE status = 'completed'
        GROUP BY date(created_at)
        ORDER BY day DESC
        LIMIT 7
    ''')
    rows = cur.fetchall()
    daily_earnings = [
        {"day": row[0], "earnings": row[1] or 0}
        for row in rows
    ]
    return jsonify({"daily_earnings": daily_earnings})

@app.route('/api/admin/send_message', methods=['POST'])
def api_send_message():
    if 'user_id' not in session:
        return jsonify({"success": False, "message": "Требуется авторизация"}), 401
    try:
        data = request.get_json()
        user_ids = data.get('user_ids', [])
        message_text = data.get('message_text', '')
        if not user_ids or not message_text:
            return jsonify({"success": False, "message": "Не указаны получатели или текст"}), 400
        full_message = f"📢 От руководства службы такси:\n{message_text}"
        BROADCAST_QUEUE.put({
            "user_ids": user_ids,
            "message_text": full_message
        })
        return jsonify({
            "success": True,
            "message": f"Рассылка поставлена в очередь для {len(user_ids)} пользователей"
        })
    except Exception as e:
        print(f"Ошибка в рассылке: {e}")
        return jsonify({"success": False, "message": "Ошибка сервера"}), 500

@app.route('/api/admin/create_driver', methods=['POST'])
def create_driver():
    try:
        data = request.get_json()
        user_id = data.get('user_id')
        driver_data = data.get('driver_data', {})
        if not user_id:
            return jsonify({"success": False, "message": "Не указан ID пользователя"}), 400
        full_name = driver_data.get('name')
        car_brand = driver_data.get('car_brand')
        car_model = driver_data.get('car_model')
        license_plate = driver_data.get('license_plate')
        contact_phone = driver_data.get('contact_phone')
        payment_phone = driver_data.get('payment_phone')
        bank = driver_data.get('bank')
        cur = DB.cursor()
        cur.execute('''
            UPDATE users SET
                role = 'driver',
                full_name = ?,
                car_brand = ?,
                car_model = ?,
                license_plate = ?,
                phone_number = ?,
                payment_number = ?,
                bank_name = ?
            WHERE telegram_id = ?
        ''', (
            full_name, car_brand, car_model, license_plate,
            contact_phone, payment_phone, bank, user_id
        ))
        DB.commit()
        if cur.rowcount == 0:
            return jsonify({"success": False, "message": "Пользователь не найден"}), 404
        return jsonify({"success": True, "message": "Водитель успешно создан!"})
    except Exception as e:
        print(f"Ошибка при создании водителя: {e}")
        return jsonify({"success": False, "message": "Внутренняя ошибка сервера"}), 500

@app.route('/api/admin/ban', methods=['POST'])
def ban_user():
    try:
        data = request.get_json()
        user_id = data.get('user_id')
        if not user_id:
            return jsonify({"success": False, "message": "Не указан ID пользователя"}), 400
        cur = DB.cursor()
        cur.execute("UPDATE users SET is_banned = 1 WHERE telegram_id = ?", (user_id,))
        DB.commit()
        if cur.rowcount == 0:
            return jsonify({"success": False, "message": "Пользователь не найден"}), 404
        return jsonify({"success": True, "message": "Пользователь заблокирован"})
    except Exception as e:
        print(f"Ошибка при блокировке: {e}")
        return jsonify({"success": False, "message": "Внутренняя ошибка"}), 500

@app.route('/api/admin/unban', methods=['POST'])
def unban_user():
    try:
        data = request.get_json()
        user_id = data.get('user_id')
        if not user_id:
            return jsonify({"success": False, "message": "Не указан ID пользователя"}), 400
        cur = DB.cursor()
        cur.execute("UPDATE users SET is_banned = 0 WHERE telegram_id = ?", (user_id,))
        DB.commit()
        if cur.rowcount == 0:
            return jsonify({"success": False, "message": "Пользователь не найден"}), 404
        return jsonify({"success": True, "message": "Пользователь разблокирован"})
    except Exception as e:
        print(f"Ошибка при разблокировке: {e}")
        return jsonify({"success": False, "message": "Внутренняя ошибка"}), 500

@app.route('/api/admin/delete_driver', methods=['POST'])
def delete_driver():
    try:
        data = request.get_json()
        user_id = data.get('user_id')
        if not user_id:
            return jsonify({"success": False, "message": "Не указан ID пользователя"}), 400
        cur = DB.cursor()
        cur.execute("UPDATE users SET role = 'passenger', full_name = NULL, car_brand = NULL, car_model = NULL, license_plate = NULL, car_color = NULL, phone_number = NULL, payment_number = NULL, bank_name = NULL WHERE telegram_id = ? AND role = 'driver'", (user_id,))
        DB.commit()
        if cur.rowcount == 0:
            return jsonify({"success": False, "message": "Водитель не найден"}), 404
        return jsonify({"success": True, "message": "Водитель удалён"})
    except Exception as e:
        print(f"Ошибка при удалении водителя: {e}")
        return jsonify({"success": False, "message": "Внутренняя ошибка"}), 500

@app.route('/api/admin/cancel_order', methods=['POST'])
def cancel_order():
    try:
        data = request.get_json()
        order_id = data.get('order_id')
        if not order_id:
            return jsonify({"success": False, "message": "Не указан ID заказа"}), 400
        cur = DB.cursor()
        cur.execute("UPDATE trips SET status = 'cancelled' WHERE id = ?", (order_id,))
        DB.commit()
        if cur.rowcount == 0:
            return jsonify({"success": False, "message": "Заказ не найден"}), 404
        return jsonify({"success": True, "message": "Заказ отменён"})
    except Exception as e:
        print(f"Ошибка при отмене заказа: {e}")
        return jsonify({"success": False, "message": "Внутренняя ошибка"}), 500

# === TELEGRAM BOT ===
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(storage=MemoryStorage())

class UserState(StatesGroup):
    entering_pickup = State()
    entering_destination = State()

def get_user_role(telegram_id):
    cur = DB.cursor()
    res = cur.execute("SELECT role FROM users WHERE telegram_id = ?", (telegram_id,)).fetchone()
    return res[0] if res else None

def save_user(telegram_id, username, first_name):
    cur = DB.cursor()
    cur.execute("INSERT OR IGNORE INTO users (telegram_id, username, first_name) VALUES (?, ?, ?)", (telegram_id, username, first_name))
    DB.commit()

def create_trip(passenger_id, pickup, destination):
    cur = DB.cursor()
    cur.execute("INSERT INTO trips (passenger_id, pickup, destination) VALUES (?, ?, ?)", (passenger_id, pickup, destination))
    trip_id = cur.lastrowid
    DB.commit()
    return trip_id

def update_passenger_message_id(trip_id, message_id):
    cur = DB.cursor()
    cur.execute("UPDATE trips SET passenger_message_id = ? WHERE id = ?", (message_id, trip_id))
    DB.commit()

def update_driver_message_id(trip_id, message_id):
    cur = DB.cursor()
    cur.execute("UPDATE trips SET driver_message_id = ? WHERE id = ?", (message_id, trip_id))
    DB.commit()

def get_trip(trip_id):
    cur = DB.cursor()
    return cur.execute("SELECT * FROM trips WHERE id = ?", (trip_id,)).fetchone()

def assign_driver_to_trip(trip_id, driver_id):
    cur = DB.cursor()
    cur.execute("UPDATE trips SET driver_id = ?, status = 'accepted', accepted_at = datetime('now') WHERE id = ? AND status = 'requested'", (driver_id, trip_id))
    updated = cur.rowcount
    DB.commit()
    return updated > 0

def mark_arrived(trip_id):
    cur = DB.cursor()
    cur.execute("UPDATE trips SET status = 'in_progress', arrived_at = datetime('now') WHERE id = ?", (trip_id,))
    DB.commit()

def complete_trip(trip_id):
    cur = DB.cursor()
    cur.execute("UPDATE trips SET status = 'completed', completed_at = datetime('now') WHERE id = ?", (trip_id,))
    DB.commit()

def cancel_trip(trip_id, reason="cancelled"):
    cur = DB.cursor()
    cur.execute("UPDATE trips SET status = ? WHERE id = ?", (reason, trip_id))
    DB.commit()
    # Удаляем сообщение у пассажира и водителя
    trip = get_trip(trip_id)
    if trip:
        asyncio.create_task(safe_delete_message(trip[1], trip[11]))
        asyncio.create_task(safe_delete_message(trip[2], trip[12]))

async def safe_delete_message(chat_id, message_id):
    if not chat_id or not message_id:
        return
    try:
        await bot.delete_message(chat_id=chat_id, message_id=message_id)
    except Exception as e:
        print(f"Не удалось удалить сообщение {message_id} у {chat_id}: {e}")

def get_all_drivers():
    cur = DB.cursor()
    return cur.execute("SELECT telegram_id FROM users WHERE role = 'driver' AND is_banned = 0").fetchall()

def get_tariffs():
    cur = DB.cursor()
    return cur.execute("SELECT id, name, price FROM tariffs").fetchall()

def get_passenger_menu():
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="🚕 Вызвать такси")],
            [KeyboardButton(text="📞 Контакты")]
        ],
        resize_keyboard=True,
        one_time_keyboard=False
    )

@dp.message(Command("start"))
async def cmd_start(message: types.Message, state: FSMContext):
    save_user(message.from_user.id, message.from_user.username, message.from_user.first_name)
    welcome_text = (
        "Привет! 👋\n"
        "Ты можешь вызвать такси прямо здесь — в Telegram, без приложений и регистрации!\n"
        "✅ Просто напиши, откуда и куда едешь\n"
        "✅ Отменить можно в любой момент\n"
        "✅ После поездки — оцени водителя\n"
        "Все водители проверены: указаны авто, гос. номер, телефон и реквизиты для оплаты.\n"
        "Без скрытых комиссий. Без задержек. Только комфорт!\n"
        "Нажми «🚕 Вызвать такси» — и поехали! 🚗💨"
    )
    await message.answer(welcome_text, reply_markup=get_passenger_menu())

@dp.message(Command("contacts"))
async def cmd_contacts(message: types.Message):
    contact_info = (
        "Если у вас есть жалоба или предложение, вы можете написать или позвонить по номеру телефона:\n"
        "📞 +7 (XXX) XXX-XX-XX\n"
        "Мы всегда рады улучшать нашу службу такси! 🙏"
    )
    await message.answer(contact_info)

@dp.message(lambda message: message.text == "🚕 Вызвать такси")
async def order_taxi(message: types.Message, state: FSMContext):
    if get_user_role(message.from_user.id) != "passenger":
        await message.answer("Только пассажиры могут заказывать такси.")
        return
    await message.answer("📍 Отправьте точку отправления:")
    await state.set_state(UserState.entering_pickup)

@dp.message(lambda message: message.text == "📞 Контакты")
async def contacts_button(message: types.Message):
    await cmd_contacts(message)

@dp.message(UserState.entering_pickup)
async def enter_pickup(message: types.Message, state: FSMContext):
    if not message.text:
        await message.answer("📍 Пожалуйста, отправьте точку отправления текстом.")
        return
    await state.update_data(pickup=message.text)
    await message.answer("📍 Отправьте пункт назначения:")
    await state.set_state(UserState.entering_destination)

@dp.message(UserState.entering_destination)
async def enter_destination(message: types.Message, state: FSMContext):
    if not message.text:
        await message.answer("📍 Пожалуйста, отправьте пункт назначения текстом.")
        return
    data = await state.get_data()
    pickup = data["pickup"]
    destination = message.text
    trip_id = create_trip(message.from_user.id, pickup, destination)
    sent_passenger = await message.answer(
        f"✅ Ваш заказ создан!\nОт: {pickup}\nКуда: {destination}\nОжидайте подтверждения от водителя.",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="❌ Отменить заказ", callback_data=f"cancel_{trip_id}")]
        ])
    )
    update_passenger_message_id(trip_id, sent_passenger.message_id)
    drivers = get_all_drivers()
    if not drivers:
        await message.answer("❌ Нет активных водителей.")
    else:
        for (driver_id,) in drivers:
            sent_driver = await bot.send_message(
                driver_id,
                f"🚕 Новый заказ!\nОт: {pickup}\nКуда: {destination}\nПримите заказ?",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="✅ Принять", callback_data=f"accept_{trip_id}")],
                    [InlineKeyboardButton(text="❌ Отклонить", callback_data=f"reject_{trip_id}")]
                ])
            )
            update_driver_message_id(trip_id, sent_driver.message_id)
    await state.clear()

@dp.callback_query(lambda c: c.data.startswith("accept_"))
async def accept_trip(callback: types.CallbackQuery):
    trip_id = int(callback.data.split("_")[1])
    if assign_driver_to_trip(trip_id, callback.from_user.id):
        cur = DB.cursor()
        driver = cur.execute("SELECT full_name, car_brand, car_model, license_plate, phone_number FROM users WHERE telegram_id = ?", (callback.from_user.id,)).fetchone()
        if driver:
            driver_info = f"Водитель: {driver[0]}\nАвто: {driver[1]} {driver[2]} ({driver[3]})\nТелефон: {driver[4]}"
        else:
            driver_info = f"Водитель: @{callback.from_user.username or callback.from_user.id}"
        trip = get_trip(trip_id)
        try:
            await bot.send_message(trip[1], f"✅ {driver_info}\nПринял ваш заказ!")
        except:
            pass
        tariffs = get_tariffs()
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text=f"{name} — {price} ₽", callback_data=f"setfare_{trip_id}_{price}")]
            for _, name, price in tariffs
        ])
        await bot.send_message(callback.from_user.id, "Выберите тариф для поездки:", reply_markup=kb)
        await callback.message.edit_text("✅ Вы приняли заказ! Выберите тариф.")
    else:
        await callback.message.edit_text("⚠️ Заказ уже принят другим водителем.")
    await callback.answer()

@dp.callback_query(lambda c: c.data.startswith("setfare_"))
async def set_fare(callback: types.CallbackQuery):
    _, trip_id_str, fare_str = callback.data.split("_")
    trip_id = int(trip_id_str)
    fare = float(fare_str)
    cur = DB.cursor()
    cur.execute("UPDATE trips SET fare = ? WHERE id = ?", (fare, trip_id))
    DB.commit()
    trip = get_trip(trip_id)
    passenger_id = trip[1]
    await bot.send_message(passenger_id, f"💰 Стоимость: {fare} ₽\nВодитель скоро приедет!")
    time_kb = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="5 мин", callback_data=f"eta_{trip_id}_5"),
            InlineKeyboardButton(text="10 мин", callback_data=f"eta_{trip_id}_10"),
            InlineKeyboardButton(text="15 мин", callback_data=f"eta_{trip_id}_15")
        ],
        [
            InlineKeyboardButton(text="20 мин", callback_data=f"eta_{trip_id}_20"),
            InlineKeyboardButton(text="30 мин", callback_data=f"eta_{trip_id}_30"),
            InlineKeyboardButton(text=">30 мин", callback_data=f"eta_{trip_id}_60")
        ]
    ])
    await bot.send_message(callback.from_user.id, "⏱️ Укажите ориентировочное время прибытия:", reply_markup=time_kb)
    await callback.message.edit_text(f"✅ Стоимость установлена: {fare} ₽")

@dp.callback_query(lambda c: c.data.startswith("eta_"))
async def set_eta(callback: types.CallbackQuery):
    _, trip_id_str, minutes_str = callback.data.split("_")
    trip_id = int(trip_id_str)
    minutes = int(minutes_str)
    trip = get_trip(trip_id)
    if not trip or not trip[1]:
        await callback.answer("Заказ не найден.", show_alert=True)
        return
    passenger_id = trip[1]
    text = f"Водитель прибудет на место через {minutes} минут" if minutes != 60 else "Водитель прибудет на место более чем через 30 минут"
    try:
        await bot.send_message(passenger_id, text)
    except:
        pass
    await callback.message.edit_text(f"✅ Время прибытия отправлено пассажиру.\n{text}")
    await callback.answer()

@dp.callback_query(lambda c: c.data.startswith("arrived_"))
async def confirm_arrival(callback: types.CallbackQuery):
    trip_id = int(callback.data.split("_")[1])
    mark_arrived(trip_id)
    trip = get_trip(trip_id)
    try:
        await bot.send_message(trip[1], "🚗 Водитель подтвердил прибытие! Поездка началась.")
    except:
        pass
    await callback.answer()

@dp.callback_query(lambda c: c.data.startswith("complete_"))
async def complete_ride(callback: types.CallbackQuery):
    trip_id = int(callback.data.split("_")[1])
    complete_trip(trip_id)
    trip = get_trip(trip_id)
    passenger_id = trip[1]
    if trip[11]:
        try:
            await bot.delete_message(chat_id=passenger_id, message_id=trip[11])
        except:
            pass
    try:
        await bot.send_message(
            passenger_id,
            "🏁 Поездка завершена! Оцените водителя:",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="⭐", callback_data=f"rate_{trip_id}_1")],
                [InlineKeyboardButton(text="⭐⭐", callback_data=f"rate_{trip_id}_2")],
                [InlineKeyboardButton(text="⭐⭐⭐", callback_data=f"rate_{trip_id}_3")],
                [InlineKeyboardButton(text="⭐⭐⭐⭐", callback_data=f"rate_{trip_id}_4")],
                [InlineKeyboardButton(text="⭐⭐⭐⭐⭐", callback_data=f"rate_{trip_id}_5")]
            ])
        )
    except:
        pass
    await callback.answer()

@dp.callback_query(lambda c: c.data.startswith("rate_"))
async def rate_driver(callback: types.CallbackQuery):
    _, trip_id_str, rating_str = callback.data.split("_")
    trip_id = int(trip_id_str)
    rating = int(rating_str)
    trip = get_trip(trip_id)
    if not trip or trip[1] != callback.from_user.id:
        await callback.answer("Ошибка.", show_alert=True)
        return
    cur = DB.cursor()
    cur.execute("INSERT OR IGNORE INTO ratings (trip_id, driver_id, passenger_id, rating) VALUES (?, ?, ?, ?)", (trip_id, trip[2], trip[1], rating))
    DB.commit()
    await callback.message.edit_text(f"Спасибо за оценку! ⭐{rating}")
    await callback.message.answer("Спасибо за поездку!", reply_markup=get_passenger_menu())
    await callback.answer()

@dp.callback_query(lambda c: c.data.startswith("cancel_") and not c.data.startswith("cancel_driver_"))
async def cancel_order(callback: types.CallbackQuery):
    try:
        trip_id = int(callback.data.split("_")[1])
    except (ValueError, IndexError):
        await callback.answer("Некорректный запрос.", show_alert=True)
        return
    trip = get_trip(trip_id)
    if not trip or trip[1] != callback.from_user.id:
        await callback.answer("Это не ваш заказ.", show_alert=True)
        return
    if trip[3] in ("completed", "cancelled", "expired"):
        await callback.answer("Заказ уже завершён.", show_alert=True)
        return
    cancel_trip(trip_id, "cancelled_by_passenger")
    await callback.answer("Заказ отменён.")

@dp.callback_query(lambda c: c.data.startswith("cancel_driver_"))
async def cancel_by_driver(callback: types.CallbackQuery):
    try:
        trip_id = int(callback.data.split("_")[2])
    except (ValueError, IndexError):
        await callback.answer("Некорректный запрос.", show_alert=True)
        return
    cancel_trip(trip_id, "cancelled_by_driver")
    trip = get_trip(trip_id)
    if trip and trip[1]:
        try:
            await bot.send_message(trip[1], "❌ Водитель отменил ваш заказ.")
        except:
            pass
    await callback.answer("Заказ отменён.")

@dp.callback_query(lambda c: c.data.startswith("reject_"))
async def reject_trip(callback: types.CallbackQuery):
    await callback.message.edit_text("❌ Вы отклонили заказ.")
    await callback.answer()

async def update_passenger_order_message(trip_id):
    trip = get_trip(trip_id)
    if not trip or not trip[11]:
        return
    passenger_id = trip[1]
    status_text = {
        "requested": "Ожидает подтверждения",
        "accepted": "✅ Принят водителем",
        "in_progress": "🚗 Водитель в пути",
        "completed": "🏁 Поездка завершена",
        "cancelled": "❌ Отменён",
        "expired": "🕒 Авто-отмена"
    }.get(trip[3], trip[3])
    text = f"Ваш заказ:\nОт: {trip[4]}\nКуда: {trip[5]}\nСтатус: {status_text}"
    if trip[6]:
        text += f"\nСтоимость: {trip[6]} ₽"
    buttons = []
    if trip[3] in ("requested", "accepted", "in_progress"):
        buttons = [[InlineKeyboardButton(text="❌ Отменить заказ", callback_data=f"cancel_{trip_id}")]]
    try:
        await bot.edit_message_text(
            chat_id=passenger_id,
            message_id=trip[11],
            text=text,
            reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons) if buttons else None
        )
    except:
        pass

async def update_driver_order_message(trip_id):
    trip = get_trip(trip_id)
    if not trip or not trip[12]:
        return
    driver_id = trip[2]
    status_text = {
        "accepted": "✅ Заказ принят",
        "in_progress": "🚗 В пути",
        "completed": "🏁 Поездка завершена",
        "cancelled": "❌ Отменён",
        "expired": "🕒 Авто-отмена"
    }.get(trip[3], "Заказ создан")
    text = f"Ваш заказ:\nОт: {trip[4]}\nКуда: {trip[5]}\nСтатус: {status_text}"
    if trip[6]:
        text += f"\nСтоимость: {trip[6]} ₽"
    buttons = []
    if trip[3] == "accepted":
        buttons = [
            [InlineKeyboardButton(text="🚗 Я на месте", callback_data=f"arrived_{trip_id}")],
            [InlineKeyboardButton(text="🏁 Завершить", callback_data=f"complete_{trip_id}")],
            [InlineKeyboardButton(text="❌ Отменить поездку", callback_data=f"cancel_driver_{trip_id}")]
        ]
    elif trip[3] == "in_progress":
        buttons = [
            [InlineKeyboardButton(text="🏁 Завершить", callback_data=f"complete_{trip_id}")],
            [InlineKeyboardButton(text="❌ Отменить поездку", callback_data=f"cancel_driver_{trip_id}")]
        ]
    try:
        await bot.edit_message_text(
            chat_id=driver_id,
            message_id=trip[12],
            text=text,
            reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons) if buttons else None
        )
    except:
        pass

# === BACKGROUND TASKS ===
async def process_broadcast_queue():
    while True:
        try:
            if not BROADCAST_QUEUE.empty():
                task = BROADCAST_QUEUE.get()
                user_ids = task["user_ids"]
                message_text = task["message_text"]
                success_count = 0
                for user_id in user_ids:
                    try:
                        await bot.send_message(chat_id=user_id, text=message_text)
                        success_count += 1
                    except TelegramForbiddenError:
                        print(f"Пользователь {user_id} заблокировал бота")
                    except TelegramRetryAfter as e:
                        await asyncio.sleep(e.retry_after)
                        await bot.send_message(chat_id=user_id, text=message_text)
                        success_count += 1
                    except TelegramAPIError as e:
                        print(f"Ошибка отправки {user_id}: {e}")
                print(f"Рассылка завершена: {success_count}/{len(user_ids)}")
            await asyncio.sleep(1)
        except Exception as e:
            print(f"Ошибка в фоновой рассылке: {e}")
            await asyncio.sleep(5)

async def cancel_expired_orders():
    while True:
        try:
            cur = DB.cursor()
            cur.execute("""
                SELECT id, passenger_id, passenger_message_id, driver_message_id
                FROM trips
                WHERE status = 'requested'
                  AND datetime(created_at) < datetime('now', '-{} minutes')
            """.format(ORDER_TIMEOUT))
            expired = cur.fetchall()
            for trip_id, passenger_id, p_msg_id, d_msg_id in expired:
                cancel_trip(trip_id, "expired")
                try:
                    await bot.send_message(
                        passenger_id,
                        f"🕒 Ваш заказ #{trip_id} автоматически отменён: никто из водителей не принял его в течение {ORDER_TIMEOUT} минут."
                    )
                except Exception as e:
                    print(f"Не удалось отправить уведомление пассажиру {passenger_id}: {e}")
        except Exception as e:
            print(f"Ошибка в cancel_expired_orders: {e}")
        await asyncio.sleep(30)

# === MAIN ===
def run_flask():
    app.run(host="0.0.0.0", port=5000, debug=False)

async def main():
    asyncio.create_task(process_broadcast_queue())
    asyncio.create_task(cancel_expired_orders())
    await dp.start_polling(bot)

if __name__ == "__main__":
    flask_thread = threading.Thread(target=run_flask, daemon=True)
    flask_thread.start()
    print("🚀 Бот и дашборд запущены.")
    asyncio.run(main())