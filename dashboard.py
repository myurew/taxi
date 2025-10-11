# dashboard.py
import secrets
import sqlite3
from flask import Flask, render_template_string, jsonify, request, session
from db_utils import (
    DB, get_all_drivers, get_tariffs, get_trip, get_user_role,
    add_cancellation_reason, update_cancellation_reason, delete_cancellation_reason,
    ban_user, unban_user, get_cancellation_reasons, get_ban_info
)

ADMIN_USERNAME = "admin"
ADMIN_PASSWORD = "admin123"

DASHBOARD_HTML = r"""
<!DOCTYPE html>
<html lang="ru">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>🚕 Такси — Админка</title>
    <link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700&display=swap">
    <style>
        :root {
            --primary: #2563eb;
            --primary-dark: #1d4ed8;
            --success: #059669;
            --danger: #dc2626;
            --warning: #d97706;
            --gray-50: #f9fafb;
            --gray-100: #f3f4f6;
            --gray-200: #e5e7eb;
            --gray-300: #d1d5db;
            --gray-400: #9ca3af;
            --gray-500: #6b7280;
            --gray-600: #4b5563;
            --gray-700: #374151;
            --gray-800: #1f2937;
            --gray-900: #111827;
            --white: #ffffff;
            --shadow: 0 1px 3px 0 rgba(0, 0, 0, 0.1), 0 1px 2px 0 rgba(0, 0, 0, 0.06);
            --shadow-md: 0 4px 6px -1px rgba(0, 0, 0, 0.1), 0 2px 4px -1px rgba(0, 0, 0, 0.06);
            --border: 1px solid var(--gray-300);
        }
        * {
            margin: 0;
            padding: 0;
            box-sizing: border-box;
        }
        body {
            font-family: 'Inter', -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            background-color: var(--gray-50);
            color: var(--gray-900);
            line-height: 1.5;
            font-size: 14px;
        }
        .container {
            max-width: 1400px;
            margin: 0 auto;
            padding: 20px;
        }
        /* Header */
        header {
            background: var(--white);
            border: var(--border);
            padding: 20px 24px;
            margin-bottom: 24px;
            box-shadow: var(--shadow);
        }
        header h1 {
            font-weight: 700;
            font-size: 24px;
            color: var(--primary);
            text-align: center;
        }
        /* Cards */
        .card {
            background: var(--white);
            border: var(--border);
            padding: 24px;
            margin-bottom: 24px;
            box-shadow: var(--shadow);
        }
        .card h2 {
            font-size: 20px;
            font-weight: 600;
            margin-bottom: 20px;
            color: var(--gray-900);
        }
        .card h3 {
            font-size: 18px;
            font-weight: 600;
            margin-bottom: 16px;
            color: var(--gray-800);
        }
        .card h4 {
            font-size: 16px;
            font-weight: 600;
            margin-bottom: 12px;
            color: var(--gray-700);
        }
        /* Tabs */
        .tabs {
            display: flex;
            background: var(--white);
            border: var(--border);
            border-bottom: none;
            margin-bottom: 0;
            overflow-x: auto;
        }
        .tab {
            padding: 12px 24px;
            background: var(--white);
            border: none;
            border-right: var(--border);
            cursor: pointer;
            font-weight: 500;
            font-size: 14px;
            color: var(--gray-600);
            transition: all 0.15s ease;
            white-space: nowrap;
            flex-shrink: 0;
        }
        .tab:hover {
            background: var(--gray-50);
            color: var(--gray-800);
        }
        .tab.active {
            background: var(--primary);
            color: var(--white);
            font-weight: 600;
        }
        .tab-content {
            display: none;
            background: var(--white);
            border: var(--border);
            border-top: none;
        }
        .tab-content.active {
            display: block;
        }
        /* Statistics Grid */
        .stats {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
            gap: 16px;
            margin-bottom: 24px;
        }
        .stat-card {
            background: var(--white);
            border: var(--border);
            padding: 20px;
            text-align: center;
        }
        .stat-card h3 {
            font-size: 28px;
            color: var(--primary);
            margin: 8px 0 4px;
            font-weight: 700;
        }
        .stat-card div {
            font-size: 14px;
            color: var(--gray-600);
            font-weight: 500;
        }
        /* Tables */
        .table-container {
            overflow-x: auto;
            margin: 0 -24px;
            padding: 0 24px;
        }
        table {
            width: 100%;
            border-collapse: collapse;
            margin-top: 16px;
            min-width: 800px;
        }
        th, td {
            padding: 12px;
            text-align: left;
            border-bottom: var(--border);
            border-right: var(--border);
            white-space: nowrap;
            font-size: 13px;
        }
        th {
            background: var(--gray-50);
            font-weight: 600;
            color: var(--gray-800);
            border-bottom: 2px solid var(--gray-300);
            position: sticky;
            top: 0;
        }
        th:last-child, td:last-child {
            border-right: none;
        }
        tr:hover td {
            background-color: var(--gray-50);
        }
        /* Buttons */
        .btn {
            padding: 8px 16px;
            border: 1px solid;
            font-weight: 500;
            cursor: pointer;
            transition: all 0.15s ease;
            font-size: 13px;
            display: inline-flex;
            align-items: center;
            justify-content: center;
            gap: 6px;
            text-decoration: none;
            background: var(--white);
        }
        .btn-primary {
            background: var(--primary);
            border-color: var(--primary);
            color: var(--white);
        }
        .btn-success {
            background: var(--success);
            border-color: var(--success);
            color: var(--white);
        }
        .btn-danger {
            background: var(--danger);
            border-color: var(--danger);
            color: var(--white);
        }
        .btn-warning {
            background: var(--warning);
            border-color: var(--warning);
            color: var(--white);
        }
        .btn-outline {
            background: transparent;
            border-color: var(--gray-300);
            color: var(--gray-700);
        }
        .btn:hover {
            opacity: 0.9;
            transform: translateY(-1px);
        }
        .btn-sm {
            padding: 6px 12px;
            font-size: 12px;
        }
        /* Forms */
        .form-group {
            margin: 16px 0;
        }
        label {
            display: block;
            margin-bottom: 6px;
            font-weight: 500;
            font-size: 13px;
            color: var(--gray-700);
        }
        input, select, textarea {
            width: 100%;
            padding: 10px 12px;
            border: var(--border);
            background: var(--white);
            font-size: 14px;
            font-family: inherit;
            transition: border-color 0.15s ease;
        }
        input:focus, select:focus, textarea:focus {
            outline: none;
            border-color: var(--primary);
            box-shadow: 0 0 0 3px rgba(37, 99, 235, 0.1);
        }
        /* Utility */
        .hidden {
            display: none !important;
        }
        .message {
            padding: 12px 16px;
            margin: 16px 0;
            font-weight: 500;
            border: 1px solid;
        }
        .message.success {
            background: #f0fdf4;
            border-color: #bbf7d0;
            color: #166534;
        }
        .message.error {
            background: #fef2f2;
            border-color: #fecaca;
            color: #991b1b;
        }
        .actions {
            display: flex;
            gap: 4px;
            flex-wrap: wrap;
        }
        /* Status Badges */
        .status-badge {
            padding: 4px 8px;
            font-size: 11px;
            font-weight: 600;
            white-space: nowrap;
            border: 1px solid;
        }
        .status-completed {
            background: #f0fdf4;
            border-color: #bbf7d0;
            color: #166534;
        }
        .status-cancelled {
            background: #fef2f2;
            border-color: #fecaca;
            color: #991b1b;
        }
        .status-requested {
            background: #eff6ff;
            border-color: #bfdbfe;
            color: #1e40af;
        }
        .status-accepted, .status-in_progress {
            background: #fffbeb;
            border-color: #fed7aa;
            color: #92400e;
        }
        .status-expired {
            background: #faf5ff;
            border-color: #e9d5ff;
            color: #7c3aed;
        }
        /* Filters */
        .filters {
            display: flex;
            gap: 16px;
            margin-bottom: 20px;
            flex-wrap: wrap;
            align-items: end;
            padding: 16px;
            background: var(--gray-50);
            border: var(--border);
            border-bottom: none;
        }
        .filter-group {
            display: flex;
            flex-direction: column;
            min-width: 150px;
        }
        .filter-group label {
            margin-bottom: 6px;
            font-weight: 500;
            font-size: 13px;
        }
        /* Radio and Checkbox */
        input[type="radio"], input[type="checkbox"] {
            width: auto;
            margin-right: 8px;
        }
        .radio-group {
            display: flex;
            gap: 16px;
            flex-wrap: wrap;
        }
        .radio-group label {
            display: flex;
            align-items: center;
            font-weight: normal;
            margin-bottom: 0;
        }
        /* Footer */
        footer {
            text-align: center;
            margin-top: 40px;
            color: var(--gray-500);
            font-size: 13px;
            padding: 16px;
            border-top: var(--border);
        }
        /* Responsive */
        @media (max-width: 768px) {
            .container {
                padding: 16px;
            }
            .tabs {
                flex-direction: column;
            }
            .tab {
                border-right: none;
                border-bottom: var(--border);
            }
            .stats {
                grid-template-columns: 1fr;
            }
            th, td {
                padding: 8px;
                font-size: 12px;
            }
            .btn {
                padding: 6px 12px;
                font-size: 12px;
            }
            .filters {
                flex-direction: column;
            }
            .filter-group {
                min-width: 100%;
            }
        }
        @media (max-width: 1024px) {
            table {
                min-width: 600px;
            }
        }
        /* Form layouts */
        .form-grid {
            display: grid;
            grid-template-columns: 1fr 1fr;
            gap: 16px;
        }
        .form-full {
            grid-column: 1 / -1;
        }
        /* Chart styling */
        .chart-container {
            height: 200px;
            background: var(--gray-50);
            border: var(--border);
            padding: 16px;
            margin-top: 16px;
        }
        /* Empty states */
        .empty-state {
            text-align: center;
            padding: 40px 20px;
            color: var(--gray-500);
        }
        /* Header actions */
        .header-actions {
            display: flex;
            justify-content: space-between;
            align-items: center;
            margin-bottom: 16px;
            flex-wrap: wrap;
            gap: 10px;
        }
        /* Loading indicator */
        .loading {
            display: inline-block;
            width: 20px;
            height: 20px;
            border: 2px solid var(--gray-300);
            border-top: 2px solid var(--primary);
            animation: spin 1s linear infinite;
        }
        @keyframes spin {
            0% { transform: rotate(0deg); }
            100% { transform: rotate(360deg); }
        }
    </style>
</head>
<body>
<div class="container">
    <!-- Экран входа -->
    <div id="auth-screen" class="card">
        <h2>🔐 Вход для администратора</h2>
        <div class="form-group">
            <label>Логин</label>
            <input type="text" id="login-username" value="admin" autocomplete="off">
        </div>
        <div class="form-group">
            <label>Пароль</label>
            <input type="password" id="login-password" value="admin123">
        </div>
        <button class="btn btn-primary" onclick="login()">Войти</button>
        <div id="login-message"></div>
    </div>
    <!-- Основной интерфейс -->
    <div id="main-app" class="hidden">
        <div class="header-actions">
            <div></div>
            <button class="btn btn-outline" onclick="logout()">🚪 Выйти</button>
        </div>
        <div class="tabs">
            <div class="tab active" data-tab="dashboard">📊 Обзор</div>
            <div class="tab" data-tab="users">👥 Пассажиры</div>
            <div class="tab" data-tab="drivers">🚗 Водители</div>
            <div class="tab" data-tab="orders">📋 Заказы</div>
            <div class="tab" data-tab="tariffs">💰 Тарифы</div>
            <div class="tab" data-tab="cancellation-reasons">📝 Причины отмены</div>
            <div class="tab" data-tab="bans">🚫 Баны</div>
            <div class="tab" data-tab="broadcast">📢 Рассылка</div>
        </div>
        <!-- Вкладка: Обзор -->
        <div class="tab-content active" id="tab-dashboard">
            <div class="stats" id="stats-container">
                <div class="stat-card">
                    <div>Пассажиры</div>
                    <h3>0</h3>
                </div>
                <div class="stat-card">
                    <div>Водители</div>
                    <h3>0</h3>
                </div>
                <div class="stat-card">
                    <div>Всего заказов</div>
                    <h3>0</h3>
                </div>
                <div class="stat-card">
                    <div>Завершено</div>
                    <h3>0</h3>
                </div>
                <div class="stat-card">
                    <div>Отменено</div>
                    <h3>0</h3>
                </div>
                <div class="stat-card">
                    <div>Выручка</div>
                    <h3>0 ₽</h3>
                </div>
            </div>
            <div class="card">
                <h3>🏆 Топ-5 водителей по заработку</h3>
                <div class="table-container">
                    <table id="top-drivers-table">
                        <thead>
                            <tr>
                                <th>ID</th>
                                <th>Имя</th>
                                <th>Завершено</th>
                                <th>Заработок</th>
                                <th>Рейтинг</th>
                            </tr>
                        </thead>
                        <tbody></tbody>
                    </table>
                </div>
            </div>
            <div class="card">
                <h3>📈 Доходы за последние 7 дней</h3>
                <div id="financial-chart" class="chart-container">
                    <div style="display: flex; justify-content: center; align-items: center; height: 100%; color: var(--gray-500);">
                        Загрузка графика...
                    </div>
                </div>
            </div>
        </div>
        <!-- Вкладка: Пассажиры -->
        <div class="tab-content" id="tab-users">
            <div class="card">
                <div class="header-actions">
                    <h3>👥 Список пассажиров</h3>
                    <div class="filters" style="margin: 0; padding: 0; border: none; background: none;">
                        <div class="filter-group">
                            <label>Статус</label>
                            <select id="user-status-filter" onchange="loadPassengers()">
                                <option value="all">Все</option>
                                <option value="active">Активные</option>
                                <option value="banned">Забанены</option>
                            </select>
                        </div>
                    </div>
                </div>
                <div class="table-container">
                    <table id="passengers-table">
                        <thead>
                            <tr>
                                <th>ID</th>
                                <th>Имя</th>
                                <th>Юзернейм</th>
                                <th>Дата регистрации</th>
                                <th>Статус</th>
                                <th>Действия</th>
                            </tr>
                        </thead>
                        <tbody></tbody>
                    </table>
                </div>
            </div>
        </div>
        <!-- Вкладка: Водители -->
        <div class="tab-content" id="tab-drivers">
            <div class="card">
                <div class="header-actions">
                    <h3>🚗 Управление водителями</h3>
                    <button class="btn btn-success" onclick="toggleCreateDriverForm()">+ Создать водителя</button>
                </div>
                <div id="create-driver-form" class="card hidden">
                    <h4 id="form-title">Создать водителя из существующего пользователя</h4>
                    <div class="form-grid">
                        <div class="form-group">
                            <label>Telegram ID *</label>
                            <input type="number" id="driver-user-id" readonly>
                        </div>
                        <div class="form-group">
                            <label>ФИО водителя *</label>
                            <input type="text" id="driver-name" placeholder="Иванов Иван Иванович">
                        </div>
                        <div class="form-group">
                            <label>Марка автомобиля *</label>
                            <input type="text" id="driver-car-brand" placeholder="Toyota">
                        </div>
                        <div class="form-group">
                            <label>Модель *</label>
                            <input type="text" id="driver-car-model" placeholder="Camry">
                        </div>
                        <div class="form-group">
                            <label>Гос. номер *</label>
                            <input type="text" id="driver-license-plate" placeholder="А123БВ777">
                        </div>
                        <div class="form-group">
                            <label>Телефон *</label>
                            <input type="text" id="driver-phone" placeholder="+7 (999) 123-45-67">
                        </div>
                        <div class="form-group form-full">
                            <label>Реквизиты (СБП / карта) *</label>
                            <input type="text" id="driver-payment" placeholder="79991234567">
                        </div>
                        <div class="form-group">
                            <label>Банк</label>
                            <input type="text" id="driver-bank" placeholder="Сбербанк">
                        </div>
                    </div>
                    <div style="display: flex; gap: 8px; margin-top: 20px;">
                        <button class="btn btn-success" onclick="createDriver()">Сохранить</button>
                        <button class="btn btn-outline" onclick="toggleCreateDriverForm()">Отмена</button>
                    </div>
                </div>
                <div class="filters">
                    <div class="filter-group">
                        <label>Статус бана</label>
                        <select id="driver-ban-filter" onchange="loadDrivers()">
                            <option value="all">Все</option>
                            <option value="banned">Забанены</option>
                            <option value="not_banned">Не забанены</option>
                        </select>
                    </div>
                    <div class="filter-group">
                        <label>Сортировка</label>
                        <select id="driver-sort" onchange="loadDrivers()">
                            <option value="earnings">По заработку</option>
                            <option value="rating">По рейтингу</option>
                            <option value="orders">По заказам</option>
                        </select>
                    </div>
                </div>
                <div class="table-container">
                    <table id="drivers-table">
                        <thead>
                            <tr>
                                <th>ID</th>
                                <th>Имя</th>
                                <th>Авто</th>
                                <th>Заказов</th>
                                <th>Заработок</th>
                                <th>Рейтинг</th>
                                <th>Статус</th>
                                <th>Действия</th>
                            </tr>
                        </thead>
                        <tbody></tbody>
                    </table>
                </div>
            </div>
        </div>
        <!-- Вкладка: Заказы -->
        <div class="tab-content" id="tab-orders">
            <div class="card">
                <h3>📋 Последние 50 заказов</h3>
                <div class="filters">
                    <div class="filter-group">
                        <label>Статус</label>
                        <select id="order-status-filter" onchange="loadOrders()">
                            <option value="all">Все</option>
                            <option value="completed">Завершены</option>
                            <option value="cancelled">Отменены</option>
                            <option value="active">Активные</option>
                        </select>
                    </div>
                    <div class="filter-group">
                        <label>Сортировка</label>
                        <select id="order-sort" onchange="loadOrders()">
                            <option value="newest">Сначала новые</option>
                            <option value="oldest">Сначала старые</option>
                            <option value="price">По цене</option>
                        </select>
                    </div>
                    <div class="filter-group">
                        <label>Период</label>
                        <select id="order-period" onchange="loadOrders()">
                            <option value="all">Все время</option>
                            <option value="today">Сегодня</option>
                            <option value="week">Неделя</option>
                            <option value="month">Месяц</option>
                        </select>
                    </div>
                </div>
                <div class="table-container">
                    <table id="orders-table">
                        <thead>
                            <tr>
                                <th>ID</th>
                                <th>Пассажир</th>
                                <th>Водитель</th>
                                <th>Откуда</th>
                                <th>Куда</th>
                                <th>Статус</th>
                                <th>Причина отмены</th>
                                <th>Цена</th>
                                <th>Создан</th>
                                <th>Действия</th>
                            </tr>
                        </thead>
                        <tbody></tbody>
                    </table>
                </div>
            </div>
        </div>
        <!-- Вкладка: Тарифы -->
        <div class="tab-content" id="tab-tariffs">
            <div class="card">
                <h3>💰 Управление тарифами</h3>
                <div class="form-grid">
                    <div class="form-group">
                        <label>Название тарифа</label>
                        <input type="text" id="new-tariff-name" placeholder="Эконом">
                    </div>
                    <div class="form-group">
                        <label>Цена (₽)</label>
                        <input type="number" step="0.01" id="new-tariff-price" placeholder="100.00">
                    </div>
                </div>
                <button class="btn btn-success" onclick="createTariff()">Добавить тариф</button>
                <div class="table-container" style="margin-top: 24px;">
                    <table id="tariffs-table">
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
        </div>
        <!-- Вкладка: Причины отмены -->
        <div class="tab-content" id="tab-cancellation-reasons">
            <div class="card">
                <h3>📝 Причины отмены</h3>
                <div class="form-grid">
                    <div class="form-group">
                        <label>Тип пользователя</label>
                        <select id="reason-user-type">
                            <option value="driver">Водитель</option>
                            <option value="passenger">Пассажир</option>
                        </select>
                    </div>
                    <div class="form-group">
                        <label>Текст причины *</label>
                        <input type="text" id="new-reason-text" placeholder="Долгое ожидание">
                    </div>
                </div>
                <button class="btn btn-success" onclick="addCancellationReason()">Добавить причину</button>
                <div class="table-container" style="margin-top: 24px;">
                    <table id="cancellation-reasons-table">
                        <thead>
                            <tr>
                                <th>ID</th>
                                <th>Тип</th>
                                <th>Текст</th>
                                <th>Действия</th>
                            </tr>
                        </thead>
                        <tbody></tbody>
                    </table>
                </div>
            </div>
        </div>
        <!-- Вкладка: Баны -->
        <div class="tab-content" id="tab-bans">
            <div class="card">
                <h3>🚫 Управление банами</h3>
                <div class="form-grid">
                    <div class="form-group">
                        <label>ID пользователя *</label>
                        <input type="number" id="ban-user-id" placeholder="123456789">
                    </div>
                    <div class="form-group">
                        <label>Причина *</label>
                        <input type="text" id="ban-reason" placeholder="Оскорбление">
                    </div>
                    <div class="form-group">
                        <label>Срок</label>
                        <select id="ban-duration">
                            <option value="1">1 день</option>
                            <option value="3">3 дня</option>
                            <option value="7" selected>7 дней</option>
                            <option value="30">30 дней</option>
                            <option value="">Навсегда</option>
                        </select>
                    </div>
                </div>
                <button class="btn btn-danger" onclick="banUserAdmin()">Забанить пользователя</button>
                <h4 style="margin: 24px 0 16px;">Активные баны</h4>
                <div class="table-container">
                    <table id="bans-table">
                        <thead>
                            <tr>
                                <th>ID</th>
                                <th>Пользователь</th>
                                <th>Причина</th>
                                <th>Дата бана</th>
                                <th>До</th>
                                <th>Действия</th>
                            </tr>
                        </thead>
                        <tbody></tbody>
                    </table>
                </div>
            </div>
        </div>
        <!-- Вкладка: Рассылка -->
        <div class="tab-content" id="tab-broadcast">
            <div class="card">
                <h3>📢 Массовая рассылка</h3>
                <div class="form-group">
                    <label>Получатели</label>
                    <div class="radio-group">
                        <label><input type="radio" name="broadcast-type" value="drivers" checked> Водителям</label>
                        <label><input type="radio" name="broadcast-type" value="passengers"> Пассажирам</label>
                        <label><input type="radio" name="broadcast-type" value="all"> Всем пользователям</label>
                    </div>
                </div>
                <div class="form-group">
                    <label>Текст сообщения *</label>
                    <textarea id="broadcast-message" rows="5" placeholder="Важное объявление для пользователей..."></textarea>
                </div>
                <button class="btn btn-primary" onclick="sendBroadcast()">Отправить рассылку</button>
                <div id="broadcast-result"></div>
            </div>
        </div>
    </div>
    <footer>
        Админ-панель службы такси • Обновлено: <span id="current-date"></span>
    </footer>
</div>
<script>
    const qs = (sel) => document.querySelector(sel);
    const qsa = (sel) => document.querySelectorAll(sel);
    let currentTab = 'dashboard';
    // Инициализация вкладок
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
    function loadTabData(tabName) {
        if (tabName === 'dashboard') loadDashboard();
        else if (tabName === 'users') loadPassengers();
        else if (tabName === 'drivers') loadDrivers();
        else if (tabName === 'orders') loadOrders();
        else if (tabName === 'tariffs') loadTariffs();
        else if (tabName === 'cancellation-reasons') loadCancellationReasons();
        else if (tabName === 'bans') loadBans();
    }
    // Аутентификация
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
    function logout() {
        fetch('/logout').then(() => checkAuth());
    }
    // API функции
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
    // Загрузка дашборда
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
                    <td>${d.avg_rating ? '⭐' + d.avg_rating : '—'}</td>
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
                    <div style="display: flex; flex-direction: column; align-items: center; margin: 0 6px;">
                        <div style="width: 32px; height: ${height}px; background: var(--primary); margin-bottom: 6px;"></div>
                        <small>${formattedDate}</small>
                        <small>${e.earnings.toFixed(0)} ₽</small>
                    </div>
                `;
            }).join('');
            container.innerHTML = `
                <h3>Доход за последние ${earnings.length} дней</h3>
                <div style="display: flex; justify-content: center; align-items: flex-end; height: 130px; background: var(--gray-100); padding: 12px; margin-top: 16px;">
                    ${chartHtml}
                </div>
            `;
        } catch (e) {
            console.error('Ошибка загрузки графика:', e);
        }
    }
    // === ПОЛНОСТЬЮ РАБОЧИЕ ФУНКЦИИ ДЛЯ ВАШЕГО БОТА ===
    async function loadPassengers() {
        try {
            const passengers = await apiCall('/api/admin/passengers');
            const tbody = qs('#passengers-table tbody');
            tbody.innerHTML = passengers.map(p => `
                <tr>
                    <td>${p.user_id}</td>
                    <td>${p.first_name || '—'}</td>
                    <td>@${p.username || '—'}</td>
                    <td>${new Date(p.registration_date).toLocaleDateString('ru-RU')}</td>
                    <td>${p.is_banned ? '🚫 Забанен' : '✅ Активен'}</td>
                    <td class="actions">
                        ${p.is_banned ?
                            `<button class="btn btn-sm btn-success" onclick="unbanUser(${p.user_id})">Разбанить</button>` :
                            `<button class="btn btn-sm btn-danger" onclick="showBanModal(${p.user_id})">Забанить</button>`
                        }
                        <button class="btn btn-sm btn-warning" onclick="makeDriver(${p.user_id})">Водитель</button>
                    </td>
                </tr>
            `).join('');
        } catch (e) {
            console.error('Ошибка загрузки пассажиров:', e);
        }
    }
    async function loadDrivers() {
        try {
            const banFilter = qs('#driver-ban-filter').value;
            const sortBy = qs('#driver-sort').value;
            const drivers = await apiCall('/api/drivers');
            let filteredDrivers = drivers;
            if (banFilter === 'banned') {
                filteredDrivers = drivers.filter(d => d.is_banned);
            } else if (banFilter === 'not_banned') {
                filteredDrivers = drivers.filter(d => !d.is_banned);
            }
            filteredDrivers.sort((a, b) => {
                if (sortBy === 'earnings') return b.total_earnings - a.total_earnings;
                else if (sortBy === 'rating') return (b.avg_rating || 0) - (a.avg_rating || 0);
                else if (sortBy === 'orders') return b.completed_orders - a.completed_orders;
                return 0;
            });
            const tbody = qs('#drivers-table tbody');
            tbody.innerHTML = filteredDrivers.map(d => {
                const displayName = d.name || d.first_name || `ID ${d.user_id}`;
                const carInfo = [d.car_brand, d.car_model, d.license_plate ? `(${d.license_plate})` : ''].filter(Boolean).join(' ');
                return `
                    <tr>
                        <td>${d.user_id}</td>
                        <td>${displayName}</td>
                        <td>${carInfo || '—'}</td>
                        <td>${d.completed_orders}</td>
                        <td>${(d.total_earnings || 0).toFixed(2)} ₽</td>
                        <td>${d.avg_rating ? '⭐' + d.avg_rating : '—'}</td>
                        <td>${d.is_banned ? '🚫 Забанен' : '✅ Активен'}</td>
                        <td class="actions">
                            ${d.is_banned ?
                                `<button class="btn btn-sm btn-success" onclick="unbanUser(${d.user_id})">Разбанить</button>` :
                                `<button class="btn btn-sm btn-danger" onclick="showBanModal(${d.user_id})">Забанить</button>`
                            }
                            <button class="btn btn-sm btn-danger" onclick="deleteDriver(${d.user_id})">Удалить</button>
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
            title.textContent = `Редактировать водителя ID ${userId}`;
        } else {
            qs('#driver-user-id').value = '';
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
    async function loadOrders() {
        try {
            const statusFilter = qs('#order-status-filter').value;
            const sortBy = qs('#order-sort').value;
            const data = await apiCall('/api/orders');
            let orders = data.recent_orders;
            if (statusFilter === 'completed') {
                orders = orders.filter(o => o.status === 'completed');
            } else if (statusFilter === 'cancelled') {
                orders = orders.filter(o => o.status.includes('cancelled') || o.status === 'expired');
            } else if (statusFilter === 'active') {
                orders = orders.filter(o => ['requested', 'accepted', 'in_progress'].includes(o.status));
            }
            orders.sort((a, b) => {
                if (sortBy === 'newest') return new Date(b.created_at) - new Date(a.created_at);
                else if (sortBy === 'oldest') return new Date(a.created_at) - new Date(b.created_at);
                else if (sortBy === 'price') return (b.price || 0) - (a.price || 0);
                return 0;
            });
            const tbody = qs('#orders-table tbody');
            const getStatusBadge = (status) => {
                const texts = {
                    'requested': 'Ожидает водителя',
                    'accepted': 'Принят водителем',
                    'in_progress': 'В пути',
                    'completed': 'Завершён',
                    'cancelled': 'Отменён админом',
                    'cancelled_by_passenger': 'Отменён пассажиром',
                    'cancelled_by_driver': 'Отменён водителем',
                    'expired': 'Авто-отмена (таймаут)'
                };
                const cls = {
                    'requested': 'status-requested',
                    'accepted': 'status-accepted',
                    'in_progress': 'status-in_progress',
                    'completed': 'status-completed',
                    'cancelled': 'status-cancelled',
                    'cancelled_by_passenger': 'status-cancelled',
                    'cancelled_by_driver': 'status-cancelled',
                    'expired': 'status-expired'
                };
                return `<span class="status-badge ${cls[status] || 'status-cancelled'}">${texts[status] || status}</span>`;
            };
            tbody.innerHTML = orders.map(o => {
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
                        <td>${getStatusBadge(o.status)}</td>
                        <td>${o.cancellation_reason || '—'}</td>
                        <td>${o.price ? o.price.toFixed(2) + ' ₽' : '—'}</td>
                        <td>${new Date(o.created_at).toLocaleString('ru-RU', { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' })}</td>
                        <td class="actions">
                            ${['requested', 'accepted', 'in_progress'].includes(o.status) ?
                                `<button class="btn btn-sm btn-danger" onclick="cancelOrder(${o.order_id})">Отменить</button>` : ''
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
        const reason = prompt("Укажите причину отмены заказа:");
        if (reason === null) return;
        if (!confirm(`Вы уверены, что хотите отменить заказ #${orderId}?`)) return;
        try {
            await apiCall('/api/admin/cancel_order', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ order_id: orderId, reason: reason })
            });
            alert('✅ Заказ отменён диспетчером');
            loadOrders();
        } catch (e) {
            alert('❌ Ошибка: ' + e.message);
        }
    }
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
                        <button class="btn btn-sm btn-warning" onclick="editTariff(${t.id}, '${t.name}', ${t.price})">✏️</button>
                        <button class="btn btn-sm btn-danger" onclick="deleteTariff(${t.id})">🗑️</button>
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
    async function loadCancellationReasons() {
        try {
            const reasons = await apiCall('/api/cancellation_reasons?user_type=all');
            const tbody = qs('#cancellation-reasons-table tbody');
            tbody.innerHTML = reasons.map(r => `
                <tr>
                    <td>${r.id}</td>
                    <td>${r.user_type === 'driver' ? '🚗 Водитель' : '👤 Пассажир'}</td>
                    <td>
                        <input type="text" value="${r.reason_text}" id="reason-${r.id}" 
                               onchange="updateCancellationReason(${r.id})" style="width: 100%;">
                    </td>
                    <td class="actions">
                        <button class="btn btn-sm btn-danger" onclick="deleteCancellationReason(${r.id})">🗑️</button>
                    </td>
                </tr>
            `).join('');
        } catch (e) {
            console.error('Ошибка загрузки причин:', e);
        }
    }
    async function addCancellationReason() {
        const userType = qs('#reason-user-type').value;
        const reasonText = qs('#new-reason-text').value.trim();
        if (!reasonText) {
            alert('Введите текст причины');
            return;
        }
        try {
            await apiCall('/api/cancellation_reasons', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ user_type: userType, reason_text: reasonText })
            });
            alert('✅ Причина добавлена');
            qs('#new-reason-text').value = '';
            loadCancellationReasons();
        } catch (e) {
            alert('❌ Ошибка: ' + e.message);
        }
    }
    async function updateCancellationReason(reasonId) {
        const reasonText = qs(`#reason-${reasonId}`).value.trim();
        if (!reasonText) {
            alert('Текст причины не может быть пустым');
            return;
        }
        try {
            await apiCall(`/api/cancellation_reasons/${reasonId}`, {
                method: 'PUT',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ reason_text: reasonText })
            });
            alert('✅ Причина обновлена');
        } catch (e) {
            alert('❌ Ошибка: ' + e.message);
        }
    }
    async function deleteCancellationReason(reasonId) {
        if (!confirm('Удалить причину?')) return;
        try {
            await apiCall(`/api/cancellation_reasons/${reasonId}`, { method: 'DELETE' });
            alert('✅ Причина удалена');
            loadCancellationReasons();
        } catch (e) {
            alert('❌ Ошибка: ' + e.message);
        }
    }
    async function loadBans() {
        try {
            const users = await apiCall('/api/admin/users');
            const bannedUsers = users.filter(u => u.is_banned);
            const tbody = qs('#bans-table tbody');
            tbody.innerHTML = bannedUsers.map(u => `
                <tr>
                    <td>${u.user_id}</td>
                    <td>${u.first_name || u.user_id} @${u.username || ''}</td>
                    <td>${u.ban_reason || '—'}</td>
                    <td>${new Date(u.banned_at).toLocaleDateString('ru-RU')}</td>
                    <td>${u.banned_until ? new Date(u.banned_until).toLocaleDateString('ru-RU') : 'Навсегда'}</td>
                    <td class="actions">
                        <button class="btn btn-sm btn-success" onclick="unbanUser(${u.user_id})">Разбанить</button>
                    </td>
                </tr>
            `).join('');
        } catch (e) {
            console.error('Ошибка загрузки банов:', e);
        }
    }
    async function banUserAdmin() {
        const userId = parseInt(qs('#ban-user-id').value);
        const reason = qs('#ban-reason').value.trim();
        const duration = qs('#ban-duration').value;
        const durationDays = duration ? parseInt(duration) : null;
        if (!userId || !reason) {
            alert('Заполните ID пользователя и причину');
            return;
        }
        if (!confirm(`Забанить пользователя ${userId}?`)) return;
        try {
            await apiCall('/api/admin/ban_user', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ 
                    user_id: userId, 
                    reason: reason, 
                    duration_days: durationDays 
                })
            });
            alert('✅ Пользователь забанен');
            qs('#ban-user-id').value = '';
            qs('#ban-reason').value = '';
            loadBans();
            if (currentTab === 'users') loadPassengers();
            if (currentTab === 'drivers') loadDrivers();
        } catch (e) {
            alert('❌ Ошибка: ' + e.message);
        }
    }
    function showBanModal(userId) {
        qs('#ban-user-id').value = userId;
        qs('#ban-reason').value = '';
        qs('#ban-duration').value = '7';
        qsa('.tab').forEach(t => t.classList.remove('active'));
        qs('.tab[data-tab="bans"]').classList.add('active');
        qsa('.tab-content').forEach(t => t.classList.remove('active'));
        qs('#tab-bans').classList.add('active');
        currentTab = 'bans';
        loadBans();
    }
    async function unbanUser(userId) {
        if (!confirm(`Разбанить пользователя ${userId}?`)) return;
        try {
            await apiCall('/api/admin/unban_user', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ user_id: userId })
            });
            alert('✅ Пользователь разбанен');
            loadBans();
            if (currentTab === 'users') loadPassengers();
            if (currentTab === 'drivers') loadDrivers();
        } catch (e) {
            alert('❌ Ошибка: ' + e.message);
        }
    }
    async function sendBroadcast() {
        const message = qs('#broadcast-message').value.trim();
        if (!message) {
            alert('Введите текст сообщения');
            return;
        }
        const type = document.querySelector('input[name="broadcast-type"]:checked').value;
        try {
            const result = await apiCall('/api/admin/send_message', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ 
                    message_text: message,
                    broadcast_type: type  // 'drivers', 'passengers', 'all'
                })
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
"""

def create_app():
    app = Flask(__name__)
    app.secret_key = secrets.token_hex(16)

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
        
        # === ИСПРАВЛЕННЫЙ ЗАПРОС: ТОЛЬКО ЗАВЕРШЁННЫЕ ЗАКАЗЫ, БЕЗ ДУБЛИРОВАНИЯ ===
        cur.execute('''
            SELECT 
                u.telegram_id, 
                u.full_name, 
                u.first_name,
                COUNT(t.id) as total_orders,
                COALESCE(SUM(t.fare), 0) as total_earnings,
                COALESCE(AVG(r.rating), 0) as avg_rating
            FROM users u
            LEFT JOIN trips t ON u.telegram_id = t.driver_id AND t.status = 'completed'
            LEFT JOIN ratings r ON t.id = r.trip_id
            WHERE u.role = 'driver'
            GROUP BY u.telegram_id, u.full_name, u.first_name
            ORDER BY total_earnings DESC
            LIMIT 5
        ''')
        top_drivers = cur.fetchall()
        top_drivers_list = []
        for d in top_drivers:
            user_id, full_name, first_name, total_orders, total_earnings, avg_rating = d
            top_drivers_list.append({
                "user_id": user_id,
                "name": full_name or first_name or f"ID {user_id}",
                "total_orders": total_orders or 0,
                "total_earnings": float(total_earnings) if total_earnings else 0.0,
                "avg_rating": round(avg_rating, 1) if avg_rating > 0 else None
            })
        
        return jsonify({
            "users": {
                "role_stats": {
                    "passenger": len([u for u in users if u[3] == 'passenger']),
                    "driver": len(drivers)
                }
            },
            "orders": {
                "total_stats": total_stats
            },
            "financial": {
                "daily_earnings": [],
                "top_drivers": top_drivers_list
            }
        })

    @app.route('/api/admin/users')
    def api_users():
        cur = DB.cursor()
        cur.execute('''
            SELECT u.*, b.reason as ban_reason, b.banned_until, b.banned_at
            FROM users u 
            LEFT JOIN bans b ON u.telegram_id = b.user_id
        ''')
        users = cur.fetchall()
        return jsonify([{
            "user_id": u[0], "username": u[1], "first_name": u[2], "role": u[3],
            "is_banned": bool(u[4]), "registration_date": u[13],
            "ban_reason": u[14], "banned_until": u[15], "banned_at": u[16]
        } for u in users])

    @app.route('/api/admin/passengers')
    def api_passengers():
        cur = DB.cursor()
        cur.execute('''
            SELECT u.*, b.reason as ban_reason, b.banned_until, b.banned_at
            FROM users u 
            LEFT JOIN bans b ON u.telegram_id = b.user_id
            WHERE u.role = 'passenger'
        ''')
        passengers = cur.fetchall()
        return jsonify([{
            "user_id": p[0],
            "username": p[1],
            "first_name": p[2],
            "is_banned": bool(p[4]),
            "registration_date": p[13],
            "ban_reason": p[14],
            "banned_until": p[15],
            "banned_at": p[16]
        } for p in passengers])

    @app.route('/api/admin/drivers_for_messaging')
    def api_drivers_for_messaging():
        cur = DB.cursor()
        drivers = cur.execute("SELECT * FROM users WHERE role = 'driver' AND is_banned = 0").fetchall()
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
                COUNT(DISTINCT t.id) as total_orders,
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
                t.id, t.passenger_id, t.driver_id, t.status, t.pickup, t.destination, t.fare, t.created_at, t.cancellation_reason,
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
                "cancellation_reason": o[8],
                "driver_name": o[9] or f"ID {o[2]}" if o[2] else None,
                "license_plate": o[10]
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
        if not name or price is None:
            return jsonify({"success": False, "message": "Название и цена обязательны"}), 400
        try:
            price = float(price)
            cur = DB.cursor()
            cur.execute("INSERT INTO tariffs (name, price) VALUES (?, ?)", (name, price))
            DB.commit()
            return jsonify({"success": True, "message": "Тариф добавлен"})
        except ValueError:
            return jsonify({"success": False, "message": "Цена должна быть числом"}), 400
        except sqlite3.IntegrityError:
            return jsonify({"success": False, "message": "Тариф с таким названием уже существует"}), 400

    @app.route('/api/tariffs/<int:tariff_id>', methods=['PUT'])
    def update_tariff(tariff_id):
        data = request.get_json()
        name = data.get('name')
        price = data.get('price')
        if not name or price is None:
            return jsonify({"success": False, "message": "Название и цена обязательны"}), 400
        try:
            price = float(price)
            cur = DB.cursor()
            cur.execute("UPDATE tariffs SET name = ?, price = ? WHERE id = ?", (name, price, tariff_id))
            DB.commit()
            if cur.rowcount == 0:
                return jsonify({"success": False, "message": "Тариф не найден"}), 404
            return jsonify({"success": True, "message": "Тариф обновлён"})
        except ValueError:
            return jsonify({"success": False, "message": "Цена должна быть числом"}), 400

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

    @app.route('/api/cancellation_reasons')
    def api_cancellation_reasons():
        user_type = request.args.get('user_type', 'all')
        cur = DB.cursor()
        if user_type == 'all':
            reasons = cur.execute("SELECT id, user_type, reason_text FROM cancellation_reasons").fetchall()
        else:
            reasons = cur.execute("SELECT id, user_type, reason_text FROM cancellation_reasons WHERE user_type = ?", (user_type,)).fetchall()
        return jsonify([{
            "id": r[0],
            "user_type": r[1],
            "reason_text": r[2]
        } for r in reasons])

    @app.route('/api/cancellation_reasons', methods=['POST'])
    def create_cancellation_reason():
        data = request.get_json()
        user_type = data.get('user_type')
        reason_text = data.get('reason_text')
        if not user_type or not reason_text:
            return jsonify({"success": False, "message": "Заполните все поля"}), 400
        try:
            reason_id = add_cancellation_reason(user_type, reason_text)
            return jsonify({"success": True, "message": "Причина добавлена", "id": reason_id})
        except Exception as e:
            return jsonify({"success": False, "message": f"Ошибка: {str(e)}"}), 500

    @app.route('/api/cancellation_reasons/<int:reason_id>', methods=['PUT'])
    def update_cancellation_reason_api(reason_id):
        data = request.get_json()
        reason_text = data.get('reason_text')
        if not reason_text:
            return jsonify({"success": False, "message": "Введите текст причины"}), 400
        try:
            update_cancellation_reason(reason_id, reason_text)
            return jsonify({"success": True, "message": "Причина обновлена"})
        except Exception as e:
            return jsonify({"success": False, "message": f"Ошибка: {str(e)}"}), 500

    @app.route('/api/cancellation_reasons/<int:reason_id>', methods=['DELETE'])
    def delete_cancellation_reason_api(reason_id):
        try:
            delete_cancellation_reason(reason_id)
            return jsonify({"success": True, "message": "Причина удалена"})
        except Exception as e:
            return jsonify({"success": False, "message": f"Ошибка: {str(e)}"}), 500

    @app.route('/api/admin/ban_user', methods=['POST'])
    def api_ban_user():
        data = request.get_json()
        user_id = data.get('user_id')
        reason = data.get('reason')
        duration_days = data.get('duration_days')
        if not user_id or not reason:
            return jsonify({"success": False, "message": "Заполните все поля"}), 400
        try:
            ban_user(user_id, reason, duration_days)
            # УДАЛЕН ИМПОРТ BOTA — рассылка работает через очередь
            return jsonify({"success": True, "message": "Пользователь забанен"})
        except Exception as e:
            return jsonify({"success": False, "message": f"Ошибка: {str(e)}"}), 500

    @app.route('/api/admin/unban_user', methods=['POST'])
    def api_unban_user():
        data = request.get_json()
        user_id = data.get('user_id')
        if not user_id:
            return jsonify({"success": False, "message": "Укажите ID пользователя"}), 400
        try:
            unban_user(user_id)
            # УДАЛЕН ИМПОРТ BOTA
            return jsonify({"success": True, "message": "Пользователь разбанен"})
        except Exception as e:
            return jsonify({"success": False, "message": f"Ошибка: {str(e)}"}), 500

    @app.route('/api/admin/send_message', methods=['POST'])
    def api_send_message():
        if 'user_id' not in session:
            return jsonify({"success": False, "message": "Требуется авторизация"}), 401

        data = request.get_json()
        message_text = data.get('message_text', '').strip()
        broadcast_type = data.get('broadcast_type', 'all')  # 'drivers', 'passengers', 'all'

        if not message_text:
            return jsonify({"success": False, "message": "Текст сообщения не может быть пустым"}), 400

        # Получаем только НЕЗАБАНЕННЫХ пользователей нужной роли
        cur = DB.cursor()
        if broadcast_type == 'drivers':
            cur.execute("SELECT telegram_id FROM users WHERE role = 'driver' AND is_banned = 0")
        elif broadcast_type == 'passengers':
            cur.execute("SELECT telegram_id FROM users WHERE role = 'passenger' AND is_banned = 0")
        else:  # 'all'
            cur.execute("SELECT telegram_id FROM users WHERE is_banned = 0")
        
        user_ids = [row[0] for row in cur.fetchall()]

        if not user_ids:
            return jsonify({"success": False, "message": "Нет активных получателей для рассылки"}), 400

        full_message = f"📢 От руководства службы такси:\n{message_text}"

        # Отправляем в очередь из db_utils
        from db_utils import BROADCAST_QUEUE
        BROADCAST_QUEUE.put({
            "user_ids": user_ids,
            "message_text": full_message
        })

        return jsonify({
            "success": True,
            "message": f"Рассылка поставлена в очередь для {len(user_ids)} пользователей"
        })


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
            reason = data.get('reason', 'Отменено диспетчером')
            if not order_id:
                return jsonify({"success": False, "message": "Не указан ID заказа"}), 400
            cur = DB.cursor()
            trip = cur.execute("SELECT * FROM trips WHERE id = ?", (order_id,)).fetchone()
            if not trip:
                return jsonify({"success": False, "message": "Заказ не найден"}), 404
            cur.execute("UPDATE trips SET status = 'cancelled', cancellation_reason = ? WHERE id = ?", (reason, order_id))
            DB.commit()
            try:
                from bot import cancel_trip_cleanup
                import threading
                # Запускаем в фоне через поток (не асинхронно)
                def notify():
                    cancel_trip_cleanup(order_id, 'admin', reason)
                threading.Thread(target=notify, daemon=True).start()
            except Exception as e:
                print(f"Ошибка при отправке уведомлений: {e}")
            return jsonify({"success": True, "message": "Заказ отменён"})
        except Exception as e:
            print(f"Ошибка при отмене заказа: {e}")
            return jsonify({"success": False, "message": "Внутренняя ошибка"}), 500

    return app