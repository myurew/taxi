# db_utils.py
import sqlite3
import queue
from datetime import datetime, timedelta

# Глобальная очередь для рассылки
BROADCAST_QUEUE = queue.Queue()

DB = None

def init_db(db_path='taxi.db'):
    global DB
    if DB is None:
        DB = sqlite3.connect(db_path, check_same_thread=False)
        _create_tables()
        _insert_defaults()
        _update_schema()
    return DB

def _create_tables():
    c = DB.cursor()
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
        CREATE TABLE IF NOT EXISTS eta_options (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            text TEXT NOT NULL,
            minutes INTEGER NOT NULL
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
            driver_eta_select_message_id INTEGER,
            confirm_timeout_at TEXT,
            return_count INTEGER DEFAULT 0
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
    c.execute('''
        CREATE TABLE IF NOT EXISTS cancellation_reasons (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_type TEXT CHECK(user_type IN ('driver', 'passenger')),
            reason_text TEXT NOT NULL
        )
    ''')
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
    c.execute('''
        CREATE TABLE IF NOT EXISTS user_stats (
            user_id INTEGER PRIMARY KEY,
            cancel_count_24h INTEGER DEFAULT 0,
            last_cancel_reset TEXT DEFAULT (datetime('now', '-1 day'))
        )
    ''')
    DB.commit()

def _insert_defaults():
    c = DB.cursor()
    c.execute("SELECT COUNT(*) FROM tariffs")
    if c.fetchone()[0] == 0:
        c.execute("INSERT INTO tariffs (name, price) VALUES ('300 ₽', 300.0), ('500 ₽', 500.0), ('700 ₽', 700.0), ('1000 ₽', 1000.0)")

    c.execute("SELECT COUNT(*) FROM eta_options")
    if c.fetchone()[0] == 0:
        c.execute("INSERT INTO eta_options (text, minutes) VALUES ('5 мин', 5), ('10 мин', 10), ('15 мин', 15), ('20 мин', 20), ('25 мин', 25), ('30 мин', 30), ('>30 мин', 60)")

    c.execute("SELECT COUNT(*) FROM cancellation_reasons")
    if c.fetchone()[0] == 0:
        # Причины для водителя
        c.execute("INSERT INTO cancellation_reasons (user_type, reason_text) VALUES ('driver', 'Отменён водителем')")
        c.execute("INSERT INTO cancellation_reasons (user_type, reason_text) VALUES ('driver', 'Не договорились о цене')")
        c.execute("INSERT INTO cancellation_reasons (user_type, reason_text) VALUES ('driver', 'Долгое ожидание')")
        c.execute("INSERT INTO cancellation_reasons (user_type, reason_text) VALUES ('driver', 'Отменён пассажиром')")
        # Причины для пассажира
        c.execute("INSERT INTO cancellation_reasons (user_type, reason_text) VALUES ('passenger', 'Передумал')")
        c.execute("INSERT INTO cancellation_reasons (user_type, reason_text) VALUES ('passenger', 'Не устраивает водитель')")
        c.execute("INSERT INTO cancellation_reasons (user_type, reason_text) VALUES ('passenger', 'Не устраивает машина')")
        c.execute("INSERT INTO cancellation_reasons (user_type, reason_text) VALUES ('passenger', 'Долгое ожидание')")

    DB.commit()

def _update_schema():
    cur = DB.cursor()
    # Добавляем новые поля, если их нет
    for col in [
        "confirm_timeout_at",
        "return_count"
    ]:
        try:
            cur.execute(f"ALTER TABLE trips ADD COLUMN {col} TEXT")
        except sqlite3.OperationalError:
            pass
    try:
        cur.execute("ALTER TABLE users ADD COLUMN is_banned INTEGER DEFAULT 0")
    except sqlite3.OperationalError:
        pass
    DB.commit()

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

def has_active_order(user_id):
    cur = DB.cursor()
    res = cur.execute("""
        SELECT 1 FROM trips 
        WHERE passenger_id = ? AND status IN ('requested', 'accepted', 'in_progress')
    """, (user_id,)).fetchone()
    return bool(res)

def create_trip(passenger_id, pickup, destination):
    cur = DB.cursor()
    cur.execute("INSERT INTO trips (passenger_id, pickup, destination) VALUES (?, ?, ?)", (passenger_id, pickup, destination))
    trip_id = cur.lastrowid
    DB.commit()
    return trip_id

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

def get_eta_options():
    cur = DB.cursor()
    return cur.execute("SELECT id, text, minutes FROM eta_options ORDER BY minutes").fetchall()

def get_driver_rating(driver_id):
    cur = DB.cursor()
    res = cur.execute("SELECT AVG(rating) FROM ratings WHERE driver_id = ?", (driver_id,)).fetchone()
    return round(res[0], 1) if res[0] else None

def save_rating(trip_id, driver_id, passenger_id, rating):
    cur = DB.cursor()
    cur.execute("INSERT OR REPLACE INTO ratings (trip_id, driver_id, passenger_id, rating) VALUES (?, ?, ?, ?)", 
                (trip_id, driver_id, passenger_id, rating))
    DB.commit()

# === Система отмен и банов ===
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

def increment_cancel_count(user_id):
    cur = DB.cursor()
    now = datetime.now()
    cur.execute("SELECT cancel_count_24h, last_cancel_reset FROM user_stats WHERE user_id = ?", (user_id,))
    row = cur.fetchone()
    if not row:
        cur.execute("INSERT INTO user_stats (user_id, cancel_count_24h, last_cancel_reset) VALUES (?, 1, ?)", (user_id, now))
    else:
        count, last_reset = row
        last_reset_dt = datetime.strptime(last_reset, '%Y-%m-%d %H:%M:%S')
        if (now - last_reset_dt).days >= 1:
            count = 0
            cur.execute("UPDATE user_stats SET cancel_count_24h = 1, last_cancel_reset = ? WHERE user_id = ?", (now, user_id))
        else:
            new_count = count + 1
            cur.execute("UPDATE user_stats SET cancel_count_24h = ? WHERE user_id = ?", (new_count, user_id))
            if new_count > 3:
                ban_user(user_id, "Частые отмены заказов", 1)
    DB.commit()

def get_active_drivers_count():
    cur = DB.cursor()
    return cur.execute("SELECT COUNT(*) FROM users WHERE role = 'driver' AND is_banned = 0").fetchone()[0]

def get_driver_active_orders_count(driver_id):
    cur = DB.cursor()
    return cur.execute("SELECT COUNT(*) FROM trips WHERE driver_id = ? AND status IN ('accepted', 'in_progress')", (driver_id,)).fetchone()[0]

# === UPDATE MESSAGE ID FUNCTIONS ===
def update_passenger_message_id(trip_id, message_id):
    _update_trip_field(trip_id, "passenger_message_id", message_id)

def update_driver_message_id(trip_id, message_id):
    _update_trip_field(trip_id, "driver_message_id", message_id)

def update_driver_card_message_id(trip_id, message_id):
    _update_trip_field(trip_id, "driver_card_message_id", message_id)

def update_passenger_fare_message_id(trip_id, message_id):
    _update_trip_field(trip_id, "passenger_fare_message_id", message_id)

def update_passenger_eta_message_id(trip_id, message_id):
    _update_trip_field(trip_id, "passenger_eta_message_id", message_id)

def update_driver_fare_message_id(trip_id, message_id):
    _update_trip_field(trip_id, "driver_fare_message_id", message_id)

def update_driver_eta_message_id(trip_id, message_id):
    _update_trip_field(trip_id, "driver_eta_message_id", message_id)

def update_driver_control_message_id(trip_id, message_id):
    _update_trip_field(trip_id, "driver_control_message_id", message_id)

def update_passenger_arrival_message_id(trip_id, message_id):
    _update_trip_field(trip_id, "passenger_arrival_message_id", message_id)

def update_driver_tariff_message_id(trip_id, message_id):
    _update_trip_field(trip_id, "driver_tariff_message_id", message_id)

def update_driver_eta_select_message_id(trip_id, message_id):
    _update_trip_field(trip_id, "driver_eta_select_message_id", message_id)

def update_confirm_timeout(trip_id, timeout_at):
    _update_trip_field(trip_id, "confirm_timeout_at", timeout_at)

def increment_return_count(trip_id):
    cur = DB.cursor()
    cur.execute("UPDATE trips SET return_count = return_count + 1 WHERE id = ?", (trip_id,))
    DB.commit()

def get_return_count(trip_id):
    cur = DB.cursor()
    res = cur.execute("SELECT return_count FROM trips WHERE id = ?", (trip_id,)).fetchone()
    return res[0] if res else 0

def _update_trip_field(trip_id, field, value):
    cur = DB.cursor()
    cur.execute(f"UPDATE trips SET {field} = ? WHERE id = ?", (value, trip_id))
    DB.commit()