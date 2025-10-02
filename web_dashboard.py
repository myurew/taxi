from flask import Flask, render_template, jsonify, request, session, redirect, url_for
import sqlite3
import json
from datetime import datetime, timedelta
import logging
from functools import wraps

# Настройка логирования
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__)
app.secret_key = 'your-secret-key-here'  # Измените на случайный ключ

# Конфигурация аутентификации
ADMIN_CREDENTIALS = {
    'username': 'admin',
    'password': 'admin123'  # Измените на свой пароль
}

def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not session.get('logged_in'):
            return jsonify({'error': 'Требуется авторизация'}), 401
        return f(*args, **kwargs)
    return decorated_function

class TaxiDashboard:
    def __init__(self, db_path='taxi_bot.db'):
        self.db_path = db_path
    
    def get_connection(self):
        return sqlite3.connect(self.db_path)
    
    def get_drivers_stats(self):
        """Получает статистику по всем водителям"""
        conn = self.get_connection()
        cursor = conn.cursor()
        
        cursor.execute('''
            SELECT 
                d.user_id,
                d.name,
                d.car_brand,
                d.car_model,
                d.license_plate,
                d.total_orders,
                d.completed_orders,
                d.canceled_orders,
                d.today_orders,
                d.total_earnings,
                d.today_earnings,
                u.username,
                u.first_name,
                u.is_banned,
                u.ban_reason,
                u.ban_until,
                u.registration_date
            FROM drivers d
            JOIN users u ON d.user_id = u.user_id
            ORDER BY d.total_earnings DESC
        ''')
        
        drivers = []
        for row in cursor.fetchall():
            drivers.append({
                'user_id': row[0],
                'name': row[1],
                'car_brand': row[2],
                'car_model': row[3],
                'license_plate': row[4],
                'total_orders': row[5],
                'completed_orders': row[6],
                'canceled_orders': row[7],
                'today_orders': row[8],
                'total_earnings': float(row[9]),
                'today_earnings': float(row[10]),
                'username': row[11],
                'first_name': row[12],
                'is_banned': bool(row[13]),
                'ban_reason': row[14],
                'ban_until': row[15],
                'registration_date': row[16],
                'success_rate': round((row[6] / row[5] * 100) if row[5] > 0 else 0, 2)
            })
        
        conn.close()
        return drivers
    
    def get_orders_stats(self, days=7):
        """Получает статистику по заказам за последние N дней"""
        conn = self.get_connection()
        cursor = conn.cursor()
        
        cursor.execute('''
            SELECT 
                COUNT(*) as total_orders,
                SUM(CASE WHEN status = 'completed' THEN 1 ELSE 0 END) as completed_orders,
                SUM(CASE WHEN status = 'canceled' THEN 1 ELSE 0 END) as canceled_orders,
                SUM(CASE WHEN status = 'searching' THEN 1 ELSE 0 END) as searching_orders,
                SUM(CASE WHEN status = 'accepted' THEN 1 ELSE 0 END) as accepted_orders,
                SUM(CASE WHEN status = 'waiting' THEN 1 ELSE 0 END) as waiting_orders,
                SUM(CASE WHEN status = 'completed' THEN price ELSE 0 END) as total_earnings
            FROM orders
        ''')
        
        total_stats = cursor.fetchone()
        
        start_date = (datetime.now() - timedelta(days=days)).strftime('%Y-%m-%d')
        cursor.execute('''
            SELECT 
                COUNT(*) as total_orders,
                SUM(CASE WHEN status = 'completed' THEN 1 ELSE 0 END) as completed_orders,
                SUM(CASE WHEN status = 'canceled' THEN 1 ELSE 0 END) as canceled_orders,
                SUM(CASE WHEN status = 'completed' THEN price ELSE 0 END) as total_earnings,
                DATE(created_at) as order_date
            FROM orders 
            WHERE created_at >= ?
            GROUP BY DATE(created_at)
            ORDER BY order_date DESC
        ''', (start_date,))
        
        daily_stats = []
        for row in cursor.fetchall():
            daily_stats.append({
                'date': row[4],
                'total_orders': row[0],
                'completed_orders': row[1],
                'canceled_orders': row[2],
                'earnings': float(row[3]) if row[3] else 0
            })
        
        cursor.execute('''
            SELECT 
                o.order_id,
                o.passenger_id,
                o.driver_id,
                o.from_location,
                o.to_location,
                o.price,
                o.status,
                o.created_at,
                o.completed_at,
                o.canceled_reason,
                o.canceled_by,
                p.name as passenger_name,
                d.name as driver_name
            FROM orders o
            LEFT JOIN passengers p ON o.passenger_id = p.user_id
            LEFT JOIN drivers d ON o.driver_id = d.user_id
            ORDER BY o.created_at DESC
            LIMIT 100
        ''')
        
        recent_orders = []
        for row in cursor.fetchall():
            recent_orders.append({
                'order_id': row[0],
                'passenger_id': row[1],
                'driver_id': row[2],
                'from_location': row[3],
                'to_location': row[4],
                'price': float(row[5]) if row[5] else 0,
                'status': row[6],
                'created_at': row[7],
                'completed_at': row[8],
                'canceled_reason': row[9],
                'canceled_by': row[10],
                'passenger_name': row[11],
                'driver_name': row[12]
            })
        
        conn.close()
        
        return {
            'total_stats': {
                'total_orders': total_stats[0],
                'completed_orders': total_stats[1],
                'canceled_orders': total_stats[2],
                'searching_orders': total_stats[3],
                'accepted_orders': total_stats[4],
                'waiting_orders': total_stats[5],
                'total_earnings': float(total_stats[6]) if total_stats[6] else 0
            },
            'daily_stats': daily_stats,
            'recent_orders': recent_orders
        }
    
    def get_users_stats(self):
        """Получает статистику по пользователям"""
        conn = self.get_connection()
        cursor = conn.cursor()
        
        cursor.execute('''
            SELECT 
                role,
                COUNT(*) as count,
                SUM(CASE WHEN is_banned = 1 THEN 1 ELSE 0 END) as banned_count
            FROM users 
            GROUP BY role
        ''')
        
        role_stats = {}
        for row in cursor.fetchall():
            role_stats[row[0]] = {
                'total': row[1],
                'banned': row[2],
                'active': row[1] - row[2]
            }
        
        start_date = (datetime.now() - timedelta(days=7)).strftime('%Y-%m-%d')
        cursor.execute('''
            SELECT 
                u.role,
                COUNT(DISTINCT u.user_id) as active_users
            FROM users u
            LEFT JOIN orders o ON u.user_id = o.passenger_id OR u.user_id = o.driver_id
            WHERE o.created_at >= ? OR u.registration_date >= ?
            GROUP BY u.role
        ''', (start_date, start_date))
        
        active_users = {}
        for row in cursor.fetchall():
            active_users[row[0]] = row[1]
        
        conn.close()
        
        return {
            'role_stats': role_stats,
            'active_users': active_users
        }
    
    def get_financial_stats(self):
        """Получает финансовую статистику"""
        conn = self.get_connection()
        cursor = conn.cursor()
        
        start_date = (datetime.now() - timedelta(days=30)).strftime('%Y-%m-%d')
        cursor.execute('''
            SELECT 
                DATE(completed_at) as completion_date,
                COUNT(*) as completed_orders,
                SUM(price) as daily_earnings
            FROM orders 
            WHERE status = 'completed' AND completed_at >= ?
            GROUP BY DATE(completed_at)
            ORDER BY completion_date DESC
        ''', (start_date,))
        
        daily_earnings = []
        for row in cursor.fetchall():
            daily_earnings.append({
                'date': row[0],
                'completed_orders': row[1],
                'earnings': float(row[2]) if row[2] else 0
            })
        
        cursor.execute('''
            SELECT 
                d.name,
                d.total_earnings,
                d.completed_orders,
                d.canceled_orders,
                u.username
            FROM drivers d
            JOIN users u ON d.user_id = u.user_id
            ORDER BY d.total_earnings DESC
            LIMIT 10
        ''')
        
        top_drivers = []
        for row in cursor.fetchall():
            top_drivers.append({
                'name': row[0],
                'total_earnings': float(row[1]) if row[1] else 0,
                'completed_orders': row[2],
                'canceled_orders': row[3],
                'username': row[4]
            })
        
        conn.close()
        
        return {
            'daily_earnings': daily_earnings,
            'top_drivers': top_drivers
        }
    
    def get_all_users(self):
        """Получает список всех пользователей"""
        conn = self.get_connection()
        cursor = conn.cursor()
        
        cursor.execute('''
            SELECT 
                user_id,
                username,
                first_name,
                role,
                is_banned,
                ban_reason,
                ban_until,
                registration_date
            FROM users 
            ORDER BY registration_date DESC
        ''')
        
        users = []
        for row in cursor.fetchall():
            users.append({
                'user_id': row[0],
                'username': row[1],
                'first_name': row[2],
                'role': row[3],
                'is_banned': bool(row[4]),
                'ban_reason': row[5],
                'ban_until': row[6],
                'registration_date': row[7]
            })
        
        conn.close()
        return users
    
    def ban_user(self, user_id, reason, duration_days):
        """Банит пользователя"""
        try:
            conn = self.get_connection()
            cursor = conn.cursor()
            
            ban_until = (datetime.now() + timedelta(days=duration_days)).isoformat()
            
            cursor.execute('''
                UPDATE users 
                SET is_banned = TRUE, ban_reason = ?, ban_until = ?
                WHERE user_id = ?
            ''', (reason, ban_until, user_id))
            
            conn.commit()
            conn.close()
            
            logger.info(f"User {user_id} banned for {duration_days} days. Reason: {reason}")
            return True
            
        except Exception as e:
            logger.error(f"Error banning user {user_id}: {e}")
            return False
    
    def unban_user(self, user_id):
        """Разбанивает пользователя"""
        try:
            conn = self.get_connection()
            cursor = conn.cursor()
            
            cursor.execute('''
                UPDATE users 
                SET is_banned = FALSE, ban_reason = NULL, ban_until = NULL
                WHERE user_id = ?
            ''', (user_id,))
            
            conn.commit()
            conn.close()
            
            logger.info(f"User {user_id} unbanned")
            return True
            
        except Exception as e:
            logger.error(f"Error unbanning user {user_id}: {e}")
            return False
    
    def create_driver(self, user_id, driver_data):
        """Создает водителя из существующего пользователя"""
        try:
            conn = self.get_connection()
            cursor = conn.cursor()
            
            # Проверяем, существует ли пользователь
            cursor.execute('SELECT user_id FROM users WHERE user_id = ?', (user_id,))
            if not cursor.fetchone():
                conn.close()
                return False, "Пользователь не найден"
            
            # Проверяем, не является ли пользователь уже водителем
            cursor.execute('SELECT user_id FROM drivers WHERE user_id = ?', (user_id,))
            if cursor.fetchone():
                conn.close()
                return False, "Пользователь уже является водителем"
            
            # Создаем запись водителя
            cursor.execute('''
                INSERT INTO drivers 
                (user_id, name, car_brand, car_model, license_plate, car_color, contact_phone, payment_phone, bank)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (
                user_id,
                driver_data['name'],
                driver_data['car_brand'],
                driver_data['car_model'],
                driver_data['license_plate'],
                driver_data['car_color'],
                driver_data['contact_phone'],
                driver_data['payment_phone'],
                driver_data['bank']
            ))
            
            # Обновляем роль пользователя
            cursor.execute('''
                UPDATE users SET role = 'driver' WHERE user_id = ?
            ''', (user_id,))
            
            conn.commit()
            conn.close()
            
            logger.info(f"Driver created for user {user_id}")
            return True, "Водитель успешно создан"
            
        except Exception as e:
            logger.error(f"Error creating driver for user {user_id}: {e}")
            return False, f"Ошибка при создании водителя: {str(e)}"
    
    def delete_driver(self, user_id):
        """Удаляет водителя (возвращает в статус пользователя)"""
        try:
            conn = self.get_connection()
            cursor = conn.cursor()
            
            # Удаляем запись водителя
            cursor.execute('DELETE FROM drivers WHERE user_id = ?', (user_id,))
            
            # Обновляем роль пользователя на 'user'
            cursor.execute('''
                UPDATE users SET role = 'user' WHERE user_id = ?
            ''', (user_id,))
            
            conn.commit()
            conn.close()
            
            logger.info(f"Driver deleted for user {user_id}")
            return True, "Водитель успешно удален"
            
        except Exception as e:
            logger.error(f"Error deleting driver for user {user_id}: {e}")
            return False, f"Ошибка при удалении водителя: {str(e)}"

    def get_available_drivers(self):
        """Получает список доступных водителей"""
        conn = self.get_connection()
        cursor = conn.cursor()
        
        cursor.execute('''
            SELECT 
                d.user_id,
                d.name,
                d.car_brand,
                d.car_model,
                d.license_plate,
                u.username,
                u.first_name,
                u.is_banned
            FROM drivers d
            JOIN users u ON d.user_id = u.user_id
            WHERE u.is_banned = FALSE
            ORDER BY d.name
        ''')
        
        drivers = []
        for row in cursor.fetchall():
            drivers.append({
                'user_id': row[0],
                'name': row[1],
                'car_brand': row[2],
                'car_model': row[3],
                'license_plate': row[4],
                'username': row[5],
                'first_name': row[6],
                'is_banned': bool(row[7])
            })
        
        conn.close()
        return drivers

    def assign_driver_to_order(self, order_id, driver_id):
        """Назначает водителя на заказ"""
        try:
            conn = self.get_connection()
            cursor = conn.cursor()
            
            # Проверяем существование заказа и его статус
            cursor.execute('SELECT status FROM orders WHERE order_id = ?', (order_id,))
            order = cursor.fetchone()
            
            if not order:
                return False, "Заказ не найден"
            
            if order[0] != 'searching':
                return False, "Невозможно назначить водителя на этот заказ"
            
            # Проверяем существование водителя
            cursor.execute('SELECT user_id FROM drivers WHERE user_id = ?', (driver_id,))
            driver = cursor.fetchone()
            
            if not driver:
                return False, "Водитель не найден"
            
            # Обновляем заказ
            cursor.execute('''
                UPDATE orders 
                SET driver_id = ?, status = 'accepted'
                WHERE order_id = ?
            ''', (driver_id, order_id))
            
            conn.commit()
            conn.close()
            
            logger.info(f"Driver {driver_id} assigned to order {order_id}")
            return True, "Водитель успешно назначен"
            
        except Exception as e:
            logger.error(f"Error assigning driver to order: {e}")
            return False, f"Ошибка при назначении водителя: {str(e)}"

    def cancel_order(self, order_id, reason):
        """Отменяет заказ"""
        try:
            conn = self.get_connection()
            cursor = conn.cursor()
            
            # Проверяем существование заказа
            cursor.execute('SELECT status FROM orders WHERE order_id = ?', (order_id,))
            order = cursor.fetchone()
            
            if not order:
                return False, "Заказ не найден"
            
            if order[0] in ['completed', 'canceled']:
                return False, "Невозможно отменить этот заказ"
            
            # Отменяем заказ
            cursor.execute('''
                UPDATE orders 
                SET status = 'canceled', 
                    canceled_reason = ?,
                    canceled_by = 'admin',
                    completed_at = CURRENT_TIMESTAMP
                WHERE order_id = ?
            ''', (reason, order_id))
            
            conn.commit()
            conn.close()
            
            logger.info(f"Order {order_id} canceled by admin. Reason: {reason}")
            return True, "Заказ успешно отменен"
            
        except Exception as e:
            logger.error(f"Error canceling order: {e}")
            return False, f"Ошибка при отмене заказа: {str(e)}"

    def get_passenger_details(self, user_id):
        """Получает детальную информацию о пассажире"""
        conn = self.get_connection()
        cursor = conn.cursor()
        
        # Получаем основную информацию о пользователе
        cursor.execute('''
            SELECT 
                user_id,
                username,
                first_name,
                role,
                is_banned,
                ban_reason,
                ban_until,
                registration_date
            FROM users 
            WHERE user_id = ?
        ''', (user_id,))
        
        user = cursor.fetchone()
        if not user:
            conn.close()
            return None
        
        user_data = {
            'user_id': user[0],
            'username': user[1],
            'first_name': user[2],
            'role': user[3],
            'is_banned': bool(user[4]),
            'ban_reason': user[5],
            'ban_until': user[6],
            'registration_date': user[7]
        }
        
        # Получаем историю заказов пассажира
        cursor.execute('''
            SELECT 
                order_id,
                from_location,
                to_location,
                price,
                status,
                created_at
            FROM orders 
            WHERE passenger_id = ?
            ORDER BY created_at DESC
            LIMIT 50
        ''', (user_id,))
        
        orders = []
        for row in cursor.fetchall():
            orders.append({
                'order_id': row[0],
                'from_location': row[1],
                'to_location': row[2],
                'price': float(row[3]) if row[3] else 0,
                'status': row[4],
                'created_at': row[5]
            })
        
        user_data['orders'] = orders
        conn.close()
        
        return user_data

    def get_driver_details(self, user_id):
        """Получает детальную информацию о водителе"""
        conn = self.get_connection()
        cursor = conn.cursor()
        
        cursor.execute('''
            SELECT 
                d.user_id,
                d.name,
                d.car_brand,
                d.car_model,
                d.license_plate,
                d.car_color,
                d.contact_phone,
                d.payment_phone,
                d.bank,
                d.total_orders,
                d.completed_orders,
                d.canceled_orders,
                d.today_orders,
                d.total_earnings,
                d.today_earnings,
                u.username,
                u.first_name,
                u.is_banned,
                u.ban_reason,
                u.ban_until,
                u.registration_date
            FROM drivers d
            JOIN users u ON d.user_id = u.user_id
            WHERE d.user_id = ?
        ''', (user_id,))
        
        driver = cursor.fetchone()
        if not driver:
            conn.close()
            return None
        
        driver_data = {
            'user_id': driver[0],
            'name': driver[1],
            'car_brand': driver[2],
            'car_model': driver[3],
            'license_plate': driver[4],
            'car_color': driver[5],
            'contact_phone': driver[6],
            'payment_phone': driver[7],
            'bank': driver[8],
            'total_orders': driver[9],
            'completed_orders': driver[10],
            'canceled_orders': driver[11],
            'today_orders': driver[12],
            'total_earnings': float(driver[13]) if driver[13] else 0,
            'today_earnings': float(driver[14]) if driver[14] else 0,
            'username': driver[15],
            'first_name': driver[16],
            'is_banned': bool(driver[17]),
            'ban_reason': driver[18],
            'ban_until': driver[19],
            'registration_date': driver[20]
        }
        
        # Рассчитываем процент успеха
        if driver_data['total_orders'] > 0:
            driver_data['success_rate'] = round((driver_data['completed_orders'] / driver_data['total_orders']) * 100, 2)
        else:
            driver_data['success_rate'] = 0
        
        conn.close()
        return driver_data

    def get_all_passengers(self):
        """Получает список всех пассажиров"""
        conn = self.get_connection()
        cursor = conn.cursor()
        
        cursor.execute('''
            SELECT 
                u.user_id,
                u.username,
                u.first_name,
                u.is_banned,
                u.registration_date,
                COUNT(o.order_id) as total_orders
            FROM users u
            LEFT JOIN orders o ON u.user_id = o.passenger_id
            WHERE u.role = 'user'
            GROUP BY u.user_id
            ORDER BY u.registration_date DESC
        ''')
        
        passengers = []
        for row in cursor.fetchall():
            passengers.append({
                'user_id': row[0],
                'username': row[1],
                'first_name': row[2],
                'is_banned': bool(row[3]),
                'registration_date': row[4],
                'total_orders': row[5]
            })
        
        conn.close()
        return passengers

    def get_all_drivers_for_messaging(self):
        """Получает список всех водителей для рассылки"""
        conn = self.get_connection()
        cursor = conn.cursor()
        
        cursor.execute('''
            SELECT 
                u.user_id,
                u.username,
                u.first_name,
                u.is_banned,
                u.registration_date,
                d.name,
                d.car_brand,
                d.car_model
            FROM users u
            JOIN drivers d ON u.user_id = d.user_id
            ORDER BY u.registration_date DESC
        ''')
        
        drivers = []
        for row in cursor.fetchall():
            drivers.append({
                'user_id': row[0],
                'username': row[1],
                'first_name': row[2],
                'is_banned': bool(row[3]),
                'registration_date': row[4],
                'name': row[5],
                'car_brand': row[6],
                'car_model': row[7]
            })
        
        conn.close()
        return drivers

    def send_message_to_users(self, user_ids, message_text, message_type):
        """Отправляет сообщение пользователям через Telegram бота"""
        try:
            # Здесь должна быть интеграция с вашим Telegram ботом
            # Это пример реализации - замените на реальную отправку через ваш бот
            
            logger.info(f"Sending {message_type} message to {len(user_ids)} users: {message_text}")
            
            # Имитация отправки сообщений
            success_count = 0
            failed_count = 0
            
            for user_id in user_ids:
                try:
                    # ЗАМЕНИТЕ НА РЕАЛЬНЫЙ КОД ОТПРАВКИ ЧЕРЕЗ ВАШЕГО БОТА:
                    # bot.send_message(chat_id=user_id, text=message_text)
                    
                    # Временная заглушка для демонстрации
                    logger.info(f"Sent message to user {user_id}: {message_text[:50]}...")
                    success_count += 1
                    
                    # Небольшая задержка чтобы не превысить лимиты Telegram API
                    import time
                    time.sleep(0.05)  # 50ms задержка между сообщениями
                    
                except Exception as e:
                    logger.error(f"Failed to send message to user {user_id}: {e}")
                    failed_count += 1
            
            if failed_count == 0:
                return True, f"Сообщение успешно отправлено {success_count} пользователям"
            else:
                return True, f"Сообщение отправлено {success_count} пользователям, не удалось отправить {failed_count}"
            
        except Exception as e:
            logger.error(f"Error sending messages: {e}")
            return False, f"Ошибка при отправке сообщений: {str(e)}"

# Создаем экземпляр дашборда
dashboard = TaxiDashboard()

# Маршруты аутентификации
@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.json.get('username')
        password = request.json.get('password')
        
        if (username == ADMIN_CREDENTIALS['username'] and 
            password == ADMIN_CREDENTIALS['password']):
            session['logged_in'] = True
            return jsonify({'success': True, 'message': 'Успешный вход'})
        else:
            return jsonify({'success': False, 'message': 'Неверные учетные данные'}), 401
    
    return jsonify({'message': 'Используйте POST запрос для входа'})

@app.route('/logout')
def logout():
    session.pop('logged_in', None)
    return jsonify({'success': True, 'message': 'Выход выполнен'})

@app.route('/check_auth')
def check_auth():
    return jsonify({'logged_in': session.get('logged_in', False)})

# Основные маршруты
@app.route('/')
def index():
    return render_template('index.html')

# API маршруты (требуют аутентификации)
@app.route('/api/drivers')
@login_required
def api_drivers():
    drivers = dashboard.get_drivers_stats()
    return jsonify(drivers)

@app.route('/api/orders')
@login_required
def api_orders():
    days = request.args.get('days', 7, type=int)
    orders_stats = dashboard.get_orders_stats(days)
    return jsonify(orders_stats)

@app.route('/api/users')
@login_required
def api_users():
    users_stats = dashboard.get_users_stats()
    return jsonify(users_stats)

@app.route('/api/financial')
@login_required
def api_financial():
    financial_stats = dashboard.get_financial_stats()
    return jsonify(financial_stats)

@app.route('/api/dashboard')
@login_required
def api_dashboard():
    """Все данные для главной страницы дашборда"""
    drivers = dashboard.get_drivers_stats()
    orders_stats = dashboard.get_orders_stats(7)
    users_stats = dashboard.get_users_stats()
    financial_stats = dashboard.get_financial_stats()
    
    return jsonify({
        'drivers': drivers,
        'orders': orders_stats,
        'users': users_stats,
        'financial': financial_stats
    })

# API маршруты для управления
@app.route('/api/admin/users')
@login_required
def api_admin_users():
    """Получает список всех пользователей для админки"""
    users = dashboard.get_all_users()
    return jsonify(users)

@app.route('/api/admin/ban', methods=['POST'])
@login_required
def api_admin_ban():
    """Банит пользователя"""
    data = request.json
    user_id = data.get('user_id')
    reason = data.get('reason', 'Не указана')
    duration_days = data.get('duration_days', 1)
    
    if not user_id:
        return jsonify({'success': False, 'message': 'ID пользователя обязателен'}), 400
    
    success = dashboard.ban_user(user_id, reason, duration_days)
    
    if success:
        return jsonify({'success': True, 'message': f'Пользователь {user_id} забанен'})
    else:
        return jsonify({'success': False, 'message': 'Ошибка при бане пользователя'}), 500

@app.route('/api/admin/unban', methods=['POST'])
@login_required
def api_admin_unban():
    """Разбанивает пользователя"""
    data = request.json
    user_id = data.get('user_id')
    
    if not user_id:
        return jsonify({'success': False, 'message': 'ID пользователя обязателен'}), 400
    
    success = dashboard.unban_user(user_id)
    
    if success:
        return jsonify({'success': True, 'message': f'Пользователь {user_id} разбанен'})
    else:
        return jsonify({'success': False, 'message': 'Ошибка при разбане пользователя'}), 500

@app.route('/api/admin/create_driver', methods=['POST'])
@login_required
def api_admin_create_driver():
    """Создает водителя из пользователя"""
    data = request.json
    user_id = data.get('user_id')
    driver_data = data.get('driver_data')
    
    if not user_id or not driver_data:
        return jsonify({'success': False, 'message': 'ID пользователя и данные водителя обязательны'}), 400
    
    # Проверяем обязательные поля
    required_fields = ['name', 'car_brand', 'car_model', 'license_plate', 'car_color', 'contact_phone']
    for field in required_fields:
        if not driver_data.get(field):
            return jsonify({'success': False, 'message': f'Поле {field} обязательно'}), 400
    
    success, message = dashboard.create_driver(user_id, driver_data)
    
    if success:
        return jsonify({'success': True, 'message': message})
    else:
        return jsonify({'success': False, 'message': message}), 500

@app.route('/api/admin/delete_driver', methods=['POST'])
@login_required
def api_admin_delete_driver():
    """Удаляет водителя"""
    data = request.json
    user_id = data.get('user_id')
    
    if not user_id:
        return jsonify({'success': False, 'message': 'ID пользователя обязателен'}), 400
    
    success, message = dashboard.delete_driver(user_id)
    
    if success:
        return jsonify({'success': True, 'message': message})
    else:
        return jsonify({'success': False, 'message': message}), 500

@app.route('/api/admin/assign_driver', methods=['POST'])
@login_required
def api_admin_assign_driver():
    """Назначает водителя на заказ"""
    data = request.json
    order_id = data.get('order_id')
    driver_id = data.get('driver_id')
    
    if not order_id or not driver_id:
        return jsonify({'success': False, 'message': 'ID заказа и водителя обязательны'}), 400
    
    success, message = dashboard.assign_driver_to_order(order_id, driver_id)
    
    if success:
        return jsonify({'success': True, 'message': message})
    else:
        return jsonify({'success': False, 'message': message}), 500

@app.route('/api/admin/cancel_order', methods=['POST'])
@login_required
def api_admin_cancel_order():
    """Отменяет заказ"""
    data = request.json
    order_id = data.get('order_id')
    reason = data.get('reason', 'Отменен администратором')
    
    if not order_id:
        return jsonify({'success': False, 'message': 'ID заказа обязателен'}), 400
    
    success, message = dashboard.cancel_order(order_id, reason)
    
    if success:
        return jsonify({'success': True, 'message': message})
    else:
        return jsonify({'success': False, 'message': message}), 500

@app.route('/api/admin/passenger/<int:user_id>')
@login_required
def api_admin_passenger_details(user_id):
    """Получает детальную информацию о пассажире"""
    passenger_data = dashboard.get_passenger_details(user_id)
    
    if not passenger_data:
        return jsonify({'success': False, 'message': 'Пассажир не найден'}), 404
    
    return jsonify(passenger_data)

@app.route('/api/admin/driver/<int:user_id>')
@login_required
def api_admin_driver_details(user_id):
    """Получает детальную информацию о водителе"""
    driver_data = dashboard.get_driver_details(user_id)
    
    if not driver_data:
        return jsonify({'success': False, 'message': 'Водитель не найден'}), 404
    
    return jsonify(driver_data)

@app.route('/api/admin/available_drivers')
@login_required
def api_admin_available_drivers():
    """Получает список доступных водителей"""
    drivers = dashboard.get_available_drivers()
    return jsonify(drivers)

# Новые API маршруты для рассылки сообщений
@app.route('/api/admin/passengers')
@login_required
def api_admin_passengers():
    """Получает список всех пассажиров"""
    passengers = dashboard.get_all_passengers()
    return jsonify(passengers)

@app.route('/api/admin/drivers_for_messaging')
@login_required
def api_admin_drivers_for_messaging():
    """Получает список всех водителей для рассылки"""
    drivers = dashboard.get_all_drivers_for_messaging()
    return jsonify(drivers)

@app.route('/api/admin/send_message', methods=['POST'])
@login_required
def api_admin_send_message():
    """Отправляет сообщение пользователям"""
    data = request.json
    user_ids = data.get('user_ids', [])
    message_text = data.get('message_text', '')
    message_type = data.get('message_type', 'broadcast')
    
    if not user_ids:
        return jsonify({'success': False, 'message': 'Не выбраны пользователи'}), 400
    
    if not message_text.strip():
        return jsonify({'success': False, 'message': 'Текст сообщения не может быть пустым'}), 400
    
    success, message = dashboard.send_message_to_users(user_ids, message_text, message_type)
    
    if success:
        return jsonify({'success': True, 'message': message})
    else:
        return jsonify({'success': False, 'message': message}), 500

if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5000)