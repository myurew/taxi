# bot.py
import asyncio
import threading
import os
import queue
from datetime import datetime
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.context import FSMContext
from db_utils import (
    init_db, get_user_role, save_user, create_trip, get_trip, assign_driver_to_trip,
    mark_arrived, complete_trip, get_all_drivers, get_tariffs, get_driver_rating,
    save_rating, get_cancellation_reasons, ban_user, unban_user, is_user_banned,
    update_passenger_message_id, update_driver_message_id, update_driver_card_message_id,
    update_passenger_fare_message_id, update_passenger_eta_message_id,
    update_driver_fare_message_id, update_driver_eta_message_id,
    update_driver_control_message_id, update_passenger_arrival_message_id,
    update_driver_tariff_message_id, update_driver_eta_select_message_id,
    get_ban_info, BROADCAST_QUEUE
)

# Инициализация БД
DB = init_db()

# Конфигурация
BOT_TOKEN = os.getenv("BOT_TOKEN", "your_bot_token")
ORDER_TIMEOUT = 10  # минут

ACTIVE_ORDER_MESSAGES = {}
ACTIVE_DRIVERS = set()  # Множество активных водителей

# Инициализация бота
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(storage=MemoryStorage())

# Состояния для FSM
class OrderState(StatesGroup):
    waiting_for_pickup = State()
    waiting_for_destination = State()

class ManualFareState(StatesGroup):
    waiting_for_fare = State()

# Вспомогательные функции
async def check_ban(user_id):
    if is_user_banned(user_id):
        ban_info = get_ban_info(user_id)
        if ban_info:
            reason, banned_until = ban_info
            if banned_until:
                banned_until_date = datetime.strptime(banned_until, '%Y-%m-%d %H:%M:%S')
                days_left = (banned_until_date - datetime.now()).days
                duration_text = f"на {days_left} дней"
            else:
                duration_text = "навсегда"
            try:
                await bot.send_message(
                    user_id, 
                    f"🚫 Вы забанены по причине: {reason}, {duration_text}."
                )
            except:
                pass
        return True
    return False

async def cancel_trip_cleanup(trip_id, cancelled_by, reason_text=None):
    trip = get_trip(trip_id)
    if not trip:
        return
    passenger_id = trip[1]
    driver_id = trip[2]
    messages_to_delete = [
        (passenger_id, trip[11]), (driver_id, trip[12]), (passenger_id, trip[13]),
        (passenger_id, trip[14]), (passenger_id, trip[15]), (driver_id, trip[16]),
        (driver_id, trip[17]), (driver_id, trip[18]), (passenger_id, trip[19]),
        (driver_id, trip[21]), (driver_id, trip[22])
    ]
    for chat_id, msg_id in messages_to_delete:
        if msg_id:
            try:
                await bot.delete_message(chat_id=chat_id, message_id=msg_id)
            except:
                pass
    if trip_id in ACTIVE_ORDER_MESSAGES:
        for drv_id, msg_id in ACTIVE_ORDER_MESSAGES[trip_id].items():
            try:
                await bot.delete_message(chat_id=drv_id, message_id=msg_id)
            except:
                pass
        del ACTIVE_ORDER_MESSAGES[trip_id]
    if cancelled_by == 'driver':
        if driver_id: 
            await bot.send_message(driver_id, f"✅ Вы отменили заказ по причине: {reason_text}")
        if passenger_id: 
            await bot.send_message(passenger_id, f"❌ Водитель отменил заказ по причине: {reason_text}")
    elif cancelled_by == 'passenger':
        if passenger_id: 
            await bot.send_message(passenger_id, f"✅ Вы отменили заказ по причине: {reason_text}")
        if driver_id: 
            await bot.send_message(driver_id, f"❌ Пассажир отменил заказ по причине: {reason_text}")
    elif cancelled_by == 'admin':
        # Отправляем сообщение всем участникам о отмене диспетчером
        if passenger_id:
            await bot.send_message(passenger_id, f"❌ Заказ отменен диспетчером. Причина: {reason_text or 'не указана'}")
        if driver_id:
            await bot.send_message(driver_id, f"❌ Заказ отменен диспетчером. Причина: {reason_text or 'не указана'}")
    cur = DB.cursor()
    if cancelled_by == 'admin':
        status = 'cancelled'
    else:
        status = 'cancelled_by_driver' if cancelled_by == 'driver' else 'cancelled_by_passenger'
    cur.execute("UPDATE trips SET status = ?, cancellation_reason = ? WHERE id = ?", (status, reason_text, trip_id))
    DB.commit()

def get_main_menu():
    """Главное меню с inline-кнопками для пассажиров"""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🚕 Вызвать такси", callback_data="order_taxi")],
        [InlineKeyboardButton(text="📞 Контакты", callback_data="contacts"),
         InlineKeyboardButton(text="ℹ️ Помощь", callback_data="help")],
        [InlineKeyboardButton(text="📊 Моя статистика", callback_data="my_stats")]
    ])

def get_driver_menu():
    """Меню для водителей"""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🚗 Стать доступным", callback_data="become_available")],
        [InlineKeyboardButton(text="🚫 Перестать принимать заказы", callback_data="stop_accepting")],
        [InlineKeyboardButton(text="📊 Моя статистика", callback_data="my_stats")],
        [InlineKeyboardButton(text="📞 Контакты", callback_data="contacts"),
         InlineKeyboardButton(text="ℹ️ Помощь", callback_data="help")]
    ])

def get_back_menu():
    """Кнопка возврата в главное меню"""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔙 Главное меню", callback_data="main_menu")]
    ])

# Хендлеры Telegram
@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    if await check_ban(message.from_user.id):
        return
    save_user(message.from_user.id, message.from_user.username, message.from_user.first_name)
    welcome_text = (
        "🚕 Добро пожаловать в службу такси!\n"
        "Выберите действие:"
    )
    user_role = get_user_role(message.from_user.id) or "passenger"
    if user_role == "driver":
        await message.answer(welcome_text, reply_markup=get_driver_menu())
    else:
        await message.answer(welcome_text, reply_markup=get_main_menu())

@dp.message(Command("menu"))
async def cmd_menu(message: types.Message):
    if await check_ban(message.from_user.id):
        return
    user_role = get_user_role(message.from_user.id) or "passenger"
    if user_role == "driver":
        await message.answer("Главное меню:", reply_markup=get_driver_menu())
    else:
        await message.answer("Главное меню:", reply_markup=get_main_menu())

# Обработчики inline-кнопок главного меню
@dp.callback_query(lambda c: c.data == "main_menu")
async def main_menu_callback(callback: types.CallbackQuery):
    if await check_ban(callback.from_user.id):
        return
    user_role = get_user_role(callback.from_user.id) or "passenger"
    if user_role == "driver":
        await callback.message.edit_text("Главное меню:", reply_markup=get_driver_menu())
    else:
        await callback.message.edit_text("Главное меню:", reply_markup=get_main_menu())
    await callback.answer()

@dp.callback_query(lambda c: c.data == "order_taxi")
async def order_taxi_callback(callback: types.CallbackQuery, state: FSMContext):
    if await check_ban(callback.from_user.id):
        return
    if get_user_role(callback.from_user.id) != "passenger":
        await callback.answer("❌ Только пассажиры могут заказывать такси.", show_alert=True)
        return
    await callback.message.edit_text(
        "📍 <b>Заказ такси</b>\n"
        "Пожалуйста, отправьте <b>точку отправления</b>:",
        parse_mode="HTML",
        reply_markup=get_back_menu()
    )
    await state.set_state(OrderState.waiting_for_pickup)
    await callback.answer()

@dp.callback_query(lambda c: c.data == "become_available")
async def become_available_callback(callback: types.CallbackQuery):
    if await check_ban(callback.from_user.id):
        return
    if get_user_role(callback.from_user.id) != "driver":
        await callback.answer("❌ Только водители могут становиться доступными.", show_alert=True)
        return
    ACTIVE_DRIVERS.add(callback.from_user.id)
    await callback.message.edit_text(
        "✅ <b>Вы теперь доступны для получения заказов!</b>\n"
        "Новые заказы будут приходить вам автоматически.\n"
        "Чтобы перестать получать заказы, нажмите «🚫 Перестать принимать заказы».",
        parse_mode="HTML",
        reply_markup=get_back_menu()
    )
    await callback.answer()

@dp.callback_query(lambda c: c.data == "stop_accepting")
async def stop_accepting_callback(callback: types.CallbackQuery):
    if await check_ban(callback.from_user.id):
        return
    if get_user_role(callback.from_user.id) != "driver":
        await callback.answer("❌ Только водители могут использовать эту функцию.", show_alert=True)
        return
    if callback.from_user.id in ACTIVE_DRIVERS:
        ACTIVE_DRIVERS.remove(callback.from_user.id)
    await callback.message.edit_text(
        "🚫 <b>Вы больше не получаете заказы</b>\n"
        "Чтобы снова начать получать заказы, нажмите «🚗 Стать доступным».",
        parse_mode="HTML",
        reply_markup=get_back_menu()
    )
    await callback.answer()

@dp.callback_query(lambda c: c.data == "my_stats")
async def my_stats_callback(callback: types.CallbackQuery):
    if await check_ban(callback.from_user.id):
        return
    user_role = get_user_role(callback.from_user.id)
    if user_role == "driver":
        # Статистика для водителя - ТОЛЬКО завершенные поездки
        cur = DB.cursor()
        cur.execute('''
            SELECT 
                COUNT(DISTINCT t.id) as completed_orders,
                SUM(t.fare) as total_earnings,
                AVG(r.rating) as avg_rating
            FROM users u
            LEFT JOIN trips t ON u.telegram_id = t.driver_id AND t.status = 'completed'
            LEFT JOIN ratings r ON t.id = r.trip_id
            WHERE u.telegram_id = ?
        ''', (callback.from_user.id,))
        stats = cur.fetchone()
        completed_orders = stats[0] or 0
        total_earnings = stats[1] or 0
        avg_rating = round(stats[2], 1) if stats[2] else "еще нет"
        stats_text = (
            f"📊 <b>Ваша статистика:</b>\n"
            f"🚗 Завершено поездок: <b>{completed_orders}</b>\n"
            f"💰 Общий заработок: <b>{total_earnings:.2f} ₽</b>\n"
            f"⭐ Средний рейтинг: <b>{avg_rating}</b>\n"
            f"Продолжайте в том же духе! 💪"
        )
    else:
        # Статистика для пассажира
        cur = DB.cursor()
        cur.execute('''
            SELECT COUNT(*) 
            FROM trips 
            WHERE passenger_id = ? AND status = 'completed'
        ''', (callback.from_user.id,))
        completed_trips = cur.fetchone()[0] or 0
        stats_text = (
            f"📊 <b>Ваша статистика:</b>\n"
            f"🚕 Совершено поездок: <b>{completed_trips}</b>\n"
            f"Спасибо, что выбираете нашу службу такси! ❤️"
        )
    await callback.message.edit_text(
        stats_text,
        parse_mode="HTML",
        reply_markup=get_back_menu()
    )
    await callback.answer()

@dp.callback_query(lambda c: c.data == "contacts")
async def contacts_callback(callback: types.CallbackQuery):
    if await check_ban(callback.from_user.id):
        return
    contact_info = (
        "📞 <b>Контакты службы такси</b>\n"
        "Если у вас есть жалоба или предложение:\n"
        "📞 Телефон: <b>+7 (XXX) XXX-XX-XX</b>\n"
        "🕒 Время работы: <b>круглосуточно</b>\n"
        "✉️ Email: <b>support@taxi.ru</b>\n"
        "Мы всегда рады улучшать наш сервис! 🙏"
    )
    await callback.message.edit_text(
        contact_info,
        parse_mode="HTML",
        reply_markup=get_back_menu()
    )
    await callback.answer()

@dp.callback_query(lambda c: c.data == "help")
async def help_callback(callback: types.CallbackQuery):
    if await check_ban(callback.from_user.id):
        return
    user_role = get_user_role(callback.from_user.id) or "passenger"
    if user_role == "driver":
        help_text = (
            "ℹ️ <b>Помощь для водителей</b>\n"
            "🚗 <b>Как начать работать:</b>\n"
            "• Нажмите «🚗 Стать доступным»\n"
            "• Получайте уведомления о новых заказах\n"
            "• Принимайте заказы и зарабатывайте\n"
            "💰 <b>Установка стоимости:</b>\n"
            "• Вы можете выбрать тариф из списка\n"
            "• Или указать свою сумму вручную\n"
            "⏱️ <b>Управление поездкой:</b>\n"
            "• Укажите время прибытия\n"
            "• Подтвердите прибытие на место\n"
            "• Завершите поездку после окончания\n"
            "📞 <b>Поддержка:</b>\n"
            "По всем вопросам обращайтесь в раздел «Контакты»\n"
            "⚡ <b>Быстрые команды:</b>\n"
            "/start - Главное меню\n"
            "/menu - Показать меню"
        )
    else:
        help_text = (
            "ℹ️ <b>Помощь для пассажиров</b>\n"
            "🚕 <b>Как заказать такси:</b>\n"
            "• Нажмите «Вызвать такси»\n"
            "• Укажите адрес отправления\n"
            "• Укажите адрес назначения\n"
            "• Ожидайте подтверждения водителя\n"
            "👤 <b>Информация о водителе:</b>\n"
            "• После принятия заказа вы увидите данные водителя\n"
            "• Модель автомобиля и гос. номер\n"
            "• Контактные данные и рейтинг\n"
            "⭐ <b>Оценка поездки:</b>\n"
            "• После завершения поездки оцените водителя\n"
            "• Это поможет улучшить качество сервиса\n"
            "📞 <b>Поддержка:</b>\n"
            "По всем вопросам обращайтесь в раздел «Контакты»\n"
            "⚡ <b>Быстрые команды:</b>\n"
            "/start - Главное меню\n"
            "/menu - Показать меню"
        )
    await callback.message.edit_text(
        help_text,
        parse_mode="HTML",
        reply_markup=get_back_menu()
    )
    await callback.answer()

# Обработка состояний заказа такси
@dp.message(OrderState.waiting_for_pickup)
async def process_pickup(message: types.Message, state: FSMContext):
    if await check_ban(message.from_user.id):
        await state.clear()
        return
    if not message.text:
        await message.answer("📍 Пожалуйста, отправьте точку отправления текстом.")
        return
    await state.update_data(pickup=message.text)
    await message.answer(
        "📍 Теперь отправьте <b>пункт назначения</b>:",
        parse_mode="HTML",
        reply_markup=get_back_menu()
    )
    await state.set_state(OrderState.waiting_for_destination)

@dp.message(OrderState.waiting_for_destination)
async def process_destination(message: types.Message, state: FSMContext):
    if await check_ban(message.from_user.id):
        await state.clear()
        return
    if not message.text:
        await message.answer("📍 Пожалуйста, отправьте пункт назначения текстом.")
        return
    data = await state.get_data()
    pickup = data["pickup"]
    destination = message.text
    # Создаем заказ
    trip_id = create_trip(message.from_user.id, pickup, destination)
    sent_passenger = await message.answer(
        "🚕 <b>Ваш заказ принят в обработку</b>\n"
        f"📍 <b>Откуда:</b> {pickup}\n"
        f"📍 <b>Куда:</b> {destination}\n"
        "⏳ Ожидайте подтверждения от ближайшего водителя...",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="❌ Отменить заказ", callback_data=f"cancel_passenger_{trip_id}")]
        ])
    )
    update_passenger_message_id(trip_id, sent_passenger.message_id)
    # Рассылка только активным водителям
    active_drivers = [driver_id for driver_id in ACTIVE_DRIVERS if not is_user_banned(driver_id)]
    if not active_drivers:
        await message.answer("❌ В данный момент нет активных водителей.")
    else:
        ACTIVE_ORDER_MESSAGES[trip_id] = {}
        for driver_id in active_drivers:
            sent_driver = await bot.send_message(
                driver_id,
                "🚕 <b>Новый заказ!</b>\n"
                f"📍 <b>Откуда:</b> {pickup}\n"
                f"📍 <b>Куда:</b> {destination}\n"
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

# Callback-хендлеры для заказов
@dp.callback_query(lambda c: c.data.startswith("accept_"))
async def accept_trip(callback: types.CallbackQuery):
    if await check_ban(callback.from_user.id):
        return
    # Проверяем, активен ли водитель
    if callback.from_user.id not in ACTIVE_DRIVERS:
        await callback.answer("❌ Сначала станьте доступным для получения заказов.", show_alert=True)
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
        # Улучшенное сообщение для водителя
        await callback.message.edit_text(
            f"✅ <b>Заказ принят!</b>\n"
            f"📋 <b>Заказ №{trip_id}</b>\n"
            f"📍 <b>Откуда:</b> {trip[4]}\n"
            f"📍 <b>Куда:</b> {trip[5]}\n"
            f"Выберите тариф или укажите свою стоимость:",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="❌ Отменить заказ", callback_data=f"cancel_driver_{trip_id}")]
            ])
        )
        update_driver_message_id(trip_id, callback.message.message_id)
        # Тарифы + кнопка ручного ввода
        tariffs = get_tariffs()
        kb_rows = []
        for _, name, price in tariffs:
            kb_rows.append([InlineKeyboardButton(text=f"{name} — {price} ₽", callback_data=f"setfare_{trip_id}_{price}")])
        # Добавляем кнопку для ручного ввода суммы
        kb_rows.append([InlineKeyboardButton(text="💰 Указать свою сумму", callback_data=f"manual_fare_{trip_id}")])
        tariff_message = await bot.send_message(
            callback.from_user.id, 
            "Выберите тариф для поездки:", 
            reply_markup=InlineKeyboardMarkup(inline_keyboard=kb_rows)
        )
        update_driver_tariff_message_id(trip_id, tariff_message.message_id)
        # Отправляем информацию о водителе пассажиру
        cur = DB.cursor()
        driver = cur.execute("""
            SELECT full_name, car_brand, car_model, license_plate, phone_number, payment_number, bank_name
            FROM users WHERE telegram_id = ?
        """, (callback.from_user.id,)).fetchone()
        driver_rating = get_driver_rating(callback.from_user.id)
        if driver:
            full_name, car_brand, car_model, license_plate, phone_number, payment_number, bank_name = driver
            car_info = f"{car_brand} {car_model}".strip()
            rating_text = f"⭐ <b>Рейтинг:</b> {driver_rating}/5" if driver_rating else "⭐ <b>Рейтинг:</b> пока нет оценок"
            driver_card = (
                "👤 <b>Ваш водитель:</b>\n"
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

@dp.callback_query(lambda c: c.data.startswith("manual_fare_"))
async def manual_fare_callback(callback: types.CallbackQuery, state: FSMContext):
    if await check_ban(callback.from_user.id):
        return
    trip_id = int(callback.data.split("_")[2])
    await state.update_data(trip_id=trip_id)
    await callback.message.edit_text(
        "💰 <b>Укажите стоимость поездки</b>\n"
        "Введите сумму в рублях (например: 250 или 350.50):",
        parse_mode="HTML",
        reply_markup=get_back_menu()
    )
    await state.set_state(ManualFareState.waiting_for_fare)
    await callback.answer()

@dp.message(ManualFareState.waiting_for_fare)
async def process_manual_fare(message: types.Message, state: FSMContext):
    if await check_ban(message.from_user.id):
        await state.clear()
        return
    try:
        fare = float(message.text.replace(',', '.'))
        if fare <= 0:
            await message.answer("❌ Стоимость должна быть положительным числом.")
            return
    except ValueError:
        await message.answer("❌ Пожалуйста, введите корректную сумму (например: 250 или 350.50).")
        return
    data = await state.get_data()
    trip_id = data["trip_id"]
    # Сохраняем стоимость
    cur = DB.cursor()
    cur.execute("UPDATE trips SET fare = ? WHERE id = ?", (fare, trip_id))
    DB.commit()
    trip = get_trip(trip_id)
    passenger_id = trip[1]
    # Обновляем сообщение для водителя
    await message.answer(
        f"✅ <b>Стоимость установлена:</b> {fare:.2f} ₽",
        parse_mode="HTML"
    )
    # Отправляем информацию о стоимости пассажиру
    sent_fare = await bot.send_message(
        passenger_id,
        f"💰 <b>Стоимость поездки:</b> {fare:.2f} ₽\n"
        f"Водитель скоро приедет!",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="❌ Отменить заказ", callback_data=f"cancel_passenger_{trip_id}")]
        ])
    )
    update_passenger_fare_message_id(trip_id, sent_fare.message_id)
    # Предлагаем выбрать время прибытия
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
    eta_select_message = await bot.send_message(
        message.from_user.id, 
        "⏱️ <b>Укажите ориентировочное время прибытия:</b>", 
        parse_mode="HTML",
        reply_markup=time_kb
    )
    update_driver_eta_select_message_id(trip_id, eta_select_message.message_id)
    await state.clear()

@dp.callback_query(lambda c: c.data.startswith("reject_"))
async def reject_trip(callback: types.CallbackQuery):
    if await check_ban(callback.from_user.id):
        return
    trip_id = int(callback.data.split("_")[1])
    try:
        await callback.message.delete()
    except:
        pass
    if trip_id in ACTIVE_ORDER_MESSAGES and callback.from_user.id in ACTIVE_ORDER_MESSAGES[trip_id]:
        del ACTIVE_ORDER_MESSAGES[trip_id][callback.from_user.id]
    await callback.answer("Вы отказались от заказа")

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
    update_driver_fare_message_id(trip_id, callback.message.message_id)
    # Улучшенное сообщение для водителя
    await callback.message.edit_text(
        f"✅ <b>Стоимость установлена:</b> {fare:.2f} ₽",
        parse_mode="HTML"
    )
    # Улучшенное сообщение для пассажира
    sent_fare = await bot.send_message(
        passenger_id,
        f"💰 <b>Стоимость поездки:</b> {fare:.2f} ₽\n"
        f"Водитель скоро приедет!",
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
    eta_select_message = await bot.send_message(
        callback.from_user.id, 
        "⏱️ <b>Укажите ориентировочное время прибытия:</b>", 
        parse_mode="HTML",
        reply_markup=time_kb
    )
    update_driver_eta_select_message_id(trip_id, eta_select_message.message_id)

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
    sent_eta = await bot.send_message(
        passenger_id, 
        f"⏱️ <b>{text}</b>",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="❌ Отменить заказ", callback_data=f"cancel_passenger_{trip_id}")]
        ])
    )
    update_passenger_eta_message_id(trip_id, sent_eta.message_id)
    update_driver_eta_message_id(trip_id, callback.message.message_id)
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
        f"✅ <b>Время прибытия отправлено пассажиру.</b>\n"
        f"{text}\n"
        f"<b>Используйте кнопки ниже для управления поездкой:</b>",
        parse_mode="HTML",
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
    try:
        arrival_message = await bot.send_message(
            trip[1], 
            "🚗 <b>Водитель подтвердил прибытие! Поездка началась.</b>",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="❌ Отменить заказ", callback_data=f"cancel_passenger_{trip_id}")]
            ])
        )
        update_passenger_arrival_message_id(trip_id, arrival_message.message_id)
    except:
        pass
    complete_kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Завершить поездку", callback_data=f"complete_{trip_id}")],
        [InlineKeyboardButton(text="❌ Отменить заказ", callback_data=f"cancel_driver_{trip_id}")]
    ])
    await callback.message.edit_text(
        "✅ <b>Вы подтвердили прибытие. Поездка началась!</b>\n"
        "Нажмите 'Завершить поездку' после окончания поездки.",
        parse_mode="HTML",
        reply_markup=complete_kb
    )
    await callback.answer()

@dp.callback_query(lambda c: c.data.startswith("cancel_driver_") and not c.data.startswith("cancel_driver_reason_"))
async def cancel_by_driver(callback: types.CallbackQuery):
    if await check_ban(callback.from_user.id):
        return
    trip_id = int(callback.data.split("_")[2])
    reasons = get_cancellation_reasons('driver')
    if not reasons:
        await callback.answer("Нет доступных причин отмены")
        return
    keyboard = [[InlineKeyboardButton(text=reason_text, callback_data=f"cancel_driver_reason_{trip_id}_{reason_id}")] for reason_id, reason_text in reasons]
    await callback.message.edit_text("Выберите причину отмены заказа:", reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard))
    await callback.answer()

@dp.callback_query(lambda c: c.data.startswith("cancel_driver_reason_"))
async def cancel_driver_reason(callback: types.CallbackQuery):
    if await check_ban(callback.from_user.id):
        return
    parts = callback.data.split("_")
    trip_id = int(parts[3])
    reason_id = int(parts[4])
    cur = DB.cursor()
    reason = cur.execute("SELECT reason_text FROM cancellation_reasons WHERE id = ?", (reason_id,)).fetchone()
    reason_text = reason[0] if reason else "Не указана"
    await cancel_trip_cleanup(trip_id, 'driver', reason_text)
    await callback.answer()

@dp.callback_query(lambda c: c.data.startswith("cancel_passenger_") and not c.data.startswith("cancel_passenger_reason_"))
async def cancel_by_passenger(callback: types.CallbackQuery):
    if await check_ban(callback.from_user.id):
        return
    trip_id = int(callback.data.split("_")[2])
    reasons = get_cancellation_reasons('passenger')
    if not reasons:
        await callback.answer("Нет доступных причин отмены")
        return
    keyboard = [[InlineKeyboardButton(text=reason_text, callback_data=f"cancel_passenger_reason_{trip_id}_{reason_id}")] for reason_id, reason_text in reasons]
    await callback.message.edit_text("Выберите причину отмены заказа:", reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard))
    await callback.answer()

@dp.callback_query(lambda c: c.data.startswith("cancel_passenger_reason_"))
async def cancel_passenger_reason(callback: types.CallbackQuery):
    if await check_ban(callback.from_user.id):
        return
    parts = callback.data.split("_")
    trip_id = int(parts[3])
    reason_id = int(parts[4])
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
    messages_to_delete = [
        (passenger_id, trip[11]), (driver_id, trip[12]), (passenger_id, trip[13]),
        (passenger_id, trip[14]), (passenger_id, trip[15]), (driver_id, trip[16]),
        (driver_id, trip[17]), (driver_id, trip[18]), (passenger_id, trip[19]),
        (driver_id, trip[21]), (driver_id, trip[22])
    ]
    for chat_id, msg_id in messages_to_delete:
        if msg_id:
            try:
                await bot.delete_message(chat_id=chat_id, message_id=msg_id)
            except:
                pass
    try:
        await callback.message.delete()
    except:
        pass
    try:
        await bot.send_message(
            passenger_id,
            "🏁 <b>Поездка завершена!</b>\n"
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
    try:
        await bot.send_message(
            driver_id,
            f"✅ <b>Заказ завершён.</b>\n"
            f"💰 <b>Заработано:</b> {fare:.2f} ₽\n"
            f"Ожидайте оценку от пассажира...",
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
        save_rating(trip_id, trip[2], callback.from_user.id, rating)
        await callback.message.edit_text(
            f"✅ <b>Спасибо за оценку!</b>\n"
            f"Вы поставили {rating} ⭐\n"
            f"Будем рады видеть вас снова! 🚖",
            parse_mode="HTML"
        )
        # После оценки показываем главное меню
        user_role = get_user_role(callback.from_user.id) or "passenger"
        if user_role == "driver":
            await callback.message.answer("Главное меню:", reply_markup=get_driver_menu())
        else:
            await callback.message.answer("Главное меню:", reply_markup=get_main_menu())
        driver_rating = get_driver_rating(trip[2])
        try:
            await bot.send_message(
                trip[2],
                f"⭐ <b>Пассажир оценил вашу работу:</b> {rating}/5\n"
                f"📊 <b>Ваш текущий рейтинг:</b> {driver_rating or 'еще нет оценок'}",
                parse_mode="HTML"
            )
        except:
            pass
    except Exception as e:
        print(f"Ошибка в оценке: {e}")
        await callback.answer("Произошла ошибка.", show_alert=True)
    await callback.answer()

# Фоновые задачи
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
                    except Exception as e:
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
                SELECT id, passenger_id
                FROM trips
                WHERE status = 'requested'
                  AND datetime(created_at) < datetime('now', '-{} minutes')
            """.format(ORDER_TIMEOUT))
            expired = cur.fetchall()
            for trip_id, passenger_id in expired:
                cur2 = DB.cursor()
                cur2.execute("UPDATE trips SET status = 'expired' WHERE id = ?", (trip_id,))
                DB.commit()
                # Удаляем карточку у пассажира
                if trip_id in ACTIVE_ORDER_MESSAGES:
                    for drv_id, msg_id in ACTIVE_ORDER_MESSAGES[trip_id].items():
                        try:
                            await bot.delete_message(chat_id=drv_id, message_id=msg_id)
                        except:
                            pass
                    del ACTIVE_ORDER_MESSAGES[trip_id]
                # Получаем ID сообщения пассажира из БД
                cur3 = DB.cursor()
                cur3.execute("SELECT passenger_message_id FROM trips WHERE id = ?", (trip_id,))
                passenger_msg_id = cur3.fetchone()
                if passenger_msg_id and passenger_msg_id[0]:
                    try:
                        await bot.delete_message(chat_id=passenger_id, message_id=passenger_msg_id[0])
                    except:
                        pass
                # Отправляем уведомление пассажиру
                try:
                    await bot.send_message(
                        passenger_id,
                        f"❌ <b>Заказ автоматически отменён</b>\n"
                        f"Никто не принял его в течение {ORDER_TIMEOUT} минут.",
                        parse_mode="HTML"
                    )
                except:
                    pass
        except Exception as e:
            print(f"Ошибка в cancel_expired_orders: {e}")
        await asyncio.sleep(30)

# Запуск дашборда
import dashboard
def run_flask():
    flask_app = dashboard.create_app()
    flask_app.run(host="0.0.0.0", port=5000, debug=False, use_reloader=False)

async def main():
    flask_thread = threading.Thread(target=run_flask, daemon=True)
    flask_thread.start()
    print("🚀 Flask дашборд запущен на http://0.0.0.0:5000")
    asyncio.create_task(process_broadcast_queue())
    asyncio.create_task(cancel_expired_orders())
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
