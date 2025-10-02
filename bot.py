import logging
import sqlite3
import json
from datetime import datetime, timedelta
from aiogram import Bot, Dispatcher, Router, F
from aiogram.types import Message, CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.utils.keyboard import InlineKeyboardBuilder
import asyncio

# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('taxi_bot.log', encoding='utf-8'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# Конфигурация
ADMIN_IDS = [7780744086]  # Замените на реальные ID администраторов
BOT_TOKEN = "8297146262:AAG72LEJM2xVds5KDEoB0dJb52iwz8W4_qw"

# Инициализация базы данных
def init_db():
    conn = sqlite3.connect('taxi_bot.db')
    cursor = conn.cursor()
    
    # Пользователи
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            username TEXT,
            first_name TEXT,
            role TEXT CHECK(role IN ('driver', 'passenger', 'admin', 'user')),
            registration_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            is_banned BOOLEAN DEFAULT FALSE,
            ban_reason TEXT,
            ban_until TIMESTAMP
        )
    ''')
    
    # Данные водителей
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS drivers (
            user_id INTEGER PRIMARY KEY,
            name TEXT,
            car_brand TEXT,
            car_model TEXT,
            license_plate TEXT,
            car_color TEXT,
            contact_phone TEXT,
            payment_phone TEXT,
            bank TEXT,
            total_orders INTEGER DEFAULT 0,
            completed_orders INTEGER DEFAULT 0,
            canceled_orders INTEGER DEFAULT 0,
            today_orders INTEGER DEFAULT 0,
            total_earnings REAL DEFAULT 0,
            today_earnings REAL DEFAULT 0,
            last_active_date DATE DEFAULT CURRENT_DATE,
            FOREIGN KEY (user_id) REFERENCES users (user_id)
        )
    ''')
    
    # Данные пассажиров
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS passengers (
            user_id INTEGER PRIMARY KEY,
            name TEXT,
            contact_phone TEXT,
            FOREIGN KEY (user_id) REFERENCES users (user_id)
        )
    ''')
    
    # Заказы
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS orders (
            order_id INTEGER PRIMARY KEY AUTOINCREMENT,
            passenger_id INTEGER,
            driver_id INTEGER,
            from_location TEXT,
            to_location TEXT,
            price REAL,
            status TEXT CHECK(status IN ('searching', 'accepted', 'waiting', 'completed', 'canceled')),
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            accepted_at TIMESTAMP,
            completed_at TIMESTAMP,
            canceled_reason TEXT,
            canceled_by TEXT,
            passenger_message_id INTEGER,
            driver_message_id INTEGER,
            FOREIGN KEY (passenger_id) REFERENCES users (user_id),
            FOREIGN KEY (driver_id) REFERENCES users (user_id)
        )
    ''')
    
    # Активные заказы водителей (для ограничения 2 заказов)
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS driver_active_orders (
            driver_id INTEGER,
            order_id INTEGER,
            PRIMARY KEY (driver_id, order_id),
            FOREIGN KEY (driver_id) REFERENCES users (user_id),
            FOREIGN KEY (order_id) REFERENCES orders (order_id)
        )
    ''')
    
    # Сообщения заказов для водителей
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS driver_order_messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            driver_id INTEGER,
            order_id INTEGER,
            message_id INTEGER,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (driver_id) REFERENCES users (user_id),
            FOREIGN KEY (order_id) REFERENCES orders (order_id)
        )
    ''')
    
    # Уведомительные сообщения пассажиров
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS passenger_notifications (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            passenger_id INTEGER,
            order_id INTEGER,
            message_id INTEGER,
            message_type TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (passenger_id) REFERENCES users (user_id),
            FOREIGN KEY (order_id) REFERENCES orders (order_id)
        )
    ''')
    
    # Рабочие сообщения водителей
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS driver_working_messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            driver_id INTEGER,
            order_id INTEGER,
            message_id INTEGER,
            message_type TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (driver_id) REFERENCES users (user_id),
            FOREIGN KEY (order_id) REFERENCES orders (order_id)
        )
    ''')
    
    conn.commit()
    conn.close()
    logger.info("База данных инициализирована")

# States для FSM
class PassengerRegistration(StatesGroup):
    name = State()
    contact_phone = State()

class OrderCreation(StatesGroup):
    from_location = State()
    to_location = State()
    price = State()

class DriverRegistration(StatesGroup):
    name = State()
    car_brand = State()
    car_model = State()
    license_plate = State()
    car_color = State()
    contact_phone = State()
    payment_phone = State()
    bank = State()

class DriverEdit(StatesGroup):
    name = State()
    car_brand = State()
    car_model = State()
    license_plate = State()
    car_color = State()
    contact_phone = State()
    payment_phone = State()
    bank = State()

class AdminBan(StatesGroup):
    user_id = State()
    reason = State()
    duration = State()

class TaxiBot:
    def __init__(self, token):
        self.bot = Bot(token=token)
        self.storage = MemoryStorage()
        self.dp = Dispatcher(storage=self.storage)
        self.router = Router()
        self.dp.include_router(self.router)
        self.setup_handlers()
        
    def setup_handlers(self):
        # Команды
        self.router.message.register(self.start, CommandStart())
        
        # Обработчики callback запросов
        self.router.callback_query.register(self.role_handler, F.data.startswith("role_"))
        self.router.callback_query.register(self.driver_handler, F.data.startswith("driver_"))
        self.router.callback_query.register(self.passenger_handler, F.data.startswith("passenger_"))
        self.router.callback_query.register(self.order_handler, F.data.startswith("order_"))
        self.router.callback_query.register(self.admin_handler, F.data.startswith("admin_"))
        
        # Обработчики состояний
        self.router.message.register(self.process_passenger_name, PassengerRegistration.name)
        self.router.message.register(self.process_passenger_phone, PassengerRegistration.contact_phone)
        
        self.router.message.register(self.process_order_from, OrderCreation.from_location)
        self.router.message.register(self.process_order_to, OrderCreation.to_location)
        self.router.message.register(self.process_order_price, OrderCreation.price)
        
        self.router.message.register(self.process_driver_registration_name, DriverRegistration.name)
        self.router.message.register(self.process_driver_registration_car_brand, DriverRegistration.car_brand)
        self.router.message.register(self.process_driver_registration_car_model, DriverRegistration.car_model)
        self.router.message.register(self.process_driver_registration_license_plate, DriverRegistration.license_plate)
        self.router.message.register(self.process_driver_registration_car_color, DriverRegistration.car_color)
        self.router.message.register(self.process_driver_registration_contact_phone, DriverRegistration.contact_phone)
        self.router.message.register(self.process_driver_registration_payment_phone, DriverRegistration.payment_phone)
        self.router.message.register(self.process_driver_registration_bank, DriverRegistration.bank)
        
        self.router.message.register(self.process_driver_edit_name, DriverEdit.name)
        self.router.message.register(self.process_driver_edit_car_brand, DriverEdit.car_brand)
        self.router.message.register(self.process_driver_edit_car_model, DriverEdit.car_model)
        self.router.message.register(self.process_driver_edit_license_plate, DriverEdit.license_plate)
        self.router.message.register(self.process_driver_edit_car_color, DriverEdit.car_color)
        self.router.message.register(self.process_driver_edit_contact_phone, DriverEdit.contact_phone)
        self.router.message.register(self.process_driver_edit_payment_phone, DriverEdit.payment_phone)
        self.router.message.register(self.process_driver_edit_bank, DriverEdit.bank)
        
        self.router.message.register(self.process_admin_ban_user, AdminBan.user_id)
        self.router.message.register(self.process_admin_ban_reason, AdminBan.reason)
        self.router.message.register(self.process_admin_ban_duration, AdminBan.duration)
        
        # Общие сообщения
        self.router.message.register(self.handle_message)
    
    async def start(self, message: Message, state: FSMContext):
        user_id = message.from_user.id
        username = message.from_user.username
        first_name = message.from_user.first_name
        
        # Автоматическая регистрация пользователя при входе
        if not self.user_exists(user_id):
            self.register_user(user_id, username, first_name)
            logger.info(f"Новый пользователь зарегистрирован: {user_id} ({first_name})")
        
        # Проверка бана
        if self.is_user_banned(user_id):
            ban_info = self.get_ban_info(user_id)
            await message.answer(
                f"❌ Вы забанены до {ban_info['until']}. Причина: {ban_info['reason']}"
            )
            return
        
        await self.show_main_menu(message, state)
    
    async def show_main_menu(self, message: Message, state: FSMContext):
        user_id = message.from_user.id
        user_role = self.get_user_role(user_id)
        
        if user_role == 'driver':
            await self.show_driver_menu(message)
        elif user_role == 'passenger':
            await self.show_passenger_menu(message)
        elif user_role == 'admin':
            await self.show_admin_menu(message)
        else:
            # Пользователь без роли
            keyboard = [
                [InlineKeyboardButton(text="👤 Стать пассажиром", callback_data="role_passenger")]
            ]
            if user_id in ADMIN_IDS:
                keyboard.append([InlineKeyboardButton(text="👑 Администратор", callback_data="role_admin")])
            
            reply_markup = InlineKeyboardMarkup(inline_keyboard=keyboard)
            await message.answer(
                "👋 Добро пожаловать в такси-сервис!\n"
                "Выберите свою роль:",
                reply_markup=reply_markup
            )
    
    async def role_handler(self, callback: CallbackQuery, state: FSMContext):
        data = callback.data
        user_id = callback.from_user.id
        
        if data == "role_passenger":
            current_role = self.get_user_role(user_id)
            if current_role == 'driver':
                await callback.message.edit_text("❌ Вы уже зарегистрированы как водитель!")
                return
            
            if not self.is_passenger_registered(user_id):
                await state.set_state(PassengerRegistration.name)
                await callback.message.edit_text(
                    "👤 Регистрация пассажира\n\n"
                    "Пожалуйста, введите ваше имя:"
                )
            else:
                self.update_user_role(user_id, 'passenger')
                await self.show_passenger_menu_from_callback(callback)
        
        elif data == "role_admin":
            if user_id in ADMIN_IDS:
                self.update_user_role(user_id, 'admin')
                await self.show_admin_menu_from_callback(callback)
        
        await callback.answer()
    
    # === РЕГИСТРАЦИЯ ПАССАЖИРА ===
    async def process_passenger_name(self, message: Message, state: FSMContext):
        await state.update_data(name=message.text)
        await state.set_state(PassengerRegistration.contact_phone)
        await message.answer("Введите ваш номер телефона для связи:")
    
    async def process_passenger_phone(self, message: Message, state: FSMContext):
        user_id = message.from_user.id
        data = await state.get_data()
        data['contact_phone'] = message.text
        
        # Сохранение данных пассажира
        self.save_passenger_data(user_id, data)
        self.update_user_role(user_id, 'passenger')
        
        # Очистка состояния
        await state.clear()
        
        await message.answer("✅ Регистрация пассажира завершена!")
        await self.show_passenger_menu(message)
    
    # === СОЗДАНИЕ ЗАКАЗА ===
    async def process_order_from(self, message: Message, state: FSMContext):
        await state.update_data(from_location=message.text)
        await state.set_state(OrderCreation.to_location)
        await message.answer("Укажите куда поедем:")
    
    async def process_order_to(self, message: Message, state: FSMContext):
        await state.update_data(to_location=message.text)
        await state.set_state(OrderCreation.price)
        await message.answer("Укажите цену, которую готовы заплатить:")
    
    async def process_order_price(self, message: Message, state: FSMContext):
        user_id = message.from_user.id
        try:
            price = float(message.text)
            if price <= 0:
                await message.answer("❌ Цена должна быть положительным числом!")
                return
            
            data = await state.get_data()
            data['price'] = price
            
            # Создание заказа
            order_id = self.create_order(user_id, data)
            
            # Очистка состояния
            await state.clear()
            
            # Показать карточку заказа пассажиру
            order_info = self.get_order_info(order_id)
            passenger_info = self.get_passenger_info(user_id)
            
            order_text = (
                f"📍 Из: {order_info['from_location']}\n"
                f"🎯 В: {order_info['to_location']}\n"
                f"💰 Цена: {order_info['price']} руб.\n"
                f"👤 Пассажир: {passenger_info['name']}\n"
                f"📞 Телефон: {passenger_info['contact_phone']}\n"
                f"{'─' * 20}\n"
                f"⏳ Ожидайте принятия заказа водителем..."
            )
            
            keyboard = [[InlineKeyboardButton(text="❌ Отменить заказ", callback_data=f"passenger_cancel_{order_id}")]]
            reply_markup = InlineKeyboardMarkup(inline_keyboard=keyboard)
            
            message_obj = await message.answer(order_text, reply_markup=reply_markup)
            
            # Сохраняем ID сообщения заказа
            self.update_order_message_id(order_id, 'passenger', message_obj.message_id)
            
            # Сразу рассылаем заказ всем водителям
            await self.broadcast_order_to_drivers(order_id)
            
        except ValueError:
            await message.answer("❌ Пожалуйста, введите корректную цену (число)!")
    
    async def broadcast_order_to_drivers(self, order_id: int):
        """Рассылает заказ всем активным водителям - ОДИН РАЗ"""
        # Проверяем, не был ли уже отправлен этот заказ
        if self.is_order_already_broadcasted(order_id):
            logger.info(f"Заказ #{order_id} уже был разослан водителям, пропускаем")
            return
            
        order_info = self.get_order_info(order_id)
        if not order_info:
            logger.error(f"Заказ #{order_id} не найден для рассылки")
            return
            
        passenger_info = self.get_passenger_info(order_info['passenger_id'])
        
        order_text = (
            f"📍 Из: {order_info['from_location']}\n"
            f"🎯 В: {order_info['to_location']}\n"
            f"💰 Цена: {order_info['price']} руб.\n"
            f"👤 Пассажир: {passenger_info['name']}\n"
            f"📞 Телефон: {passenger_info['contact_phone']}\n"
            f"{'─' * 20}"
        )
        
        keyboard = [[InlineKeyboardButton(
            text=f"Принять заказ #{order_id}", 
            callback_data=f"order_accept_{order_id}"
        )]]
        reply_markup = InlineKeyboardMarkup(inline_keyboard=keyboard)
        
        drivers = self.get_all_active_drivers()
        logger.info(f"Рассылаем заказ #{order_id} {len(drivers)} водителям")
        
        sent_count = 0
        for driver in drivers:
            try:
                message_obj = await self.bot.send_message(
                    driver['user_id'], 
                    order_text, 
                    reply_markup=reply_markup
                )
                # Сохраняем ID сообщения для водителя
                self.save_driver_order_message(driver['user_id'], order_id, message_obj.message_id)
                sent_count += 1
            except Exception as e:
                logger.error(f"Не удалось отправить заказ водителю {driver['user_id']}: {e}")
        
        logger.info(f"Заказ #{order_id} успешно разослан {sent_count} водителям")
        # Помечаем заказ как разосланный
        self.mark_order_as_broadcasted(order_id)
    
    # === ПАССАЖИРСКИЙ ФУНКЦИОНАЛ ===
    async def passenger_handler(self, callback: CallbackQuery, state: FSMContext):
        data = callback.data
        user_id = callback.from_user.id
        
        if data == "passenger_order":
            await state.set_state(OrderCreation.from_location)
            await callback.message.edit_text(
                "🚖 Создание заказа\n\n"
                "Укажите откуда вас забрать (только текст):"
            )
        
        elif data.startswith("passenger_cancel_"):
            order_id = int(data.split("_")[2])
            await self.show_passenger_cancel_reasons(callback, order_id)
        
        await callback.answer()
    
    async def show_passenger_menu(self, message: Message):
        user_id = message.from_user.id
        
        keyboard = []
        if user_id in ADMIN_IDS:
            keyboard.append([InlineKeyboardButton(text="👑 Админ панель", callback_data="admin_back")])
        keyboard.append([InlineKeyboardButton(text="🚖 Сделать заказ", callback_data="passenger_order")])
        
        reply_markup = InlineKeyboardMarkup(inline_keyboard=keyboard)
        await message.answer("👤 Меню пассажира\nВыберите действие:", reply_markup=reply_markup)
    
    async def show_passenger_menu_from_callback(self, callback: CallbackQuery):
        user_id = callback.from_user.id
        
        keyboard = []
        if user_id in ADMIN_IDS:
            keyboard.append([InlineKeyboardButton(text="👑 Админ панель", callback_data="admin_back")])
        keyboard.append([InlineKeyboardButton(text="🚖 Сделать заказ", callback_data="passenger_order")])
        
        reply_markup = InlineKeyboardMarkup(inline_keyboard=keyboard)
        await callback.message.edit_text(
            "👤 Меню пассажира\nВыберите действие:",
            reply_markup=reply_markup
        )
    
    async def show_passenger_cancel_reasons(self, callback: CallbackQuery, order_id: int):
        keyboard = [
            [InlineKeyboardButton(text="⏰ Долгое ожидание", callback_data=f"order_cancel_reason_{order_id}_long_wait")],
            [InlineKeyboardButton(text="🤔 Передумал", callback_data=f"order_cancel_reason_{order_id}_changed_mind")],
            [InlineKeyboardButton(text="👎 Не устраивает водитель", callback_data=f"order_cancel_reason_{order_id}_bad_driver")],
            [InlineKeyboardButton(text="🚗 Не устраивает машина", callback_data=f"order_cancel_reason_{order_id}_bad_car")]
        ]
        reply_markup = InlineKeyboardMarkup(inline_keyboard=keyboard)
        await callback.message.edit_text("Выберите причину отмены:", reply_markup=reply_markup)
    
    # === ВОДИТЕЛЬСКИЙ ФУНКЦИОНАЛ ===
    async def driver_handler(self, callback: CallbackQuery, state: FSMContext):
        data = callback.data
        user_id = callback.from_user.id
        
        if data == "driver_stats":
            await self.show_driver_stats(callback, user_id)
        
        elif data == "driver_edit":
            await state.set_state(DriverEdit.name)
            await callback.message.answer("Введите ваше имя для изменения:")
        
        elif data == "driver_back":
            await self.show_driver_menu_from_callback(callback)
        
        await callback.answer()
    
    async def show_driver_menu(self, message: Message):
        user_id = message.from_user.id
        
        keyboard = [
            [InlineKeyboardButton(text="📊 Статистика заказов", callback_data="driver_stats")],
            [InlineKeyboardButton(text="✏️ Изменить свои данные", callback_data="driver_edit")]
        ]
        
        if user_id in ADMIN_IDS:
            keyboard.append([InlineKeyboardButton(text="👑 Админ панель", callback_data="admin_back")])
        
        reply_markup = InlineKeyboardMarkup(inline_keyboard=keyboard)
        await message.answer("🚗 Меню водителя\nВыберите действие:", reply_markup=reply_markup)
    
    async def show_driver_menu_from_callback(self, callback: CallbackQuery):
        user_id = callback.from_user.id
        
        keyboard = [
            [InlineKeyboardButton(text="📊 Статистика заказов", callback_data="driver_stats")],
            [InlineKeyboardButton(text="✏️ Изменить свои данные", callback_data="driver_edit")]
        ]
        
        if user_id in ADMIN_IDS:
            keyboard.append([InlineKeyboardButton(text="👑 Админ панель", callback_data="admin_back")])
        
        reply_markup = InlineKeyboardMarkup(inline_keyboard=keyboard)
        await callback.message.edit_text(
            "🚗 Меню водителя\nВыберите действие:",
            reply_markup=reply_markup
        )
    
    async def show_driver_stats(self, callback: CallbackQuery, user_id: int):
        stats = self.get_driver_stats(user_id)
        text = (
            f"📊 Статистика водителя\n\n"
            f"📦 Всего заказов: {stats['total_orders']}\n"
            f"✅ Выполнено: {stats['completed_orders']}\n"
            f"❌ Отменено: {stats['canceled_orders']}\n"
            f"📅 За сегодня: {stats['today_orders']}\n"
            f"💰 Заработано всего: {stats['total_earnings']:.2f} руб.\n"
            f"💰 Заработано за сегодня: {stats['today_earnings']:.2f} руб."
        )
        
        keyboard = [[InlineKeyboardButton(text="🔙 Назад", callback_data="driver_back")]]
        reply_markup = InlineKeyboardMarkup(inline_keyboard=keyboard)
        
        await callback.message.edit_text(text, reply_markup=reply_markup)
    
    # === РЕГИСТРАЦИЯ И РЕДАКТИРОВАНИЕ ВОДИТЕЛЯ ===
    async def process_driver_registration_name(self, message: Message, state: FSMContext):
        await state.update_data(name=message.text)
        await state.set_state(DriverRegistration.car_brand)
        await message.answer("Введите марку автомобиля:")
    
    async def process_driver_registration_car_brand(self, message: Message, state: FSMContext):
        await state.update_data(car_brand=message.text)
        await state.set_state(DriverRegistration.car_model)
        await message.answer("Введите модель автомобиля:")
    
    async def process_driver_registration_car_model(self, message: Message, state: FSMContext):
        await state.update_data(car_model=message.text)
        await state.set_state(DriverRegistration.license_plate)
        await message.answer("Введите гос. номер автомобиля:")
    
    async def process_driver_registration_license_plate(self, message: Message, state: FSMContext):
        await state.update_data(license_plate=message.text)
        await state.set_state(DriverRegistration.car_color)
        await message.answer("Введите цвет автомобиля:")
    
    async def process_driver_registration_car_color(self, message: Message, state: FSMContext):
        await state.update_data(car_color=message.text)
        await state.set_state(DriverRegistration.contact_phone)
        await message.answer("Введите номер телефона для связи:")
    
    async def process_driver_registration_contact_phone(self, message: Message, state: FSMContext):
        await state.update_data(contact_phone=message.text)
        await state.set_state(DriverRegistration.payment_phone)
        await message.answer("Введите номер телефона для оплаты:")
    
    async def process_driver_registration_payment_phone(self, message: Message, state: FSMContext):
        await state.update_data(payment_phone=message.text)
        await state.set_state(DriverRegistration.bank)
        await message.answer("Введите банк:")
    
    async def process_driver_registration_bank(self, message: Message, state: FSMContext):
        user_id = message.from_user.id
        data = await state.get_data()
        data['bank'] = message.text
        
        # Сохранение данных водителя и обновление роли
        self.save_driver_data(user_id, data)
        self.update_user_role(user_id, 'driver')
        
        # Очистка состояния
        await state.clear()
        
        await message.answer("✅ Регистрация водителя завершена!")
        await self.show_driver_menu(message)
    
    # Редактирование данных водителя
    async def process_driver_edit_name(self, message: Message, state: FSMContext):
        await state.update_data(name=message.text)
        await state.set_state(DriverEdit.car_brand)
        await message.answer("Введите марку автомобиля:")
    
    async def process_driver_edit_car_brand(self, message: Message, state: FSMContext):
        await state.update_data(car_brand=message.text)
        await state.set_state(DriverEdit.car_model)
        await message.answer("Введите модель автомобиля:")
    
    async def process_driver_edit_car_model(self, message: Message, state: FSMContext):
        await state.update_data(car_model=message.text)
        await state.set_state(DriverEdit.license_plate)
        await message.answer("Введите гос. номер автомобиля:")
    
    async def process_driver_edit_license_plate(self, message: Message, state: FSMContext):
        await state.update_data(license_plate=message.text)
        await state.set_state(DriverEdit.car_color)
        await message.answer("Введите цвет автомобиля:")
    
    async def process_driver_edit_car_color(self, message: Message, state: FSMContext):
        await state.update_data(car_color=message.text)
        await state.set_state(DriverEdit.contact_phone)
        await message.answer("Введите номер телефона для связи:")
    
    async def process_driver_edit_contact_phone(self, message: Message, state: FSMContext):
        await state.update_data(contact_phone=message.text)
        await state.set_state(DriverEdit.payment_phone)
        await message.answer("Введите номер телефона для оплаты:")
    
    async def process_driver_edit_payment_phone(self, message: Message, state: FSMContext):
        await state.update_data(payment_phone=message.text)
        await state.set_state(DriverEdit.bank)
        await message.answer("Введите банк:")
    
    async def process_driver_edit_bank(self, message: Message, state: FSMContext):
        user_id = message.from_user.id
        data = await state.get_data()
        data['bank'] = message.text
        
        # Сохранение обновленных данных водителя
        self.save_driver_data(user_id, data)
        
        # Очистка состояния
        await state.clear()
        
        await message.answer("✅ Данные водителя успешно обновлены!")
        await self.show_driver_menu(message)
    
    # === ОБРАБОТКА ЗАКАЗОВ ===
    async def order_handler(self, callback: CallbackQuery, state: FSMContext):
        data = callback.data
        user_id = callback.from_user.id
        
        if data.startswith("order_accept_"):
            order_id = int(data.split("_")[2])
            await self.accept_order(callback, user_id, order_id)
        
        elif data.startswith("order_cancel_reason_"):
            parts = data.split("_")
            order_id = int(parts[3])
            reason = "_".join(parts[4:])
            await self.cancel_order_with_reason(callback, order_id, reason, "passenger")
        
        elif data.startswith("order_arrival_"):
            order_id = int(data.split("_")[2])
            minutes = data.split("_")[3]
            await self.set_arrival_time(callback, order_id, minutes)
        
        elif data.startswith("order_waiting_"):
            order_id = int(data.split("_")[2])
            await self.set_waiting_on_spot(callback, order_id)
        
        elif data.startswith("order_complete_"):
            order_id = int(data.split("_")[2])
            await self.complete_order(callback, order_id)
        
        elif data.startswith("order_driver_cancel_"):
            parts = data.split("_")
            order_id = int(parts[3])
            reason = "_".join(parts[4:])
            await self.cancel_order_with_reason(callback, order_id, reason, "driver")
        
        elif data.startswith("order_cancel_menu_"):
            order_id = int(data.split("_")[3])
            await self.show_cancel_menu(callback, order_id)
        
        await callback.answer()
    
    async def accept_order(self, callback: CallbackQuery, driver_id: int, order_id: int):
        # Проверка количества активных заказов
        active_orders_count = self.get_driver_active_orders_count(driver_id)
        if active_orders_count >= 2:
            # Для отладки выведем информацию об активных заказах
            active_orders = self.get_driver_active_orders_info(driver_id)
            logger.warning(f"Водитель {driver_id} пытается взять заказ #{order_id}, но у него уже {active_orders_count} активных заказов: {active_orders}")
            await callback.answer("❌ Вы не можете взять более 2 заказов одновременно!", show_alert=True)
            return
        
        # Принятие заказа
        if self.accept_order_by_driver(order_id, driver_id):
            order_info = self.get_order_info(order_id)
            passenger_id = order_info['passenger_id']
            
            # Удаляем заказ у всех водителей
            await self.remove_order_from_all_drivers(order_id)
            
            # Уведомление пассажира
            try:
                message_obj = await self.bot.send_message(
                    passenger_id,
                    "✅ Водитель найден, ожидайте!"
                )
                # Сохраняем ID уведомительного сообщения
                self.save_passenger_notification_message(passenger_id, order_id, message_obj.message_id, 'driver_found')
            except Exception as e:
                logger.error(f"Не удалось уведомить пассажира {passenger_id}: {e}")
            
            # Создаем правильное сообщение для водителя
            passenger_info = self.get_passenger_info(passenger_id)
            order_text = (
                f"📍 Из: {order_info['from_location']}\n"
                f"🎯 В: {order_info['to_location']}\n"
                f"💰 Цена: {order_info['price']} руб.\n"
                f"👤 Пассажир: {passenger_info['name']}\n"
                f"📞 Телефон: {passenger_info['contact_phone']}\n"
                f"{'─' * 20}\n"
                "Укажите через сколько прибудете на место:"
            )
            
            keyboard = [
                [InlineKeyboardButton(text="5 минут", callback_data=f"order_arrival_{order_id}_5")],
                [InlineKeyboardButton(text="10 минут", callback_data=f"order_arrival_{order_id}_10")],
                [InlineKeyboardButton(text="15 минут", callback_data=f"order_arrival_{order_id}_15")],
                [InlineKeyboardButton(text="20 минут", callback_data=f"order_arrival_{order_id}_20")],
                [InlineKeyboardButton(text="25 минут", callback_data=f"order_arrival_{order_id}_25")],
                [InlineKeyboardButton(text="30 минут", callback_data=f"order_arrival_{order_id}_30")],
                [InlineKeyboardButton(text="Более 30 минут", callback_data=f"order_arrival_{order_id}_more30")]
            ]
            reply_markup = InlineKeyboardMarkup(inline_keyboard=keyboard)
            
            # Удаляем старое сообщение и создаем новое
            try:
                await callback.message.delete()
            except:
                pass  # Игнорируем ошибки удаления
            
            message_obj = await callback.message.answer(order_text, reply_markup=reply_markup)
            # Сохраняем ID сообщения с выбором времени
            self.save_driver_working_message(driver_id, order_id, message_obj.message_id, 'time_selection')
        else:
            await callback.answer("❌ Заказ уже был принят другим водителем!", show_alert=True)
    
    async def set_arrival_time(self, callback: CallbackQuery, order_id: int, minutes: str):
        order_info = self.get_order_info(order_id)
        passenger_id = order_info['passenger_id']
        driver_info = self.get_driver_info(order_info['driver_id'])
        
        # Обновление статуса заказа
        self.update_order_status(order_id, 'waiting')
        
        # Уведомление пассажира
        arrival_text = "более 30 минут" if minutes == "more30" else f"{minutes} минут"
        
        passenger_text = (
            f"🚗 Водитель: {driver_info['name']}\n"
            f"🏎 Марка, модель: {driver_info['car_brand']} {driver_info['car_model']}\n"
            f"🔢 Гос. номер: {driver_info['license_plate']}\n"
            f"🎨 Цвет: {driver_info['car_color']}\n"
            f"📞 Телефон для связи: {driver_info['contact_phone']}\n"
            f"💳 Телефон для оплаты: {driver_info['payment_phone']}\n"
            f"🏦 Банк: {driver_info['bank']}\n\n"
            f"⏱ Прибудет: Через {arrival_text}"
        )
        
        keyboard = [[InlineKeyboardButton(text="❌ Отменить заказ", callback_data=f"passenger_cancel_{order_id}")]]
        reply_markup = InlineKeyboardMarkup(inline_keyboard=keyboard)
        
        try:
            message_obj = await self.bot.send_message(passenger_id, passenger_text, reply_markup=reply_markup)
            # Сохраняем ID сообщения с информацией о водителе
            self.save_passenger_notification_message(passenger_id, order_id, message_obj.message_id, 'driver_info')
        except Exception as e:
            logger.error(f"Не удалось отправить информацию водителя пассажиру {passenger_id}: {e}")
        
        # Обновляем сообщение водителя
        passenger_info = self.get_passenger_info(passenger_id)
        order_text = (
            f"📍 Из: {order_info['from_location']}\n"
            f"🎯 В: {order_info['to_location']}\n"
            f"💰 Цена: {order_info['price']} руб.\n"
            f"👤 Пассажир: {passenger_info['name']}\n"
            f"📞 Телефон: {passenger_info['contact_phone']}\n"
            f"{'─' * 20}\n"
            f"⏱ Вы указали время прибытия: {arrival_text}\n"
            "Пассажир уведомлен."
        )
        
        keyboard = [
            [InlineKeyboardButton(text="✅ Ожидаю на месте", callback_data=f"order_waiting_{order_id}")],
            [InlineKeyboardButton(text="❌ Отменить", callback_data=f"order_cancel_menu_{order_id}")]
        ]
        reply_markup = InlineKeyboardMarkup(inline_keyboard=keyboard)
        
        try:
            await callback.message.delete()
        except:
            pass
        
        message_obj = await callback.message.answer(order_text, reply_markup=reply_markup)
        # Сохраняем ID сообщения с подтверждением времени
        self.save_driver_working_message(order_info['driver_id'], order_id, message_obj.message_id, 'time_confirmation')
    
    async def set_waiting_on_spot(self, callback: CallbackQuery, order_id: int):
        order_info = self.get_order_info(order_id)
        passenger_info = self.get_passenger_info(order_info['passenger_id'])
        
        order_text = (
            f"📍 Из: {order_info['from_location']}\n"
            f"🎯 В: {order_info['to_location']}\n"
            f"💰 Цена: {order_info['price']} руб.\n"
            f"👤 Пассажир: {passenger_info['name']}\n"
            f"📞 Телефон: {passenger_info['contact_phone']}\n"
            f"{'─' * 20}\n"
            f"📍 Ожидаю пассажира на месте\nЗаказ #{order_id}"
        )
        
        keyboard = [
            [InlineKeyboardButton(text="✅ Завершено", callback_data=f"order_complete_{order_id}")],
            [InlineKeyboardButton(text="❌ Отменить", callback_data=f"order_cancel_menu_{order_id}")]
        ]
        reply_markup = InlineKeyboardMarkup(inline_keyboard=keyboard)
        
        try:
            await callback.message.delete()
        except:
            pass
        
        message_obj = await callback.message.answer(order_text, reply_markup=reply_markup)
        # Сохраняем ID сообщения с ожиданием на месте
        self.save_driver_working_message(order_info['driver_id'], order_id, message_obj.message_id, 'waiting_on_spot')
    
    async def complete_order(self, callback: CallbackQuery, order_id: int):
        order_info = self.get_order_info(order_id)
        passenger_id = order_info['passenger_id']
        
        # Завершение заказа
        self.complete_order_in_db(order_id)
        
        # Удаляем все сообщения заказа
        await self.delete_order_messages(order_id)
        
        # Удаляем уведомительные сообщения пассажира
        await self.delete_passenger_notification_messages(passenger_id, order_id)
        
        # Уведомление пассажира
        keyboard = [[InlineKeyboardButton(text="🚖 Сделать заказ", callback_data="passenger_order")]]
        reply_markup = InlineKeyboardMarkup(inline_keyboard=keyboard)
        
        try:
            await self.bot.send_message(
                passenger_id,
                "✅ Поездка завершена. Спасибо! Можете сделать новый заказ.",
                reply_markup=reply_markup
            )
        except Exception as e:
            logger.error(f"Не удалось уведомить пассажира {passenger_id}: {e}")
        
        # Сообщение водителю
        earnings = order_info['price']
        text = f"✅ Заказ #{order_id} завершен\n💰 Заработано: {earnings:.2f} руб."
        
        keyboard = [
            [InlineKeyboardButton(text="📊 Статистика", callback_data="driver_stats")],
            [InlineKeyboardButton(text="✏️ Изменить данные", callback_data="driver_edit")]
        ]
        reply_markup = InlineKeyboardMarkup(inline_keyboard=keyboard)
        
        try:
            await callback.message.delete()
        except:
            pass
        
        await callback.message.answer(text, reply_markup=reply_markup)
    
    async def show_cancel_menu(self, callback: CallbackQuery, order_id: int):
        keyboard = [
            [InlineKeyboardButton(text="⏰ Долгое ожидание", callback_data=f"order_driver_cancel_{order_id}_long_wait")],
            [InlineKeyboardButton(text="👎 Отменен пассажиром", callback_data=f"order_driver_cancel_{order_id}_passenger_canceled")],
            [InlineKeyboardButton(text="🚫 Отменен водителем", callback_data=f"order_driver_cancel_{order_id}_driver_canceled")]
        ]
        reply_markup = InlineKeyboardMarkup(inline_keyboard=keyboard)
        
        try:
            await callback.message.delete()
        except:
            pass
        
        await callback.message.answer("Выберите причину отмены:", reply_markup=reply_markup)
    
    async def cancel_order_with_reason(self, callback: CallbackQuery, order_id: int, reason: str, canceled_by: str):
        order_info = self.get_order_info(order_id)
        reason_text = self.get_reason_text(reason)
        
        # Отмена заказа
        self.cancel_order_in_db(order_id, reason, canceled_by)
        
        # Удаляем ВСЕ сообщения заказа (пассажира и водителя)
        await self.delete_order_messages(order_id)
        
        # Удаляем уведомительные сообщения пассажира
        if order_info and order_info['passenger_id']:
            await self.delete_passenger_notification_messages(order_info['passenger_id'], order_id)
        
        # Уведомление участников
        if canceled_by == "driver":
            passenger_id = order_info['passenger_id']
            keyboard = [[InlineKeyboardButton(text="🚖 Сделать заказ", callback_data="passenger_order")]]
            reply_markup = InlineKeyboardMarkup(inline_keyboard=keyboard)
            
            try:
                await self.bot.send_message(
                    passenger_id,
                    f"❌ Заказ отменен по причине: {reason_text}. Пожалуйста, сделайте новый заказ.",
                    reply_markup=reply_markup
                )
            except Exception as e:
                logger.error(f"Не удалось уведомить пассажира {passenger_id}: {e}")
            
            # Сообщение водителю
            text = f"❌ Вы отменили заказ #{order_id} по причине: {reason_text}."
            keyboard = [
                [InlineKeyboardButton(text="📊 Статистика", callback_data="driver_stats")],
                [InlineKeyboardButton(text="✏️ Изменить данные", callback_data="driver_edit")]
            ]
            reply_markup = InlineKeyboardMarkup(inline_keyboard=keyboard)
            
            try:
                await callback.message.delete()
            except:
                pass
            
            await callback.message.answer(text, reply_markup=reply_markup)
        
        elif canceled_by == "passenger":
            if order_info['driver_id']:
                # Отправляем уведомление водителю
                try:
                    await self.bot.send_message(
                        order_info['driver_id'],
                        f"❌ Заказ #{order_id} отменен пассажиром. Причина: {reason_text}"
                    )
                except Exception as e:
                    logger.error(f"Не удалось уведомить водителя {order_info['driver_id']}: {e}")
                
                # Удаляем ВСЕ сообщения водителя об этом заказе
                await self.delete_all_driver_messages(order_info['driver_id'], order_id)
                
                # Заказ уже удален из активных в методе cancel_order_in_db
                logger.info(f"Заказ #{order_id} удален из активных заказов водителя {order_info['driver_id']}")
        
        # Сообщение пассажиру
        text = f"❌ Вы отменили заказ #{order_id} по причине: {reason_text}."
        keyboard = [[InlineKeyboardButton(text="🚖 Сделать заказ", callback_data="passenger_order")]]
        reply_markup = InlineKeyboardMarkup(inline_keyboard=keyboard)
        
        try:
            await callback.message.delete()
        except:
            pass
        
        await callback.message.answer(text, reply_markup=reply_markup)
    
    async def delete_order_messages(self, order_id: int):
        """Удаляет все сообщения связанные с заказом"""
        order_info = self.get_order_info(order_id)
        
        # Удаляем сообщение пассажира
        if order_info and order_info['passenger_message_id']:
            try:
                await self.bot.delete_message(
                    order_info['passenger_id'],
                    order_info['passenger_message_id']
                )
            except Exception as e:
                logger.debug(f"Не удалось удалить сообщение пассажира: {e}")
        
        # Удаляем сообщения водителей из таблицы driver_order_messages
        driver_messages = self.get_driver_order_messages_by_order(order_id)
        for msg in driver_messages:
            try:
                await self.bot.delete_message(msg['driver_id'], msg['message_id'])
            except Exception as e:
                logger.debug(f"Не удалось удалить сообщение водителя {msg['driver_id']}: {e}")
        
        # Если у заказа есть водитель, удаляем его рабочие сообщения
        if order_info and order_info['driver_id']:
            # Получаем все рабочие сообщения этого водителя для данного заказа
            driver_working_messages = self.get_driver_working_messages(order_info['driver_id'], order_id)
            for message_id in driver_working_messages:
                try:
                    await self.bot.delete_message(order_info['driver_id'], message_id)
                except Exception as e:
                    logger.debug(f"Не удалось удалить рабочее сообщение водителя {order_info['driver_id']}: {e}")
    
    async def delete_passenger_notification_messages(self, passenger_id: int, order_id: int):
        """Удаляет уведомительные сообщения пассажира о водителе и времени прибытия"""
        try:
            # Получаем ID всех уведомительных сообщений для этого заказа
            message_ids = self.get_passenger_notification_messages(passenger_id, order_id)
            
            # Удаляем каждое сообщение
            for message_id in message_ids:
                try:
                    await self.bot.delete_message(passenger_id, message_id)
                    logger.info(f"Удалено уведомительное сообщение {message_id} пассажира {passenger_id}")
                except Exception as e:
                    logger.debug(f"Не удалось удалить уведомительное сообщение {message_id}: {e}")
            
            # Удаляем записи из базы данных
            self.delete_passenger_notification_messages_db(passenger_id, order_id)
            
        except Exception as e:
            logger.error(f"Ошибка при удалении уведомительных сообщений пассажира {passenger_id}: {e}")
    
    async def delete_all_driver_messages(self, driver_id: int, order_id: int):
        """Удаляет все сообщения водителя связанные с заказом"""
        try:
            # 1. Удаляем сообщения из таблицы driver_order_messages (рассылка заказов)
            driver_messages = self.get_driver_order_messages(driver_id, order_id)
            for msg in driver_messages:
                try:
                    await self.bot.delete_message(driver_id, msg['message_id'])
                    logger.info(f"Удалено сообщение рассылки водителя {driver_id}: {msg['message_id']}")
                except Exception as e:
                    logger.debug(f"Не удалось удалить сообщение рассылки водителя {driver_id}: {e}")
            
            # 2. Удаляем рабочие сообщения водителя
            working_messages = self.get_driver_working_messages(driver_id, order_id)
            for message_id in working_messages:
                try:
                    await self.bot.delete_message(driver_id, message_id)
                    logger.info(f"Удалено рабочее сообщение водителя {driver_id}: {message_id}")
                except Exception as e:
                    logger.debug(f"Не удалось удалить рабочее сообщение водителя {driver_id}: {e}")
            
            # 3. Очищаем записи из базы данных
            self.delete_driver_order_messages_db(driver_id, order_id)
            self.delete_driver_working_messages_db(driver_id, order_id)
            
            logger.info(f"Все сообщения водителя {driver_id} для заказа #{order_id} удалены")
            
        except Exception as e:
            logger.error(f"Ошибка при удалении сообщений водителя {driver_id}: {e}")
    
    async def remove_order_from_all_drivers(self, order_id: int):
        """Удаляет заказ у всех водителей"""
        driver_messages = self.get_driver_order_messages_by_order(order_id)
        for msg in driver_messages:
            try:
                await self.bot.delete_message(msg['driver_id'], msg['message_id'])
            except Exception as e:
                logger.debug(f"Не удалось удалить сообщение водителя {msg['driver_id']}: {e}")
    
    # === АДМИНИСТРАТОРСКИЙ ФУНКЦИОНАЛ ===
    async def admin_handler(self, callback: CallbackQuery, state: FSMContext):
        data = callback.data
        user_id = callback.from_user.id
        
        if data == "admin_assign_driver":
            await self.show_users_for_driver_assignment(callback)
        
        elif data == "admin_ban":
            await self.show_users_for_ban(callback)
        
        elif data == "admin_unban":
            await self.show_banned_users(callback)
        
        elif data == "admin_drivers":
            await self.show_drivers_list(callback)
        
        elif data == "admin_users":
            await self.show_users_list(callback)
        
        elif data == "admin_driver":
            # Администратор становится водителем - запрашиваем данные
            if not self.is_driver_registered(user_id):
                await state.set_state(DriverRegistration.name)
                await callback.message.answer("Введите ваше имя для регистрации водителя:")
            else:
                self.update_user_role(user_id, 'driver')
                await self.show_driver_menu_from_callback(callback)
        
        elif data == "admin_passenger":
            if not self.is_passenger_registered(user_id):
                await state.set_state(PassengerRegistration.name)
                await callback.message.answer("Введите ваше имя для регистрации пассажира:")
            else:
                self.update_user_role(user_id, 'passenger')
                await self.show_passenger_menu_from_callback(callback)
        
        elif data == "admin_back":
            await self.show_admin_menu_from_callback(callback)
        
        elif data.startswith("admin_assign_"):
            target_id = int(data.split("_")[2])
            await state.set_state(DriverRegistration.name)
            await state.update_data(target_user_id=target_id)
            await callback.message.answer("Введите имя водителя:")
        
        elif data.startswith("admin_ban_"):
            target_id = int(data.split("_")[2])
            await state.set_state(AdminBan.user_id)
            await state.update_data(user_id=target_id)
            await state.set_state(AdminBan.reason)
            await callback.message.answer("Введите причину бана:")
        
        elif data.startswith("admin_unban_"):
            target_id = int(data.split("_")[2])
            self.unban_user(target_id)
            await callback.message.answer(f"✅ Пользователь {target_id} разбанен")
            await self.show_admin_menu_from_callback(callback)
        
        await callback.answer()
    
    async def show_admin_menu(self, message: Message):
        keyboard = [
            [InlineKeyboardButton(text="🚗 Назначить водителем", callback_data="admin_assign_driver")],
            [InlineKeyboardButton(text="🔨 Забанить пользователя", callback_data="admin_ban")],
            [InlineKeyboardButton(text="🔓 Разбанить пользователя", callback_data="admin_unban")],
            [InlineKeyboardButton(text="🚗 Список водителей", callback_data="admin_drivers")],
            [InlineKeyboardButton(text="👥 Список пользователей", callback_data="admin_users")],
            [InlineKeyboardButton(text="🚗 Стать водителем", callback_data="admin_driver")],
            [InlineKeyboardButton(text="👤 Стать пассажиром", callback_data="admin_passenger")]
        ]
        
        reply_markup = InlineKeyboardMarkup(inline_keyboard=keyboard)
        await message.answer("👑 Панель администратора\nВыберите действие:", reply_markup=reply_markup)
    
    async def show_admin_menu_from_callback(self, callback: CallbackQuery):
        keyboard = [
            [InlineKeyboardButton(text="🚗 Назначить водителем", callback_data="admin_assign_driver")],
            [InlineKeyboardButton(text="🔨 Забанить пользователя", callback_data="admin_ban")],
            [InlineKeyboardButton(text="🔓 Разбанить пользователя", callback_data="admin_unban")],
            [InlineKeyboardButton(text="🚗 Список водителей", callback_data="admin_drivers")],
            [InlineKeyboardButton(text="👥 Список пользователей", callback_data="admin_users")],
            [InlineKeyboardButton(text="🚗 Стать водителем", callback_data="admin_driver")],
            [InlineKeyboardButton(text="👤 Стать пассажиром", callback_data="admin_passenger")]
        ]
        
        reply_markup = InlineKeyboardMarkup(inline_keyboard=keyboard)
        await callback.message.edit_text(
            "👑 Панель администратора\nВыберите действие:",
            reply_markup=reply_markup
        )
    
    async def show_users_for_driver_assignment(self, callback: CallbackQuery):
        users = self.get_all_users()
        keyboard = []
        
        for user in users:
            if user['role'] in ['user', 'passenger'] and not self.is_driver_registered(user['user_id']):
                keyboard.append([InlineKeyboardButton(
                    text=f"{user['first_name']} (@{user['username'] or 'без username'})",
                    callback_data=f"admin_assign_{user['user_id']}"
                )])
        
        if not keyboard:
            await callback.message.edit_text("Нет пользователей для назначения водителем")
            return
        
        keyboard.append([InlineKeyboardButton(text="🔙 Назад", callback_data="admin_back")])
        reply_markup = InlineKeyboardMarkup(inline_keyboard=keyboard)
        
        await callback.message.edit_text("Выберите пользователя для назначения водителем:", reply_markup=reply_markup)
    
    async def show_users_for_ban(self, callback: CallbackQuery):
        users = self.get_all_users()
        keyboard = []
        
        for user in users:
            if not self.is_user_banned(user['user_id']):
                keyboard.append([InlineKeyboardButton(
                    text=f"{user['first_name']} (@{user['username'] or 'без username'})",
                    callback_data=f"admin_ban_{user['user_id']}"
                )])
        
        if not keyboard:
            await callback.message.edit_text("Нет пользователей для бана")
            return
        
        keyboard.append([InlineKeyboardButton(text="🔙 Назад", callback_data="admin_back")])
        reply_markup = InlineKeyboardMarkup(inline_keyboard=keyboard)
        
        await callback.message.edit_text("Выберите пользователя для бана:", reply_markup=reply_markup)
    
    async def show_banned_users(self, callback: CallbackQuery):
        users = self.get_banned_users()
        
        if not users:
            await callback.message.edit_text("Нет забаненных пользователей")
            return
        
        keyboard = []
        for user in users:
            keyboard.append([InlineKeyboardButton(
                text=f"{user['first_name']} (@{user['username'] or 'без username'}) - до {user['ban_until']}",
                callback_data=f"admin_unban_{user['user_id']}"
            )])
        
        keyboard.append([InlineKeyboardButton(text="🔙 Назад", callback_data="admin_back")])
        reply_markup = InlineKeyboardMarkup(inline_keyboard=keyboard)
        
        await callback.message.edit_text("Забаненные пользователи:", reply_markup=reply_markup)
    
    # Бан пользователя
    async def process_admin_ban_user(self, message: Message, state: FSMContext):
        try:
            target_id = int(message.text)
            if not self.user_exists(target_id):
                await message.answer("❌ Пользователь с таким ID не найден!")
                return
            
            await state.update_data(user_id=target_id)
            await state.set_state(AdminBan.reason)
            await message.answer("Введите причину бана:")
            
        except ValueError:
            await message.answer("❌ Введите корректный ID пользователя!")
    
    async def process_admin_ban_reason(self, message: Message, state: FSMContext):
        await state.update_data(ban_reason=message.text)
        await state.set_state(AdminBan.duration)
        await message.answer("Введите длительность бана в днях:")
    
    async def process_admin_ban_duration(self, message: Message, state: FSMContext):
        try:
            duration = int(message.text)
            data = await state.get_data()
            target_id = data['user_id']
            reason = data['ban_reason']
            
            # Бан пользователя
            self.ban_user(target_id, reason, duration)
            
            # Очистка состояния
            await state.clear()
            
            await message.answer(f"✅ Пользователь {target_id} забанен на {duration} дней")
            await self.show_admin_menu(message)
            
        except ValueError:
            await message.answer("❌ Введите корректное число дней!")
    
    async def show_drivers_list(self, callback: CallbackQuery):
        drivers = self.get_all_drivers_with_info()
        
        if not drivers:
            await callback.message.edit_text("Нет зарегистрированных водителей")
            return
        
        text = "🚗 Список водителей:\n\n"
        for driver in drivers:
            text += f"👤 {driver['name']}\n"
            text += f"🚗 {driver['car_brand']} {driver['car_model']} ({driver['car_color']})\n"
            text += f"🔢 {driver['license_plate']}\n"
            text += f"📞 {driver['contact_phone']}\n"
            text += "─" * 20 + "\n"
        
        keyboard = [[InlineKeyboardButton(text="🔙 Назад", callback_data="admin_back")]]
        reply_markup = InlineKeyboardMarkup(inline_keyboard=keyboard)
        
        await callback.message.edit_text(text, reply_markup=reply_markup)
    
    async def show_users_list(self, callback: CallbackQuery):
        users = self.get_all_users()
        
        text = "👥 Список пользователей:\n\n"
        for user in users:
            role_emoji = "👤" if user['role'] == 'passenger' else "🚗" if user['role'] == 'driver' else "👑" if user['role'] == 'admin' else "❓"
            ban_status = "🔴 ЗАБАНЕН" if self.is_user_banned(user['user_id']) else "🟢 АКТИВЕН"
            text += f"{role_emoji} {user['first_name']} (@{user['username'] or 'без username'}) - {user['role']} - {ban_status}\n"
        
        keyboard = [[InlineKeyboardButton(text="🔙 Назад", callback_data="admin_back")]]
        reply_markup = InlineKeyboardMarkup(inline_keyboard=keyboard)
        
        await callback.message.edit_text(text, reply_markup=reply_markup)
    
    async def handle_message(self, message: Message):
        user_id = message.from_user.id
        
        # Проверка бана
        if self.is_user_banned(user_id):
            ban_info = self.get_ban_info(user_id)
            await message.answer(
                f"❌ Вы забанены до {ban_info['until']}. Причина: {ban_info['reason']}"
            )
            return
        
        await message.answer("Используйте кнопки для взаимодействия с ботом")
    
    # === ВСПОМОГАТЕЛЬНЫЕ МЕТОДЫ БАЗЫ ДАННЫХ ===
    def user_exists(self, user_id):
        conn = sqlite3.connect('taxi_bot.db')
        cursor = conn.cursor()
        cursor.execute("SELECT 1 FROM users WHERE user_id = ?", (user_id,))
        exists = cursor.fetchone() is not None
        conn.close()
        return exists
    
    def register_user(self, user_id, username, first_name):
        conn = sqlite3.connect('taxi_bot.db')
        cursor = conn.cursor()
        cursor.execute(
            "INSERT OR IGNORE INTO users (user_id, username, first_name, role) VALUES (?, ?, ?, 'user')",
            (user_id, username, first_name)
        )
        conn.commit()
        conn.close()
    
    def update_user_role(self, user_id, role):
        conn = sqlite3.connect('taxi_bot.db')
        cursor = conn.cursor()
        cursor.execute(
            "UPDATE users SET role = ? WHERE user_id = ?",
            (role, user_id)
        )
        conn.commit()
        conn.close()
    
    def get_user_role(self, user_id):
        conn = sqlite3.connect('taxi_bot.db')
        cursor = conn.cursor()
        cursor.execute("SELECT role FROM users WHERE user_id = ?", (user_id,))
        result = cursor.fetchone()
        conn.close()
        return result[0] if result else None
    
    def is_passenger_registered(self, user_id):
        conn = sqlite3.connect('taxi_bot.db')
        cursor = conn.cursor()
        cursor.execute("SELECT 1 FROM passengers WHERE user_id = ?", (user_id,))
        exists = cursor.fetchone() is not None
        conn.close()
        return exists
    
    def is_driver_registered(self, user_id):
        conn = sqlite3.connect('taxi_bot.db')
        cursor = conn.cursor()
        cursor.execute("SELECT 1 FROM drivers WHERE user_id = ?", (user_id,))
        exists = cursor.fetchone() is not None
        conn.close()
        return exists
    
    def save_driver_data(self, user_id, driver_data):
        conn = sqlite3.connect('taxi_bot.db')
        cursor = conn.cursor()
        cursor.execute('''
            INSERT OR REPLACE INTO drivers 
            (user_id, name, car_brand, car_model, license_plate, car_color, contact_phone, payment_phone, bank)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (
            user_id, driver_data['name'], driver_data['car_brand'], driver_data['car_model'],
            driver_data['license_plate'], driver_data['car_color'], driver_data['contact_phone'],
            driver_data['payment_phone'], driver_data['bank']
        ))
        conn.commit()
        conn.close()
    
    def save_passenger_data(self, user_id, passenger_data):
        conn = sqlite3.connect('taxi_bot.db')
        cursor = conn.cursor()
        cursor.execute('''
            INSERT OR REPLACE INTO passengers 
            (user_id, name, contact_phone)
            VALUES (?, ?, ?)
        ''', (user_id, passenger_data['name'], passenger_data['contact_phone']))
        conn.commit()
        conn.close()
    
    def get_driver_stats(self, user_id):
        conn = sqlite3.connect('taxi_bot.db')
        cursor = conn.cursor()
        cursor.execute('''
            SELECT total_orders, completed_orders, canceled_orders, today_orders, total_earnings, today_earnings
            FROM drivers WHERE user_id = ?
        ''', (user_id,))
        result = cursor.fetchone()
        conn.close()
        
        if result:
            return {
                'total_orders': result[0],
                'completed_orders': result[1],
                'canceled_orders': result[2],
                'today_orders': result[3],
                'total_earnings': result[4],
                'today_earnings': result[5]
            }
        return {
            'total_orders': 0, 'completed_orders': 0, 'canceled_orders': 0,
            'today_orders': 0, 'total_earnings': 0, 'today_earnings': 0
        }
    
    def get_available_orders(self):
        conn = sqlite3.connect('taxi_bot.db')
        cursor = conn.cursor()
        cursor.execute('''
            SELECT order_id, passenger_id, from_location, to_location, price
            FROM orders WHERE status = 'searching'
            ORDER BY created_at DESC
        ''')
        orders = []
        for row in cursor.fetchall():
            orders.append({
                'order_id': row[0],
                'passenger_id': row[1],
                'from_location': row[2],
                'to_location': row[3],
                'price': row[4]
            })
        conn.close()
        return orders
    
    def get_passenger_info(self, user_id):
        conn = sqlite3.connect('taxi_bot.db')
        cursor = conn.cursor()
        cursor.execute("SELECT name, contact_phone FROM passengers WHERE user_id = ?", (user_id,))
        result = cursor.fetchone()
        conn.close()
        
        if result:
            return {'name': result[0], 'contact_phone': result[1]}
        return {'name': 'Неизвестно', 'contact_phone': 'Неизвестно'}
    
    def get_driver_info(self, user_id):
        conn = sqlite3.connect('taxi_bot.db')
        cursor = conn.cursor()
        cursor.execute('''
            SELECT name, car_brand, car_model, license_plate, car_color, contact_phone, payment_phone, bank
            FROM drivers WHERE user_id = ?
        ''', (user_id,))
        result = cursor.fetchone()
        conn.close()
        
        if result:
            return {
                'name': result[0],
                'car_brand': result[1],
                'car_model': result[2],
                'license_plate': result[3],
                'car_color': result[4],
                'contact_phone': result[5],
                'payment_phone': result[6],
                'bank': result[7]
            }
        return {
            'name': 'Неизвестно', 'car_brand': 'Неизвестно', 'car_model': 'Неизвестно',
            'license_plate': 'Неизвестно', 'car_color': 'Неизвестно',
            'contact_phone': 'Неизвестно', 'payment_phone': 'Неизвестно', 'bank': 'Неизвестно'
        }
    
    def create_order(self, passenger_id, order_data):
        conn = sqlite3.connect('taxi_bot.db')
        cursor = conn.cursor()
        cursor.execute('''
            INSERT INTO orders (passenger_id, from_location, to_location, price, status)
            VALUES (?, ?, ?, ?, 'searching')
        ''', (passenger_id, order_data['from_location'], order_data['to_location'], order_data['price']))
        order_id = cursor.lastrowid
        conn.commit()
        conn.close()
        return order_id
    
    def get_order_info(self, order_id):
        conn = sqlite3.connect('taxi_bot.db')
        cursor = conn.cursor()
        cursor.execute('''
            SELECT order_id, passenger_id, driver_id, from_location, to_location, price, status, passenger_message_id
            FROM orders WHERE order_id = ?
        ''', (order_id,))
        result = cursor.fetchone()
        conn.close()
        
        if result:
            return {
                'order_id': result[0],
                'passenger_id': result[1],
                'driver_id': result[2],
                'from_location': result[3],
                'to_location': result[4],
                'price': result[5],
                'status': result[6],
                'passenger_message_id': result[7]
            }
        return None
    
    def update_order_message_id(self, order_id, message_type, message_id):
        conn = sqlite3.connect('taxi_bot.db')
        cursor = conn.cursor()
        if message_type == 'passenger':
            cursor.execute('''
                UPDATE orders SET passenger_message_id = ? WHERE order_id = ?
            ''', (message_id, order_id))
        conn.commit()
        conn.close()
    
    def save_driver_order_message(self, driver_id, order_id, message_id):
        conn = sqlite3.connect('taxi_bot.db')
        cursor = conn.cursor()
        cursor.execute('''
            INSERT INTO driver_order_messages (driver_id, order_id, message_id)
            VALUES (?, ?, ?)
        ''', (driver_id, order_id, message_id))
        conn.commit()
        conn.close()
    
    def get_driver_order_messages(self, driver_id, order_id=None):
        """Получает сообщения водителя по заказу"""
        conn = sqlite3.connect('taxi_bot.db')
        cursor = conn.cursor()
        
        if order_id:
            cursor.execute('''
                SELECT message_id FROM driver_order_messages 
                WHERE driver_id = ? AND order_id = ?
            ''', (driver_id, order_id))
        else:
            cursor.execute('''
                SELECT message_id FROM driver_order_messages 
                WHERE driver_id = ?
            ''', (driver_id,))
        
        results = cursor.fetchall()
        conn.close()
        
        messages = []
        for result in results:
            messages.append({
                'driver_id': driver_id,
                'message_id': result[0]
            })
        return messages
    
    def get_driver_order_messages_by_order(self, order_id: int):
        """Получает все сообщения водителей для конкретного заказа"""
        conn = sqlite3.connect('taxi_bot.db')
        cursor = conn.cursor()
        cursor.execute('''
            SELECT driver_id, message_id FROM driver_order_messages WHERE order_id = ?
        ''', (order_id,))
        results = cursor.fetchall()
        conn.close()
        
        messages = []
        for result in results:
            messages.append({
                'driver_id': result[0],
                'message_id': result[1]
            })
        return messages
    
    def save_passenger_notification_message(self, passenger_id: int, order_id: int, message_id: int, message_type: str):
        """Сохраняет ID уведомительного сообщения пассажира"""
        conn = sqlite3.connect('taxi_bot.db')
        cursor = conn.cursor()
        cursor.execute('''
            INSERT INTO passenger_notifications (passenger_id, order_id, message_id, message_type)
            VALUES (?, ?, ?, ?)
        ''', (passenger_id, order_id, message_id, message_type))
        conn.commit()
        conn.close()
    
    def get_passenger_notification_messages(self, passenger_id: int, order_id: int):
        """Получает все уведомительные сообщения пассажира для заказа"""
        conn = sqlite3.connect('taxi_bot.db')
        cursor = conn.cursor()
        cursor.execute('''
            SELECT message_id FROM passenger_notifications 
            WHERE passenger_id = ? AND order_id = ?
        ''', (passenger_id, order_id))
        results = cursor.fetchall()
        conn.close()
        
        message_ids = []
        for result in results:
            message_ids.append(result[0])
        return message_ids
    
    def delete_passenger_notification_messages_db(self, passenger_id: int, order_id: int):
        """Удаляет записи об уведомительных сообщениях пассажира из БД"""
        conn = sqlite3.connect('taxi_bot.db')
        cursor = conn.cursor()
        cursor.execute('''
            DELETE FROM passenger_notifications 
            WHERE passenger_id = ? AND order_id = ?
        ''', (passenger_id, order_id))
        conn.commit()
        conn.close()
    
    def save_driver_working_message(self, driver_id: int, order_id: int, message_id: int, message_type: str):
        """Сохраняет ID рабочего сообщения водителя"""
        conn = sqlite3.connect('taxi_bot.db')
        cursor = conn.cursor()
        cursor.execute('''
            INSERT INTO driver_working_messages (driver_id, order_id, message_id, message_type)
            VALUES (?, ?, ?, ?)
        ''', (driver_id, order_id, message_id, message_type))
        conn.commit()
        conn.close()
    
    def get_driver_working_messages(self, driver_id: int, order_id: int):
        """Получает все рабочие сообщения водителя для заказа"""
        conn = sqlite3.connect('taxi_bot.db')
        cursor = conn.cursor()
        cursor.execute('''
            SELECT message_id FROM driver_working_messages 
            WHERE driver_id = ? AND order_id = ?
        ''', (driver_id, order_id))
        results = cursor.fetchall()
        conn.close()
        
        message_ids = []
        for result in results:
            message_ids.append(result[0])
        return message_ids
    
    def delete_driver_working_messages_db(self, driver_id: int, order_id: int):
        """Удаляет записи о рабочих сообщениях водителя из БД"""
        conn = sqlite3.connect('taxi_bot.db')
        cursor = conn.cursor()
        cursor.execute('''
            DELETE FROM driver_working_messages 
            WHERE driver_id = ? AND order_id = ?
        ''', (driver_id, order_id))
        conn.commit()
        conn.close()
    
    def delete_driver_order_messages_db(self, driver_id: int, order_id: int):
        """Удаляет записи о сообщениях водителя из таблицы driver_order_messages"""
        conn = sqlite3.connect('taxi_bot.db')
        cursor = conn.cursor()
        cursor.execute('''
            DELETE FROM driver_order_messages 
            WHERE driver_id = ? AND order_id = ?
        ''', (driver_id, order_id))
        conn.commit()
        conn.close()
    
    def is_order_already_broadcasted(self, order_id):
        """Проверяет, был ли заказ уже разослан водителям"""
        conn = sqlite3.connect('taxi_bot.db')
        cursor = conn.cursor()
        cursor.execute('''
            SELECT COUNT(*) FROM driver_order_messages WHERE order_id = ?
        ''', (order_id,))
        count = cursor.fetchone()[0]
        conn.close()
        return count > 0
    
    def mark_order_as_broadcasted(self, order_id):
        """Помечает заказ как разосланный (для отладки)"""
        # В этой реализации мы просто логируем факт рассылки
        logger.info(f"Заказ #{order_id} помечен как разосланный")
    
    def get_all_active_drivers(self):
        """Получает список активных водителей (не забаненных)"""
        conn = sqlite3.connect('taxi_bot.db')
        cursor = conn.cursor()
        cursor.execute('''
            SELECT d.user_id 
            FROM drivers d
            JOIN users u ON d.user_id = u.user_id
            WHERE u.is_banned = FALSE
        ''')
        drivers = []
        for row in cursor.fetchall():
            drivers.append({'user_id': row[0]})
        conn.close()
        return drivers
    
    def get_all_drivers(self):
        """Получает список всех водителей (для обратной совместимости)"""
        return self.get_all_active_drivers()
    
    def accept_order_by_driver(self, order_id, driver_id):
        conn = sqlite3.connect('taxi_bot.db')
        cursor = conn.cursor()
        
        # Проверяем, не принят ли заказ уже другим водителем
        cursor.execute("SELECT status FROM orders WHERE order_id = ?", (order_id,))
        result = cursor.fetchone()
        
        if not result or result[0] != 'searching':
            conn.close()
            return False
        
        # Принимаем заказ
        cursor.execute('''
            UPDATE orders SET driver_id = ?, status = 'accepted', accepted_at = CURRENT_TIMESTAMP
            WHERE order_id = ?
        ''', (driver_id, order_id))
        
        # Добавляем в активные заказы водителя
        cursor.execute('''
            INSERT OR IGNORE INTO driver_active_orders (driver_id, order_id) VALUES (?, ?)
        ''', (driver_id, order_id))
        
        conn.commit()
        conn.close()
        return True
    
    def get_driver_active_orders_count(self, driver_id):
        """Получает количество активных заказов водителя с автоматической очисткой некорректных записей"""
        # Сначала очищаем возможные некорректные записи
        self.cleanup_driver_active_orders(driver_id)
        
        conn = sqlite3.connect('taxi_bot.db')
        cursor = conn.cursor()
        cursor.execute('''
            SELECT COUNT(*) FROM driver_active_orders WHERE driver_id = ?
        ''', (driver_id,))
        count = cursor.fetchone()[0]
        conn.close()
        return count
    
    def get_driver_active_orders_info(self, driver_id):
        """Получает информацию об активных заказах водителя (для отладки)"""
        conn = sqlite3.connect('taxi_bot.db')
        cursor = conn.cursor()
        cursor.execute('''
            SELECT o.order_id, o.status, o.passenger_id 
            FROM orders o
            JOIN driver_active_orders dao ON o.order_id = dao.order_id
            WHERE dao.driver_id = ?
        ''', (driver_id,))
        results = cursor.fetchall()
        conn.close()
        
        orders = []
        for result in results:
            orders.append({
                'order_id': result[0],
                'status': result[1],
                'passenger_id': result[2]
            })
        return orders
    
    def cleanup_driver_active_orders(self, driver_id):
        """Очищает активные заказы водителя, которые находятся в некорректном состоянии"""
        conn = sqlite3.connect('taxi_bot.db')
        cursor = conn.cursor()
        
        # Удаляем записи об активных заказах, которые уже завершены или отменены
        cursor.execute('''
            DELETE FROM driver_active_orders 
            WHERE driver_id = ? AND order_id IN (
                SELECT order_id FROM orders 
                WHERE status IN ('completed', 'canceled') AND driver_id = ?
            )
        ''', (driver_id, driver_id))
        
        deleted_count = cursor.rowcount
        conn.commit()
        conn.close()
        
        if deleted_count > 0:
            logger.info(f"Очищено {deleted_count} некорректных активных заказов для водителя {driver_id}")
        
        return deleted_count
    
    def update_order_status(self, order_id, status):
        conn = sqlite3.connect('taxi_bot.db')
        cursor = conn.cursor()
        cursor.execute('''
            UPDATE orders SET status = ? WHERE order_id = ?
        ''', (status, order_id))
        conn.commit()
        conn.close()
    
    def complete_order_in_db(self, order_id):
        conn = sqlite3.connect('taxi_bot.db')
        cursor = conn.cursor()
        
        # Получаем информацию о заказе
        cursor.execute('''
            SELECT driver_id, price FROM orders WHERE order_id = ?
        ''', (order_id,))
        result = cursor.fetchone()
        
        if result:
            driver_id, price = result
            
            # Обновляем статистику водителя
            cursor.execute('''
                UPDATE drivers SET 
                    total_orders = total_orders + 1,
                    completed_orders = completed_orders + 1,
                    today_orders = today_orders + 1,
                    total_earnings = total_earnings + ?,
                    today_earnings = today_earnings + ?
                WHERE user_id = ?
            ''', (price, price, driver_id))
            
            # Обновляем статус заказа
            cursor.execute('''
                UPDATE orders SET status = 'completed', completed_at = CURRENT_TIMESTAMP
                WHERE order_id = ?
            ''', (order_id,))
            
            # Удаляем из активных заказов
            cursor.execute('''
                DELETE FROM driver_active_orders WHERE order_id = ?
            ''', (order_id,))
        
        conn.commit()
        conn.close()
    
    def cancel_order_in_db(self, order_id, reason, canceled_by):
        conn = sqlite3.connect('taxi_bot.db')
        cursor = conn.cursor()
        
        # Обновляем статус заказа
        cursor.execute('''
            UPDATE orders SET status = 'canceled', canceled_reason = ?, canceled_by = ?
            WHERE order_id = ?
        ''', (reason, canceled_by, order_id))
        
        # Удаляем из активных заказов ВСЕГДА, независимо от того, кто отменил
        cursor.execute('''
            DELETE FROM driver_active_orders WHERE order_id = ?
        ''', (order_id,))
        
        # Если отменял водитель, обновляем его статистику
        if canceled_by == 'driver':
            cursor.execute('''
                UPDATE drivers SET canceled_orders = canceled_orders + 1
                WHERE user_id = (SELECT driver_id FROM orders WHERE order_id = ?)
            ''', (order_id,))
        
        conn.commit()
        conn.close()
        return 1
    
    def get_passenger_active_order(self, user_id):
        conn = sqlite3.connect('taxi_bot.db')
        cursor = conn.cursor()
        cursor.execute('''
            SELECT order_id FROM orders 
            WHERE passenger_id = ? AND status IN ('searching', 'accepted', 'waiting')
            ORDER BY created_at DESC LIMIT 1
        ''', (user_id,))
        result = cursor.fetchone()
        conn.close()
        return result[0] if result else None
    
    def get_reason_text(self, reason):
        reasons = {
            'long_wait': 'Долгое ожидание',
            'changed_mind': 'Передумал',
            'bad_driver': 'Не устраивает водитель',
            'bad_car': 'Не устраивает машина',
            'driver_canceled': 'Отменен водителем',
            'passenger_canceled': 'Отменен пассажиром',
            'price_issue': 'Не устраивает цена'
        }
        return reasons.get(reason, 'Не указана')
    
    def is_user_banned(self, user_id):
        conn = sqlite3.connect('taxi_bot.db')
        cursor = conn.cursor()
        cursor.execute('''
            SELECT is_banned, ban_until FROM users WHERE user_id = ?
        ''', (user_id,))
        result = cursor.fetchone()
        conn.close()
        
        if result and result[0]:
            ban_until = datetime.fromisoformat(result[1])
            return datetime.now() < ban_until
        return False
    
    def get_ban_info(self, user_id):
        conn = sqlite3.connect('taxi_bot.db')
        cursor = conn.cursor()
        cursor.execute('''
            SELECT ban_reason, ban_until FROM users WHERE user_id = ?
        ''', (user_id,))
        result = cursor.fetchone()
        conn.close()
        
        if result:
            return {
                'reason': result[0] or 'Не указана',
                'until': result[1]
            }
        return {'reason': 'Не указана', 'until': 'Неизвестно'}
    
    def ban_user(self, user_id, reason, duration_days):
        ban_until = datetime.now() + timedelta(days=duration_days)
        conn = sqlite3.connect('taxi_bot.db')
        cursor = conn.cursor()
        cursor.execute('''
            UPDATE users SET is_banned = TRUE, ban_reason = ?, ban_until = ?
            WHERE user_id = ?
        ''', (reason, ban_until.isoformat(), user_id))
        conn.commit()
        conn.close()
    
    def unban_user(self, user_id):
        conn = sqlite3.connect('taxi_bot.db')
        cursor = conn.cursor()
        cursor.execute('''
            UPDATE users SET is_banned = FALSE, ban_reason = NULL, ban_until = NULL
            WHERE user_id = ?
        ''', (user_id,))
        conn.commit()
        conn.close()
    
    def get_all_users(self):
        conn = sqlite3.connect('taxi_bot.db')
        cursor = conn.cursor()
        cursor.execute('''
            SELECT user_id, username, first_name, role FROM users
        ''')
        users = []
        for row in cursor.fetchall():
            users.append({
                'user_id': row[0],
                'username': row[1],
                'first_name': row[2],
                'role': row[3]
            })
        conn.close()
        return users
    
    def get_banned_users(self):
        conn = sqlite3.connect('taxi_bot.db')
        cursor = conn.cursor()
        cursor.execute('''
            SELECT user_id, username, first_name, ban_until FROM users WHERE is_banned = TRUE
        ''')
        users = []
        for row in cursor.fetchall():
            users.append({
                'user_id': row[0],
                'username': row[1],
                'first_name': row[2],
                'ban_until': row[3]
            })
        conn.close()
        return users
    
    def get_all_drivers_with_info(self):
        conn = sqlite3.connect('taxi_bot.db')
        cursor = conn.cursor()
        cursor.execute('''
            SELECT d.user_id, d.name, d.car_brand, d.car_model, d.license_plate, d.car_color, d.contact_phone
            FROM drivers d
            JOIN users u ON d.user_id = u.user_id
            WHERE u.is_banned = FALSE
        ''')
        drivers = []
        for row in cursor.fetchall():
            drivers.append({
                'user_id': row[0],
                'name': row[1],
                'car_brand': row[2],
                'car_model': row[3],
                'license_plate': row[4],
                'car_color': row[5],
                'contact_phone': row[6]
            })
        conn.close()
        return drivers
    
    def run(self):
        logger.info("Бот запущен и готов к работе")
        return self.dp.start_polling(self.bot)

# Запуск бота
if __name__ == "__main__":
    init_db()
    logger.info("База данных инициализирована")
    logger.info("Код проверен")
    
    bot = TaxiBot(BOT_TOKEN)
    asyncio.run(bot.run())