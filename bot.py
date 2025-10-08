import asyncio
import sqlite3
import threading
from datetime import datetime, timedelta
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

# === АКТИВНЫЕ СООБЩЕНИЯ ЗАКАЗОВ ===
ACTIVE_ORDER_MESSAGES = {}  # trip_id -> {driver_id: message_id}

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
            driver_message_id INTEGER,
            driver_card_message_id INTEGER,
            passenger_fare_message_id INTEGER,
            passenger_eta_message_id INTEGER,
            driver_fare_message_id INTEGER,
            driver_eta_message_id INTEGER,
            driver_control_message_id INTEGER,
            passenger_arrival_message_id INTEGER,
            cancellation_reason TEXT,
            driver_tariff_message_id INTEGER,
            driver_eta_select_message_id INTEGER
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
    # Новая таблица для причин отмены
    c.execute('''
        CREATE TABLE IF NOT EXISTS cancellation_reasons (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_type TEXT CHECK(user_type IN ('driver', 'passenger')),
            reason_text TEXT NOT NULL
        )
    ''')
    # Новая таблица для банов
    c.execute('''
        CREATE TABLE IF NOT EXISTS bans (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            reason TEXT NOT NULL,
            banned_until TEXT,
            banned_at TEXT DEFAULT (datetime('now')),
            FOREIGN KEY (user_id) REFERENCES users (telegram_id)
        )
    ''')
    # Добавляем начальные данные для тарифов только если их нет
    c.execute("SELECT COUNT(*) FROM tariffs")
    if c.fetchone()[0] == 0:
        c.execute("INSERT INTO tariffs (name, price) VALUES ('Эконом', 100.0), ('Стандарт', 200.0), ('Премиум', 300.0)")
    
    # Добавляем начальные данные для причин отмены только если их нет
    c.execute("SELECT COUNT(*) FROM cancellation_reasons")
    if c.fetchone()[0] == 0:
        c.execute("""
            INSERT INTO cancellation_reasons (user_type, reason_text) VALUES 
            ('driver', 'Долгое ожидание'),
            ('driver', 'Отказ пассажира'), 
            ('driver', 'Отказ водителя'),
            ('passenger', 'Долгое ожидание'),
            ('passenger', 'Передумал'),
            ('passenger', 'Не устраивает водитель'),
            ('passenger', 'Не устраивает автомобиль')
        """)
    conn.commit()
    return conn

DB = init_db()

# Проверяем и добавляем новые поля если их нет
def update_db_schema():
    cur = DB.cursor()
    try:
        cur.execute("ALTER TABLE trips ADD COLUMN driver_tariff_message_id INTEGER")
    except sqlite3.OperationalError:
        pass  # Поле уже существует
    
    try:
        cur.execute("ALTER TABLE trips ADD COLUMN driver_eta_select_message_id INTEGER")
    except sqlite3.OperationalError:
        pass  # Поле уже существует
    DB.commit()

# Вызываем после init_db()
update_db_schema()

# === ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ===
def get_user_role(telegram_id):
    cur = DB.cursor()
    res = cur.execute("SELECT role FROM users WHERE telegram_id = ?", (telegram_id,)).fetchone()
    return res[0] if res else None

def save_user(telegram_id, username, first_name):
    cur = DB.cursor()
    cur.execute("SELECT 1 FROM users WHERE telegram_id = ?", (telegram_id,))
    if cur.fetchone():
        cur.execute("UPDATE users SET username = ?, first_name = ? WHERE telegram_id = ?", (username, first_name, telegram_id))
    else:
        cur.execute("INSERT INTO users (telegram_id, username, first_name, role) VALUES (?, ?, ?, 'passenger')", (telegram_id, username, first_name))
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

def update_driver_card_message_id(trip_id, message_id):
    cur = DB.cursor()
    cur.execute("UPDATE trips SET driver_card_message_id = ? WHERE id = ?", (message_id, trip_id))
    DB.commit()

def update_passenger_fare_message_id(trip_id, message_id):
    cur = DB.cursor()
    cur.execute("UPDATE trips SET passenger_fare_message_id = ? WHERE id = ?", (message_id, trip_id))
    DB.commit()

def update_passenger_eta_message_id(trip_id, message_id):
    cur = DB.cursor()
    cur.execute("UPDATE trips SET passenger_eta_message_id = ? WHERE id = ?", (message_id, trip_id))
    DB.commit()

def update_driver_fare_message_id(trip_id, message_id):
    cur = DB.cursor()
    cur.execute("UPDATE trips SET driver_fare_message_id = ? WHERE id = ?", (message_id, trip_id))
    DB.commit()

def update_driver_eta_message_id(trip_id, message_id):
    cur = DB.cursor()
    cur.execute("UPDATE trips SET driver_eta_message_id = ? WHERE id = ?", (message_id, trip_id))
    DB.commit()

def update_driver_control_message_id(trip_id, message_id):
    cur = DB.cursor()
    cur.execute("UPDATE trips SET driver_control_message_id = ? WHERE id = ?", (message_id, trip_id))
    DB.commit()

def update_passenger_arrival_message_id(trip_id, message_id):
    cur = DB.cursor()
    cur.execute("UPDATE trips SET passenger_arrival_message_id = ? WHERE id = ?", (message_id, trip_id))
    DB.commit()

def update_driver_tariff_message_id(trip_id, message_id):
    cur = DB.cursor()
    cur.execute("UPDATE trips SET driver_tariff_message_id = ? WHERE id = ?", (message_id, trip_id))
    DB.commit()

def update_driver_eta_select_message_id(trip_id, message_id):
    cur = DB.cursor()
    cur.execute("UPDATE trips SET driver_eta_select_message_id = ? WHERE id = ?", (message_id, trip_id))
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

def get_driver_rating(driver_id):
    cur = DB.cursor()
    res = cur.execute("SELECT AVG(rating) FROM ratings WHERE driver_id = ?", (driver_id,)).fetchone()
    return round(res[0], 1) if res[0] else None

def save_rating(trip_id, driver_id, passenger_id, rating):
    cur = DB.cursor()
    cur.execute("INSERT OR REPLACE INTO ratings (trip_id, driver_id, passenger_id, rating) VALUES (?, ?, ?, ?)", 
                (trip_id, driver_id, passenger_id, rating))
    DB.commit()

# Новые функции для системы отмен и банов
def get_cancellation_reasons(user_type):
    cur = DB.cursor()
    return cur.execute("SELECT id, reason_text FROM cancellation_reasons WHERE user_type = ?", (user_type,)).fetchall()

def add_cancellation_reason(user_type, reason_text):
    cur = DB.cursor()
    cur.execute("INSERT INTO cancellation_reasons (user_type, reason_text) VALUES (?, ?)", (user_type, reason_text))
    DB.commit()
    return cur.lastrowid

def update_cancellation_reason(reason_id, reason_text):
    cur = DB.cursor()
    cur.execute("UPDATE cancellation_reasons SET reason_text = ? WHERE id = ?", (reason_text, reason_id))
    DB.commit()

def delete_cancellation_reason(reason_id):
    cur = DB.cursor()
    cur.execute("DELETE FROM cancellation_reasons WHERE id = ?", (reason_id,))
    DB.commit()

def ban_user(user_id, reason, ban_duration_days=None):
    cur = DB.cursor()
    banned_until = None
    if ban_duration_days:
        banned_until = datetime.now() + timedelta(days=ban_duration_days)
        banned_until = banned_until.strftime('%Y-%m-%d %H:%M:%S')
    
    cur.execute("INSERT INTO bans (user_id, reason, banned_until) VALUES (?, ?, ?)", 
                (user_id, reason, banned_until))
    cur.execute("UPDATE users SET is_banned = 1 WHERE telegram_id = ?", (user_id,))
    DB.commit()

def unban_user(user_id):
    cur = DB.cursor()
    cur.execute("UPDATE users SET is_banned = 0 WHERE telegram_id = ?", (user_id,))
    cur.execute("DELETE FROM bans WHERE user_id = ?", (user_id,))
    DB.commit()

def get_ban_info(user_id):
    cur = DB.cursor()
    return cur.execute("SELECT reason, banned_until FROM bans WHERE user_id = ?", (user_id,)).fetchone()

def is_user_banned(user_id):
    cur = DB.cursor()
    ban_info = get_ban_info(user_id)
    if not ban_info:
        return False
    
    reason, banned_until = ban_info
    if banned_until and datetime.now() > datetime.strptime(banned_until, '%Y-%m-%d %H:%M:%S'):
        unban_user(user_id)
        return False
    
    return True

async def cancel_trip_cleanup(trip_id, cancelled_by, reason_text=None):
    """Удаляет все сообщения заказа и отправляет уведомления"""
    trip = get_trip(trip_id)
    if not trip:
        return

    passenger_id = trip[1]
    driver_id = trip[2]

    # Удаляем ВСЕ сообщения связанные с заказом
    messages_to_delete = [
        (passenger_id, trip[11]),  # passenger_message_id
        (driver_id, trip[12]),     # driver_message_id  
        (passenger_id, trip[13]),  # driver_card_message_id
        (passenger_id, trip[14]),  # passenger_fare_message_id
        (passenger_id, trip[15]),  # passenger_eta_message_id
        (driver_id, trip[16]),     # driver_fare_message_id
        (driver_id, trip[17]),     # driver_eta_message_id
        (driver_id, trip[18]),     # driver_control_message_id
        (passenger_id, trip[19]),  # passenger_arrival_message_id
        (driver_id, trip[21]),     # driver_tariff_message_id (новое поле)
        (driver_id, trip[22])      # driver_eta_select_message_id (новое поле)
    ]

    for chat_id, message_id in messages_to_delete:
        if message_id:
            try:
                await bot.delete_message(chat_id=chat_id, message_id=message_id)
            except:
                pass

    # Удаляем сообщения из ACTIVE_ORDER_MESSAGES
    if trip_id in ACTIVE_ORDER_MESSAGES:
        for drv_id, msg_id in ACTIVE_ORDER_MESSAGES[trip_id].items():
            try:
                await bot.delete_message(chat_id=drv_id, message_id=msg_id)
            except:
                pass
        del ACTIVE_ORDER_MESSAGES[trip_id]

    # Отправляем уведомления об отмене
    if cancelled_by == 'driver':
        if driver_id:
            try:
                await bot.send_message(driver_id, f"✅ Вы отменили заказ по причине: {reason_text}")
            except:
                pass
        if passenger_id:
            try:
                await bot.send_message(passenger_id, f"❌ Водитель отменил заказ по причине: {reason_text}")
            except:
                pass
    elif cancelled_by == 'passenger':
        if passenger_id:
            try:
                await bot.send_message(passenger_id, f"✅ Вы отменили заказ по причине: {reason_text}")
            except:
                pass
        if driver_id:
            try:
                await bot.send_message(driver_id, f"❌ Пассажир отменил заказ по причине: {reason_text}")
            except:
                pass

    # Обновляем статус заказа
    cur = DB.cursor()
    status = 'cancelled_by_driver' if cancelled_by == 'driver' else 'cancelled_by_passenger'
    cur.execute("UPDATE trips SET status = ?, cancellation_reason = ? WHERE id = ?", (status, reason_text, trip_id))
    DB.commit()

# === TELEGRAM BOT ===
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(storage=MemoryStorage())

class UserState(StatesGroup):
    entering_pickup = State()
    entering_destination = State()

async def check_ban(user_id):
    if is_user_banned(user_id):
        ban_info = get_ban_info(user_id)
        reason, banned_until = ban_info
        duration_text = "навсегда" if not banned_until else f"до {banned_until}"
        try:
            await bot.send_message(
                user_id, 
                f"🚫 Вы забанены Администратором по причине: {reason}, {duration_text}."
            )
        except:
            pass
        return True
    return False

@dp.message(Command("start"))
async def cmd_start(message: types.Message, state: FSMContext):
    if await check_ban(message.from_user.id):
        return
        
    save_user(message.from_user.id, message.from_user.username, message.from_user.first_name)
    welcome_text = (
        "Привет! 👋\n"
        "Ты можешь вызвать такси прямо здесь — в Telegram, без приложений и регистрации!\n"
        "✅ Просто напиши, откуда и куда едешь\n"
        "✅ После поездки — оцени водителя\n"
        "Все водители проверены: указаны авто, гос. номер, телефон и реквизиты для оплаты.\n"
        "Без скрытых комиссий. Без задержек. Только комфорт!\n"
        "Нажми «🚕 Вызвать такси» — и поехали! 🚗💨"
    )
    await message.answer(welcome_text, reply_markup=get_passenger_menu())

@dp.message(Command("contacts"))
async def cmd_contacts(message: types.Message):
    if await check_ban(message.from_user.id):
        return
        
    contact_info = (
        "Если у вас есть жалоба или предложение, вы можете написать или позвонить по номеру телефона:\n"
        "📞 +7 (XXX) XXX-XX-XX\n"
        "Мы всегда рады улучшать нашу службу такси! 🙏"
    )
    await message.answer(contact_info)

@dp.message(lambda message: message.text == "🚕 Вызвать такси")
async def order_taxi(message: types.Message, state: FSMContext):
    if await check_ban(message.from_user.id):
        return
        
    if get_user_role(message.from_user.id) != "passenger":
        await message.answer("Только пассажиры могут заказывать такси.")
        return
    await message.answer("📍 Отправьте точку отправления:")
    await state.set_state(UserState.entering_pickup)

@dp.message(lambda message: message.text == "📞 Контакты")
async def contacts_button(message: types.Message):
    if await check_ban(message.from_user.id):
        return
    await cmd_contacts(message)

@dp.message(UserState.entering_pickup)
async def enter_pickup(message: types.Message, state: FSMContext):
    if await check_ban(message.from_user.id):
        return
        
    if not message.text:
        await message.answer("📍 Пожалуйста, отправьте точку отправления текстом.")
        return
    await state.update_data(pickup=message.text)
    await message.answer("📍 Отправьте пункт назначения:")
    await state.set_state(UserState.entering_destination)

@dp.message(UserState.entering_destination)
async def enter_destination(message: types.Message, state: FSMContext):
    if await check_ban(message.from_user.id):
        return
        
    if not message.text:
        await message.answer("📍 Пожалуйста, отправьте пункт назначения текстом.")
        return
    data = await state.get_data()
    pickup = data["pickup"]
    destination = message.text
    trip_id = create_trip(message.from_user.id, pickup, destination)

    sent_passenger = await message.answer(
        "🚕 <b>Ваш заказ принят в обработку</b>\n\n"
        f"📍 <b>Откуда:</b> {pickup}\n"
        f"📍 <b>Куда:</b> {destination}\n\n"
        "⏳ Ожидайте подтверждения от ближайшего водителя...",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="❌ Отменить заказ", callback_data=f"cancel_passenger_{trip_id}")]
        ])
    )
    update_passenger_message_id(trip_id, sent_passenger.message_id)

    drivers = get_all_drivers()
    if not drivers:
        await message.answer("❌ Нет активных водителей.")
    else:
        ACTIVE_ORDER_MESSAGES[trip_id] = {}
        for (driver_id,) in drivers:
            sent_driver = await bot.send_message(
                driver_id,
                "🚕 <b>Новый заказ!</b>\n\n"
                f"📍 <b>Откуда:</b> {pickup}\n"
                f"📍 <b>Куда:</b> {destination}\n\n"
                "Нажмите «✅ Принять», чтобы взять заказ.",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                    [
                        InlineKeyboardButton(text="✅ Принять", callback_data=f"accept_{trip_id}"),
                        InlineKeyboardButton(text="❌ Отказаться", callback_data=f"reject_{trip_id}")
                    ]
                ]),
                parse_mode="HTML"
            )
            ACTIVE_ORDER_MESSAGES[trip_id][driver_id] = sent_driver.message_id
    await state.clear()

# Кнопка отказа для водителя в карточке заказа (до принятия)
@dp.callback_query(lambda c: c.data.startswith("reject_"))
async def reject_trip(callback: types.CallbackQuery):
    if await check_ban(callback.from_user.id):
        return
        
    trip_id = int(callback.data.split("_")[1])
    
    # Удаляем сообщение у этого водителя
    try:
        await callback.message.delete()
    except:
        pass
    
    # Убираем из активных сообщений для этого водителя
    if trip_id in ACTIVE_ORDER_MESSAGES:
        if callback.from_user.id in ACTIVE_ORDER_MESSAGES[trip_id]:
            del ACTIVE_ORDER_MESSAGES[trip_id][callback.from_user.id]
    
    await callback.answer("Вы отказались от заказа")

@dp.callback_query(lambda c: c.data.startswith("accept_"))
async def accept_trip(callback: types.CallbackQuery):
    if await check_ban(callback.from_user.id):
        return
        
    trip_id = int(callback.data.split("_")[1])
    if assign_driver_to_trip(trip_id, callback.from_user.id):
        if trip_id in ACTIVE_ORDER_MESSAGES:
            for drv_id, msg_id in ACTIVE_ORDER_MESSAGES[trip_id].items():
                if drv_id != callback.from_user.id:
                    try:
                        await bot.delete_message(chat_id=drv_id, message_id=msg_id)
                    except:
                        pass
            del ACTIVE_ORDER_MESSAGES[trip_id]

        trip = get_trip(trip_id)
        new_text = f"Заказ №{trip_id}\nОт: {trip[4]}\nКуда: {trip[5]}"
        await callback.message.edit_text(
            new_text,
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="❌ Отменить заказ", callback_data=f"cancel_driver_{trip_id}")]
            ])
        )
        update_driver_message_id(trip_id, callback.message.message_id)

        tariffs = get_tariffs()
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text=f"{name} — {price} ₽", callback_data=f"setfare_{trip_id}_{price}")]
            for _, name, price in tariffs
        ])
        tariff_message = await bot.send_message(callback.from_user.id, "Выберите тариф для поездки:", reply_markup=kb)
        update_driver_tariff_message_id(trip_id, tariff_message.message_id)  # сохраняем ID сообщения с тарифами

        cur = DB.cursor()
        driver = cur.execute("""
            SELECT full_name, car_brand, car_model, license_plate, phone_number, payment_number, bank_name
            FROM users WHERE telegram_id = ?
        """, (callback.from_user.id,)).fetchone()

        # Получаем рейтинг водителя
        driver_rating = get_driver_rating(callback.from_user.id)

        if driver:
            full_name, car_brand, car_model, license_plate, phone_number, payment_number, bank_name = driver
            car_info = f"{car_brand} {car_model}".strip()
            rating_text = f"⭐ <b>Рейтинг:</b> {driver_rating}/5" if driver_rating else "⭐ <b>Рейтинг:</b> пока нет оценок"
            
            driver_card = (
                "👤 <b>Ваш водитель:</b>\n\n"
                f"📛 <b>Имя:</b> {full_name or '—'}\n"
                f"🚗 <b>Авто:</b> {car_info or '—'}\n"
                f"🔢 <b>Гос. номер:</b> {license_plate or '—'}\n"
                f"📱 <b>Телефон:</b> {phone_number or '—'}\n"
                f"💳 <b>Реквизиты:</b> {payment_number or '—'}\n"
                f"🏦 <b>Банк:</b> {bank_name or '—'}\n"
                f"{rating_text}"
            )
        else:
            driver_card = f"👤 <b>Ваш водитель:</b> @{callback.from_user.username or callback.from_user.id}"
            
            if driver_rating:
                driver_card += f"\n⭐ <b>Рейтинг:</b> {driver_rating}/5"

        try:
            sent_card = await bot.send_message(
                trip[1], 
                driver_card, 
                parse_mode="HTML",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="❌ Отменить заказ", callback_data=f"cancel_passenger_{trip_id}")]
                ])
            )
            update_driver_card_message_id(trip_id, sent_card.message_id)
        except:
            pass
    else:
        await callback.message.edit_text("⚠️ Заказ уже принят другим водителем.")
    await callback.answer()

@dp.callback_query(lambda c: c.data.startswith("setfare_"))
async def set_fare(callback: types.CallbackQuery):
    if await check_ban(callback.from_user.id):
        return
        
    _, trip_id_str, fare_str = callback.data.split("_")
    trip_id = int(trip_id_str)
    fare = float(fare_str)
    cur = DB.cursor()
    cur.execute("UPDATE trips SET fare = ? WHERE id = ?", (fare, trip_id))
    DB.commit()
    trip = get_trip(trip_id)
    passenger_id = trip[1]
    
    # Сохраняем ID сообщения о стоимости у водителя
    update_driver_fare_message_id(trip_id, callback.message.message_id)
    
    sent_fare = await bot.send_message(
        passenger_id,
        f"💰 <b>Стоимость:</b> {fare:.2f} ₽\nВодитель скоро приедет!",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="❌ Отменить заказ", callback_data=f"cancel_passenger_{trip_id}")]
        ])
    )
    update_passenger_fare_message_id(trip_id, sent_fare.message_id)
    
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
    
    # Сохраняем ID сообщения с выбором времени
    eta_select_message = await bot.send_message(callback.from_user.id, "⏱️ Укажите ориентировочное время прибытия:", reply_markup=time_kb)
    update_driver_eta_select_message_id(trip_id, eta_select_message.message_id)
    
    await callback.message.edit_text(f"✅ Стоимость установлена: {fare:.2f} ₽")

@dp.callback_query(lambda c: c.data.startswith("eta_"))
async def set_eta(callback: types.CallbackQuery):
    if await check_ban(callback.from_user.id):
        return
        
    _, trip_id_str, minutes_str = callback.data.split("_")
    trip_id = int(trip_id_str)
    minutes = int(minutes_str)
    trip = get_trip(trip_id)
    if not trip or not trip[1]:
        await callback.answer("Заказ не найден.", show_alert=True)
        return
    passenger_id = trip[1]
    text = f"Водитель прибудет на место через {minutes} минут" if minutes != 60 else "Водитель прибудет на место более чем через 30 минут"
    
    # Сохраняем сообщение о времени у пассажира
    sent_eta = await bot.send_message(
        passenger_id, 
        text,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="❌ Отменить заказ", callback_data=f"cancel_passenger_{trip_id}")]
        ])
    )
    update_passenger_eta_message_id(trip_id, sent_eta.message_id)
    
    # Сохраняем сообщение о времени у водителя
    update_driver_eta_message_id(trip_id, callback.message.message_id)
    
    # Добавляем кнопки управления поездкой для водителя
    ride_kb = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="🚗 Я на месте", callback_data=f"arrived_{trip_id}"),
            InlineKeyboardButton(text="✅ Завершить", callback_data=f"complete_{trip_id}")
        ],
        [
            InlineKeyboardButton(text="❌ Отменить заказ", callback_data=f"cancel_driver_{trip_id}")
        ]
    ])
    
    control_message = await callback.message.answer(
        f"✅ Время прибытия отправлено пассажиру.\n{text}\n\n"
        "Используйте кнопки ниже для управления поездкой:",
        reply_markup=ride_kb
    )
    update_driver_control_message_id(trip_id, control_message.message_id)
    
    await callback.answer()

@dp.callback_query(lambda c: c.data.startswith("arrived_"))
async def confirm_arrival(callback: types.CallbackQuery):
    if await check_ban(callback.from_user.id):
        return
        
    trip_id = int(callback.data.split("_")[1])
    mark_arrived(trip_id)
    trip = get_trip(trip_id)
    
    # Сохраняем ID сообщения о прибытии у пассажира
    try:
        arrival_message = await bot.send_message(
            trip[1], 
            "🚗 Водитель подтвердил прибытие! Поездка началась.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="❌ Отменить заказ", callback_data=f"cancel_passenger_{trip_id}")]
            ])
        )
        update_passenger_arrival_message_id(trip_id, arrival_message.message_id)
    except:
        pass
    
    # Обновляем сообщение водителя после подтверждения прибытия
    complete_kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Завершить поездку", callback_data=f"complete_{trip_id}")],
        [InlineKeyboardButton(text="❌ Отменить заказ", callback_data=f"cancel_driver_{trip_id}")]
    ])
    
    await callback.message.edit_text(
        "✅ Вы подтвердили прибытие. Поездка началась!\n"
        "Нажмите 'Завершить поездку' после окончания поездки.",
        reply_markup=complete_kb
    )
    await callback.answer()

# Отмена заказа водителем после принятия
@dp.callback_query(lambda c: c.data.startswith("cancel_driver_") and not c.data.startswith("cancel_driver_reason_"))
async def cancel_by_driver(callback: types.CallbackQuery):
    if await check_ban(callback.from_user.id):
        return
        
    trip_id = int(callback.data.split("_")[2])
    
    reasons = get_cancellation_reasons('driver')
    if not reasons:
        await callback.answer("Нет доступных причин отмены")
        return
    
    keyboard = []
    for reason_id, reason_text in reasons:
        keyboard.append([InlineKeyboardButton(text=reason_text, callback_data=f"cancel_driver_reason_{trip_id}_{reason_id}")])
    
    await callback.message.edit_text(
        "Выберите причину отмены заказа:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard)
    )
    await callback.answer()

@dp.callback_query(lambda c: c.data.startswith("cancel_driver_reason_"))
async def cancel_driver_reason(callback: types.CallbackQuery):
    if await check_ban(callback.from_user.id):
        return
        
    parts = callback.data.split("_")
    trip_id = int(parts[3])
    reason_id = int(parts[4])
    
    # Получаем текст причины
    cur = DB.cursor()
    reason = cur.execute("SELECT reason_text FROM cancellation_reasons WHERE id = ?", (reason_id,)).fetchone()
    reason_text = reason[0] if reason else "Не указана"
    
    await cancel_trip_cleanup(trip_id, 'driver', reason_text)
    await callback.answer()

# Отмена заказа пассажиром
@dp.callback_query(lambda c: c.data.startswith("cancel_passenger_") and not c.data.startswith("cancel_passenger_reason_"))
async def cancel_by_passenger(callback: types.CallbackQuery):
    if await check_ban(callback.from_user.id):
        return
        
    trip_id = int(callback.data.split("_")[2])
    
    reasons = get_cancellation_reasons('passenger')
    if not reasons:
        await callback.answer("Нет доступных причин отмены")
        return
    
    keyboard = []
    for reason_id, reason_text in reasons:
        keyboard.append([InlineKeyboardButton(text=reason_text, callback_data=f"cancel_passenger_reason_{trip_id}_{reason_id}")])
    
    await callback.message.edit_text(
        "Выберите причину отмены заказа:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard)
    )
    await callback.answer()

@dp.callback_query(lambda c: c.data.startswith("cancel_passenger_reason_"))
async def cancel_passenger_reason(callback: types.CallbackQuery):
    if await check_ban(callback.from_user.id):
        return
        
    parts = callback.data.split("_")
    trip_id = int(parts[3])
    reason_id = int(parts[4])
    
    # Получаем текст причины
    cur = DB.cursor()
    reason = cur.execute("SELECT reason_text FROM cancellation_reasons WHERE id = ?", (reason_id,)).fetchone()
    reason_text = reason[0] if reason else "Не указана"
    
    await cancel_trip_cleanup(trip_id, 'passenger', reason_text)
    await callback.answer()

@dp.callback_query(lambda c: c.data.startswith("complete_"))
async def complete_ride(callback: types.CallbackQuery):
    if await check_ban(callback.from_user.id):
        return
        
    trip_id = int(callback.data.split("_")[1])
    trip = get_trip(trip_id)
    if not trip:
        await callback.answer("Заказ не найден.", show_alert=True)
        return

    passenger_id = trip[1]
    driver_id = trip[2]
    fare = trip[6] or 0

    # Удаляем ВСЕ сообщения связанные с заказом
    messages_to_delete = [
        (passenger_id, trip[11]),  # passenger_message_id
        (driver_id, trip[12]),     # driver_message_id  
        (passenger_id, trip[13]),  # driver_card_message_id
        (passenger_id, trip[14]),  # passenger_fare_message_id
        (passenger_id, trip[15]),  # passenger_eta_message_id
        (driver_id, trip[16]),     # driver_fare_message_id
        (driver_id, trip[17]),     # driver_eta_message_id
        (driver_id, trip[18]),     # driver_control_message_id
        (passenger_id, trip[19]),  # passenger_arrival_message_id
        (driver_id, trip[21]),     # driver_tariff_message_id (новое поле)
        (driver_id, trip[22])      # driver_eta_select_message_id (новое поле)
    ]

    for chat_id, message_id in messages_to_delete:
        if message_id:
            try:
                await bot.delete_message(chat_id=chat_id, message_id=message_id)
            except:
                pass

    # Также удаляем текущее сообщение с кнопками
    try:
        await callback.message.delete()
    except:
        pass

    # Отправляем пассажиру запрос на оценку
    try:
        await bot.send_message(
            passenger_id,
            "🏁 <b>Поездка завершена!</b>\n\n"
            "Пожалуйста, оцените вашего водителя:",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [
                    InlineKeyboardButton(text="⭐", callback_data=f"rate_{trip_id}_1"),
                    InlineKeyboardButton(text="⭐⭐", callback_data=f"rate_{trip_id}_2"),
                    InlineKeyboardButton(text="⭐⭐⭐", callback_data=f"rate_{trip_id}_3"),
                    InlineKeyboardButton(text="⭐⭐⭐⭐", callback_data=f"rate_{trip_id}_4"),
                    InlineKeyboardButton(text="⭐⭐⭐⭐⭐", callback_data=f"rate_{trip_id}_5")
                ]
            ])
        )
    except: 
        pass

    # Отправляем водителю информацию о завершении
    try:
        await bot.send_message(
            driver_id,
            f"✅ <b>Заказ завершён.</b>\n\n"
            f"💰 <b>Заработано:</b> {fare:.2f} ₽\n\n"
            "Ожидайте оценку от пассажира...",
            parse_mode="HTML"
        )
    except: 
        pass

    complete_trip(trip_id)
    await callback.answer()

@dp.callback_query(lambda c: c.data.startswith("rate_"))
async def rate_driver(callback: types.CallbackQuery):
    try:
        _, trip_id_str, rating_str = callback.data.split("_")
        trip_id = int(trip_id_str)
        rating = int(rating_str)
        
        trip = get_trip(trip_id)
        if not trip or trip[1] != callback.from_user.id:
            await callback.answer("Ошибка оценки.", show_alert=True)
            return
            
        if rating < 1 or rating > 5:
            await callback.answer("Некорректная оценка.", show_alert=True)
            return

        # Сохраняем оценку
        save_rating(trip_id, trip[2], callback.from_user.id, rating)
        
        # Обновляем сообщение с убранными кнопками
        await callback.message.edit_text(
            f"✅ Спасибо за оценку! Вы поставили {rating} ⭐\n\n"
            "Будем рады видеть вас снова! 🚖"
        )
        
        # Отправляем меню пассажира
        await callback.message.answer(
            "Выберите действие:",
            reply_markup=get_passenger_menu()
        )
        
        # Уведомляем водителя об оценке
        driver_rating = get_driver_rating(trip[2])
        try:
            await bot.send_message(
                trip[2],
                f"⭐ Пассажир оценил вашу работу: {rating}/5\n"
                f"📊 Ваш текущий рейтинг: {driver_rating or 'еще нет оценок'}"
            )
        except:
            pass
            
    except Exception as e:
        print(f"Ошибка в оценке: {e}")
        await callback.answer("Произошла ошибка.", show_alert=True)
    
    await callback.answer()

# === FLASK DASHBOARD ===
DASHBOARD_HTML = '''
<!DOCTYPE html>
<html lang="ru">
<head>
    <meta charset="UTF-8">
    <title>🚕 Такси — Админка</title>
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <!-- Tailwind CSS -->
    <script src="https://cdn.tailwindcss.com"></script>
    <!-- Lucide Icons -->
    <script src="https://unpkg.com/lucide@latest/dist/umd/lucide.js"></script>
    <style>
        .sidebar {
            transition: all 0.3s ease;
        }
        .sidebar.collapsed {
            width: 70px;
        }
        .sidebar.collapsed .nav-text {
            display: none;
        }
        .main-content {
            transition: all 0.3s ease;
        }
        .filter-section {
            transition: all 0.3s ease;
            overflow: hidden;
        }
        .filter-section.collapsed {
            max-height: 0;
            opacity: 0;
        }
        .filter-section.expanded {
            max-height: 500px;
            opacity: 1;
        }
    </style>
</head>
<body class="bg-gray-50">
    <!-- Экран входа -->
    <div id="auth-screen" class="min-h-screen flex items-center justify-center bg-gradient-to-br from-blue-600 to-purple-700">
        <div class="bg-white rounded-2xl shadow-2xl p-8 w-full max-w-md">
            <div class="text-center mb-8">
                <div class="w-16 h-16 bg-blue-100 rounded-2xl flex items-center justify-center mx-auto mb-4">
                    <i data-lucide="car" class="w-8 h-8 text-blue-600"></i>
                </div>
                <h1 class="text-2xl font-bold text-gray-900">Админ-панель Такси</h1>
                <p class="text-gray-600 mt-2">Войдите в систему управления</p>
            </div>
            
            <div class="space-y-4">
                <div>
                    <label class="block text-sm font-medium text-gray-700 mb-2">Логин</label>
                    <div class="relative">
                        <i data-lucide="user" class="absolute left-3 top-1/2 transform -translate-y-1/2 text-gray-400 w-5 h-5"></i>
                        <input type="text" id="login-username" value="admin" 
                               class="w-full pl-10 pr-4 py-3 border border-gray-300 rounded-xl focus:ring-2 focus:ring-blue-500 focus:border-transparent transition-all duration-200" 
                               placeholder="Введите логин">
                    </div>
                </div>
                
                <div>
                    <label class="block text-sm font-medium text-gray-700 mb-2">Пароль</label>
                    <div class="relative">
                        <i data-lucide="lock" class="absolute left-3 top-1/2 transform -translate-y-1/2 text-gray-400 w-5 h-5"></i>
                        <input type="password" id="login-password" value="admin123"
                               class="w-full pl-10 pr-4 py-3 border border-gray-300 rounded-xl focus:ring-2 focus:ring-blue-500 focus:border-transparent transition-all duration-200" 
                               placeholder="Введите пароль">
                    </div>
                </div>
                
                <button onclick="login()" 
                        class="w-full bg-gradient-to-r from-blue-600 to-purple-600 text-white py-3 rounded-xl font-semibold shadow-lg hover:shadow-xl transform hover:-translate-y-0.5 transition-all duration-200 flex items-center justify-center gap-2">
                    <i data-lucide="log-in" class="w-5 h-5"></i>
                    Войти в систему
                </button>
            </div>
            
            <div id="login-message" class="mt-4"></div>
        </div>
    </div>

    <!-- Основной интерфейс -->
    <div id="main-app" class="hidden min-h-screen">
        <!-- Sidebar -->
        <div class="sidebar bg-white shadow-xl fixed left-0 top-0 h-full w-64 z-50">
            <div class="p-6 border-b border-gray-200">
                <div class="flex items-center gap-3">
                    <div class="w-10 h-10 bg-gradient-to-br from-blue-600 to-purple-600 rounded-xl flex items-center justify-center">
                        <i data-lucide="car" class="w-6 h-6 text-white"></i>
                    </div>
                    <div>
                        <h1 class="font-bold text-gray-900">Такси Админ</h1>
                        <p class="text-xs text-gray-500">Управление системой</p>
                    </div>
                </div>
            </div>

            <nav class="p-4 space-y-2">
                <a href="#" data-tab="dashboard" class="nav-item flex items-center gap-3 p-3 rounded-xl text-gray-700 hover:bg-blue-50 hover:text-blue-600 transition-all duration-200">
                    <i data-lucide="layout-dashboard" class="w-5 h-5"></i>
                    <span class="nav-text font-medium">Обзор системы</span>
                </a>
                
                <a href="#" data-tab="users" class="nav-item flex items-center gap-3 p-3 rounded-xl text-gray-700 hover:bg-blue-50 hover:text-blue-600 transition-all duration-200">
                    <i data-lucide="users" class="w-5 h-5"></i>
                    <span class="nav-text font-medium">Пассажиры</span>
                </a>
                
                <a href="#" data-tab="drivers" class="nav-item flex items-center gap-3 p-3 rounded-xl text-gray-700 hover:bg-blue-50 hover:text-blue-600 transition-all duration-200">
                    <i data-lucide="steering-wheel" class="w-5 h-5"></i>
                    <span class="nav-text font-medium">Водители</span>
                </a>
                
                <a href="#" data-tab="orders" class="nav-item flex items-center gap-3 p-3 rounded-xl text-gray-700 hover:bg-blue-50 hover:text-blue-600 transition-all duration-200">
                    <i data-lucide="clipboard-list" class="w-5 h-5"></i>
                    <span class="nav-text font-medium">Заказы</span>
                </a>
                
                <a href="#" data-tab="tariffs" class="nav-item flex items-center gap-3 p-3 rounded-xl text-gray-700 hover:bg-blue-50 hover:text-blue-600 transition-all duration-200">
                    <i data-lucide="credit-card" class="w-5 h-5"></i>
                    <span class="nav-text font-medium">Тарифы</span>
                </a>
                
                <a href="#" data-tab="cancellation-reasons" class="nav-item flex items-center gap-3 p-3 rounded-xl text-gray-700 hover:bg-blue-50 hover:text-blue-600 transition-all duration-200">
                    <i data-lucide="file-text" class="w-5 h-5"></i>
                    <span class="nav-text font-medium">Причины отмены</span>
                </a>
                
                <a href="#" data-tab="bans" class="nav-item flex items-center gap-3 p-3 rounded-xl text-gray-700 hover:bg-blue-50 hover:text-blue-600 transition-all duration-200">
                    <i data-lucide="shield-alert" class="w-5 h-5"></i>
                    <span class="nav-text font-medium">Баны</span>
                </a>
                
                <a href="#" data-tab="broadcast" class="nav-item flex items-center gap-3 p-3 rounded-xl text-gray-700 hover:bg-blue-50 hover:text-blue-600 transition-all duration-200">
                    <i data-lucide="megaphone" class="w-5 h-5"></i>
                    <span class="nav-text font-medium">Рассылка</span>
                </a>
            </nav>

            <div class="absolute bottom-4 left-4 right-4">
                <button onclick="logout()" class="w-full flex items-center gap-3 p-3 rounded-xl text-gray-700 hover:bg-red-50 hover:text-red-600 transition-all duration-200">
                    <i data-lucide="log-out" class="w-5 h-5"></i>
                    <span class="font-medium">Выйти</span>
                </button>
            </div>
        </div>

        <!-- Main Content -->
        <div class="main-content ml-64 p-6">
            <!-- Dashboard Tab -->
            <div class="tab-content active" id="tab-dashboard">
                <div class="mb-8">
                    <h1 class="text-3xl font-bold text-gray-900 mb-2">Обзор системы</h1>
                    <p class="text-gray-600">Статистика и ключевые показатели вашего такси-сервиса</p>
                </div>

                <!-- Stats Grid -->
                <div class="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-6 mb-8" id="stats-container">
                    <!-- Stats will be loaded here -->
                </div>

                <!-- Charts and Top Drivers -->
                <div class="grid grid-cols-1 lg:grid-cols-2 gap-8">
                    <!-- Financial Chart -->
                    <div class="bg-white rounded-2xl shadow-sm border border-gray-200 p-6">
                        <div class="flex items-center justify-between mb-6">
                            <h3 class="text-lg font-semibold text-gray-900">Доход за последние 7 дней</h3>
                            <i data-lucide="trending-up" class="w-5 h-5 text-green-500"></i>
                        </div>
                        <div id="financial-chart"></div>
                    </div>

                    <!-- Top Drivers -->
                    <div class="bg-white rounded-2xl shadow-sm border border-gray-200 p-6">
                        <div class="flex items-center justify-between mb-6">
                            <h3 class="text-lg font-semibold text-gray-900">Топ-5 водителей</h3>
                            <i data-lucide="trophy" class="w-5 h-5 text-yellow-500"></i>
                        </div>
                        <div class="overflow-hidden">
                            <table class="w-full">
                                <thead>
                                    <tr class="border-b border-gray-200">
                                        <th class="text-left py-3 text-sm font-semibold text-gray-600">Водитель</th>
                                        <th class="text-right py-3 text-sm font-semibold text-gray-600">Поездки</th>
                                        <th class="text-right py-3 text-sm font-semibold text-gray-600">Заработок</th>
                                        <th class="text-right py-3 text-sm font-semibold text-gray-600">Рейтинг</th>
                                    </tr>
                                </thead>
                                <tbody id="top-drivers-table">
                                    <!-- Top drivers will be loaded here -->
                                </tbody>
                            </table>
                        </div>
                    </div>
                </div>
            </div>

            <!-- Other tabs will be loaded here with similar structure -->
            <div id="other-tabs-content">
                <!-- Other tab contents will be dynamically loaded -->
            </div>
        </div>
    </div>

    <script>
        // Initialize Lucide icons
        lucide.createIcons();

        const qs = (sel) => document.querySelector(sel);
        const qsa = (sel) => document.querySelectorAll(sel);
        let currentTab = 'dashboard';

        // Navigation
        qsa('.nav-item').forEach(item => {
            item.addEventListener('click', (e) => {
                e.preventDefault();
                const target = item.dataset.tab;
                switchTab(target);
            });
        });

        function switchTab(tabName) {
            // Update active nav item
            qsa('.nav-item').forEach(item => {
                item.classList.remove('bg-blue-50', 'text-blue-600');
                if (item.dataset.tab === tabName) {
                    item.classList.add('bg-blue-50', 'text-blue-600');
                }
            });

            // Load tab content
            currentTab = tabName;
            loadTabData(tabName);
        }

        function loadTabData(tabName) {
            if (tabName === 'dashboard') {
                loadDashboard();
            } else {
                // Load other tabs dynamically
                fetch(`/api/tab/${tabName}`)
                    .then(response => response.text())
                    .then(html => {
                        document.getElementById('other-tabs-content').innerHTML = html;
                        initializeTabScripts(tabName);
                    });
            }
        }

        function initializeTabScripts(tabName) {
            switch(tabName) {
                case 'users':
                    loadPassengers();
                    break;
                case 'drivers':
                    loadDrivers();
                    break;
                case 'orders':
                    loadOrders();
                    initializeFilters();
                    break;
                case 'tariffs':
                    loadTariffs();
                    break;
                case 'cancellation-reasons':
                    loadCancellationReasons();
                    break;
                case 'bans':
                    loadBans();
                    break;
                case 'broadcast':
                    // Broadcast scripts will be initialized
                    break;
            }
        }

        // Auth functions
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
            msgEl.className = 'text-center p-3 rounded-lg';
            
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
                    msgEl.className = 'text-center p-3 rounded-lg bg-green-50 text-green-700';
                    msgEl.innerHTML = '<div class="flex items-center justify-center gap-2"><i data-lucide="check-circle" class="w-5 h-5"></i> Успешный вход!</div>';
                    setTimeout(checkAuth, 1000);
                } else {
                    msgEl.className = 'text-center p-3 rounded-lg bg-red-50 text-red-700';
                    msgEl.innerHTML = '<div class="flex items-center justify-center gap-2"><i data-lucide="alert-circle" class="w-5 h-5"></i> Неверный логин или пароль</div>';
                }
            } catch (e) {
                msgEl.className = 'text-center p-3 rounded-lg bg-red-50 text-red-700';
                msgEl.innerHTML = '<div class="flex items-center justify-center gap-2"><i data-lucide="wifi-off" class="w-5 h-5"></i> Ошибка подключения</div>';
            }
            lucide.createIcons();
        }

        function logout() {
            fetch('/logout').then(() => {
                qs('#auth-screen').classList.remove('hidden');
                qs('#main-app').classList.add('hidden');
            });
        }

        // API functions
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

        // Dashboard functions
        async function loadDashboard() {
            try {
                const data = await apiCall('/api/dashboard');
                const statsContainer = qs('#stats-container');
                
                statsContainer.innerHTML = `
                    <div class="bg-white rounded-2xl shadow-sm border border-gray-200 p-6">
                        <div class="flex items-center justify-between mb-4">
                            <h3 class="text-sm font-medium text-gray-600">Пассажиры</h3>
                            <i data-lucide="users" class="w-5 h-5 text-blue-500"></i>
                        </div>
                        <div class="text-2xl font-bold text-gray-900">${data.users.role_stats.passenger}</div>
                        <div class="text-sm text-gray-500 mt-1">Всего зарегистрировано</div>
                    </div>
                    
                    <div class="bg-white rounded-2xl shadow-sm border border-gray-200 p-6">
                        <div class="flex items-center justify-between mb-4">
                            <h3 class="text-sm font-medium text-gray-600">Водители</h3>
                            <i data-lucide="steering-wheel" class="w-5 h-5 text-green-500"></i>
                        </div>
                        <div class="text-2xl font-bold text-gray-900">${data.users.role_stats.driver}</div>
                        <div class="text-sm text-gray-500 mt-1">Активные водители</div>
                    </div>
                    
                    <div class="bg-white rounded-2xl shadow-sm border border-gray-200 p-6">
                        <div class="flex items-center justify-between mb-4">
                            <h3 class="text-sm font-medium text-gray-600">Всего заказов</h3>
                            <i data-lucide="clipboard-list" class="w-5 h-5 text-purple-500"></i>
                        </div>
                        <div class="text-2xl font-bold text-gray-900">${data.orders.total_stats.total_orders}</div>
                        <div class="text-sm text-gray-500 mt-1">За все время</div>
                    </div>
                    
                    <div class="bg-white rounded-2xl shadow-sm border border-gray-200 p-6">
                        <div class="flex items-center justify-between mb-4">
                            <h3 class="text-sm font-medium text-gray-600">Выручка</h3>
                            <i data-lucide="credit-card" class="w-5 h-5 text-yellow-500"></i>
                        </div>
                        <div class="text-2xl font-bold text-gray-900">${(data.orders.total_stats.total_earnings || 0).toFixed(2)} ₽</div>
                        <div class="text-sm text-gray-500 mt-1">Общий доход</div>
                    </div>
                `;

                // Load top drivers
                const topDriversTbody = qs('#top-drivers-table');
                topDriversTbody.innerHTML = data.financial.top_drivers.map(d => `
                    <tr class="border-b border-gray-100 last:border-0">
                        <td class="py-3">
                            <div class="flex items-center gap-3">
                                <div class="w-8 h-8 bg-gradient-to-br from-blue-500 to-purple-600 rounded-full flex items-center justify-center text-white text-xs font-bold">
                                    ${d.name.charAt(0)}
                                </div>
                                <div>
                                    <div class="font-medium text-gray-900">${d.name}</div>
                                    <div class="text-xs text-gray-500">ID: ${d.user_id}</div>
                                </div>
                            </div>
                        </td>
                        <td class="py-3 text-right font-medium text-gray-900">${d.total_orders}</td>
                        <td class="py-3 text-right font-bold text-green-600">${d.total_earnings.toFixed(2)} ₽</td>
                        <td class="py-3 text-right">
                            ${d.avg_rating ? `
                                <div class="inline-flex items-center gap-1 bg-yellow-50 text-yellow-700 px-2 py-1 rounded-full text-sm">
                                    <i data-lucide="star" class="w-3 h-3 fill-current"></i>
                                    ${d.avg_rating}
                                </div>
                            ` : '<span class="text-gray-400">—</span>'}
                        </td>
                    </tr>
                `).join('');

                loadFinancialChart();
                lucide.createIcons();
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
                    container.innerHTML = '<div class="text-center text-gray-500 py-8"><i data-lucide="bar-chart-3" class="w-12 h-12 mx-auto mb-2 opacity-50"></i><p>Нет данных о доходах</p></div>';
                    return;
                }

                earnings.sort((a, b) => a.day.localeCompare(b.day));
                const maxEarnings = Math.max(...earnings.map(e => e.earnings));
                
                const chartHtml = earnings.map(e => {
                    const height = maxEarnings > 0 ? Math.max(20, (e.earnings / maxEarnings) * 120) : 20;
                    const dateParts = e.day.split('-');
                    const formattedDate = `${dateParts[2]}.${dateParts[1]}`;
                    
                    return `
                        <div class="flex flex-col items-center justify-end h-32">
                            <div class="w-8 bg-gradient-to-t from-blue-500 to-blue-600 rounded-t-lg transition-all duration-300 hover:from-blue-600 hover:to-blue-700" 
                                 style="height: ${height}px" 
                                 title="${e.earnings.toFixed(2)} ₽">
                            </div>
                            <div class="mt-2 text-xs text-gray-600 font-medium">${formattedDate}</div>
                            <div class="text-xs text-gray-500">${e.earnings.toFixed(0)}₽</div>
                        </div>
                    `;
                }).join('');

                container.innerHTML = `
                    <div class="flex items-end justify-center gap-4 h-40 px-4">
                        ${chartHtml}
                    </div>
                `;
                
                lucide.createIcons();
            } catch (e) {
                console.error('Ошибка загрузки графика:', e);
            }
        }

        // Initialize
        checkAuth();
        setInterval(checkAuth, 60000);
        lucide.createIcons();
    </script>
</body>
</html>
'''

# Новые API endpoints для загрузки контента вкладок
@app.route('/api/tab/<tab_name>')
def api_tab_content(tab_name):
    if tab_name == 'users':
        return '''
        <div class="mb-8">
            <h1 class="text-3xl font-bold text-gray-900 mb-2">Управление пассажирами</h1>
            <p class="text-gray-600">Список всех пассажиров и управление их статусами</p>
        </div>
        <div class="bg-white rounded-2xl shadow-sm border border-gray-200 p-6">
            <div class="flex items-center justify-between mb-6">
                <h3 class="text-lg font-semibold text-gray-900">Список пассажиров</h3>
                <div class="flex items-center gap-2">
                    <i data-lucide="users" class="w-5 h-5 text-gray-400"></i>
                    <span class="text-sm text-gray-500" id="passengers-count">Загрузка...</span>
                </div>
            </div>
            <div class="overflow-x-auto">
                <table class="w-full">
                    <thead>
                        <tr class="border-b border-gray-200">
                            <th class="text-left py-3 text-sm font-semibold text-gray-600">ID</th>
                            <th class="text-left py-3 text-sm font-semibold text-gray-600">Имя</th>
                            <th class="text-left py-3 text-sm font-semibold text-gray-600">Юзернейм</th>
                            <th class="text-left py-3 text-sm font-semibold text-gray-600">Статус</th>
                            <th class="text-right py-3 text-sm font-semibold text-gray-600">Действия</th>
                        </tr>
                    </thead>
                    <tbody id="passengers-table">
                        <!-- Passengers will be loaded here -->
                    </tbody>
                </table>
            </div>
        </div>
        <script>
            function loadPassengers() {
                fetch('/api/admin/passengers')
                    .then(response => response.json())
                    .then(passengers => {
                        const tbody = document.getElementById('passengers-table');
                        const countElement = document.getElementById('passengers-count');
                        
                        countElement.textContent = `${passengers.length} пассажиров`;
                        
                        tbody.innerHTML = passengers.map(p => `
                            <tr class="border-b border-gray-100 last:border-0 hover:bg-gray-50">
                                <td class="py-4">
                                    <div class="font-mono text-sm text-gray-900">${p.user_id}</div>
                                </td>
                                <td class="py-4">
                                    <div class="font-medium text-gray-900">${p.first_name || '—'}</div>
                                </td>
                                <td class="py-4">
                                    <div class="text-gray-600">@${p.username || '—'}</div>
                                </td>
                                <td class="py-4">
                                    ${p.is_banned ? 
                                        '<span class="inline-flex items-center gap-1 bg-red-100 text-red-700 px-2 py-1 rounded-full text-xs font-medium"><i data-lucide="ban" class="w-3 h-3"></i>Заблокирован</span>' : 
                                        '<span class="inline-flex items-center gap-1 bg-green-100 text-green-700 px-2 py-1 rounded-full text-xs font-medium"><i data-lucide="check-circle" class="w-3 h-3"></i>Активен</span>'
                                    }
                                </td>
                                <td class="py-4 text-right">
                                    <div class="flex items-center justify-end gap-2">
                                        ${p.is_banned ?
                                            `<button onclick="unbanUser(${p.user_id})" class="flex items-center gap-1 bg-green-500 text-white px-3 py-1 rounded-lg text-sm hover:bg-green-600 transition-colors">
                                                <i data-lucide="unlock" class="w-4 h-4"></i>
                                                Разблокировать
                                            </button>` :
                                            `<button onclick="showBanModal(${p.user_id})" class="flex items-center gap-1 bg-red-500 text-white px-3 py-1 rounded-lg text-sm hover:bg-red-600 transition-colors">
                                                <i data-lucide="ban" class="w-4 h-4"></i>
                                                Заблокировать
                                            </button>`
                                        }
                                        <button onclick="makeDriver(${p.user_id})" class="flex items-center gap-1 bg-blue-500 text-white px-3 py-1 rounded-lg text-sm hover:bg-blue-600 transition-colors">
                                            <i data-lucide="user-plus" class="w-4 h-4"></i>
                                            Водитель
                                        </button>
                                    </div>
                                </td>
                            </tr>
                        `).join('');
                        lucide.createIcons();
                    });
            }
        </script>
        '''
    elif tab_name == 'orders':
        return '''
        <div class="mb-8">
            <h1 class="text-3xl font-bold text-gray-900 mb-2">Управление заказами</h1>
            <p class="text-gray-600">Просмотр и управление всеми заказами такси</p>
        </div>

        <!-- Filters Section -->
        <div class="bg-white rounded-2xl shadow-sm border border-gray-200 p-6 mb-6">
            <div class="flex items-center justify-between mb-4">
                <h3 class="text-lg font-semibold text-gray-900">Фильтры заказов</h3>
                <button onclick="toggleFilters()" class="flex items-center gap-2 text-blue-600 hover:text-blue-700">
                    <i data-lucide="filter" class="w-5 h-5"></i>
                    <span>Фильтры</span>
                </button>
            </div>
            
            <div id="filters-section" class="filter-section expanded grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-4">
                <!-- Period -->
                <div>
                    <label class="block text-sm font-medium text-gray-700 mb-2">Период</label>
                    <select id="filter-period" class="w-full border border-gray-300 rounded-lg px-3 py-2 focus:ring-2 focus:ring-blue-500 focus:border-transparent">
                        <option value="">Все время</option>
                        <option value="today">Сегодня</option>
                        <option value="week">Неделя</option>
                        <option value="month">Месяц</option>
                        <option value="custom">Выбрать период</option>
                    </select>
                </div>
                
                <!-- Driver -->
                <div>
                    <label class="block text-sm font-medium text-gray-700 mb-2">Водитель</label>
                    <select id="filter-driver" class="w-full border border-gray-300 rounded-lg px-3 py-2 focus:ring-2 focus:ring-blue-500 focus:border-transparent">
                        <option value="">Все водители</option>
                    </select>
                </div>
                
                <!-- Status -->
                <div>
                    <label class="block text-sm font-medium text-gray-700 mb-2">Статус</label>
                    <select id="filter-status" class="w-full border border-gray-300 rounded-lg px-3 py-2 focus:ring-2 focus:ring-blue-500 focus:border-transparent">
                        <option value="">Все статусы</option>
                        <option value="requested">Ожидает</option>
                        <option value="accepted">Принят</option>
                        <option value="in_progress">В пути</option>
                        <option value="completed">Завершён</option>
                        <option value="cancelled">Отменён</option>
                    </select>
                </div>
                
                <!-- Price Range -->
                <div>
                    <label class="block text-sm font-medium text-gray-700 mb-2">Цена</label>
                    <div class="flex gap-2">
                        <input type="number" id="filter-price-min" placeholder="Мин" class="w-1/2 border border-gray-300 rounded-lg px-3 py-2 focus:ring-2 focus:ring-blue-500 focus:border-transparent">
                        <input type="number" id="filter-price-max" placeholder="Макс" class="w-1/2 border border-gray-300 rounded-lg px-3 py-2 focus:ring-2 focus:ring-blue-500 focus:border-transparent">
                    </div>
                </div>
                
                <!-- From Location -->
                <div>
                    <label class="block text-sm font-medium text-gray-700 mb-2">Откуда</label>
                    <select id="filter-from" class="w-full border border-gray-300 rounded-lg px-3 py-2 focus:ring-2 focus:ring-blue-500 focus:border-transparent">
                        <option value="">Все адреса</option>
                    </select>
                </div>
                
                <!-- To Location -->
                <div>
                    <label class="block text-sm font-medium text-gray-700 mb-2">Куда</label>
                    <select id="filter-to" class="w-full border border-gray-300 rounded-lg px-3 py-2 focus:ring-2 focus:ring-blue-500 focus:border-transparent">
                        <option value="">Все адреса</option>
                    </select>
                </div>
                
                <!-- Cancellation Reason -->
                <div>
                    <label class="block text-sm font-medium text-gray-700 mb-2">Причина отмены</label>
                    <select id="filter-cancellation" class="w-full border border-gray-300 rounded-lg px-3 py-2 focus:ring-2 focus:ring-blue-500 focus:border-transparent">
                        <option value="">Все причины</option>
                    </select>
                </div>
                
                <!-- Actions -->
                <div class="flex items-end gap-2">
                    <button onclick="applyFilters()" class="flex-1 bg-blue-600 text-white px-4 py-2 rounded-lg hover:bg-blue-700 transition-colors flex items-center justify-center gap-2">
                        <i data-lucide="search" class="w-4 h-4"></i>
                        Применить
                    </button>
                    <button onclick="resetFilters()" class="flex-1 bg-gray-500 text-white px-4 py-2 rounded-lg hover:bg-gray-600 transition-colors flex items-center justify-center gap-2">
                        <i data-lucide="rotate-ccw" class="w-4 h-4"></i>
                        Сбросить
                    </button>
                </div>
            </div>
        </div>

        <!-- Orders Table -->
        <div class="bg-white rounded-2xl shadow-sm border border-gray-200 p-6">
            <div class="flex items-center justify-between mb-6">
                <h3 class="text-lg font-semibold text-gray-900">Последние заказы</h3>
                <div class="flex items-center gap-2">
                    <i data-lucide="clipboard-list" class="w-5 h-5 text-gray-400"></i>
                    <span class="text-sm text-gray-500" id="orders-count">Загрузка...</span>
                </div>
            </div>
            <div class="overflow-x-auto">
                <table class="w-full">
                    <thead>
                        <tr class="border-b border-gray-200">
                            <th class="text-left py-3 text-sm font-semibold text-gray-600">ID</th>
                            <th class="text-left py-3 text-sm font-semibold text-gray-600">Пассажир</th>
                            <th class="text-left py-3 text-sm font-semibold text-gray-600">Водитель</th>
                            <th class="text-left py-3 text-sm font-semibold text-gray-600">Откуда</th>
                            <th class="text-left py-3 text-sm font-semibold text-gray-600">Куда</th>
                            <th class="text-left py-3 text-sm font-semibold text-gray-600">Статус</th>
                            <th class="text-right py-3 text-sm font-semibold text-gray-600">Цена</th>
                            <th class="text-left py-3 text-sm font-semibold text-gray-600">Создан</th>
                            <th class="text-left py-3 text-sm font-semibold text-gray-600">Причина отмены</th>
                            <th class="text-right py-3 text-sm font-semibold text-gray-600">Действия</th>
                        </tr>
                    </thead>
                    <tbody id="orders-table">
                        <!-- Orders will be loaded here -->
                    </tbody>
                </table>
            </div>
        </div>

        <script>
            let filtersVisible = true;
            
            function toggleFilters() {
                const filtersSection = document.getElementById('filters-section');
                filtersVisible = !filtersVisible;
                
                if (filtersVisible) {
                    filtersSection.classList.remove('collapsed');
                    filtersSection.classList.add('expanded');
                } else {
                    filtersSection.classList.remove('expanded');
                    filtersSection.classList.add('collapsed');
                }
            }
            
            function initializeFilters() {
                // Load filter options
                loadFilterOptions();
            }
            
            function loadFilterOptions() {
                // This would be populated from API calls
                // For now, we'll use placeholder data
            }
            
            function applyFilters() {
                loadOrders();
            }
            
            function resetFilters() {
                document.getElementById('filter-period').value = '';
                document.getElementById('filter-driver').value = '';
                document.getElementById('filter-status').value = '';
                document.getElementById('filter-price-min').value = '';
                document.getElementById('filter-price-max').value = '';
                document.getElementById('filter-from').value = '';
                document.getElementById('filter-to').value = '';
                document.getElementById('filter-cancellation').value = '';
                loadOrders();
            }
            
            function loadOrders() {
                fetch('/api/orders')
                    .then(response => response.json())
                    .then(data => {
                        const tbody = document.getElementById('orders-table');
                        const countElement = document.getElementById('orders-count');
                        
                        countElement.textContent = `${data.recent_orders.length} заказов`;
                        
                        const getStatusBadge = (status) => {
                            const statusConfig = {
                                'requested': { color: 'yellow', text: 'Ожидает', icon: 'clock' },
                                'accepted': { color: 'blue', text: 'Принят', icon: 'check-circle' },
                                'in_progress': { color: 'purple', text: 'В пути', icon: 'car' },
                                'completed': { color: 'green', text: 'Завершён', icon: 'check-circle-2' },
                                'cancelled': { color: 'red', text: 'Отменён', icon: 'x-circle' },
                                'cancelled_by_passenger': { color: 'orange', text: 'Отм. пассажиром', icon: 'user-x' },
                                'cancelled_by_driver': { color: 'red', text: 'Отм. водителем', icon: 'steering-wheel' },
                                'expired': { color: 'gray', text: 'Авто-отмена', icon: 'hourglass' }
                            };
                            
                            const config = statusConfig[status] || { color: 'gray', text: status, icon: 'help-circle' };
                            
                            return `
                                <span class="inline-flex items-center gap-1 bg-${config.color}-100 text-${config.color}-700 px-2 py-1 rounded-full text-xs font-medium">
                                    <i data-lucide="${config.icon}" class="w-3 h-3"></i>
                                    ${config.text}
                                </span>
                            `;
                        };
                        
                        tbody.innerHTML = data.recent_orders.map(o => {
                            const driverDisplay = o.driver_id ?
                                (o.driver_name + (o.license_plate ? ` (${o.license_plate})` : '')) :
                                '—';
                                
                            return `
                                <tr class="border-b border-gray-100 last:border-0 hover:bg-gray-50">
                                    <td class="py-4">
                                        <div class="font-mono text-sm text-gray-900 font-medium">#${o.order_id}</div>
                                    </td>
                                    <td class="py-4">
                                        <div class="font-medium text-gray-900">${o.passenger_id}</div>
                                    </td>
                                    <td class="py-4">
                                        <div class="text-gray-600">${driverDisplay}</div>
                                    </td>
                                    <td class="py-4">
                                        <div class="text-gray-600 max-w-xs truncate">${o.from_location || '—'}</div>
                                    </td>
                                    <td class="py-4">
                                        <div class="text-gray-600 max-w-xs truncate">${o.to_location || '—'}</div>
                                    </td>
                                    <td class="py-4">
                                        ${getStatusBadge(o.status)}
                                    </td>
                                    <td class="py-4 text-right">
                                        <div class="font-bold text-green-600">${o.price ? o.price.toFixed(2) + ' ₽' : '—'}</div>
                                    </td>
                                    <td class="py-4">
                                        <div class="text-sm text-gray-500">${new Date(o.created_at).toLocaleString('ru-RU')}</div>
                                    </td>
                                    <td class="py-4">
                                        <div class="text-sm text-gray-500 max-w-xs truncate">${o.cancellation_reason || '—'}</div>
                                    </td>
                                    <td class="py-4 text-right">
                                        ${['requested', 'accepted', 'in_progress'].includes(o.status) ?
                                            `<button onclick="cancelOrder(${o.order_id})" class="inline-flex items-center gap-1 bg-red-500 text-white px-3 py-1 rounded-lg text-sm hover:bg-red-600 transition-colors">
                                                <i data-lucide="x-circle" class="w-4 h-4"></i>
                                                Отменить
                                            </button>` : ''
                                        }
                                    </td>
                                </tr>
                            `;
                        }).join('');
                        lucide.createIcons();
                    });
            }
        </script>
        '''
    # Add other tabs similarly...
    else:
        return f'<div class="text-center py-8 text-gray-500">Вкладка {tab_name} в разработке</div>'

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
               SUM(t.fare) as total_earnings,
               AVG(r.rating) as avg_rating
        FROM users u
        LEFT JOIN trips t ON u.telegram_id = t.driver_id AND t.status = 'completed'
        LEFT JOIN ratings r ON u.telegram_id = r.driver_id
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
            "avg_rating": round(d[5], 1) if d[5] else None
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
    cur.execute('''
        SELECT u.*, b.reason as ban_reason, b.banned_until 
        FROM users u 
        LEFT JOIN bans b ON u.telegram_id = b.user_id
    ''')
    users = cur.fetchall()
    return jsonify([{
        "user_id": u[0], "username": u[1], "first_name": u[2], "role": u[3],
        "is_banned": bool(u[4]), "registration_date": u[13],
        "ban_reason": u[14], "banned_until": u[15]
    } for u in users])

@app.route('/api/admin/passengers')
def api_passengers():
    cur = DB.cursor()
    cur.execute('''
        SELECT u.*, b.reason as ban_reason, b.banned_until 
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
        "ban_reason": p[14],
        "banned_until": p[15]
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

# Новые API endpoints для причин отмены
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

# Новые API endpoints для банов
@app.route('/api/admin/ban_user', methods=['POST'])
def api_ban_user():
    data = request.get_json()
    user_id = data.get('user_id')
    reason = data.get('reason')
    duration_days = data.get('duration_days')  # null для вечного бана
    
    if not user_id or not reason:
        return jsonify({"success": False, "message": "Заполните все поля"}), 400
    
    try:
        ban_user(user_id, reason, duration_days)
        
        # Отправляем сообщение пользователю
        duration_text = "навсегда" if not duration_days else f"на {duration_days} дней"
        try:
            asyncio.create_task(bot.send_message(
                user_id,
                f"🚫 Вы забанены Администратором по причине: {reason}, {duration_text}."
            ))
        except:
            pass
        
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
        return jsonify({"success": True, "message": "Пользователь разбанен"})
    except Exception as e:
        return jsonify({"success": False, "message": f"Ошибка: {str(e)}"}), 500

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
                cur2 = DB.cursor()
                cur2.execute("UPDATE trips SET status = 'expired' WHERE id = ?", (trip_id,))
                DB.commit()
                try:
                    if p_msg_id:
                        await bot.delete_message(chat_id=passenger_id, message_id=p_msg_id)
                except:
                    pass
                if trip_id in ACTIVE_ORDER_MESSAGES:
                    for drv_id, msg_id in ACTIVE_ORDER_MESSAGES[trip_id].items():
                        try:
                            await bot.delete_message(chat_id=drv_id, message_id=msg_id)
                        except:
                            pass
                    del ACTIVE_ORDER_MESSAGES[trip_id]
                try:
                    await bot.send_message(
                        passenger_id,
                        f"❌ Заказ автоматически отменён: никто не принял его в течение {ORDER_TIMEOUT} минут."
                    )
                except:
                    pass
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