import asyncio
import logging
import random
import json
import os
import sqlite3
import time
from datetime import datetime, timedelta
from typing import Optional, Tuple, List, Dict, Any
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command, CommandStart
from aiogram.types import (
    Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton,
    ReplyKeyboardMarkup, KeyboardButton
)
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from decimal import Decimal, getcontext
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger

getcontext().prec = 1000

# ====== НАСТРОЙКИ ======
BOT_TOKEN = "8795223329:AAG0AmK9_8Ki5KMDyJbbgfRjPZCm-jY8OIo"
ADMIN_IDS = [69198496]
SUPPORT_USERNAME = "internetcomunity"

# Константы
START_BONUS = "150000"
BONUS_AMOUNT = "45000"
MINES_MULTIPLIER_PER_STEP = 0.2
MINES_COUNT = 4
GAME_TIMEOUT_MINUTES = 5
MAX_BET = "1000000000000"  # 1 триллион
MAX_CASES_PER_OPEN = 50  # Максимум кейсов за раз
TRANSACTION_CLEANUP_DAYS = 30  # Очистка транзакций старше 30 дней
LOCK_TIMEOUT = 30  # Таймаут блокировки в секундах

# Настройка логирования
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

bot = Bot(token=BOT_TOKEN)
storage = MemoryStorage()
dp = Dispatcher(storage=storage)
scheduler = AsyncIOScheduler()

# Словарь для блокировок пользователей
user_locks = {}
# Словарь для времени последней активности блокировки
lock_last_used = {}

class AdminStates(StatesGroup):
    waiting_for_user_id = State()
    waiting_for_amount = State()
    waiting_for_broadcast = State()
    waiting_for_reset_user_id = State()
    waiting_for_promo_code = State()
    waiting_for_promo_amount = State()
    waiting_for_promo_uses = State()
    waiting_for_promo_input = State()

# ====== ФУНКЦИИ ДЛЯ ФОРМАТИРОВАНИЯ ЧИСЕЛ ======
def format_number(num) -> str:
    """Форматирует число из строки с пробелами"""
    try:
        num_str = str(num)
        
        if 'e' in num_str.lower() or '.' in num_str:
            from decimal import Decimal
            d = Decimal(str(num))
            num_str = format(d, 'f')
            if '.' in num_str:
                num_str = num_str.rstrip('0').rstrip('.')
        
        is_negative = False
        if num_str.startswith('-'):
            is_negative = True
            num_str = num_str[1:]
        
        if len(num_str) > 50:
            first_part = num_str[:30]
            last_part = num_str[-15:]
            return f"{'-' if is_negative else ''}{first_part}...{last_part}"
        
        formatted = ''
        for i, char in enumerate(reversed(num_str)):
            if i > 0 and i % 3 == 0:
                formatted = ' ' + formatted
            formatted = char + formatted
        
        return ('-' + formatted) if is_negative else formatted
        
    except Exception as e:
        logger.error(f"Ошибка форматирования числа {num}: {e}")
        return str(num)

def parse_amount(amount_str: str) -> Optional[str]:
    try:
        amount_str = amount_str.strip().replace(' ', '').replace(',', '')
        is_negative = False
        if amount_str.startswith('-'):
            is_negative = True
            amount_str = amount_str[1:]
        
        if amount_str.upper().endswith('K'):
            num = float(amount_str[:-1]) * 1000
            result = str(int(num))
        elif amount_str.upper().endswith('M'):
            num = float(amount_str[:-1]) * 1_000_000
            result = str(int(num))
        elif amount_str.upper().endswith('B'):
            num = float(amount_str[:-1]) * 1_000_000_000
            result = str(int(num))
        else:
            int(amount_str)
            result = amount_str
        
        return '-' + result if is_negative else result
    except Exception as e:
        logger.error(f"Ошибка парсинга суммы {amount_str}: {e}")
        return None

async def animate_case(message: Message, case_name: str):
    msg = await message.answer("🎁 [□□□□□□□□□□] 0%")
    await asyncio.sleep(0.3)
    await msg.edit_text("🎁 [▓▓▓▓▓▓▓▓▓▓] 25%")
    await asyncio.sleep(0.3)
    await msg.edit_text("🎁 [▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓] 50%")
    await asyncio.sleep(0.3)
    await msg.edit_text("🎁 [▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓] 75%")
    await asyncio.sleep(0.3)
    await msg.edit_text("🎁 [▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓] 100%")
    await asyncio.sleep(0.3)
    await msg.delete()
    return await message.answer(f"✨ {case_name} открыт! Результат:")

# ====== ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ======
def validate_bet(bet: str, balance: str) -> Tuple[bool, str]:
    """Проверяет корректность ставки"""
    try:
        # Проверка на отрицательное число
        if bet.startswith('-'):
            return False, "❌ Ставка не может быть отрицательной"
        
        # Проверка на дробные числа
        if '.' in bet or ',' in bet:
            return False, "❌ Ставка должна быть целым числом"
        
        bet_decimal = Decimal(bet)
        
        # Проверка на ноль
        if bet_decimal <= 0:
            return False, "❌ Ставка должна быть больше 0"
        
        # Проверка на максимальную ставку
        if bet_decimal > Decimal(MAX_BET):
            return False, f"❌ Максимальная ставка: {format_number(MAX_BET)} GRAM"
        
        # Проверка на минимальную ставку
        if bet_decimal < 5:
            return False, "❌ Минимальная ставка: 5 GRAM"
        
        # Проверка на достаточность средств
        if bet_decimal > Decimal(balance.replace(' ', '')):
            return False, "❌ Недостаточно средств"
        
        return True, "✅ Ставка принята"
    except Exception as e:
        logger.error(f"Ошибка валидации ставки {bet}: {e}")
        return False, "❌ Некорректная сумма"

async def get_user_lock(user_id: int) -> asyncio.Lock:
    """Получает или создает блокировку для пользователя с очисткой старых"""
    # Очищаем старые блокировки (не использованные более LOCK_TIMEOUT секунд)
    current_time = time.time()
    expired_locks = [uid for uid, last_time in lock_last_used.items() 
                    if current_time - last_time > LOCK_TIMEOUT]
    for uid in expired_locks:
        if uid in user_locks:
            del user_locks[uid]
        if uid in lock_last_used:
            del lock_last_used[uid]
    
    if user_id not in user_locks:
        user_locks[user_id] = asyncio.Lock()
    
    lock_last_used[user_id] = current_time
    return user_locks[user_id]

# ====== КЛАСС ДЛЯ РАБОТЫ С SQLite ======
class SQLiteDatabase:
    def __init__(self):
        self.db_file = 'gram_bot.db'
        self.init_db()
        # Кэш для топ-пользователей
        self.top_cache = []
        self.top_cache_time = 0
        # Кэш для проверки дубликатов транзакций
        self.last_transaction_hash = {}
        logger.info(f"📁 База данных SQLite создана/подключена")

    def get_connection(self):
        conn = sqlite3.connect(self.db_file, timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute('PRAGMA journal_mode=WAL')
        conn.execute('PRAGMA synchronous=NORMAL')
        return conn

    def init_db(self):
        conn = None
        try:
            conn = self.get_connection()
            cursor = conn.cursor()
            
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS users (
                    user_id INTEGER PRIMARY KEY,
                    username TEXT,
                    first_name TEXT,
                    last_name TEXT,
                    balance TEXT DEFAULT '0',
                    last_bonus TIMESTAMP,
                    is_admin INTEGER DEFAULT 0,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    used_promocodes TEXT DEFAULT '[]'
                )
            ''')
            
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS transactions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    from_id INTEGER,
                    to_id INTEGER,
                    amount TEXT,
                    type TEXT,
                    chat_id INTEGER,
                    comment TEXT,
                    transaction_hash TEXT UNIQUE,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS mines_games (
                    user_id INTEGER PRIMARY KEY,
                    chat_id INTEGER,
                    bet TEXT,
                    current_win TEXT,
                    mines_positions TEXT,
                    opened_cells TEXT DEFAULT '[]',
                    status TEXT DEFAULT 'active',
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS box_games (
                    user_id INTEGER PRIMARY KEY,
                    chat_id INTEGER,
                    bet TEXT,
                    gold_box INTEGER,
                    opened_boxes TEXT DEFAULT '[]',
                    status TEXT DEFAULT 'active',
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS bonus_cases (
                    user_id INTEGER PRIMARY KEY,
                    last_bonus TIMESTAMP
                )
            ''')
            
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS skins (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER,
                    name TEXT,
                    rarity TEXT,
                    price TEXT,
                    case_type TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS secret_room_games (
                    user_id INTEGER PRIMARY KEY,
                    chat_id INTEGER,
                    bet TEXT,
                    current_win TEXT,
                    treasure_doors TEXT,
                    monster_doors TEXT,
                    empty_doors TEXT,
                    opened_doors TEXT DEFAULT '[]',
                    doors_count INTEGER DEFAULT 0,
                    treasures_found INTEGER DEFAULT 0,
                    multiplier REAL DEFAULT 1.0,
                    status TEXT DEFAULT 'active',
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS promocodes (
                    code TEXT PRIMARY KEY,
                    amount TEXT,
                    max_uses INTEGER,
                    uses INTEGER DEFAULT 0,
                    active INTEGER DEFAULT 1,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            
            conn.commit()
            logger.info("✅ Таблицы созданы/проверены")
        except Exception as e:
            logger.error(f"❌ Ошибка инициализации БД: {e}")
        finally:
            if conn:
                conn.close()

    # ====== МЕТОДЫ ДЛЯ ПОЛЬЗОВАТЕЛЕЙ ======
    
    def get_or_create_user(self, user_id: int, username: str = None,
                          first_name: str = None, last_name: str = None) -> dict:
        conn = None
        for attempt in range(3):
            try:
                conn = self.get_connection()
                cursor = conn.cursor()
                
                cursor.execute('SELECT * FROM users WHERE user_id = ?', (user_id,))
                user = cursor.fetchone()
                
                if user:
                    cursor.execute('''
                        UPDATE users SET username = ?, first_name = ?, last_name = ?
                        WHERE user_id = ?
                    ''', (username, first_name, last_name, user_id))
                    conn.commit()
                    cursor.execute('SELECT * FROM users WHERE user_id = ?', (user_id,))
                    user = cursor.fetchone()
                else:
                    cursor.execute('''
                        INSERT INTO users (user_id, username, first_name, last_name, balance)
                        VALUES (?, ?, ?, ?, '0')
                    ''', (user_id, username, first_name, last_name))
                    conn.commit()
                    cursor.execute('SELECT * FROM users WHERE user_id = ?', (user_id,))
                    user = cursor.fetchone()
                    logger.info(f"👤 Создан новый пользователь {user_id}")
                    # Инвалидируем кэш топа при создании нового пользователя
                    self.top_cache = []
                
                return dict(user)
            except sqlite3.OperationalError as e:
                if "locked" in str(e) and attempt < 2:
                    time.sleep(0.1)
                    continue
                logger.error(f"Ошибка БД (get_or_create_user) для {user_id}: {e}")
                return {}
            finally:
                if conn:
                    conn.close()
        return {}

    def get_user(self, user_id: int) -> Optional[dict]:
        conn = None
        for attempt in range(3):
            try:
                conn = self.get_connection()
                cursor = conn.cursor()
                cursor.execute('SELECT * FROM users WHERE user_id = ?', (user_id,))
                user = cursor.fetchone()
                return dict(user) if user else None
            except sqlite3.OperationalError as e:
                if "locked" in str(e) and attempt < 2:
                    time.sleep(0.1)
                    continue
                logger.error(f"Ошибка БД (get_user) для {user_id}: {e}")
                return None
            finally:
                if conn:
                    conn.close()
        return None

    def get_user_by_username(self, username: str) -> Optional[dict]:
        username = username.lower().replace('@', '')
        conn = None
        for attempt in range(3):
            try:
                conn = self.get_connection()
                cursor = conn.cursor()
                cursor.execute('SELECT * FROM users WHERE username = ?', (username,))
                user = cursor.fetchone()
                return dict(user) if user else None
            except sqlite3.OperationalError as e:
                if "locked" in str(e) and attempt < 2:
                    time.sleep(0.1)
                    continue
                logger.error(f"Ошибка БД (get_user_by_username) для {username}: {e}")
                return None
            finally:
                if conn:
                    conn.close()
        return None

    def get_balance(self, user_id: int) -> str:
        conn = None
        for attempt in range(3):
            try:
                conn = self.get_connection()
                cursor = conn.cursor()
                cursor.execute('SELECT balance FROM users WHERE user_id = ?', (user_id,))
                result = cursor.fetchone()
                if not result:
                    logger.warning(f"Пользователь {user_id} не найден при запросе баланса")
                    return '0'
                return str(result['balance'])
            except sqlite3.OperationalError as e:
                if "locked" in str(e) and attempt < 2:
                    time.sleep(0.1)
                    continue
                logger.error(f"Ошибка БД (get_balance) для {user_id}: {e}")
                return '0'
            finally:
                if conn:
                    conn.close()
        return '0'

    def update_balance(self, user_id: int, amount) -> Tuple[bool, str, str]:
        """Возвращает (успех, сообщение, новый баланс)"""
        conn = None
        
        amount_str = str(amount)
        
        # Проверка на отрицательный баланс
        if amount_str.startswith('-'):
            current = Decimal(self.get_balance(user_id).replace(' ', ''))
            if current < Decimal(amount_str.replace('-', '').replace(' ', '')):
                logger.warning(f"Попытка уйти в минус: {user_id}, сумма {amount_str}")
                return False, "❌ Недостаточно средств", self.get_balance(user_id)
        
        for attempt in range(3):
            try:
                conn = self.get_connection()
                cursor = conn.cursor()
                
                cursor.execute('SELECT balance FROM users WHERE user_id = ?', (user_id,))
                current = cursor.fetchone()
                if not current:
                    return False, "❌ Пользователь не найден", '0'
                
                current_balance = str(current['balance'])
                
                try:
                    current_dec = Decimal(str(current_balance).replace(' ', ''))
                    amount_dec = Decimal(str(amount_str).replace(' ', ''))
                    new_dec = current_dec + amount_dec
                    
                    if new_dec == new_dec.to_integral():
                        new_balance = str(int(new_dec))
                    else:
                        new_balance = format(new_dec, 'f').rstrip('0').rstrip('.')
                        
                except Exception as e:
                    logger.error(f"Ошибка при сложении для {user_id}: {e}")
                    return False, "❌ Ошибка при расчете баланса", current_balance
                
                cursor.execute('UPDATE users SET balance = ? WHERE user_id = ?', (new_balance, user_id))
                conn.commit()
                
                # Инвалидируем кэш
                self.top_cache = []
                
                logger.info(f"💰 Баланс {user_id} изменен на {amount_str}")
                return True, "✅ Баланс обновлен", new_balance
            except sqlite3.OperationalError as e:
                if "locked" in str(e) and attempt < 2:
                    time.sleep(0.1)
                    continue
                logger.error(f"Ошибка БД (update_balance) для {user_id}: {e}")
                return False, "❌ Ошибка базы данных", self.get_balance(user_id)
            finally:
                if conn:
                    conn.close()
        return False, "❌ Ошибка обновления баланса", self.get_balance(user_id)

    def reset_balance(self, user_id: int) -> Tuple[bool, str, str]:
        conn = None
        for attempt in range(3):
            try:
                conn = self.get_connection()
                cursor = conn.cursor()
                cursor.execute('SELECT balance FROM users WHERE user_id = ?', (user_id,))
                result = cursor.fetchone()
                if not result:
                    return False, "❌ Пользователь не найден", '0'
                
                old_balance = str(result['balance'])
                
                cursor.execute('UPDATE users SET balance = ? WHERE user_id = ?', ('0', user_id))
                conn.commit()
                
                self.add_transaction({
                    'from_id': user_id,
                    'amount': old_balance,
                    'type': 'balance_reset'
                })
                
                self.top_cache = []
                
                logger.info(f"🔄 Баланс {user_id} обнулен")
                return True, "✅ Баланс обнулен", old_balance
            except sqlite3.OperationalError as e:
                if "locked" in str(e) and attempt < 2:
                    time.sleep(0.1)
                    continue
                logger.error(f"Ошибка БД (reset_balance) для {user_id}: {e}")
                return False, "❌ Ошибка базы данных", '0'
            finally:
                if conn:
                    conn.close()
        return False, "❌ Ошибка обнуления баланса", '0'

    def is_admin(self, user_id: int) -> bool:
        conn = None
        for attempt in range(3):
            try:
                conn = self.get_connection()
                cursor = conn.cursor()
                cursor.execute('SELECT is_admin FROM users WHERE user_id = ?', (user_id,))
                result = cursor.fetchone()
                return bool(result and result['is_admin'])
            except sqlite3.OperationalError as e:
                if "locked" in str(e) and attempt < 2:
                    time.sleep(0.1)
                    continue
                logger.error(f"Ошибка БД (is_admin) для {user_id}: {e}")
                return False
            finally:
                if conn:
                    conn.close()
        return False

    def get_all_users(self) -> List[dict]:
        conn = None
        for attempt in range(3):
            try:
                conn = self.get_connection()
                cursor = conn.cursor()
                cursor.execute('SELECT * FROM users')
                users = []
                for row in cursor.fetchall():
                    user = dict(row)
                    if user['balance'] is not None:
                        user['balance'] = str(user['balance'])
                    else:
                        user['balance'] = '0'
                    users.append(user)
                return users
            except sqlite3.OperationalError as e:
                if "locked" in str(e) and attempt < 2:
                    time.sleep(0.1)
                    continue
                logger.error(f"Ошибка БД (get_all_users): {e}")
                return []
            finally:
                if conn:
                    conn.close()
        return []

    def get_top_users(self, limit: int = 10) -> List[dict]:
        """Кэшированный топ пользователей с правильной сортировкой"""
        current_time = time.time()
        
        if self.top_cache and current_time - self.top_cache_time < 60:
            return self.top_cache[:limit]
        
        all_users = self.get_all_users()
        # Правильная сортировка по числовому значению баланса
        all_users.sort(key=lambda x: Decimal(str(x['balance']).replace(' ', '')), reverse=True)
        
        self.top_cache = all_users
        self.top_cache_time = current_time
        
        return all_users[:limit]

    def get_stats(self) -> dict:
        conn = None
        for attempt in range(3):
            try:
                conn = self.get_connection()
                cursor = conn.cursor()
                
                cursor.execute('SELECT COUNT(*) as count FROM users')
                total_users = cursor.fetchone()['count']
                
                cursor.execute('SELECT balance FROM users')
                balances = cursor.fetchall()
                total_balance = Decimal('0')
                for b in balances:
                    try:
                        bal_str = str(b['balance']) if b['balance'] is not None else '0'
                        total_balance += Decimal(bal_str)
                    except Exception as e:
                        logger.error(f"Ошибка при суммировании баланса: {e}")
                
                cursor.execute('''
                    SELECT COUNT(*) as count FROM users 
                    WHERE date(created_at) = date('now')
                ''')
                today_users = cursor.fetchone()['count']
                
                return {
                    'total_users': total_users,
                    'total_balance': str(total_balance),
                    'today_users': today_users
                }
            except sqlite3.OperationalError as e:
                if "locked" in str(e) and attempt < 2:
                    time.sleep(0.1)
                    continue
                logger.error(f"Ошибка БД (get_stats): {e}")
                return {'total_users': 0, 'total_balance': '0', 'today_users': 0}
            finally:
                if conn:
                    conn.close()
        return {'total_users': 0, 'total_balance': '0', 'today_users': 0}

    # ====== МЕТОДЫ ДЛЯ БОНУСОВ ======
    
    def can_get_bonus(self, user_id: int) -> Tuple[bool, Optional[datetime]]:
        conn = None
        for attempt in range(3):
            try:
                conn = self.get_connection()
                cursor = conn.cursor()
                cursor.execute('SELECT last_bonus FROM users WHERE user_id = ?', (user_id,))
                result = cursor.fetchone()
                
                if not result or not result['last_bonus']:
                    return True, None
                
                last_bonus = datetime.fromisoformat(result['last_bonus'])
                next_bonus = last_bonus + timedelta(hours=24)
                now = datetime.now()
                
                return now >= next_bonus, next_bonus
            except sqlite3.OperationalError as e:
                if "locked" in str(e) and attempt < 2:
                    time.sleep(0.1)
                    continue
                logger.error(f"Ошибка БД (can_get_bonus) для {user_id}: {e}")
                return False, None
            finally:
                if conn:
                    conn.close()
        return False, None

    def give_bonus(self, user_id: int) -> Tuple[bool, str]:
        conn = None
        for attempt in range(3):
            try:
                conn = self.get_connection()
                cursor = conn.cursor()
                
                cursor.execute('SELECT balance FROM users WHERE user_id = ?', (user_id,))
                current = cursor.fetchone()
                if not current:
                    return False, "❌ Пользователь не найден"
                
                current_balance = str(current['balance'])
                
                try:
                    current_dec = Decimal(current_balance.replace(' ', ''))
                    bonus_dec = Decimal(BONUS_AMOUNT)
                    new_dec = current_dec + bonus_dec
                    new_balance = str(new_dec)
                except Exception as e:
                    logger.error(f"Ошибка при начислении бонуса {user_id}: {e}")
                    return False, "❌ Ошибка при начислении бонуса"
                
                cursor.execute('''
                    UPDATE users SET balance = ?, last_bonus = ? 
                    WHERE user_id = ?
                ''', (new_balance, datetime.now().isoformat(), user_id))
                conn.commit()
                
                self.add_transaction({
                    'to_id': user_id,
                    'amount': BONUS_AMOUNT,
                    'type': 'daily_bonus'
                })
                
                self.top_cache = []
                
                logger.info(f"🎁 Бонус {BONUS_AMOUNT} выдан {user_id}")
                return True, "✅ Бонус получен"
            except sqlite3.OperationalError as e:
                if "locked" in str(e) and attempt < 2:
                    time.sleep(0.1)
                    continue
                logger.error(f"Ошибка БД (give_bonus) для {user_id}: {e}")
                return False, "❌ Ошибка базы данных"
            finally:
                if conn:
                    conn.close()
        return False, "❌ Ошибка при выдаче бонуса"

    def give_start_bonus(self, user_id: int) -> Tuple[bool, str]:
        conn = None
        for attempt in range(3):
            try:
                conn = self.get_connection()
                cursor = conn.cursor()
                
                cursor.execute('SELECT balance FROM users WHERE user_id = ?', (user_id,))
                current = cursor.fetchone()
                if not current:
                    return False, "❌ Пользователь не найден"
                
                current_balance = str(current['balance'])
                
                try:
                    current_dec = Decimal(current_balance.replace(' ', ''))
                    start_dec = Decimal(START_BONUS)
                    new_dec = current_dec + start_dec
                    new_balance = str(new_dec)
                except Exception as e:
                    logger.error(f"Ошибка при начислении стартового бонуса {user_id}: {e}")
                    return False, "❌ Ошибка при начислении"
                
                cursor.execute('UPDATE users SET balance = ? WHERE user_id = ?', (new_balance, user_id))
                conn.commit()
                
                self.add_transaction({
                    'to_id': user_id,
                    'amount': START_BONUS,
                    'type': 'start_bonus'
                })
                
                self.top_cache = []
                
                logger.info(f"🎁 Стартовый бонус {START_BONUS} выдан {user_id}")
                return True, "✅ Стартовый бонус получен"
            except sqlite3.OperationalError as e:
                if "locked" in str(e) and attempt < 2:
                    time.sleep(0.1)
                    continue
                logger.error(f"Ошибка БД (give_start_bonus) для {user_id}: {e}")
                return False, "❌ Ошибка базы данных"
            finally:
                if conn:
                    conn.close()
        return False, "❌ Ошибка при выдаче стартового бонуса"

    def transfer(self, from_id: int, to_id: int, amount, chat_id: int = None) -> Tuple[bool, str]:
        conn = None
        for attempt in range(3):
            try:
                conn = self.get_connection()
                cursor = conn.cursor()
                
                amount_str = str(amount)
                
                # Проверяем отправителя
                cursor.execute('SELECT balance FROM users WHERE user_id = ?', (from_id,))
                from_result = cursor.fetchone()
                if not from_result:
                    return False, "❌ Отправитель не найден"
                
                from_balance = str(from_result['balance']).replace(' ', '')
                amount_clean = amount_str.replace(' ', '')
                
                # Проверяем достаточно ли средств
                try:
                    if Decimal(from_balance) < Decimal(amount_clean):
                        return False, "❌ Недостаточно средств"
                except Exception as e:
                    logger.error(f"Ошибка при проверке баланса: {e}")
                    return False, "❌ Ошибка при проверке баланса"
                
                # Получаем получателя
                cursor.execute('SELECT balance FROM users WHERE user_id = ?', (to_id,))
                to_result = cursor.fetchone()
                
                # Вычисляем новые балансы
                try:
                    from_dec = Decimal(from_balance)
                    amount_dec = Decimal(amount_clean)
                    new_from = str(from_dec - amount_dec)
                    
                    if to_result:
                        to_balance = str(to_result['balance']).replace(' ', '')
                        to_dec = Decimal(to_balance)
                    else:
                        to_dec = Decimal('0')
                    
                    new_to = str(to_dec + amount_dec)
                except Exception as e:
                    logger.error(f"Ошибка при вычислении баланса: {e}")
                    return False, f"❌ Ошибка при вычислении баланса"
                
                # Обновляем балансы
                cursor.execute('UPDATE users SET balance = ? WHERE user_id = ?', (new_from, from_id))
                
                if to_result:
                    cursor.execute('UPDATE users SET balance = ? WHERE user_id = ?', (new_to, to_id))
                else:
                    cursor.execute('INSERT INTO users (user_id, balance) VALUES (?, ?)', (to_id, new_to))
                
                conn.commit()
                
                # Добавляем транзакцию
                self.add_transaction({
                    'from_id': from_id,
                    'to_id': to_id,
                    'amount': amount_str,
                    'type': 'transfer',
                    'chat_id': chat_id
                })
                
                self.top_cache = []
                
                logger.info(f"💸 Перевод {amount_str} от {from_id} к {to_id}")
                return True, f"✅ Перевод выполнен"
            except sqlite3.OperationalError as e:
                if "locked" in str(e) and attempt < 2:
                    time.sleep(0.1)
                    continue
                logger.error(f"Ошибка БД (transfer): {e}")
                return False, "❌ Ошибка базы данных, попробуйте позже"
            finally:
                if conn:
                    conn.close()
        return False, "❌ Ошибка при переводе"

    # ====== ТРАНЗАКЦИИ ======
    
    def add_transaction(self, transaction: dict) -> bool:
        conn = None
        for attempt in range(3):
            try:
                conn = self.get_connection()
                cursor = conn.cursor()
                
                # Создаем уникальный хеш транзакции для предотвращения дубликатов
                tx_hash = f"{transaction.get('from_id', '')}_{transaction.get('to_id', '')}_{transaction.get('amount')}_{int(time.time())}"
                
                cursor.execute('''
                    INSERT INTO transactions (from_id, to_id, amount, type, chat_id, comment, transaction_hash)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                ''', (
                    transaction.get('from_id'),
                    transaction.get('to_id'),
                    transaction.get('amount'),
                    transaction.get('type'),
                    transaction.get('chat_id'),
                    transaction.get('comment'),
                    tx_hash
                ))
                conn.commit()
                return True
            except sqlite3.IntegrityError:
                # Дубликат транзакции - игнорируем
                logger.warning(f"Попытка добавить дубликат транзакции: {tx_hash}")
                return False
            except sqlite3.OperationalError as e:
                if "locked" in str(e) and attempt < 2:
                    time.sleep(0.1)
                    continue
                logger.error(f"Ошибка БД (add_transaction): {e}")
                return False
            finally:
                if conn:
                    conn.close()
        return False

    def get_history(self, user_id: int, limit: int = 20) -> List[dict]:
        conn = None
        for attempt in range(3):
            try:
                conn = self.get_connection()
                cursor = conn.cursor()
                cursor.execute('''
                    SELECT * FROM transactions 
                    WHERE from_id = ? OR to_id = ?
                    ORDER BY created_at DESC LIMIT ?
                ''', (user_id, user_id, limit))
                transactions = [dict(row) for row in cursor.fetchall()]
                return transactions
            except sqlite3.OperationalError as e:
                if "locked" in str(e) and attempt < 2:
                    time.sleep(0.1)
                    continue
                logger.error(f"Ошибка БД (get_history) для {user_id}: {e}")
                return []
            finally:
                if conn:
                    conn.close()
        return []

    def cleanup_old_transactions(self) -> int:
        """Очищает транзакции старше TRANSACTION_CLEANUP_DAYS дней"""
        conn = None
        try:
            conn = self.get_connection()
            cursor = conn.cursor()
            cleanup_date = (datetime.now() - timedelta(days=TRANSACTION_CLEANUP_DAYS)).isoformat()
            cursor.execute('DELETE FROM transactions WHERE created_at < ?', (cleanup_date,))
            deleted = cursor.rowcount
            conn.commit()
            if deleted > 0:
                logger.info(f"🧹 Очищено {deleted} старых транзакций")
            return deleted
        except Exception as e:
            logger.error(f"Ошибка при очистке транзакций: {e}")
            return 0
        finally:
            if conn:
                conn.close()

    # ====== ПРОМОКОДЫ ======
    
    def create_promocode(self, code: str, amount: str, max_uses: int = 1) -> Tuple[bool, str, dict]:
        code = code.upper().strip()
        
        # Валидация
        if not code:
            return False, "❌ Код не может быть пустым", {}
        
        try:
            amount_decimal = Decimal(amount.replace(' ', ''))
            if amount_decimal <= 0:
                return False, "❌ Сумма должна быть положительной", {}
        except:
            return False, "❌ Некорректная сумма", {}
        
        if max_uses < 0:
            return False, "❌ Количество использований не может быть отрицательным", {}
        
        conn = None
        for attempt in range(3):
            try:
                conn = self.get_connection()
                cursor = conn.cursor()
                
                # Проверяем, существует ли уже такой промокод
                cursor.execute('SELECT * FROM promocodes WHERE code = ?', (code,))
                if cursor.fetchone():
                    return False, "❌ Промокод с таким кодом уже существует", {}
                
                cursor.execute('''
                    INSERT INTO promocodes (code, amount, max_uses)
                    VALUES (?, ?, ?)
                ''', (code, amount, max_uses))
                conn.commit()
                
                cursor.execute('SELECT * FROM promocodes WHERE code = ?', (code,))
                promocode = dict(cursor.fetchone())
                logger.info(f"🎫 Создан промокод {code} на {amount} GRAM")
                return True, "✅ Промокод создан", promocode
            except sqlite3.OperationalError as e:
                if "locked" in str(e) and attempt < 2:
                    time.sleep(0.1)
                    continue
                logger.error(f"Ошибка БД (create_promocode): {e}")
                return False, "❌ Ошибка базы данных", {}
            finally:
                if conn:
                    conn.close()
        return False, "❌ Ошибка при создании промокода", {}

    def get_promocode(self, code: str) -> Optional[dict]:
        code = code.upper().strip()
        conn = None
        for attempt in range(3):
            try:
                conn = self.get_connection()
                cursor = conn.cursor()
                cursor.execute('SELECT * FROM promocodes WHERE code = ?', (code,))
                promocode = cursor.fetchone()
                return dict(promocode) if promocode else None
            except sqlite3.OperationalError as e:
                if "locked" in str(e) and attempt < 2:
                    time.sleep(0.1)
                    continue
                logger.error(f"Ошибка БД (get_promocode): {e}")
                return None
            finally:
                if conn:
                    conn.close()
        return None

    def use_promocode(self, user_id: int, code: str) -> Tuple[bool, str, str]:
        conn = None
        for attempt in range(3):
            try:
                code = code.upper().strip()
                conn = self.get_connection()
                cursor = conn.cursor()
                
                cursor.execute('SELECT * FROM promocodes WHERE code = ?', (code,))
                promocode = cursor.fetchone()
                
                if not promocode:
                    return False, "❌ Промокод не найден", "0"
                
                promocode = dict(promocode)
                
                if not promocode.get('active', 0):
                    return False, "❌ Промокод неактивен", "0"
                
                if promocode.get('max_uses', 0) > 0 and promocode.get('uses', 0) >= promocode['max_uses']:
                    return False, "❌ Промокод больше недействителен", "0"
                
                cursor.execute('SELECT used_promocodes FROM users WHERE user_id = ?', (user_id,))
                user = cursor.fetchone()
                
                if not user:
                    cursor.execute('''
                        INSERT INTO users (user_id, balance, used_promocodes)
                        VALUES (?, '0', ?)
                    ''', (user_id, json.dumps([])))
                    used = []
                else:
                    try:
                        used = json.loads(user['used_promocodes']) if user['used_promocodes'] else []
                    except:
                        used = []
                
                if code in used:
                    return False, "❌ Вы уже использовали этот промокод", "0"
                
                cursor.execute('SELECT balance FROM users WHERE user_id = ?', (user_id,))
                current = cursor.fetchone()
                current_balance = str(current['balance']) if current and current['balance'] else '0'
                
                try:
                    from decimal import Decimal
                    current_dec = Decimal(str(current_balance).replace(' ', ''))
                    promo_dec = Decimal(str(promocode['amount']).replace(' ', ''))
                    new_dec = current_dec + promo_dec
                    new_balance = str(new_dec)
                except Exception as e:
                    logger.error(f"Ошибка при начислении промокода: {e}")
                    return False, "❌ Ошибка при начислении", "0"
                
                cursor.execute('UPDATE users SET balance = ? WHERE user_id = ?', (new_balance, user_id))
                
                used.append(code)
                cursor.execute('UPDATE users SET used_promocodes = ? WHERE user_id = ?',
                              (json.dumps(used), user_id))
                
                cursor.execute('UPDATE promocodes SET uses = uses + 1 WHERE code = ?', (code,))
                
                if promocode['max_uses'] > 0 and promocode['uses'] + 1 >= promocode['max_uses']:
                    cursor.execute('UPDATE promocodes SET active = 0 WHERE code = ?', (code,))
                
                conn.commit()
                
                self.add_transaction({
                    'to_id': user_id,
                    'amount': promocode['amount'],
                    'type': 'promocode',
                    'comment': f"Промокод {code}"
                })
                
                self.top_cache = []
                
                logger.info(f"🎫 Пользователь {user_id} активировал промокод {code}")
                return True, f"✅ Промокод активирован! Получено {format_number(promocode['amount'])} GRAM", promocode['amount']
                
            except sqlite3.OperationalError as e:
                if "locked" in str(e) and attempt < 2:
                    time.sleep(0.1)
                    continue
                logger.error(f"Ошибка БД (use_promocode): {e}")
                return False, "❌ Ошибка базы данных", "0"
            except Exception as e:
                logger.error(f"Критическая ошибка в use_promocode: {e}")
                return False, "❌ Произошла ошибка при активации промокода", "0"
            finally:
                if conn:
                    conn.close()
        return False, "❌ Ошибка базы данных", "0"

    def get_all_promocodes(self) -> List[dict]:
        conn = None
        for attempt in range(3):
            try:
                conn = self.get_connection()
                cursor = conn.cursor()
                cursor.execute('SELECT * FROM promocodes ORDER BY created_at DESC')
                promocodes = [dict(row) for row in cursor.fetchall()]
                return promocodes
            except sqlite3.OperationalError as e:
                if "locked" in str(e) and attempt < 2:
                    time.sleep(0.1)
                    continue
                logger.error(f"Ошибка БД (get_all_promocodes): {e}")
                return []
            finally:
                if conn:
                    conn.close()
        return []

    # ====== МЕТОДЫ ДЛЯ ШКАТУЛОК ======
    def start_box_game(self, user_id: int, chat_id: int, bet) -> bool:
        conn = None
        try:
            bet_str = str(bet)
            gold_box = random.randint(1, 3)
            now = datetime.now().isoformat()
            conn = self.get_connection()
            cursor = conn.cursor()
            
            cursor.execute('DELETE FROM box_games WHERE user_id = ?', (user_id,))
            cursor.execute('''
                INSERT INTO box_games (user_id, chat_id, bet, gold_box, opened_boxes, status, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            ''', (user_id, chat_id, bet_str, gold_box, json.dumps([]), 'active', now))
            
            conn.commit()
            logger.info(f"📦 Игра в шкатулки начата: {user_id} (ставка {bet_str})")
            return True
        except Exception as e:
            logger.error(f"❌ Ошибка создания игры в шкатулки: {e}")
            return False
        finally:
            if conn:
                conn.close()

    def get_box_game(self, user_id: int) -> Optional[dict]:
        conn = None
        try:
            conn = self.get_connection()
            cursor = conn.cursor()
            cursor.execute('SELECT * FROM box_games WHERE user_id = ?', (user_id,))
            game = cursor.fetchone()
            
            if game:
                game_dict = dict(game)
                if game_dict['opened_boxes']:
                    try:
                        game_dict['opened_boxes'] = json.loads(game_dict['opened_boxes'])
                    except:
                        game_dict['opened_boxes'] = []
                else:
                    game_dict['opened_boxes'] = []
                return game_dict
            return None
        except Exception as e:
            logger.error(f"❌ Ошибка получения игры в шкатулки: {e}")
            return None
        finally:
            if conn:
                conn.close()

    def open_box(self, user_id: int, box_num: int) -> dict:
        conn = None
        try:
            game = self.get_box_game(user_id)
            if not game:
                return {'status': 'error', 'message': 'Игра не найдена'}
            
            if game.get('status') != 'active':
                return {'status': 'error', 'message': 'Игра уже завершена'}
            
            if box_num in game['opened_boxes']:
                return {'status': 'error', 'message': 'Шкатулка уже открыта'}
            
            game['opened_boxes'].append(box_num)
            
            conn = self.get_connection()
            cursor = conn.cursor()
            
            if game['gold_box'] == box_num:
                try:
                    bet_dec = Decimal(str(game['bet']).replace(' ', ''))
                    win_amount = str(int(bet_dec * Decimal('1.5')))
                except Exception as e:
                    logger.error(f"Ошибка расчета выигрыша: {e}")
                    win_amount = game['bet']
                
                success, msg, new_balance = self.update_balance(user_id, win_amount)
                if not success:
                    return {'status': 'error', 'message': msg}
                
                self.add_transaction({
                    'to_id': user_id,
                    'amount': win_amount,
                    'type': 'box_win',
                    'chat_id': game['chat_id']
                })
                
                cursor.execute('DELETE FROM box_games WHERE user_id = ?', (user_id,))
                conn.commit()
                
                return {
                    'status': 'win',
                    'amount': win_amount,
                    'bet': game['bet'],
                    'new_balance': new_balance
                }
            else:
                cursor.execute('''
                    UPDATE box_games SET opened_boxes = ? WHERE user_id = ?
                ''', (json.dumps(game['opened_boxes']), user_id))
                conn.commit()
                
                if len(game['opened_boxes']) >= 3:
                    cursor.execute('DELETE FROM box_games WHERE user_id = ?', (user_id,))
                    conn.commit()
                    return {
                        'status': 'lost',
                        'bet': game['bet']
                    }
                
                remaining = 3 - len(game['opened_boxes'])
                
                return {
                    'status': 'continue',
                    'opened': game['opened_boxes'],
                    'remaining': remaining
                }
        except Exception as e:
            logger.error(f"❌ Ошибка открытия шкатулки: {e}")
            return {'status': 'error', 'message': 'Ошибка игры'}
        finally:
            if conn:
                conn.close()

    def end_box_game(self, user_id: int):
        conn = None
        try:
            conn = self.get_connection()
            cursor = conn.cursor()
            cursor.execute('DELETE FROM box_games WHERE user_id = ?', (user_id,))
            conn.commit()
        except Exception as e:
            logger.error(f"Ошибка удаления игры в шкатулки: {e}")
        finally:
            if conn:
                conn.close()

    # ====== МЕТОДЫ ДЛЯ БОНУСНЫХ КЕЙСОВ ======
    
    def can_get_bonus_case(self, user_id: int) -> Tuple[bool, Optional[datetime]]:
        conn = None
        for attempt in range(3):
            try:
                conn = self.get_connection()
                cursor = conn.cursor()
                cursor.execute('SELECT last_bonus FROM bonus_cases WHERE user_id = ?', (user_id,))
                result = cursor.fetchone()
                
                if not result or not result['last_bonus']:
                    return True, None
                
                last_bonus = datetime.fromisoformat(result['last_bonus'])
                next_bonus = last_bonus + timedelta(minutes=30)
                now = datetime.now()
                
                return now >= next_bonus, next_bonus
            except sqlite3.OperationalError as e:
                if "locked" in str(e) and attempt < 2:
                    time.sleep(0.1)
                    continue
                logger.error(f"Ошибка БД (can_get_bonus_case) для {user_id}: {e}")
                return False, None
            finally:
                if conn:
                    conn.close()
        return False, None

    def get_random_skin(self, case_type: str, base_price: str) -> dict:
        rarities = [
            {"name": "💩 Шлак", "mult": 0.1, "chance": 40},
            {"name": "🔹 Бюджетный", "mult": 0.3, "chance": 30},
            {"name": "🔸 Норм", "mult": 0.6, "chance": 15},
            {"name": "🔶 Редкий", "mult": 1.0, "chance": 8},
            {"name": "🔷 Легендарный", "mult": 1.8, "chance": 4},
            {"name": "💎 Мифический", "mult": 3.0, "chance": 2},
            {"name": "👑 Немыслимый", "mult": 5.0, "chance": 1},
        ]
        
        skin_names = [
            "AK-47", "M4A4", "AWP", "Desert Eagle", "USP-S", "Glock-18",
            "SSG 08", "P250", "FAMAS", "Galil AR", "MP9", "MAC-10",
            "P90", "UMP-45", "Zeus x27", "Bayonet", "Karambit", "M9 Bayonet",
            "Butterfly Knife", "Falchion Knife", "Shadow Daggers", "Gut Knife",
            "Glove", "Sticker", "Graffiti", "Pin", "Agent", "Music Kit"
        ]
        
        skin_styles = [
            "Asiimov", "Dragon Lore", "Fire Serpent", "Redline", "Hyper Beast",
            "Neon Rider", "Printstream", "Fade", "Gamma Doppler", "Emerald",
            "Ruby", "Sapphire", "Black Pearl", "Marble Fade", "Tiger Tooth",
            "Doppler", "Phase 1", "Phase 2", "Phase 3", "Phase 4", "Slaughter",
            "Crimson Web", "Night", "Blue Gem", "Case Hardened"
        ]
        
        rand = random.randint(1, 100)
        cumulative = 0
        selected_rarity = rarities[0]
        for rarity in rarities:
            cumulative += rarity['chance']
            if rand <= cumulative:
                selected_rarity = rarity
                break
        
        try:
            base = int(Decimal(base_price))
            price = int(base * selected_rarity['mult'] * random.uniform(0.8, 1.2))
            price = max(1, price)
        except:
            price = 1
        
        name = f"{random.choice(skin_names)} | {random.choice(skin_styles)}"
        
        return {
            'name': name,
            'rarity': selected_rarity['name'],
            'price': str(price)
        }

    def open_bonus_case(self, user_id: int) -> dict:
        conn = None
        try:
            skin = self.get_random_skin('bonus', '10000')
            
            conn = self.get_connection()
            cursor = conn.cursor()
            
            # Объединяем запросы в один транзакции
            cursor.execute('''
                INSERT INTO skins (user_id, name, rarity, price, case_type)
                VALUES (?, ?, ?, ?, ?)
            ''', (user_id, skin['name'], skin['rarity'], skin['price'], 'bonus'))
            
            cursor.execute('''
                INSERT OR REPLACE INTO bonus_cases (user_id, last_bonus)
                VALUES (?, ?)
            ''', (user_id, datetime.now().isoformat()))
            
            conn.commit()
            
            return {
                'status': 'success',
                'skin': skin
            }
        except Exception as e:
            logger.error(f"❌ Ошибка открытия бонусного кейса: {e}")
            return {'status': 'error', 'message': 'Ошибка открытия кейса'}
        finally:
            if conn:
                conn.close()

    def open_paid_case(self, user_id: int, case_type: str, price: str) -> dict:
        # Валидация типа кейса
        valid_types = ['bomzh', 'voin', 'legend', 'mythic']
        if case_type not in valid_types:
            return {'status': 'error', 'message': 'Неверный тип кейса'}
        
        try:
            balance = self.get_balance(user_id)
            try:
                if Decimal(balance) < Decimal(price):
                    return {'status': 'error', 'message': 'Недостаточно средств'}
            except:
                return {'status': 'error', 'message': 'Ошибка при проверке баланса'}
            
            success, msg, new_balance = self.update_balance(user_id, '-' + price)
            if not success:
                return {'status': 'error', 'message': msg}
            
            multipliers = {
                'bomzh': {'min': 0.5, 'max': 2.0},
                'voin': {'min': 1.0, 'max': 3.0},
                'legend': {'min': 2.0, 'max': 5.0},
                'mythic': {'min': 3.0, 'max': 8.0}
            }
            
            mult = multipliers.get(case_type, {'min': 0.5, 'max': 1.5})
            multiplier = random.uniform(mult['min'], mult['max'])
            
            skin = self.get_random_skin(case_type, str(int(Decimal(price) * Decimal(multiplier))))
            
            conn = self.get_connection()
            cursor = conn.cursor()
            cursor.execute('''
                INSERT INTO skins (user_id, name, rarity, price, case_type)
                VALUES (?, ?, ?, ?, ?)
            ''', (user_id, skin['name'], skin['rarity'], skin['price'], case_type))
            conn.commit()
            conn.close()
            
            return {
                'status': 'success',
                'skin': skin,
                'price': price,
                'new_balance': new_balance
            }
        except Exception as e:
            logger.error(f"❌ Ошибка открытия платного кейса: {e}")
            return {'status': 'error', 'message': 'Ошибка открытия кейса'}

    def open_multiple_cases(self, user_id: int, case_type: str, count: int) -> dict:
        # Проверка лимита
        if count > MAX_CASES_PER_OPEN:
            return {'status': 'error', 'message': f'Максимум {MAX_CASES_PER_OPEN} кейсов за раз'}
        
        # Валидация типа кейса
        valid_types = ['bomzh', 'voin', 'legend', 'mythic']
        if case_type not in valid_types:
            return {'status': 'error', 'message': 'Неверный тип кейса'}
        
        try:
            prices = {
                'bomzh': '50000',
                'voin': '200000',
                'legend': '1000000',
                'mythic': '5000000'
            }
            
            price_per_case = prices[case_type]
            total_price = str(int(Decimal(price_per_case) * count))
            
            balance = self.get_balance(user_id)
            if Decimal(balance) < Decimal(total_price):
                return {'status': 'error', 'message': f'Недостаточно средств. Нужно {format_number(total_price)} GRAM'}
            
            success, msg, new_balance = self.update_balance(user_id, '-' + total_price)
            if not success:
                return {'status': 'error', 'message': msg}
            
            skins = []
            total_value = Decimal('0')
            
            for i in range(count):
                multipliers = {
                    'bomzh': {'min': 0.5, 'max': 2.0},
                    'voin': {'min': 1.0, 'max': 3.0},
                    'legend': {'min': 2.0, 'max': 5.0},
                    'mythic': {'min': 3.0, 'max': 8.0}
                }
                
                mult = multipliers.get(case_type, {'min': 0.5, 'max': 1.5})
                multiplier = random.uniform(mult['min'], mult['max'])
                
                skin = self.get_random_skin(case_type, str(int(Decimal(price_per_case) * Decimal(multiplier))))
                skins.append(skin)
                
                try:
                    total_value += Decimal(str(skin['price']).replace(' ', ''))
                except:
                    pass
                
                conn = self.get_connection()
                cursor = conn.cursor()
                cursor.execute('''
                    INSERT INTO skins (user_id, name, rarity, price, case_type)
                    VALUES (?, ?, ?, ?, ?)
                ''', (user_id, skin['name'], skin['rarity'], skin['price'], case_type))
                conn.commit()
                conn.close()
            
            return {
                'status': 'success',
                'skins': skins,
                'count': count,
                'total_price': total_price,
                'total_value': str(total_value),
                'new_balance': new_balance
            }
        except Exception as e:
            logger.error(f"❌ Ошибка открытия нескольких кейсов: {e}")
            return {'status': 'error', 'message': 'Ошибка открытия кейсов'}

    def sell_skin(self, user_id: int, skin_id: int) -> dict:
        conn = None
        try:
            conn = self.get_connection()
            cursor = conn.cursor()
            
            cursor.execute('SELECT * FROM skins WHERE id = ? AND user_id = ?', (skin_id, user_id))
            skin = cursor.fetchone()
            
            if not skin:
                return {'status': 'error', 'message': 'Скин не найден'}
            
            skin = dict(skin)
            
            success, msg, new_balance = self.update_balance(user_id, skin['price'])
            if not success:
                return {'status': 'error', 'message': msg}
            
            cursor.execute('DELETE FROM skins WHERE id = ?', (skin_id,))
            conn.commit()
            
            self.add_transaction({
                'to_id': user_id,
                'amount': skin['price'],
                'type': 'skin_sale',
                'comment': f"Продажа: {skin['name']}"
            })
            
            return {
                'status': 'success',
                'amount': skin['price'],
                'skin_name': skin['name'],
                'new_balance': new_balance
            }
        except Exception as e:
            logger.error(f"❌ Ошибка продажи скина: {e}")
            return {'status': 'error', 'message': 'Ошибка продажи'}
        finally:
            if conn:
                conn.close()

    def sell_all_skins(self, user_id: int) -> dict:
        conn = None
        try:
            conn = self.get_connection()
            cursor = conn.cursor()
            
            cursor.execute('SELECT * FROM skins WHERE user_id = ?', (user_id,))
            skins = cursor.fetchall()
            
            if not skins:
                return {'status': 'error', 'message': 'У вас нет скинов'}
            
            total_amount = Decimal('0')
            skins_count = len(skins)
            skins_list = []
            total_value_by_rarity = {}
            
            for skin in skins:
                skin = dict(skin)
                try:
                    price = Decimal(str(skin['price']).replace(' ', ''))
                    total_amount += price
                    skins_list.append(skin['name'])
                    
                    rarity = skin['rarity']
                    if rarity not in total_value_by_rarity:
                        total_value_by_rarity[rarity] = {'count': 0, 'total': Decimal('0')}
                    total_value_by_rarity[rarity]['count'] += 1
                    total_value_by_rarity[rarity]['total'] += price
                except:
                    pass
            
            success, msg, new_balance = self.update_balance(user_id, str(total_amount))
            if not success:
                return {'status': 'error', 'message': msg}
            
            cursor.execute('DELETE FROM skins WHERE user_id = ?', (user_id,))
            conn.commit()
            
            self.add_transaction({
                'to_id': user_id,
                'amount': str(total_amount),
                'type': 'skin_sale',
                'comment': f"Массовая продажа {skins_count} скинов"
            })
            
            logger.info(f"💰 Пользователь {user_id} продал все скины ({skins_count} шт.)")
            
            return {
                'status': 'success',
                'amount': str(total_amount),
                'count': skins_count,
                'skins': skins_list[:10],
                'rarity_stats': total_value_by_rarity,
                'new_balance': new_balance
            }
        except Exception as e:
            logger.error(f"❌ Ошибка массовой продажи скинов: {e}")
            return {'status': 'error', 'message': 'Ошибка при продаже скинов'}
        finally:
            if conn:
                conn.close()

    def get_user_skins(self, user_id: int) -> List[dict]:
        conn = None
        try:
            conn = self.get_connection()
            cursor = conn.cursor()
            cursor.execute('SELECT * FROM skins WHERE user_id = ? ORDER BY CAST(price AS INTEGER) DESC', (user_id,))
            skins = [dict(row) for row in cursor.fetchall()]
            return skins
        except Exception as e:
            logger.error(f"❌ Ошибка получения скинов: {e}")
            return []
        finally:
            if conn:
                conn.close()

    # ====== ПРОВЕРКА АКТИВНЫХ ИГР ======
    def has_active_game(self, user_id: int) -> Tuple[bool, str]:
        try:
            mines_game = self.get_mines_game(user_id)
            if mines_game and mines_game.get('status') == 'active':
                return True, "мины"
            
            secret_game = self.get_secret_room(user_id)
            if secret_game and secret_game.get('status') == 'active':
                return True, "тайную комнату"
            
            box_game = self.get_box_game(user_id)
            if box_game and box_game.get('status') == 'active':
                return True, "шкатулки"
            
            return False, ""
        except Exception as e:
            logger.error(f"Ошибка проверки активных игр для {user_id}: {e}")
            return False, ""
    
    # ====== ИГРЫ В МИНЫ ======
    
    def start_mines_game(self, user_id: int, chat_id: int, bet, mines_positions: List[int]) -> bool:
        conn = None
        try:
            bet_str = str(bet)
            now = datetime.now().isoformat()
            conn = self.get_connection()
            cursor = conn.cursor()
            cursor.execute('DELETE FROM mines_games WHERE user_id = ?', (user_id,))
            cursor.execute('''
                INSERT INTO mines_games (user_id, chat_id, bet, current_win, mines_positions, opened_cells, status, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ''', (user_id, chat_id, bet_str, bet_str, json.dumps(mines_positions), json.dumps([]), 'active', now))
            conn.commit()
            logger.info(f"💣 Игра в мины начата: {user_id} (ставка {bet_str})")
            return True
        except Exception as e:
            logger.error(f"❌ Ошибка создания игры: {e}")
            return False
        finally:
            if conn:
                conn.close()

    def get_mines_game(self, user_id: int) -> Optional[dict]:
        conn = None
        try:
            conn = self.get_connection()
            cursor = conn.cursor()
            cursor.execute('SELECT * FROM mines_games WHERE user_id = ?', (user_id,))
            game = cursor.fetchone()
            
            if game:
                game_dict = dict(game)
                game_dict['mines_positions'] = json.loads(game_dict['mines_positions'])
                game_dict['opened_cells'] = json.loads(game_dict['opened_cells'])
                return game_dict
            return None
        except Exception as e:
            logger.error(f"Ошибка получения игры в мины для {user_id}: {e}")
            return None
        finally:
            if conn:
                conn.close()

    def open_cell(self, user_id: int, cell: int) -> dict:
        conn = None
        try:
            game = self.get_mines_game(user_id)
            if not game:
                return {'status': 'error', 'message': 'Игра не найдена'}
            
            if game.get('status') != 'active':
                return {'status': 'error', 'message': 'Игра уже завершена'}
            
            if cell in game['opened_cells']:
                return {'status': 'error', 'message': 'Клетка уже открыта'}
            
            conn = self.get_connection()
            cursor = conn.cursor()
            
            if cell in game['mines_positions']:
                cursor.execute('UPDATE mines_games SET status = ? WHERE user_id = ?', ('lost', user_id))
                conn.commit()
                return {
                    'status': 'lost',
                    'mines_positions': game['mines_positions'],
                    'opened_cells': game['opened_cells'],
                    'bet': game['bet']
                }
            
            game['opened_cells'].append(cell)
            
            try:
                current_win = Decimal(str(game['current_win']).replace(' ', ''))
                multiplier = Decimal('1.2')
                new_win_dec = current_win * multiplier
                if new_win_dec == new_win_dec.to_integral():
                    new_win = str(int(new_win_dec))
                else:
                    new_win = str(new_win_dec)
            except Exception as e:
                logger.error(f"Ошибка расчета выигрыша: {e}")
                new_win = game['current_win']
            
            cursor.execute('''
                UPDATE mines_games 
                SET opened_cells = ?, current_win = ? 
                WHERE user_id = ?
            ''', (json.dumps(game['opened_cells']), new_win, user_id))
            conn.commit()
            
            safe_cells = [i for i in range(25) if i not in game['mines_positions']]
            if len(game['opened_cells']) == len(safe_cells):
                success, msg, new_balance = self.update_balance(user_id, new_win)
                if not success:
                    return {'status': 'error', 'message': msg}
                
                self.add_transaction({
                    'to_id': user_id,
                    'amount': new_win,
                    'type': 'mines_win'
                })
                cursor.execute('UPDATE mines_games SET status = ? WHERE user_id = ?', ('won', user_id))
                conn.commit()
                return {
                    'status': 'win',
                    'amount': new_win,
                    'mines_positions': game['mines_positions'],
                    'opened_cells': game['opened_cells'],
                    'new_balance': new_balance
                }
            
            return {
                'status': 'continue',
                'current_win': new_win,
                'opened_cells': game['opened_cells'],
                'bet': game['bet']
            }
        except Exception as e:
            logger.error(f"❌ Ошибка открытия клетки: {e}")
            return {'status': 'error', 'message': 'Ошибка игры'}
        finally:
            if conn:
                conn.close()

    def take_win(self, user_id: int) -> dict:
        conn = None
        try:
            game = self.get_mines_game(user_id)
            if not game:
                return {'status': 'error', 'message': 'Игра не найдена или уже завершена'}
            
            if game.get('status') != 'active':
                return {'status': 'error', 'message': 'Игра уже завершена'}
            
            win_amount = game['current_win']
            mines_positions = game['mines_positions']
            opened_cells = game['opened_cells']
            
            success, msg, new_balance = self.update_balance(user_id, win_amount)
            if not success:
                return {'status': 'error', 'message': msg}
            
            self.add_transaction({
                'to_id': user_id,
                'amount': win_amount,
                'type': 'mines_win'
            })
            
            conn = self.get_connection()
            cursor = conn.cursor()
            cursor.execute('UPDATE mines_games SET status = ? WHERE user_id = ?', ('taken', user_id))
            conn.commit()
            
            logger.info(f"💰 Игрок {user_id} забрал выигрыш {win_amount}")
            
            return {
                'status': 'taken',
                'amount': win_amount,
                'mines_positions': mines_positions,
                'opened_cells': opened_cells,
                'new_balance': new_balance
            }
        except Exception as e:
            logger.error(f"❌ Ошибка при заборе выигрыша: {e}")
            return {'status': 'error', 'message': 'Ошибка при заборе выигрыша'}
        finally:
            if conn:
                conn.close()

    def end_mines_game(self, user_id: int):
        conn = None
        try:
            conn = self.get_connection()
            cursor = conn.cursor()
            cursor.execute('DELETE FROM mines_games WHERE user_id = ?', (user_id,))
            conn.commit()
        except Exception as e:
            logger.error(f"Ошибка удаления игры в мины: {e}")
        finally:
            if conn:
                conn.close()

    # ====== МЕТОДЫ ДЛЯ ТАЙНОЙ КОМНАТЫ ======
    
    def start_secret_room(self, user_id: int, chat_id: int, bet: str) -> bool:
        conn = None
        try:
            doors = [1, 2, 3, 4, 5, 6]
            treasure_doors = random.sample(doors, 3)
            remaining_doors = [d for d in doors if d not in treasure_doors]
            monster_doors = random.sample(remaining_doors, 2)
            empty_doors = [d for d in doors if d not in treasure_doors and d not in monster_doors]
            now = datetime.now().isoformat()

            conn = self.get_connection()
            cursor = conn.cursor()
            cursor.execute('''
                INSERT OR REPLACE INTO secret_room_games 
                (user_id, chat_id, bet, current_win, treasure_doors, monster_doors, empty_doors, status, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (user_id, chat_id, bet, bet, 
                  json.dumps(treasure_doors), json.dumps(monster_doors), json.dumps(empty_doors), 'active', now))
            conn.commit()
            
            logger.info(f"🔮 Игра в тайную комнату начата: {user_id} (ставка {bet})")
            return True
        except Exception as e:
            logger.error(f"❌ Ошибка создания игры: {e}")
            return False
        finally:
            if conn:
                conn.close()

    def get_secret_room(self, user_id: int) -> Optional[dict]:
        conn = None
        try:
            conn = self.get_connection()
            cursor = conn.cursor()
            cursor.execute('SELECT * FROM secret_room_games WHERE user_id = ?', (user_id,))
            game = cursor.fetchone()
            
            if game:
                game = dict(game)
                game['treasure_doors'] = json.loads(game['treasure_doors'])
                game['monster_doors'] = json.loads(game['monster_doors'])
                game['empty_doors'] = json.loads(game['empty_doors'])
                game['opened_doors'] = json.loads(game['opened_doors'])
                return game
            return None
        except Exception as e:
            logger.error(f"❌ Ошибка получения игры: {e}")
            return None
        finally:
            if conn:
                conn.close()

    def open_secret_door(self, user_id: int, door: int) -> dict:
        conn = None
        try:
            game = self.get_secret_room(user_id)
            if not game:
                return {'status': 'error', 'message': 'Игра не найдена'}
            
            if game.get('status') != 'active':
                return {'status': 'error', 'message': 'Игра уже завершена'}
            
            if door in game['opened_doors']:
                return {'status': 'error', 'message': 'Эта дверь уже открыта'}
            
            game['opened_doors'].append(door)
            game['doors_count'] += 1
            
            conn = self.get_connection()
            cursor = conn.cursor()
            
            if door in game['monster_doors']:
                cursor.execute('UPDATE secret_room_games SET status = ? WHERE user_id = ?', ('lost', user_id))
                conn.commit()
                return {
                    'status': 'lost',
                    'treasure_doors': game['treasure_doors'],
                    'monster_doors': game['monster_doors'],
                    'empty_doors': game['empty_doors'],
                    'opened_doors': game['opened_doors'],
                    'bet': game['bet'],
                    'doors_count': game['doors_count']
                }
            
            elif door in game['treasure_doors']:
                game['treasures_found'] += 1
                game['multiplier'] *= 1.5
                try:
                    bet_dec = Decimal(game['bet'].replace(' ', ''))
                    game['current_win'] = str(int(bet_dec * Decimal(game['multiplier'])))
                except:
                    pass
                
                cursor.execute('''
                    UPDATE secret_room_games 
                    SET opened_doors = ?, doors_count = ?, treasures_found = ?, 
                        multiplier = ?, current_win = ?
                    WHERE user_id = ?
                ''', (json.dumps(game['opened_doors']), game['doors_count'], 
                      game['treasures_found'], game['multiplier'], game['current_win'], user_id))
                conn.commit()
                
                if game['treasures_found'] == 3:
                    success, msg, new_balance = self.update_balance(user_id, game['current_win'])
                    if not success:
                        return {'status': 'error', 'message': msg}
                    
                    self.add_transaction({
                        'to_id': user_id,
                        'amount': game['current_win'],
                        'type': 'secret_room_win'
                    })
                    cursor.execute('UPDATE secret_room_games SET status = ? WHERE user_id = ?', ('won', user_id))
                    conn.commit()
                    return {
                        'status': 'win',
                        'amount': game['current_win'],
                        'treasure_doors': game['treasure_doors'],
                        'monster_doors': game['monster_doors'],
                        'empty_doors': game['empty_doors'],
                        'opened_doors': game['opened_doors'],
                        'doors_count': game['doors_count'],
                        'new_balance': new_balance
                    }
                
                return {
                    'status': 'treasure',
                    'current_win': game['current_win'],
                    'opened_doors': game['opened_doors'],
                    'treasures_found': game['treasures_found'],
                    'doors_count': game['doors_count'],
                    'remaining': 3 - game['treasures_found']
                }
            
            else:
                cursor.execute('''
                    UPDATE secret_room_games 
                    SET opened_doors = ?, doors_count = ?
                    WHERE user_id = ?
                ''', (json.dumps(game['opened_doors']), game['doors_count'], user_id))
                conn.commit()
                
                return {
                    'status': 'empty',
                    'current_win': game['current_win'],
                    'opened_doors': game['opened_doors'],
                    'treasures_found': game['treasures_found'],
                    'doors_count': game['doors_count'],
                    'remaining': 3 - game['treasures_found']
                }
        except Exception as e:
            logger.error(f"❌ Ошибка открытия двери: {e}")
            return {'status': 'error', 'message': 'Ошибка игры'}
        finally:
            if conn:
                conn.close()

    def cancel_secret_room(self, user_id: int) -> dict:
        conn = None
        try:
            game = self.get_secret_room(user_id)
            if not game:
                return {'status': 'error', 'message': 'Игра не найдена'}
            
            if game.get('status') != 'active':
                return {'status': 'error', 'message': 'Игра уже завершена'}
            
            success, msg, new_balance = self.update_balance(user_id, game['bet'])
            if not success:
                return {'status': 'error', 'message': msg}
            
            self.add_transaction({
                'to_id': user_id,
                'amount': game['bet'],
                'type': 'secret_room_cancel'
            })
            
            conn = self.get_connection()
            cursor = conn.cursor()
            cursor.execute('DELETE FROM secret_room_games WHERE user_id = ?', (user_id,))
            conn.commit()
            
            return {
                'status': 'cancelled',
                'amount': game['bet'],
                'new_balance': new_balance
            }
        except Exception as e:
            logger.error(f"❌ Ошибка отмены игры: {e}")
            return {'status': 'error', 'message': 'Ошибка отмены игры'}
        finally:
            if conn:
                conn.close()

    # ====== МЕТОД ДЛЯ АВТОЗАВЕРШЕНИЯ СТАРЫХ ИГР ======
    def cleanup_old_games(self):
        """Удаляет игры старше 5 минут и возвращает ставки"""
        conn = None
        try:
            conn = self.get_connection()
            cursor = conn.cursor()
            
            timeout_ago = (datetime.now() - timedelta(minutes=GAME_TIMEOUT_MINUTES)).isoformat()
            deleted_count = 0
            
            # Мины
            cursor.execute('SELECT * FROM mines_games WHERE created_at < ? AND status = ?', (timeout_ago, 'active'))
            old_mines = cursor.fetchall()
            for game in old_mines:
                game = dict(game)
                self.update_balance(game['user_id'], game['bet'])
                self.add_transaction({
                    'to_id': game['user_id'],
                    'amount': game['bet'],
                    'type': 'game_timeout',
                    'comment': 'Автовозврат (игра устарела)'
                })
                logger.info(f"⏰ Игра в мины пользователя {game['user_id']} удалена по таймауту")
                deleted_count += 1
            
            cursor.execute('DELETE FROM mines_games WHERE created_at < ? AND status = ?', (timeout_ago, 'active'))
            
            # Шкатулки
            cursor.execute('SELECT * FROM box_games WHERE created_at < ? AND status = ?', (timeout_ago, 'active'))
            old_boxes = cursor.fetchall()
            for game in old_boxes:
                game = dict(game)
                self.update_balance(game['user_id'], game['bet'])
                self.add_transaction({
                    'to_id': game['user_id'],
                    'amount': game['bet'],
                    'type': 'game_timeout',
                    'comment': 'Автовозврат (игра устарела)'
                })
                logger.info(f"⏰ Игра в шкатулки пользователя {game['user_id']} удалена по таймауту")
                deleted_count += 1
            
            cursor.execute('DELETE FROM box_games WHERE created_at < ? AND status = ?', (timeout_ago, 'active'))
            
            # Тайная комната
            cursor.execute('SELECT * FROM secret_room_games WHERE created_at < ? AND status = ?', (timeout_ago, 'active'))
            old_secret = cursor.fetchall()
            for game in old_secret:
                game = dict(game)
                self.update_balance(game['user_id'], game['bet'])
                self.add_transaction({
                    'to_id': game['user_id'],
                    'amount': game['bet'],
                    'type': 'game_timeout',
                    'comment': 'Автовозврат (игра устарела)'
                })
                logger.info(f"⏰ Игра в тайную комнату пользователя {game['user_id']} удалена по таймауту")
                deleted_count += 1
            
            cursor.execute('DELETE FROM secret_room_games WHERE created_at < ? AND status = ?', (timeout_ago, 'active'))
            
            conn.commit()
            
            return deleted_count
        except Exception as e:
            logger.error(f"Ошибка при очистке старых игр: {e}")
            return 0
        finally:
            if conn:
                conn.close()

# СОЗДАЕМ ЭКЗЕМПЛЯР БД
db = SQLiteDatabase()

# Назначаем админов
for admin_id in ADMIN_IDS:
    user = db.get_user(admin_id)
    if user:
        if not user.get('is_admin'):
            conn = db.get_connection()
            cursor = conn.cursor()
            cursor.execute('UPDATE users SET is_admin = 1 WHERE user_id = ?', (admin_id,))
            conn.commit()
            conn.close()
            logger.info(f"👑 Админ {admin_id} активирован")
    else:
        conn = db.get_connection()
        cursor = conn.cursor()
        cursor.execute('INSERT INTO users (user_id, is_admin) VALUES (?, 1)', (admin_id,))
        conn.commit()
        conn.close()
        logger.info(f"👑 Создан администратор {admin_id}")

# ====== КЛАВИАТУРЫ ======
def get_main_keyboard(user_id: int = None, chat_type: str = "private"):
    if chat_type != "private":
        return None
    
    keyboard = [
        [KeyboardButton(text="💰 Баланс"), KeyboardButton(text="🎁 Бонус")],
        [KeyboardButton(text="👤 Профиль"), KeyboardButton(text="📊 Топ")],
        [KeyboardButton(text="📜 История"), KeyboardButton(text="⭐️ Донат")],
        [KeyboardButton(text="🎫 Промокод"), KeyboardButton(text="📋 Команды")],
        [KeyboardButton(text="🎁 Кейсы"), KeyboardButton(text="📋 Инвентарь")]
    ]

    if user_id and db.is_admin(user_id):
        keyboard.append([KeyboardButton(text="👑 Админ панель")])

    return ReplyKeyboardMarkup(keyboard=keyboard, resize_keyboard=True)

def get_admin_keyboard():
    keyboard = [
        [KeyboardButton(text="📊 Статистика"), KeyboardButton(text="💰 Выдать баланс")],
        [KeyboardButton(text="🔄 Обнулить баланс"), KeyboardButton(text="🎫 Создать промокод")],
        [KeyboardButton(text="📋 Список промокодов"), KeyboardButton(text="📢 Рассылка")],
        [KeyboardButton(text="🔙 Выход")]
    ]
    return ReplyKeyboardMarkup(keyboard=keyboard, resize_keyboard=True)

def get_mines_game_keyboard(opened_cells: List[int] = None, game_over: bool = False, show_mines: List[int] = None):
    if opened_cells is None:
        opened_cells = []

    builder = InlineKeyboardBuilder()

    for i in range(25):
        if show_mines and i in show_mines:
            builder.button(text="💣", callback_data="ignore")
        elif i in opened_cells:
            builder.button(text="‎", callback_data="ignore")
        elif game_over:
            builder.button(text="‎", callback_data="ignore")
        else:
            builder.button(text="❓", callback_data=f"cell_{i}")

    builder.adjust(5)

    if not game_over:
        if len(opened_cells) == 0:
            builder.row(InlineKeyboardButton(text="❌ Отмена", callback_data="cancel_game"))
        else:
            builder.row(InlineKeyboardButton(text="💰 Забрать", callback_data="take_win"))

    return builder.as_markup()

def get_secret_room_keyboard(opened_doors: List[int] = None, game_over: bool = False, show_treasures: List[int] = None, show_monsters: List[int] = None):
    if opened_doors is None:
        opened_doors = []

    builder = InlineKeyboardBuilder()

    for i in range(1, 7):
        if game_over:
            if show_treasures and i in show_treasures:
                builder.button(text="💰", callback_data="ignore")
            elif show_monsters and i in show_monsters:
                builder.button(text="👹", callback_data="ignore")
            elif i in opened_doors:
                builder.button(text="📦", callback_data="ignore")
            else:
                builder.button(text="⬜", callback_data="ignore")
        elif i in opened_doors:
            builder.button(text="📦", callback_data="ignore")
        else:
            builder.button(text=f"🚪{i}", callback_data=f"secret_{i}")

    builder.adjust(3)

    if not game_over and len(opened_doors) == 0:
        builder.row(InlineKeyboardButton(text="❌ Отмена", callback_data="cancel_secret"))

    return builder.as_markup()

def get_box_keyboard(opened_boxes: List[int] = None, game_over: bool = False):
    if opened_boxes is None:
        opened_boxes = []

    builder = InlineKeyboardBuilder()

    for i in range(1, 4):
        if i in opened_boxes:
            builder.button(text="📦", callback_data="ignore")
        else:
            builder.button(text=f"🎁 Шкатулка {i}", callback_data=f"box_{i}")

    builder.adjust(3)

    if not game_over and len(opened_boxes) == 0:
        builder.row(InlineKeyboardButton(text="❌ Отмена", callback_data="cancel_box"))

    return builder.as_markup()

def get_cases_keyboard():
    builder = InlineKeyboardBuilder()
    builder.button(text="🎁 Бонус кейс (бесплатно)", callback_data="case_bonus")
    builder.button(text="📦 Кейс бомжа (50 000 GRAM)", callback_data="case_bomzh")
    builder.button(text="📦 Кейс воина (200 000 GRAM)", callback_data="case_voin")
    builder.button(text="📦 Кейс легенды (1 000 000 GRAM)", callback_data="case_legend")
    builder.button(text="📦 Кейс мифический (5 000 000 GRAM)", callback_data="case_mythic")
    builder.adjust(1)
    return builder.as_markup()

def get_donate_keyboard():
    builder = InlineKeyboardBuilder()
    builder.button(text="📞 Связаться с поддержкой", url=f"https://t.me/{SUPPORT_USERNAME}")
    return builder.as_markup()

def get_bonus_keyboard(show_button: bool = True):
    if show_button:
        builder = InlineKeyboardBuilder()
        builder.button(text="🎁 Забрать бонус", callback_data="get_bonus")
        return builder.as_markup()
    return None

# ====== ЗАДАЧА ДЛЯ АВТОЗАВЕРШЕНИЯ СТАРЫХ ИГР ======
def cleanup_job():
    """Задача для очистки старых игр"""
    try:
        cleaned = db.cleanup_old_games()
        if cleaned > 0:
            logger.info(f"🧹 Автозавершение: удалено {cleaned} старых игр")
    except Exception as e:
        logger.error(f"Ошибка в задаче автозавершения: {e}")

# ====== ЗАДАЧА ДЛЯ ОЧИСТКИ СТАРЫХ ТРАНЗАКЦИЙ ======
def cleanup_transactions_job():
    """Задача для очистки старых транзакций"""
    try:
        cleaned = db.cleanup_old_transactions()
        if cleaned > 0:
            logger.info(f"🧹 Очистка транзакций: удалено {cleaned}")
    except Exception as e:
        logger.error(f"Ошибка при очистке транзакций: {e}")

# ====== ФОНОВАЯ ЗАДАЧА ДЛЯ ОЧИСТКИ БЛОКИРОВОК ======
async def cleanup_locks_task():
    """Очищает старые блокировки раз в 5 минут"""
    while True:
        await asyncio.sleep(300)  # 5 минут
        current_time = time.time()
        expired_locks = [uid for uid, last_time in lock_last_used.items() 
                        if current_time - last_time > LOCK_TIMEOUT]
        for uid in expired_locks:
            if uid in user_locks:
                del user_locks[uid]
            if uid in lock_last_used:
                del lock_last_used[uid]
        if expired_locks:
            logger.info(f"🧹 Очищено {len(expired_locks)} неактивных блокировок")

# ====== ОБРАБОТЧИКИ ======
@dp.message(CommandStart())
async def cmd_start(message: Message):
    user = message.from_user
    user_data = db.get_or_create_user(user.id, user.username, user.first_name, user.last_name)

    if message.chat.type == "private" and user_data.get('balance') == '0':
        success, msg = db.give_start_bonus(user.id)
        if success:
            balance = START_BONUS
            bonus_text = f"\n🎁 Стартовый бонус: {format_number(START_BONUS)} GRAM"
        else:
            balance = user_data.get('balance', '0')
            bonus_text = ""
    else:
        balance = user_data.get('balance', '0')
        bonus_text = ""

    bot_username = (await bot.me()).username
    invite_link = f"https://t.me/{bot_username}?startgroup=new"

    text = (
        "👋 Добро пожаловать!\n\n"
        "GRAM — игровой бот для вашего чата\n\n"
        f"📞 Связь: @{SUPPORT_USERNAME}\n\n"
        f"💰 Ваш баланс: {format_number(balance)} GRAM"
        f"{bonus_text}\n\n"
        f"[➕ Добавить бота в чат]({invite_link})"
    )

    keyboard = get_main_keyboard(user.id, message.chat.type)
    await message.answer(text, parse_mode="Markdown", reply_markup=keyboard)

@dp.message(Command("commands"))
@dp.message(F.text == "📋 Команды")
async def show_commands(message: Message):
    if message.chat.type == "private":
        keyboard = get_main_keyboard(message.from_user.id, "private")
    else:
        keyboard = None
    
    text = (
        "📋 КОМАНДЫ:\n\n"
        "💰 Баланс:\n"
        "• `б` или `баланс` - проверить баланс\n\n"
        "💸 Перевод:\n"
        "• `п @username сумма` - перевести по юзернейму\n"
        "• `п сумма` (ответом) - перевести ответом\n\n"
        "🎮 ИГРЫ В ЧАТЕ:\n"
        "• `мины [сумма]` или `мины фулл` - 💣 Мины (4 мины, +20% за ход)\n"
        "• `тк [сумма]` или `тк фулл` - 🚪 Тайная комната (6 дверей, 3 сокровища)\n"
        "• `шкатулки [сумма]` или `шкатулки фулл` - 📦 3 шкатулки, 1 с золотом (x1.5)\n\n"
        "🎁 КЕЙСЫ (в личке):\n"
        "• `кейсы` - открыть меню кейсов\n"
        "• Бонус кейс - бесплатно раз в 30 минут\n"
        "• Платные кейсы с разными шансами\n"
        "• `📋 Инвентарь` - посмотреть свои скины\n\n"
        "🎫 Промокоды:\n"
        "• Нажмите кнопку `🎫 Промокод` в меню"
    )

    await message.answer(text, parse_mode="Markdown", reply_markup=keyboard)

# ====== КЕЙСЫ ======
@dp.message(F.text == "🎁 Кейсы")
@dp.message(Command("кейсы"))
async def cmd_cases(message: Message):
    if message.chat.type != "private":
        await message.answer("❌ Кейсы доступны только в личных сообщениях с ботом")
        return
    
    can_get, next_bonus = db.can_get_bonus_case(message.from_user.id)
    
    text = "🎁 Меню Кейсов CS2\n\n"
    text += "💰 Бонус кейс (бесплатно)\n"
    if can_get:
        text += "✅ Доступен сейчас!\n"
    else:
        if next_bonus:
            time_left = next_bonus - datetime.now()
            minutes = int(time_left.total_seconds() // 60)
            seconds = int(time_left.total_seconds() % 60)
            text += f"⏳ Будет доступен через {minutes}м {seconds}с\n"
    text += "\n"
    text += "📦 Кейс бомжа - 50 000 GRAM\n"
    text += "📦 Кейс воина - 200 000 GRAM\n"
    text += "📦 Кейс легенды - 1 000 000 GRAM\n"
    text += "📦 Кейс мифический - 5 000 000 GRAM\n"
    
    keyboard = get_cases_keyboard()
    keyboard.inline_keyboard.append([
        InlineKeyboardButton(text="🎁🎁 ОТКРЫТЬ 10 КЕЙСОВ", callback_data="mass_cases_menu")
    ])
    
    await message.answer(text, parse_mode="Markdown", reply_markup=keyboard)

@dp.callback_query(F.data == "mass_cases_menu")
async def mass_cases_menu(callback: CallbackQuery):
    builder = InlineKeyboardBuilder()
    builder.button(text="📦 Кейс бомжа (10 шт)", callback_data="mass_case_bomzh_10")
    builder.button(text="📦 Кейс воина (10 шт)", callback_data="mass_case_voin_10")
    builder.button(text="📦 Кейс легенды (10 шт)", callback_data="mass_case_legend_10")
    builder.button(text="📦 Кейс мифический (10 шт)", callback_data="mass_case_mythic_10")
    builder.button(text="◀️ Назад", callback_data="back_to_cases")
    builder.adjust(1)
    
    await callback.message.edit_text(
        "🎁 Выберите кейс для массового открытия (10 шт):",
        reply_markup=builder.as_markup()
    )
    await callback.answer()

@dp.callback_query(F.data == "back_to_cases")
async def back_to_cases(callback: CallbackQuery):
    can_get, next_bonus = db.can_get_bonus_case(callback.from_user.id)
    
    text = "🎁 Меню Кейсов CS2\n\n"
    text += "💰 Бонус кейс (бесплатно)\n"
    if can_get:
        text += "✅ Доступен сейчас!\n"
    else:
        if next_bonus:
            time_left = next_bonus - datetime.now()
            minutes = int(time_left.total_seconds() // 60)
            seconds = int(time_left.total_seconds() % 60)
            text += f"⏳ Будет доступен через {minutes}м {seconds}с\n"
    text += "\n"
    text += "📦 Кейс бомжа - 50 000 GRAM\n"
    text += "📦 Кейс воина - 200 000 GRAM\n"
    text += "📦 Кейс легенды - 1 000 000 GRAM\n"
    text += "📦 Кейс мифический - 5 000 000 GRAM\n"
    
    keyboard = get_cases_keyboard()
    keyboard.inline_keyboard.append([
        InlineKeyboardButton(text="🎁🎁 ОТКРЫТЬ 10 КЕЙСОВ", callback_data="mass_cases_menu")
    ])
    
    await callback.message.edit_text(text, parse_mode="Markdown", reply_markup=keyboard)
    await callback.answer()

@dp.callback_query(F.data.startswith("mass_case_"))
async def process_mass_case(callback: CallbackQuery):
    data = callback.data.replace("mass_case_", "")
    case_type, count = data.split("_")
    count = int(count)
    
    user_id = callback.from_user.id
    
    prices = {
        'bomzh': '50000',
        'voin': '200000',
        'legend': '1000000',
        'mythic': '5000000'
    }
    
    names = {
        'bomzh': 'Кейс бомжа',
        'voin': 'Кейс воина',
        'legend': 'Кейс легенды',
        'mythic': 'Кейс мифический'
    }
    
    if case_type not in prices:
        await callback.answer("❌ Неверный тип кейса", show_alert=True)
        return
    
    if count > MAX_CASES_PER_OPEN:
        await callback.answer(f"❌ Максимум {MAX_CASES_PER_OPEN} кейсов за раз", show_alert=True)
        return
    
    price_per_case = prices[case_type]
    total_price = str(int(Decimal(price_per_case) * count))
    
    balance = db.get_balance(user_id)
    if Decimal(balance) < Decimal(total_price):
        await callback.answer(f"❌ Недостаточно средств. Нужно {format_number(total_price)} GRAM", show_alert=True)
        return
    
    builder = InlineKeyboardBuilder()
    builder.button(text="✅ Да, открыть", callback_data=f"confirm_mass_{case_type}_{count}")
    builder.button(text="❌ Отмена", callback_data="back_to_cases")
    
    await callback.message.edit_text(
        f"❓ Открыть {count} {names[case_type]} за {format_number(total_price)} GRAM?",
        reply_markup=builder.as_markup()
    )
    await callback.answer()

@dp.callback_query(F.data.startswith("confirm_mass_"))
async def confirm_mass_case(callback: CallbackQuery):
    data = callback.data.replace("confirm_mass_", "")
    case_type, count = data.split("_")
    count = int(count)
    
    user_id = callback.from_user.id
    
    names = {
        'bomzh': 'Кейс бомжа',
        'voin': 'Кейс воина',
        'legend': 'Кейс легенды',
        'mythic': 'Кейс мифический'
    }
    
    await callback.message.delete()
    msg = await callback.message.answer(f"🎁 Открываем {count} кейсов... Это может занять несколько секунд")
    
    result = db.open_multiple_cases(user_id, case_type, count)
    
    if result['status'] == 'success':
        text = f"🎉 Вы открыли {count} {names[case_type]}!\n\n"
        text += f"💰 Потрачено: {format_number(result['total_price'])} GRAM\n"
        text += f"💎 Общая стоимость скинов: {format_number(result['total_value'])} GRAM\n"
        
        try:
            profit = Decimal(result['total_value']) - Decimal(result['total_price'])
            profit_str = format_number(str(profit))
            if profit > 0:
                text += f"📊 Прибыль: +{profit_str} GRAM\n"
            else:
                text += f"📊 Убыток: {profit_str} GRAM\n"
        except:
            pass
        
        text += f"💵 Новый баланс: {format_number(result['new_balance'])} GRAM\n\n"
        text += "📋 Лучшие скины:\n"
        
        sorted_skins = sorted(result['skins'], key=lambda x: Decimal(str(x['price']).replace(' ', '')), reverse=True)
        for i, skin in enumerate(sorted_skins[:5], 1):
            text += f"{i}. {skin['name']} - {skin['rarity']} - {format_number(skin['price'])} GRAM\n"
        
        if count > 5:
            text += f"... и ещё {count - 5} скинов\n"
        
        await msg.edit_text(text, parse_mode="Markdown")
    else:
        await msg.edit_text(f"❌ {result.get('message', 'Ошибка открытия кейсов')}")
    
    await callback.answer("✅ Кейсы открыты!", show_alert=False)

@dp.callback_query(F.data.startswith("case_"))
async def process_case(callback: CallbackQuery):
    case_type = callback.data.replace("case_", "")
    user_id = callback.from_user.id
    
    if case_type == "bonus":
        can_get, next_bonus = db.can_get_bonus_case(user_id)
        if not can_get:
            if next_bonus:
                time_left = next_bonus - datetime.now()
                minutes = int(time_left.total_seconds() // 60)
                seconds = int(time_left.total_seconds() % 60)
                await callback.answer(f"⏳ Следующий бонус через {minutes}м {seconds}с", show_alert=True)
            else:
                await callback.answer("❌ Бонусный кейс ещё не доступен", show_alert=True)
            return
        
        msg = await animate_case(callback.message, "Бонусный кейс")
        result = db.open_bonus_case(user_id)
        
        if result['status'] == 'success':
            skin = result['skin']
            text = (
                f"🎉 Вам выпало:\n\n"
                f"🔫 {skin['name']}\n"
                f"📊 Редкость: {skin['rarity']}\n"
                f"💰 Цена: {format_number(skin['price'])} GRAM\n\n"
                f"Скин сохранён в инвентаре!"
            )
            await msg.edit_text(text, parse_mode="Markdown")
        else:
            await msg.edit_text(f"❌ {result.get('message', 'Ошибка открытия кейса')}")
    
    elif case_type in ['bomzh', 'voin', 'legend', 'mythic']:
        prices = {
            'bomzh': '50000',
            'voin': '200000',
            'legend': '1000000',
            'mythic': '5000000'
        }
        price = prices[case_type]
        names = {
            'bomzh': 'Кейс бомжа',
            'voin': 'Кейс воина',
            'legend': 'Кейс легенды',
            'mythic': 'Кейс мифический'
        }
        
        balance = db.get_balance(user_id)
        try:
            if Decimal(balance) < Decimal(price):
                await callback.answer(f"❌ Недостаточно средств. Нужно {format_number(price)} GRAM", show_alert=True)
                return
        except:
            await callback.answer("❌ Ошибка при проверке баланса", show_alert=True)
            return
        
        builder = InlineKeyboardBuilder()
        builder.button(text="✅ Да, открыть", callback_data=f"confirm_case_{case_type}")
        builder.button(text="❌ Отмена", callback_data="cancel_case")
        
        await callback.message.answer(
            f"❓ Открыть {names[case_type]} за {format_number(price)} GRAM?",
            reply_markup=builder.as_markup()
        )
    
    await callback.answer()

@dp.callback_query(F.data.startswith("confirm_case_"))
async def confirm_case(callback: CallbackQuery):
    case_type = callback.data.replace("confirm_case_", "")
    user_id = callback.from_user.id
    
    prices = {
        'bomzh': '50000',
        'voin': '200000',
        'legend': '1000000',
        'mythic': '5000000'
    }
    price = prices[case_type]
    names = {
        'bomzh': 'Кейс бомжа',
        'voin': 'Кейс воина',
        'legend': 'Кейс легенды',
        'mythic': 'Кейс мифический'
    }
    
    await callback.message.delete()
    msg = await animate_case(callback.message, names[case_type])
    result = db.open_paid_case(user_id, case_type, price)
    
    if result['status'] == 'success':
        skin = result['skin']
        new_balance = result.get('new_balance', db.get_balance(user_id))
        text = (
            f"🎉 Вам выпало:\n\n"
            f"🔫 {skin['name']}\n"
            f"📊 Редкость: {skin['rarity']}\n"
            f"💰 Цена: {format_number(skin['price'])} GRAM\n\n"
            f"Скин сохранён в инвентаре!\n"
            f"💰 Новый баланс: {format_number(new_balance)} GRAM"
        )
        await msg.edit_text(text, parse_mode="Markdown")
    else:
        await msg.edit_text(f"❌ {result.get('message', 'Ошибка открытия кейса')}")
    
    await callback.answer()

@dp.callback_query(F.data == "cancel_case")
async def cancel_case(callback: CallbackQuery):
    await callback.message.edit_text("❌ Отменено")
    await callback.answer()

# ====== ИНВЕНТАРЬ ======
@dp.message(F.text == "📋 Инвентарь")
@dp.message(Command("инвентарь"))
async def cmd_inventory(message: Message):
    if message.chat.type != "private":
        await message.answer("❌ Инвентарь доступен только в личных сообщениях с ботом")
        return
    
    user_id = message.from_user.id
    skins = db.get_user_skins(user_id)
    
    if not skins:
        await message.answer("📭 У вас нет скинов")
        return
    
    text = "📋 Ваш Инвентарь\n\n"
    total_value = Decimal('0')
    rarity_stats = {}
    
    for i, skin in enumerate(skins[:10], 1):
        text += f"{i}. {skin['name']}\n"
        text += f"   📊 {skin['rarity']} | 💰 {format_number(skin['price'])} GRAM\n\n"
        try:
            price = Decimal(str(skin['price']).replace(' ', ''))
            total_value += price
            
            rarity = skin['rarity']
            if rarity not in rarity_stats:
                rarity_stats[rarity] = {'count': 0, 'total': Decimal('0')}
            rarity_stats[rarity]['count'] += 1
            rarity_stats[rarity]['total'] += price
        except:
            pass
    
    if len(skins) > 10:
        text += f"\n... и ещё {len(skins) - 10} скинов\n"
    
    text += f"\n💰 Общая стоимость: {format_number(str(total_value))} GRAM"
    text += f"\n📦 Всего скинов: {len(skins)}"
    
    builder = InlineKeyboardBuilder()
    
    for i, skin in enumerate(skins[:5], 1):
        builder.button(text=f"💰 Продать {i}", callback_data=f"sell_skin_{skin['id']}")
    
    if skins:
        builder.adjust(3)
        
        builder.row(InlineKeyboardButton(
            text=f"💰💰 ПРОДАТЬ ВСЕ ({len(skins)} шт.)", 
            callback_data="sell_all_skins"
        ))
    
    await message.answer(text, parse_mode="Markdown", reply_markup=builder.as_markup() if skins else None)

@dp.callback_query(F.data.startswith("sell_skin_"))
async def sell_skin(callback: CallbackQuery):
    skin_id = int(callback.data.replace("sell_skin_", ""))
    user_id = callback.from_user.id
    result = db.sell_skin(user_id, skin_id)
    
    if result['status'] == 'success':
        new_balance = result.get('new_balance', db.get_balance(user_id))
        await callback.message.edit_text(
            f"✅ Вы продали {result['skin_name']}\n"
            f"💰 Получено: {format_number(result['amount'])} GRAM\n"
            f"💵 Новый баланс: {format_number(new_balance)} GRAM",
            parse_mode="Markdown"
        )
    else:
        await callback.message.edit_text(f"❌ {result.get('message', 'Ошибка продажи')}")
    
    await callback.answer()

@dp.callback_query(F.data == "sell_all_skins")
async def sell_all_skins(callback: CallbackQuery):
    user_id = callback.from_user.id
    
    skins = db.get_user_skins(user_id)
    if not skins:
        await callback.answer("📭 У вас нет скинов", show_alert=True)
        return
    
    total_value = Decimal('0')
    rarity_stats = {}
    
    for skin in skins:
        try:
            price = Decimal(str(skin['price']).replace(' ', ''))
            total_value += price
            
            rarity = skin['rarity']
            if rarity not in rarity_stats:
                rarity_stats[rarity] = {'count': 0, 'total': Decimal('0')}
            rarity_stats[rarity]['count'] += 1
            rarity_stats[rarity]['total'] += price
        except:
            pass
    
    text = f"❓ Вы действительно хотите продать ВСЕ свои скины?\n\n"
    text += f"📦 Количество: {len(skins)} шт.\n"
    text += f"💰 Общая стоимость: {format_number(str(total_value))} GRAM\n\n"
    text += "📊 По редкости:\n"
    
    for rarity, stats in rarity_stats.items():
        text += f"  {rarity}: {stats['count']} шт. - {format_number(str(stats['total']))} GRAM\n"
    
    text += f"\n⚠️ Это действие нельзя отменить!"
    
    # Двойное подтверждение
    builder = InlineKeyboardBuilder()
    builder.button(text="✅ Да, продать всё", callback_data="confirm_sell_all_1")
    builder.button(text="❌ Отмена", callback_data="cancel_sell_all")
    
    await callback.message.edit_text(text, parse_mode="Markdown", reply_markup=builder.as_markup())
    await callback.answer()

@dp.callback_query(F.data == "confirm_sell_all_1")
async def confirm_sell_all_first(callback: CallbackQuery):
    """Первое подтверждение продажи всех скинов"""
    user_id = callback.from_user.id
    
    skins = db.get_user_skins(user_id)
    if not skins:
        await callback.answer("📭 У вас нет скинов", show_alert=True)
        return
    
    builder = InlineKeyboardBuilder()
    builder.button(text="✅ ПОДТВЕРДИТЬ ЕЩЁ РАЗ", callback_data="confirm_sell_all_2")
    builder.button(text="❌ Отмена", callback_data="cancel_sell_all")
    
    await callback.message.edit_text(
        "⚠️ **ВНИМАНИЕ!**\n\n"
        "Вы действительно уверены? Это действие **нельзя отменить**!\n\n"
        "Нажмите подтверждение ещё раз, чтобы продать все скины.",
        parse_mode="Markdown",
        reply_markup=builder.as_markup()
    )
    await callback.answer()

@dp.callback_query(F.data == "confirm_sell_all_2")
async def confirm_sell_all_second(callback: CallbackQuery):
    """Второе подтверждение - финальная продажа"""
    user_id = callback.from_user.id
    result = db.sell_all_skins(user_id)
    
    if result['status'] == 'success':
        new_balance = result.get('new_balance', db.get_balance(user_id))
        
        text = f"✅ Вы успешно продали все скины!\n\n"
        text += f"📦 Продано скинов: {result['count']}\n"
        text += f"💰 Получено: {format_number(result['amount'])} GRAM\n"
        text += f"💵 Новый баланс: {format_number(new_balance)} GRAM\n\n"
        
        if result.get('rarity_stats'):
            text += "📊 Продано по редкости:\n"
            for rarity, stats in result['rarity_stats'].items():
                text += f"  {rarity}: {stats['count']} шт. - {format_number(str(stats['total']))} GRAM\n"
        
        if result.get('skins'):
            text += f"\n📋 В числе проданных:\n"
            for skin_name in result['skins'][:5]:
                text += f"• {skin_name}\n"
            if result['count'] > 5:
                text += f"• ... и ещё {result['count'] - 5} скинов\n"
        
        await callback.message.edit_text(text, parse_mode="Markdown")
    else:
        await callback.message.edit_text(f"❌ {result.get('message', 'Ошибка при продаже')}")
    
    await callback.answer()

@dp.callback_query(F.data == "cancel_sell_all")
async def cancel_sell_all(callback: CallbackQuery):
    user_id = callback.from_user.id
    skins = db.get_user_skins(user_id)
    
    if not skins:
        await callback.message.edit_text("📭 У вас нет скинов")
        await callback.answer()
        return
    
    text = "📋 Ваш Инвентарь\n\n"
    total_value = Decimal('0')
    
    for i, skin in enumerate(skins[:10], 1):
        text += f"{i}. {skin['name']}\n"
        text += f"   📊 {skin['rarity']} | 💰 {format_number(skin['price'])} GRAM\n\n"
        try:
            total_value += Decimal(str(skin['price']).replace(' ', ''))
        except:
            pass
    
    if len(skins) > 10:
        text += f"\n... и ещё {len(skins) - 10} скинов\n"
    
    text += f"\n💰 Общая стоимость: {format_number(str(total_value))} GRAM"
    text += f"\n📦 Всего скинов: {len(skins)}"
    
    builder = InlineKeyboardBuilder()
    
    for i, skin in enumerate(skins[:5], 1):
        builder.button(text=f"💰 Продать {i}", callback_data=f"sell_skin_{skin['id']}")
    
    if skins:
        builder.adjust(3)
        builder.row(InlineKeyboardButton(
            text=f"💰💰 ПРОДАТЬ ВСЕ ({len(skins)} шт.)", 
            callback_data="sell_all_skins"
        ))
    
    await callback.message.edit_text(text, parse_mode="Markdown", reply_markup=builder.as_markup())
    await callback.answer()

# ====== ПРОМОКОДЫ ======
@dp.message(F.text == "🎫 Промокод")
async def cmd_promocode(message: Message, state: FSMContext):
    await message.answer(
        "🎫 Введите промокод:\n\n"
        "Напишите код промокода в ответ на это сообщение.\n\n"
        "Пример: SUMMER2024",
        parse_mode="Markdown"
    )
    await state.set_state(AdminStates.waiting_for_promo_input)

@dp.message(AdminStates.waiting_for_promo_input)
async def process_promocode_input(message: Message, state: FSMContext):
    code = message.text.strip()
    user_id = message.from_user.id
    success, msg, amount = db.use_promocode(user_id, code)
    await message.answer(msg)
    await state.clear()

# ====== ПРОФИЛЬ И БАЛАНС ======
@dp.message(F.text == "💰 Баланс")
@dp.message(F.chat.type.in_({"group", "supergroup"}), F.text.lower().in_(["б", "баланс"]))
async def cmd_balance(message: Message):
    user = message.from_user
    db.get_or_create_user(user.id, user.username, user.first_name, user.last_name)
    balance = db.get_balance(user.id)
    can_get, _ = db.can_get_bonus(user.id)
    user_link = f"<a href='tg://user?id={user.id}'>{user.first_name}</a>"
    text = f"{user_link}\n💰 Баланс: {format_number(balance)} GRAM"

    if can_get and message.chat.type == "private":
        await message.answer(text, parse_mode="HTML", reply_markup=get_bonus_keyboard(True))
    else:
        await message.answer(text, parse_mode="HTML")

@dp.message(F.text == "👤 Профиль")
async def cmd_profile(message: Message):
    user = message.from_user
    user_data = db.get_or_create_user(user.id, user.username, user.first_name, user.last_name)
    
    skins = db.get_user_skins(user.id)
    total_skins = len(skins)
    total_skins_value = Decimal('0')
    for s in skins:
        try:
            total_skins_value += Decimal(s['price'])
        except:
            pass
    
    text = (
        f"🆔 <code>{user.id}</code>\n"
        f"👤 Имя: {user_data['first_name'] or 'Нет'}\n"
        f"🌐 Username: @{user_data['username'] or 'нет'}\n"
        f"💰 Баланс: {format_number(user_data['balance'])} GRAM\n"
        f"📦 Скинов: {total_skins} (на {format_number(str(total_skins_value))} GRAM)\n"
        f"📅 Регистрация: {user_data['created_at'][:10]}\n"
        f"🎫 Промокодов активировано: {len(user_data.get('used_promocodes', []))}"
    )
    await message.answer(text, parse_mode="HTML")

@dp.message(F.text == "🎁 Бонус")
async def cmd_bonus(message: Message):
    user_id = message.from_user.id
    can_get, next_bonus = db.can_get_bonus(user_id)

    if can_get:
        success, msg = db.give_bonus(user_id)
        if success:
            balance = db.get_balance(user_id)
            await message.answer(f"🎁 Бонус {format_number(BONUS_AMOUNT)} GRAM получен!\n💰 Баланс: {format_number(balance)} GRAM")
        else:
            await message.answer(msg)
    else:
        if next_bonus:
            time_left = next_bonus - datetime.now()
            hours = int(time_left.total_seconds() // 3600)
            minutes = int((time_left.total_seconds() % 3600) // 60)
            await message.answer(f"⏳ Следующий бонус через {hours}ч {minutes}м")

@dp.callback_query(F.data == "get_bonus")
async def process_bonus(callback: CallbackQuery):
    user_id = callback.from_user.id
    await callback.answer()
    can_get, next_bonus = db.can_get_bonus(user_id)

    if can_get:
        success, msg = db.give_bonus(user_id)
        if success:
            balance = db.get_balance(user_id)
            text = callback.message.text + f"\n\n✅ Бонус {format_number(BONUS_AMOUNT)} GRAM получен!"
            await callback.message.edit_text(text, parse_mode="HTML")
            await callback.message.answer(f"💰 Новый баланс: {format_number(balance)} GRAM")
        else:
            await callback.message.answer(msg)
    else:
        if next_bonus:
            time_left = next_bonus - datetime.now()
            hours = int(time_left.total_seconds() // 3600)
            minutes = int((time_left.total_seconds() % 3600) // 60)
            await callback.message.answer(f"⏳ Следующий бонус через {hours}ч {minutes}м")

@dp.message(F.text == "📜 История")
async def cmd_history(message: Message):
    user_id = message.from_user.id
    history = db.get_history(user_id, limit=10)

    if not history:
        await message.answer("📜 История пуста")
        return

    text = "📜 Последние операции:\n\n"
    for tx in history:
        if tx.get('to_id') == user_id:
            text += f"✅ +{format_number(tx['amount'])} GRAM"
            if tx['type'] == 'transfer':
                text += " (перевод)\n"
            elif tx['type'] == 'daily_bonus':
                text += " (бонус)\n"
            elif tx['type'] == 'start_bonus':
                text += " (стартовый)\n"
            elif tx['type'] == 'mines_win':
                text += " (мины)\n"
            elif tx['type'] == 'secret_room_win':
                text += " (тайная комната)\n"
            elif tx['type'] == 'secret_room_cancel':
                text += " (возврат - тайная комната)\n"
            elif tx['type'] == 'box_win':
                text += " (шкатулки)\n"
            elif tx['type'] == 'skin_sale':
                text += " (продажа скина)\n"
            elif tx['type'] == 'promocode':
                text += " (промокод)\n"
            elif tx['type'] == 'game_timeout':
                text += " (автовозврат)\n"
            else:
                text += "\n"
        else:
            text += f"📤 -{format_number(tx['amount'])} GRAM (перевод)\n"

    await message.answer(text)

@dp.message(F.text == "📊 Топ")
async def cmd_top(message: Message):
    top_users = db.get_top_users(10)

    if not top_users:
        await message.answer("📊 Топ пуст")
        return

    text = "🏆 Топ пользователей:\n\n"
    for i, user in enumerate(top_users, 1):
        name = user['first_name'] or user['username'] or f"User{user['user_id']}"
        if len(name) > 15:
            name = name[:12] + "..."
        text += f"{i}. {name} - {format_number(user['balance'])} GRAM\n"

    await message.answer(text, parse_mode="Markdown")

@dp.message(F.text == "⭐️ Донат")
async def cmd_donate(message: Message):
    text = (
        "💎 Поддержка проекта\n\n"
        f"📞 @{SUPPORT_USERNAME}\n\n"
        "Администратор поможет вам с пополнением баланса."
    )
    await message.answer(text, parse_mode="Markdown", reply_markup=get_donate_keyboard())

# ====== ПЕРЕВОДЫ ======
@dp.message(F.chat.type.in_({"group", "supergroup"}), F.reply_to_message, F.text.lower().startswith("п "))
async def chat_transfer_reply(message: Message):
    if not message.reply_to_message:
        return

    to_user = message.reply_to_message.from_user
    if to_user.is_bot:
        await message.answer("❌ Нельзя переводить боту")
        return

    from_user = message.from_user
    text = message.text

    db.get_or_create_user(from_user.id, from_user.username, from_user.first_name, from_user.last_name)
    db.get_or_create_user(to_user.id, to_user.username, to_user.first_name, to_user.last_name)

    parts = text.split()
    if len(parts) < 2:
        await message.answer("❌ Использование: п сумма (ответом на сообщение)")
        return

    try:
        amount = parts[1]
        Decimal(amount)
    except:
        await message.answer("❌ Укажите корректную сумму")
        return

    if Decimal(amount) <= 0:
        await message.answer("❌ Сумма должна быть положительной")
        return

    if to_user.id == from_user.id:
        await message.answer("❌ Нельзя перевести самому себе")
        return

    success, msg = db.transfer(from_user.id, to_user.id, amount, message.chat.id)
    if success:
        from_link = f"<a href='tg://user?id={from_user.id}'>{from_user.first_name}</a>"
        to_link = f"<a href='tg://user?id={to_user.id}'>{to_user.first_name}</a>"
        response = f"{from_link} перевел {format_number(amount)} GRAM для {to_link}"
        await message.answer(response, parse_mode="HTML")
    else:
        await message.answer(msg)

@dp.message(F.chat.type.in_({"group", "supergroup"}), F.text.lower().startswith("п @"))
async def chat_transfer_username(message: Message):
    text = message.text
    from_user = message.from_user
    db.get_or_create_user(from_user.id, from_user.username, from_user.first_name, from_user.last_name)

    parts = text.split()
    if len(parts) < 3:
        await message.answer("❌ Использование: п @username сумма")
        return

    username = parts[1]
    try:
        amount = parts[2]
        Decimal(amount)
    except:
        await message.answer("❌ Укажите корректную сумму")
        return

    if Decimal(amount) <= 0:
        await message.answer("❌ Сумма должна быть положительной")
        return

    to_user_data = db.get_user_by_username(username)
    if not to_user_data:
        await message.answer("❌ Пользователь не найден")
        return

    to_id = to_user_data['user_id']

    if to_id == from_user.id:
        await message.answer("❌ Нельзя перевести самому себе")
        return

    success, msg = db.transfer(from_user.id, to_id, amount, message.chat.id)
    if success:
        from_link = f"<a href='tg://user?id={from_user.id}'>{from_user.first_name}</a>"
        to_link = f"<a href='tg://user?id={to_id}'>{to_user_data['first_name']}</a>"
        response = f"{from_link} перевел {format_number(amount)} GRAM для {to_link}"
        await message.answer(response, parse_mode="HTML")
    else:
        await message.answer(msg)

# ====== ИГРА МИНЫ ======
@dp.message(F.chat.type.in_({"group", "supergroup"}), F.text.lower().startswith("мины"))
async def mines_start(message: Message):
    if message.sender_chat:
        await message.answer("❌ Каналы не могут играть в игры. Напишите от лица аккаунта.")
        return
    
    # Очищаем старые игры перед началом новой
    db.cleanup_old_games()
    
    user = message.from_user
    text = message.text.lower()

    db.get_or_create_user(user.id, user.username, user.first_name, user.last_name)

    has_game, game_name = db.has_active_game(user.id)
    if has_game:
        await message.answer(f"❌ У вас уже есть активная игра в {game_name}!\nСначала завершите текущую игру.")
        return

    parts = text.split()
    if len(parts) < 2:
        await message.answer("❌ Использование: мины [сумма] или мины фулл\nНапример: мины 100 или мины фулл")
        return

    if parts[1] == "фулл":
        bet = db.get_balance(user.id)
    else:
        bet = parts[1]
    
    # Валидация ставки
    balance = db.get_balance(user.id)
    is_valid, error_msg = validate_bet(bet, balance)
    if not is_valid:
        await message.answer(error_msg)
        return

    # Списываем ставку
    success, msg, new_balance = db.update_balance(user.id, '-' + bet)
    if not success:
        await message.answer(msg)
        return

    all_cells = list(range(25))
    mines_positions = random.sample(all_cells, MINES_COUNT)

    success = db.start_mines_game(user.id, message.chat.id, bet, mines_positions)
    
    if not success:
        # Возвращаем ставку при ошибке
        db.update_balance(user.id, bet)
        await message.answer("❌ Ошибка создания игры. Попробуйте еще раз.")
        return

    user_link = f"<a href='tg://user?id={user.id}'>{user.first_name}</a>"

    await message.answer(
        f"{user_link}, вы начали игру минное поле!\n"
        f"💰 Ставка: {format_number(bet)} GRAM\n"
        f"💵 Выигрыш: x1,0 | {format_number(bet)} GRAM",
        parse_mode="HTML",
        reply_markup=get_mines_game_keyboard()
    )

@dp.callback_query(F.data.startswith("cell_"))
async def process_mines_cell(callback: CallbackQuery):
    user_id = callback.from_user.id
    
    # Получаем блокировку для пользователя
    lock = await get_user_lock(user_id)
    
    async with lock:
        cell = int(callback.data.split("_")[1])

        game = db.get_mines_game(user_id)
        if not game:
            await callback.answer("❌ Игра не найдена", show_alert=True)
            return

        result = db.open_cell(user_id, cell)

        if result['status'] == 'error':
            await callback.answer(result['message'], show_alert=True)
            return

        user = callback.from_user
        user_link = f"<a href='tg://user?id={user_id}'>{user.first_name}</a>"
        
        if result['status'] == 'lost':
            await callback.message.edit_text(
                f"{user_link}, игра завершена!\n\n"
                f"💵 Вы проиграли",
                parse_mode="HTML",
                reply_markup=get_mines_game_keyboard(
                    opened_cells=result['opened_cells'],
                    game_over=True,
                    show_mines=result['mines_positions']
                )
            )
            await callback.answer(f"💥", show_alert=False)

        elif result['status'] == 'win':
            new_balance = result.get('new_balance', db.get_balance(user_id))
            await callback.message.edit_text(
                f"{user_link}, игра завершена!\n\n"
                f"🎉 Вы выиграли\n"
                f"💰 Сумма: {format_number(result['amount'])} GRAM\n"
                f"💵 Новый баланс: {format_number(new_balance)} GRAM",
                parse_mode="HTML",
                reply_markup=get_mines_game_keyboard(
                    opened_cells=result['opened_cells'],
                    game_over=True,
                    show_mines=result['mines_positions']
                )
            )
            await callback.answer(f"💰", show_alert=False)

        elif result['status'] == 'continue':
            bet_dec = Decimal(str(result['bet']).replace(' ', ''))
            win_dec = Decimal(str(result['current_win']).replace(' ', ''))
            multiplier = win_dec / bet_dec
            
            await callback.message.edit_text(
                f"{user_link}, вы начали игру минное поле!\n"
                f"💰 Ставка: {format_number(result['bet'])} GRAM\n"
                f"💵 Выигрыш: x{multiplier:.2f} | {format_number(result['current_win'])} GRAM",
                parse_mode="HTML",
                reply_markup=get_mines_game_keyboard(result['opened_cells'])
            )
            await callback.answer(f"✅", show_alert=False)

@dp.callback_query(F.data == "take_win")
async def take_mines_win(callback: CallbackQuery):
    user_id = callback.from_user.id
    result = db.take_win(user_id)

    if result['status'] == 'error':
        await callback.answer(result['message'], show_alert=True)
        return

    user = callback.from_user
    user_link = f"<a href='tg://user?id={user_id}'>{user.first_name}</a>"

    new_balance = result.get('new_balance', db.get_balance(user_id))
    
    await callback.message.edit_text(
        f"{user_link}, вы забрали выигрыш!\n"
        f"💰 Сумма: {format_number(result['amount'])} GRAM",
        parse_mode="HTML",
        reply_markup=get_mines_game_keyboard(
            opened_cells=result['opened_cells'],
            game_over=True,
            show_mines=result['mines_positions']
        )
    )
    await callback.answer(f"💰", show_alert=False)

@dp.callback_query(F.data == "cancel_game")
async def cancel_mines_game(callback: CallbackQuery):
    user_id = callback.from_user.id
    game = db.get_mines_game(user_id)

    if not game:
        await callback.answer("❌ Игра не найдена", show_alert=True)
        return

    # Возвращаем ставку
    success, msg, new_balance = db.update_balance(user_id, game['bet'])
    if success:
        db.add_transaction({
            'to_id': user_id,
            'amount': game['bet'],
            'type': 'mines_cancel',
            'chat_id': game['chat_id'],
            'created_at': datetime.now().isoformat()
        })
    db.end_mines_game(user_id)

    user = callback.from_user
    user_link = f"<a href='tg://user?id={user_id}'>{user.first_name}</a>"

    await callback.message.edit_text(f"{user_link} ❌ ОТМЕНА ИГРЫ ❌", parse_mode="HTML")
    await asyncio.sleep(0.5)
    await callback.message.edit_text(
        f"{user_link} отменил игру\n"
        f"💰 Ставка {format_number(game['bet'])} GRAM возвращена",
        parse_mode="HTML"
    )
    await callback.answer("✅ Игра отменена, ставка возвращена", show_alert=False)

# ====== ИГРА ШКАТУЛКИ ======
@dp.message(F.chat.type.in_({"group", "supergroup"}), F.text.lower().startswith("шкатулки"))
async def box_start(message: Message):
    if message.sender_chat:
        await message.answer("❌ Каналы не могут играть в игры. Напишите от лица аккаунта.")
        return
    
    # Очищаем старые игры перед началом новой
    db.cleanup_old_games()
    
    user = message.from_user
    text = message.text.lower()

    db.get_or_create_user(user.id, user.username, user.first_name, user.last_name)

    has_game, game_name = db.has_active_game(user.id)
    if has_game:
        await message.answer(f"❌ У вас уже есть активная игра в {game_name}!\nСначала завершите текущую игру.")
        return

    parts = text.split()
    if len(parts) < 2:
        await message.answer("❌ Использование: шкатулки [сумма] или шкатулки фулл\nНапример: шкатулки 100")
        return

    if parts[1] == "фулл":
        bet = db.get_balance(user.id)
    else:
        bet = parts[1]
    
    # Валидация ставки
    balance = db.get_balance(user.id)
    is_valid, error_msg = validate_bet(bet, balance)
    if not is_valid:
        await message.answer(error_msg)
        return

    # Списываем ставку
    success, msg, new_balance = db.update_balance(user.id, '-' + bet)
    if not success:
        await message.answer(msg)
        return

    success = db.start_box_game(user.id, message.chat.id, bet)
    
    if not success:
        # Возвращаем ставку при ошибке
        db.update_balance(user.id, bet)
        await message.answer("❌ Ошибка создания игры. Попробуйте еще раз.")
        return

    anim_msg = await message.answer("📦 Перемешиваем шкатулки...")
    await asyncio.sleep(0.5)
    await anim_msg.edit_text("🎁 В одной из них золото!")
    await asyncio.sleep(0.5)
    await anim_msg.delete()

    user_link = f"<a href='tg://user?id={user.id}'>{user.first_name}</a>"

    await message.answer(
        f"{user_link} начал игру в шкатулки!\n"
        f"💰 Ставка: {format_number(bet)} GRAM\n"
        f"💎 Выигрыш: {format_number(str(int(Decimal(bet) * Decimal('1.5'))))} GRAM (x1.5)\n\n"
        f"Выберите шкатулку:",
        parse_mode="HTML",
        reply_markup=get_box_keyboard()
    )

@dp.callback_query(F.data.startswith("box_"))
async def process_box(callback: CallbackQuery):
    user_id = callback.from_user.id
    
    # Получаем блокировку для пользователя
    lock = await get_user_lock(user_id)
    
    async with lock:
        box_num = int(callback.data.split("_")[1])

        game = db.get_box_game(user_id)
        if not game:
            await callback.answer("❌ Игра не найдена", show_alert=True)
            return

        result = db.open_box(user_id, box_num)

        if result['status'] == 'error':
            await callback.answer(result['message'], show_alert=True)
            return

        user = callback.from_user
        user_link = f"<a href='tg://user?id={user_id}'>{user.first_name}</a>"
        
        if result['status'] == 'win':
            new_balance = result.get('new_balance', db.get_balance(user_id))
            await callback.message.edit_text(
                f"{user_link} 🎉 Нашел Золото! 🎉\n"
                f"💰 Выигрыш: {format_number(result['amount'])} GRAM\n"
                f"💵 Новый баланс: {format_number(new_balance)} GRAM",
                parse_mode="HTML"
            )
            await callback.answer(f"💰 Вы выиграли {format_number(result['amount'])} GRAM!", show_alert=False)

        elif result['status'] == 'lost':
            await callback.message.edit_text(
                f"{user_link} 😢 Все шкатулки пусты...\n"
                f"💰 Потеряно: {format_number(result['bet'])} GRAM",
                parse_mode="HTML"
            )
            await callback.answer(f"😢 Вы проиграли {format_number(result['bet'])} GRAM", show_alert=False)

        elif result['status'] == 'continue':
            remaining = result['remaining']
            text = f"{user_link} открыл шкатулку {box_num}... там пусто 😢\n\n"
            text += f"Осталось шкатулок: {remaining}\n"
            text += f"💰 Ставка: {format_number(game['bet'])} GRAM\n"
            text += f"💎 Выигрыш: {format_number(str(int(Decimal(game['bet']) * Decimal('1.5'))))} GRAM"
            
            await callback.message.edit_text(
                text,
                parse_mode="HTML",
                reply_markup=get_box_keyboard(result['opened'])
            )
            await callback.answer(f"Шкатулка {box_num} пуста", show_alert=False)

@dp.callback_query(F.data == "cancel_box")
async def cancel_box_game(callback: CallbackQuery):
    user_id = callback.from_user.id
    game = db.get_box_game(user_id)

    if not game:
        await callback.answer("❌ Игра не найдена", show_alert=True)
        return

    # Возвращаем ставку
    success, msg, new_balance = db.update_balance(user_id, game['bet'])
    if success:
        db.add_transaction({
            'to_id': user_id,
            'amount': game['bet'],
            'type': 'box_cancel',
            'chat_id': game['chat_id'],
            'created_at': datetime.now().isoformat()
        })
    db.end_box_game(user_id)

    user = callback.from_user
    user_link = f"<a href='tg://user?id={user_id}'>{user.first_name}</a>"

    new_balance = new_balance if 'new_balance' in locals() else db.get_balance(user_id)
    await callback.message.edit_text(
        f"{user_link} отменил игру\n"
        f"💰 Ставка {format_number(game['bet'])} GRAM возвращена\n"
        f"💵 Новый баланс: {format_number(new_balance)} GRAM",
        parse_mode="HTML"
    )
    await callback.answer("✅ Игра отменена, ставка возвращена", show_alert=False)

# ====== ИГРА ТАЙНАЯ КОМНАТА ======
@dp.message(F.chat.type.in_({"group", "supergroup"}), F.text.lower().startswith("тк"))
async def secret_room_start(message: Message):
    if message.sender_chat:
        await message.answer("❌ Каналы не могут играть в игры. Напишите от лица аккаунта.")
        return
    
    # Очищаем старые игры перед началом новой
    db.cleanup_old_games()
    
    user = message.from_user
    text = message.text.lower()

    db.get_or_create_user(user.id, user.username, user.first_name, user.last_name)

    has_game, game_name = db.has_active_game(user.id)
    if has_game:
        await message.answer(f"❌ У вас уже есть активная игра в {game_name}!\nСначала завершите текущую игру.")
        return

    parts = text.split()
    if len(parts) < 2:
        await message.answer("❌ Использование: тк [сумма] или тк фулл\nНапример: тк 1000 или тк фулл")
        return

    if parts[1] == "фулл":
        bet = db.get_balance(user.id)
    else:
        bet = parts[1]
    
    # Валидация ставки
    balance = db.get_balance(user.id)
    is_valid, error_msg = validate_bet(bet, balance)
    if not is_valid:
        await message.answer(error_msg)
        return

    # Проверка минимальной ставки для тайной комнаты
    if Decimal(bet) < 100:
        await message.answer("❌ Минимальная ставка для тайной комнаты: 100 GRAM")
        return

    # Списываем ставку
    success, msg, new_balance = db.update_balance(user.id, '-' + bet)
    if not success:
        await message.answer(msg)
        return

    success = db.start_secret_room(user.id, message.chat.id, bet)
    
    if not success:
        # Возвращаем ставку при ошибке
        db.update_balance(user.id, bet)
        await message.answer("❌ Ошибка создания игры. Попробуйте еще раз.")
        return

    anim_msg = await message.answer("🔮 Открываем портал в Тайную комнату...")
    await asyncio.sleep(0.5)
    await anim_msg.edit_text("🔮 За дверями скрываются сокровища...")
    await asyncio.sleep(0.5)
    await anim_msg.edit_text("🔮 И монстры... 🫣")
    await asyncio.sleep(0.5)
    await anim_msg.delete()

    user_link = f"<a href='tg://user?id={user.id}'>{user.first_name}</a>"

    await message.answer(
        f"{user_link} вошел в Тайную комнату!\n\n"
        f"🔮 Правила:\n"
        f"• Перед вами 6 дверей\n"
        f"• За 3 дверьми спрятаны сокровища (+50% к выигрышу)\n"
        f"• За 2 дверьми прячутся монстры (проигрыш)\n"
        f"• 1 дверь пустая\n\n"
        f"💰 Ставка: {format_number(bet)} GRAM\n"
        f"💵 Текущий выигрыш: {format_number(bet)} GRAM\n"
        f"🎯 Осталось найти: 3 сокровища",
        parse_mode="HTML",
        reply_markup=get_secret_room_keyboard()
    )

@dp.callback_query(F.data.startswith("secret_"))
async def process_secret_door(callback: CallbackQuery):
    user_id = callback.from_user.id
    
    # Получаем блокировку для пользователя
    lock = await get_user_lock(user_id)
    
    async with lock:
        door = int(callback.data.split("_")[1])

        game = db.get_secret_room(user_id)
        if not game:
            await callback.answer("❌ Игра не найдена", show_alert=True)
            return

        result = db.open_secret_door(user_id, door)

        if result['status'] == 'error':
            await callback.answer(result['message'], show_alert=True)
            return

        user = callback.from_user
        user_link = f"<a href='tg://user?id={user_id}'>{user.first_name}</a>"
        
        if result['status'] == 'lost':
            await callback.message.edit_text(
                f"{user_link} проиграл! 💔\n\n"
                f"👹 За дверью {door} был монстр!\n"
                f"🚪 Открыто дверей: {result['doors_count']}/6\n"
                f"💰 Потеряно: {format_number(result['bet'])} GRAM",
                parse_mode="HTML",
                reply_markup=get_secret_room_keyboard(
                    opened_doors=result['opened_doors'],
                    game_over=True,
                    show_treasures=result['treasure_doors'],
                    show_monsters=result['monster_doors']
                )
            )
            await callback.answer(f"👹 Вас съел монстр!", show_alert=False)

        elif result['status'] == 'win':
            new_balance = result.get('new_balance', db.get_balance(user_id))
            await callback.message.edit_text(
                f"{user_link} нашел все сокровища! 🎉\n\n"
                f"💰 Найдено сокровищ: 3/3\n"
                f"🚪 Открыто дверей: {result['doors_count']}/6\n"
                f"💎 Выигрыш: {format_number(result['amount'])} GRAM\n"
                f"💵 Новый баланс: {format_number(new_balance)} GRAM",
                parse_mode="HTML",
                reply_markup=get_secret_room_keyboard(
                    opened_doors=result['opened_doors'],
                    game_over=True,
                    show_treasures=result['treasure_doors'],
                    show_monsters=result['monster_doors']
                )
            )
            await callback.answer(f"💰 Вы выиграли {format_number(result['amount'])} GRAM!", show_alert=False)

        elif result['status'] == 'treasure':
            await callback.message.edit_text(
                f"{user_link} в Тайной комнате!\n\n"
                f"✅ За дверью {door} нашлось сокровище! +50% к выигрышу\n"
                f"💰 Текущий выигрыш: {format_number(result['current_win'])} GRAM\n"
                f"🎯 Осталось найти: {result['remaining']}/3 сокровищ\n"
                f"🚪 Открыто дверей: {result['doors_count']}/6",
                parse_mode="HTML",
                reply_markup=get_secret_room_keyboard(result['opened_doors'])
            )
            await callback.answer(f"💰 Найдено сокровище!", show_alert=False)

        elif result['status'] == 'empty':
            await callback.message.edit_text(
                f"{user_link} в Тайной комнате!\n\n"
                f"🚪 За дверью {door} ничего не оказалось...\n"
                f"💰 Текущий выигрыш: {format_number(result['current_win'])} GRAM\n"
                f"🎯 Осталось найти: {result['remaining']}/3 сокровищ\n"
                f"🚪 Открыто дверей: {result['doors_count']}/6",
                parse_mode="HTML",
                reply_markup=get_secret_room_keyboard(result['opened_doors'])
            )
            await callback.answer(f"🚪 Дверь {door} пуста", show_alert=False)

@dp.callback_query(F.data == "cancel_secret")
async def cancel_secret_room(callback: CallbackQuery):
    user_id = callback.from_user.id
    result = db.cancel_secret_room(user_id)

    if result['status'] == 'error':
        await callback.answer(result['message'], show_alert=True)
        return

    user = callback.from_user
    user_link = f"<a href='tg://user?id={user_id}'>{user.first_name}</a>"

    new_balance = result.get('new_balance', db.get_balance(user_id))
    await callback.message.edit_text(
        f"{user_link} покинул Тайную комнату\n"
        f"💰 Ставка {format_number(result['amount'])} GRAM возвращена\n"
        f"💵 Новый баланс: {format_number(new_balance)} GRAM",
        parse_mode="HTML"
    )
    await callback.answer("✅ Игра отменена, ставка возвращена", show_alert=False)

# ====== АДМИНКА ======
@dp.message(F.text == "👑 Админ панель")
async def cmd_admin(message: Message):
    if not db.is_admin(message.from_user.id):
        await message.answer("❌ У вас нет прав администратора")
        return
    await message.answer("👑 Админ-панель", reply_markup=get_admin_keyboard())

@dp.message(F.text == "📊 Статистика")
async def admin_stats(message: Message):
    if not db.is_admin(message.from_user.id):
        return
    stats = db.get_stats()
    promocodes = db.get_all_promocodes()
    active_promocodes = sum(1 for p in promocodes if p.get('active', False))
    total_uses = sum(p.get('uses', 0) for p in promocodes)

    text = (
        "📊 Статистика:\n\n"
        f"👥 Всего: {stats['total_users']}\n"
        f"📈 Новых сегодня: {stats['today_users']}\n"
        f"💰 Общий баланс: {format_number(stats['total_balance'])} GRAM\n\n"
        f"🎫 Промокоды:\n"
        f"• Всего: {len(promocodes)}\n"
        f"• Активных: {active_promocodes}\n"
        f"• Использований: {total_uses}"
    )
    await message.answer(text, parse_mode="Markdown")

@dp.message(F.text == "💰 Выдать баланс")
async def admin_give_balance(message: Message, state: FSMContext):
    if not db.is_admin(message.from_user.id):
        return
    await message.answer("Введите ID пользователя:")
    await state.set_state(AdminStates.waiting_for_user_id)

@dp.message(AdminStates.waiting_for_user_id)
async def admin_process_user_id(message: Message, state: FSMContext):
    try:
        target_id = int(message.text.strip())
    except ValueError:
        await message.answer("❌ Некорректный ID. Введите число")
        await state.clear()
        return

    user = db.get_user(target_id)
    if user:
        name = user.get('first_name') or f"ID {target_id}"
        await message.answer(f"✅ Пользователь найден: {name}\nВведите сумму (можно с пробелами, K, M, B):")
    else:
        await message.answer(f"⚠️ Пользователь с ID {target_id} не найден. Введите сумму для создания:")

    await state.update_data(target_id=target_id)
    await state.set_state(AdminStates.waiting_for_amount)

@dp.message(AdminStates.waiting_for_amount)
async def admin_process_amount(message: Message, state: FSMContext):
    amount = parse_amount(message.text)

    if amount is None:
        await message.answer("❌ Некорректная сумма. Введите число (можно с пробелами, K, M, B)")
        return

    try:
        if Decimal(amount) == 0:
            await message.answer("❌ Сумма не может быть нулевой")
            return
    except:
        pass

    data = await state.get_data()
    target_id = data['target_id']

    success, msg, new_balance = db.update_balance(target_id, amount)
    if success:
        user = db.get_user(target_id)
        name = user.get('first_name') or f"ID {target_id}" if user else f"ID {target_id}"

        action = "начислено" if not amount.startswith('-') else "списано"
        abs_amount = amount[1:] if amount.startswith('-') else amount

        await message.answer(
            f"✅ Пользователю {name}\n"
            f"{action}: {format_number(abs_amount)} GRAM\n"
            f"💰 Новый баланс: {format_number(new_balance)} GRAM",
            parse_mode="Markdown"
        )
    else:
        await message.answer(msg)
    
    await state.clear()

@dp.message(F.text == "🔄 Обнулить баланс")
async def admin_reset_balance(message: Message, state: FSMContext):
    if not db.is_admin(message.from_user.id):
        return
    await message.answer("Введите ID пользователя:")
    await state.set_state(AdminStates.waiting_for_reset_user_id)

@dp.message(AdminStates.waiting_for_reset_user_id)
async def admin_process_reset_user(message: Message, state: FSMContext):
    try:
        target_id = int(message.text.strip())
    except ValueError:
        await message.answer("❌ Некорректный ID. Введите число")
        await state.clear()
        return

    user = db.get_user(target_id)
    if not user:
        builder = InlineKeyboardBuilder()
        builder.button(text="✅ Да, создать", callback_data=f"confirm_reset_new_{target_id}")
        builder.button(text="❌ Отмена", callback_data="cancel_reset")
        await message.answer(
            f"⚠️ Пользователь с ID {target_id} не найден. Создать?",
            reply_markup=builder.as_markup()
        )
        await state.clear()
        return

    name = user.get('first_name') or 'Нет имени'
    balance = user['balance']

    builder = InlineKeyboardBuilder()
    builder.button(text="✅ Да, обнулить", callback_data=f"confirm_reset_{target_id}")
    builder.button(text="❌ Отмена", callback_data="cancel_reset")

    await message.answer(
        f"👤 Пользователь: {name}\n"
        f"🆔 {target_id}\n"
        f"💰 Баланс: {format_number(balance)} GRAM\n\n"
        f"❓ Обнулить?",
        parse_mode="Markdown",
        reply_markup=builder.as_markup()
    )
    await state.clear()

@dp.callback_query(F.data.startswith("confirm_reset_"))
async def confirm_reset_balance(callback: CallbackQuery):
    target_id = int(callback.data.replace("confirm_reset_", ""))
    admin_id = callback.from_user.id

    if not db.is_admin(admin_id):
        await callback.answer("❌ Нет прав", show_alert=True)
        return

    user = db.get_user(target_id)
    if not user:
        await callback.message.edit_text("❌ Пользователь не найден")
        await callback.answer()
        return

    old_balance = user['balance']
    name = user.get('first_name') or f"ID {target_id}"

    success, msg, _ = db.reset_balance(target_id)
    if success:
        await callback.message.edit_text(
            f"✅ Баланс обнулен!\n\n"
            f"👤 {name}\n"
            f"🆔 {target_id}\n"
            f"💰 Было: {format_number(old_balance)} GRAM\n"
            f"💵 Стало: 0 GRAM",
            parse_mode="Markdown"
        )
    else:
        await callback.message.edit_text(msg)

    try:
        await bot.send_message(
            target_id,
            f"⚠️ Ваш баланс обнулен администратором.\n"
            f"💰 Предыдущий баланс: {format_number(old_balance)} GRAM"
        )
    except:
        pass

    await callback.answer("✅ Баланс обнулен")

@dp.callback_query(F.data.startswith("confirm_reset_new_"))
async def confirm_reset_new_user(callback: CallbackQuery):
    target_id = int(callback.data.replace("confirm_reset_new_", ""))
    admin_id = callback.from_user.id

    if not db.is_admin(admin_id):
        await callback.answer("❌ Нет прав", show_alert=True)
        return

    db.get_or_create_user(target_id)
    db.reset_balance(target_id)

    await callback.message.edit_text(
        f"✅ Пользователь создан и обнулен!\n\n"
        f"🆔 {target_id}\n"
        f"💰 Баланс: 0 GRAM",
        parse_mode="Markdown"
    )
    await callback.answer()

@dp.callback_query(F.data == "cancel_reset")
async def cancel_reset(callback: CallbackQuery):
    await callback.message.edit_text("❌ Операция отменена")
    await callback.answer()

# ====== ПРОМОКОДЫ (АДМИНКА) ======
@dp.message(F.text == "🎫 Создать промокод")
async def admin_create_promocode(message: Message, state: FSMContext):
    if not db.is_admin(message.from_user.id):
        return

    await message.answer(
        "🎫 Создание промокода\n\n"
        "Введите код (например: SUMMER2024):",
        parse_mode="Markdown"
    )
    await state.set_state(AdminStates.waiting_for_promo_code)

@dp.message(AdminStates.waiting_for_promo_code)
async def admin_process_promo_code(message: Message, state: FSMContext):
    code = message.text.strip().upper()

    await state.update_data(promo_code=code)
    await message.answer("Введите сумму GRAM (можно с пробелами, K, M, B):")
    await state.set_state(AdminStates.waiting_for_promo_amount)

@dp.message(AdminStates.waiting_for_promo_amount)
async def admin_process_promo_amount(message: Message, state: FSMContext):
    amount = parse_amount(message.text)

    if amount is None or amount == '0':
        await message.answer("❌ Некорректная сумма. Введите положительное число")
        return

    await state.update_data(promo_amount=amount)
    await message.answer("Введите количество использований (0 = безлимит):")
    await state.set_state(AdminStates.waiting_for_promo_uses)

@dp.message(AdminStates.waiting_for_promo_uses)
async def admin_process_promo_uses(message: Message, state: FSMContext):
    try:
        uses = int(message.text.strip())
        if uses < 0:
            raise ValueError
    except ValueError:
        await message.answer("❌ Некорректное число. Введите 0 или положительное число")
        return

    data = await state.get_data()
    code = data['promo_code']
    amount = data['promo_amount']

    success, msg, promocode = db.create_promocode(code, amount, uses)
    if success:
        await message.answer(
            f"✅ Промокод создан!\n\n"
            f"📋 Код: `{code}`\n"
            f"💰 Сумма: {format_number(amount)} GRAM\n"
            f"📊 Использований: {'Безлимит' if uses == 0 else uses}",
            parse_mode="Markdown"
        )
    else:
        await message.answer(msg)
    
    await state.clear()

@dp.message(F.text == "📋 Список промокодов")
async def admin_list_promocodes(message: Message):
    if not db.is_admin(message.from_user.id):
        return

    promocodes = db.get_all_promocodes()

    if not promocodes:
        await message.answer("📭 Промокоды не созданы")
        return

    text = "📋 Промокоды:\n\n"

    for p in promocodes:
        status = "✅" if p.get('active') else "❌"
        uses = f"{p['uses']}/{p['max_uses']}" if p['max_uses'] > 0 else f"{p['uses']}/∞"
        text += f"{status} `{p['code']}` - {format_number(p['amount'])} GRAM ({uses})\n"

    if len(text) > 4000:
        parts = [text[i:i+4000] for i in range(0, len(text), 4000)]
        for part in parts:
            await message.answer(part, parse_mode="Markdown")
    else:
        await message.answer(text, parse_mode="Markdown")

@dp.message(F.text == "📢 Рассылка")
async def admin_broadcast(message: Message, state: FSMContext):
    if not db.is_admin(message.from_user.id):
        return

    await message.answer(
        "📢 Рассылка\n\n"
        "Введите текст для рассылки.\n"
        "Можно использовать HTML теги (b, i, code, a)",
        parse_mode="Markdown"
    )
    await state.set_state(AdminStates.waiting_for_broadcast)

@dp.message(AdminStates.waiting_for_broadcast)
async def admin_process_broadcast(message: Message, state: FSMContext):
    text = message.text
    
    status_msg = await message.answer("📨 Подготовка рассылки...")
    
    users = db.get_all_users()
    total = len(users)
    
    await status_msg.edit_text(f"📨 Начинаю рассылку {total} пользователям...\n⏳ Это может занять несколько минут")
    
    sent = 0
    failed = 0
    blocked = 0
    
    for i, user in enumerate(users):
        try:
            await bot.send_message(user['user_id'], text, parse_mode="HTML")
            sent += 1
            
            if i % 10 == 0:
                await status_msg.edit_text(
                    f"📨 Рассылка...\n"
                    f"✅ Отправлено: {sent}\n"
                    f"❌ Ошибок: {failed}\n"
                    f"🚫 Заблокировали: {blocked}\n"
                    f"📊 Прогресс: {i}/{total}"
                )
            
            await asyncio.sleep(0.1)
            
        except Exception as e:
            error_str = str(e)
            if "blocked" in error_str or "deactivated" in error_str:
                blocked += 1
            else:
                failed += 1
            logger.error(f"Ошибка отправки пользователю {user['user_id']}: {e}")
    
    await status_msg.edit_text(
        f"✅ Рассылка завершена!\n\n"
        f"📊 Статистика:\n"
        f"• Всего пользователей: {total}\n"
        f"• ✅ Доставлено: {sent}\n"
        f"• 🚫 Заблокировали бота: {blocked}\n"
        f"• ❌ Ошибок: {failed}",
        parse_mode="Markdown"
    )
    
    await state.clear()

@dp.message(F.text == "🔙 Выход")
async def admin_exit(message: Message):
    keyboard = get_main_keyboard(message.from_user.id, message.chat.type)
    await message.answer("Главное меню:", reply_markup=keyboard)

@dp.callback_query(F.data == "ignore")
async def ignore_callback(callback: CallbackQuery):
    await callback.answer()

# ====== ЗАПУСК ======
async def main():
    # Запускаем планировщик для очистки старых игр (каждые 30 секунд)
    scheduler.add_job(cleanup_job, IntervalTrigger(seconds=30))
    
    # Запускаем планировщик для очистки старых транзакций (раз в день)
    scheduler.add_job(cleanup_transactions_job, IntervalTrigger(hours=24))
    
    scheduler.start()
    
    # Запускаем задачу для очистки блокировок
    asyncio.create_task(cleanup_locks_task())
    
    logger.info("✅ Бот запущен")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())