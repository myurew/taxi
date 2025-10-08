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
            <div class="tab" data-tab="cancellation-reasons">📝 Причины отмены</div>
            <div class="tab" data-tab="bans">🚫 Баны</div>
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
                            <th>Рейтинг</th>
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
                            <th>Причина отмены</th>
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
                <h3>💰 Управление тарифы</h3>
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
        <!-- Вкладка: Причины отмены -->
        <div class="tab-content" id="tab-cancellation-reasons">
            <div class="card">
                <h3>📝 Управление причинами отмены</h3>
                <div class="form-group">
                    <label>Тип пользователя:</label>
                    <select id="reason-user-type">
                        <option value="driver">Водитель</option>
                        <option value="passenger">Пассажир</option>
                    </select>
                </div>
                <div class="form-group">
                    <input type="text" id="new-reason-text" placeholder="Текст причины отмены">
                </div>
                <button class="btn-success" onclick="addCancellationReason()">Добавить причину</button>
                
                <h4 style="margin-top: 20px;">Список причин</h4>
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
        <!-- Вкладка: Баны -->
        <div class="tab-content" id="tab-bans">
            <div class="card">
                <h3>🚫 Управление банами</h3>
                <div class="form-group">
                    <input type="number" id="ban-user-id" placeholder="ID пользователя">
                </div>
                <div class="form-group">
                    <input type="text" id="ban-reason" placeholder="Причина бана">
                </div>
                <div class="form-group">
                    <label>Длительность бана:</label>
                    <select id="ban-duration">
                        <option value="1">1 день</option>
                        <option value="3">3 дня</option>
                        <option value="7">7 дней</option>
                        <option value="30">30 дней</option>
                        <option value="">Навсегда</option>
                    </select>
                </div>
                <button class="btn-danger" onclick="banUserAdmin()">Забанить</button>
                
                <h4 style="margin-top: 20px;">Активные баны</h4>
                <table id="bans-table">
                    <thead>
                        <tr>
                            <th>ID</th>
                            <th>Пользователь</th>
                            <th>Причина</th>
                            <th>До</th>
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
    const qs = (sel) => document.querySelector(sel);
    const qsa = (sel) => document.querySelectorAll(sel);
    let currentTab = 'dashboard';
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
                            `<button class="btn-danger" onclick="showBanModal(${p.user_id})">Заблокировать</button>`
                        }
                        <button class="btn-warning" onclick="makeDriver(${p.user_id})">Сделать водителем</button>
                    </td>
                </tr>
            `).join('');
        } catch (e) {
            console.error('Ошибка загрузки пассажиров:', e);
        }
    }
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
                            ${d.is_banned ?
                                `<button class="btn-success" onclick="unbanUser(${d.user_id})">Разблокировать</button>` :
                                `<button class="btn-danger" onclick="showBanModal(${d.user_id})">Заблокировать</button>`
                            }
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
                        <td>${o.cancellation_reason || '—'}</td>
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
    // Управление причинами отмены
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
                               onchange="updateCancellationReason(${r.id})">
                    </td>
                    <td class="actions">
                        <button class="btn-danger" onclick="deleteCancellationReason(${r.id})">🗑️</button>
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
    // Управление банами
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
                    <td>${u.banned_until ? new Date(u.banned_until).toLocaleDateString('ru-RU') : 'Навсегда'}</td>
                    <td class="actions">
                        <button class="btn-success" onclick="unbanUser(${u.user_id})">Разблокировать</button>
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
        
        // Переключаем на вкладку банов
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