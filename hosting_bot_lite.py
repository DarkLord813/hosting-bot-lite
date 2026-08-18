import os
import json
import sqlite3
import subprocess
import sys
import shutil
import urllib.request
import urllib.parse
from datetime import datetime, timedelta
from pathlib import Path
from time import sleep
import threading
import secrets
import string
import re
import signal
import platform
import traceback
import hashlib
import base64
import tempfile
import random
import time as time_module

# ========== CONFIGURATION ==========
BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
if not BOT_TOKEN:
    for _alias in ("TELEGRAM_TOKEN", "TELEGRAM_BOT_TOKEN", "TOKEN", "API_TOKEN", "TG_BOT_TOKEN"):
        BOT_TOKEN = os.environ.get(_alias, "")
        if BOT_TOKEN:
            os.environ["BOT_TOKEN"] = BOT_TOKEN
            print(f"ℹ️  BOT_TOKEN set from {_alias}")
            break
if not BOT_TOKEN:
    print("⚠️  WARNING: No bot token found.")
    BOT_TOKEN = "MISSING_TOKEN"

admin_ids_str = os.environ.get("ADMIN_IDS", "7713987088")
ADMIN_IDS = set()
for _x in admin_ids_str.replace(";", ",").split(","):
    try:
        if _x.strip(): ADMIN_IDS.add(int(_x.strip()))
    except ValueError:
        pass
if not ADMIN_IDS:
    ADMIN_IDS = {7713987088}

def is_admin(user_id) -> bool:
    try:
        return int(user_id) in ADMIN_IDS
    except (TypeError, ValueError):
        return False

REQUIRED_CHANNEL = os.environ.get("REQUIRED_CHANNEL", "@NCK_Dev")
CHANNEL_LINK = os.environ.get("CHANNEL_LINK", "https://t.me/NCK_Dev")

IS_RENDER = os.environ.get("RENDER") == "true"
IS_HEROKU = os.environ.get("HEROKU") == "true"
IS_CHOREO = os.environ.get("CHOREO") == "true"
IS_ANDROID = 'pydroid' in sys.executable.lower() or 'termux' in sys.executable.lower()

_persistent_override = os.environ.get("PERSISTENT_DISK_PATH", "").strip()
if _persistent_override:
    BASE_DIR = Path(_persistent_override) / "bot_hosting_data"
elif IS_RENDER:
    BASE_DIR = Path("/opt/render/project/src/bot_hosting_data")
elif IS_HEROKU:
    BASE_DIR = Path("/app/bot_hosting_data")
elif IS_CHOREO:
    BASE_DIR = Path("/choreo/app/bot_hosting_data")
elif IS_ANDROID:
    BASE_DIR = Path("/storage/emulated/0/bot_hosting_data")
else:
    BASE_DIR = Path("./bot_hosting_data")

USING_PERSISTENT_DISK = bool(_persistent_override)
BASE_DIR = BASE_DIR.resolve()

DEPLOYMENTS_DIR = BASE_DIR / "deployments"
DATABASE_FILE = BASE_DIR / "hosting_bot.db"
USER_FILES_DIR = BASE_DIR / "user_files"
LOGS_DIR = BASE_DIR / "logs"
PIP_CACHE_DIR = BASE_DIR / "pip_cache"

for dir_path in [BASE_DIR, DEPLOYMENTS_DIR, USER_FILES_DIR, LOGS_DIR, PIP_CACHE_DIR]:
    dir_path.mkdir(parents=True, exist_ok=True)

MAX_FILE_SIZE_MB = int(os.environ.get("MAX_FILE_SIZE_MB", 50))
MAX_FILE_SIZE_BYTES = MAX_FILE_SIZE_MB * 1024 * 1024

PRICE_MONTHLY_STARS = int(os.environ.get("PRICE_MONTHLY_STARS", 50))
PRICE_YEARLY_STARS = int(os.environ.get("PRICE_YEARLY_STARS", 500))
PRICE_MONTHLY_COINS = PRICE_MONTHLY_STARS * 10
PRICE_YEARLY_COINS = PRICE_YEARLY_STARS * 10

FREE_USER_MAX_DEPLOYMENTS = int(os.environ.get("FREE_USER_MAX_DEPLOYMENTS", 3))
FREE_DEPLOYMENT_DURATION_HOURS = int(os.environ.get("FREE_DEPLOYMENT_DURATION_HOURS", 24))
STARS_PER_COIN = int(os.environ.get("STARS_PER_COIN", 10))
REFERRAL_REWARD_COINS = int(os.environ.get("REFERRAL_REWARD_COINS", 50))

GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN", "")
GITHUB_REPO_OWNER = os.environ.get("GITHUB_REPO_OWNER", "")
GITHUB_REPO_NAME = os.environ.get("GITHUB_REPO_NAME", "")
GITHUB_BACKUP_BRANCH = os.environ.get("GITHUB_BACKUP_BRANCH", "main")
GITHUB_BACKUP_PATH = os.environ.get("GITHUB_BACKUP_PATH", "backups/hosting_bot.db")
GITHUB_ENABLED = bool(GITHUB_TOKEN and GITHUB_REPO_OWNER and GITHUB_REPO_NAME)

PIP_INSTALL_TIMEOUT = int(os.environ.get("PIP_INSTALL_TIMEOUT", 600))

TELEGRAM_API = f"https://api.telegram.org/bot{BOT_TOKEN}"
LAST_UPDATE_ID = 0

server_running = True
active_deployments = {}
deployment_lock = threading.Lock()

WEBHOOK_PORT = int(os.environ.get("PORT", 8080))
WEBHOOK_PATH = os.environ.get("WEBHOOK_PATH", "/webhook")

# ========== TELEGRAM FILE DOWNLOAD ==========
def download_telegram_file(file_id, save_path):
    """Download a file from Telegram using its file_id"""
    try:
        # Get file path
        url = f"{TELEGRAM_API}/getFile"
        params = {"file_id": file_id}
        req = urllib.request.Request(f"{url}?{urllib.parse.urlencode(params)}")
        with urllib.request.urlopen(req, timeout=30) as response:
            data = json.loads(response.read().decode('utf-8'))
            if not data.get('ok'):
                return False, data.get('description', 'Failed to get file info')
            file_path = data['result']['file_path']
        
        # Download file
        file_url = f"https://api.telegram.org/file/bot{BOT_TOKEN}/{file_path}"
        req = urllib.request.Request(file_url)
        with urllib.request.urlopen(req, timeout=60) as response:
            content = response.read()
        
        # Save file
        save_path = Path(save_path)
        save_path.parent.mkdir(parents=True, exist_ok=True)
        with open(save_path, 'wb') as f:
            f.write(content)
        
        return True, str(save_path)
    except Exception as e:
        return False, str(e)

# ========== ULTRA COMPRESSED RESOURCE MONITOR ==========
_RESOURCE_CACHE = {}
_RESOURCE_CACHE_LOCK = threading.Lock()
_RESOURCE_CACHE_LAST_UPDATE = 0
_RESOURCE_CACHE_INTERVAL = 30

try:
    import psutil
except ImportError:
    psutil = None

def _get_ram_usage_pid(pid):
    if not pid:
        return 0
    try:
        if psutil:
            try:
                p = psutil.Process(pid)
                return p.memory_info().rss / (1024 * 1024)
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                return 0
        try:
            with open(f'/proc/{pid}/statm', 'r') as f:
                parts = f.read().split()
                if len(parts) >= 2:
                    return int(parts[1]) * 4 / 1024
        except Exception:
            pass
    except Exception:
        pass
    return 0

def _get_cpu_usage_pid(pid):
    if not pid or not psutil:
        return 0
    try:
        p = psutil.Process(pid)
        return p.cpu_percent(interval=0.0)
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        return 0

def _read_proc_cpu_usage():
    try:
        with open('/proc/stat', 'r') as f:
            line = f.readline()
        parts = list(map(int, line.split()[1:]))
        total = sum(parts)
        idle = parts[3] + parts[4] if len(parts) > 4 else parts[3]
        return total, idle
    except Exception:
        return None, None

def _read_proc_mem_info():
    try:
        mem = {}
        with open('/proc/meminfo', 'r') as f:
            for line in f:
                k, v = line.split(':', 1)
                mem[k.strip()] = int(v.strip().split()[0]) / 1024
        total = mem.get('MemTotal', 0)
        available = mem.get('MemAvailable', mem.get('MemFree', 0))
        used = total - available
        return total, used, available
    except Exception:
        return 0, 0, 0

def get_ultra_compressed_stats(refresh=False):
    global _RESOURCE_CACHE_LAST_UPDATE
    
    now = time_module.time()
    if not refresh and now - _RESOURCE_CACHE_LAST_UPDATE < _RESOURCE_CACHE_INTERVAL:
        return _RESOURCE_CACHE.get('stats', 'R:0/0 C:0 L:0,0,0 D:0')
    
    with _RESOURCE_CACHE_LOCK:
        ram_total, ram_used, _ = _read_proc_mem_info()
        
        cpu_percent = 0
        t0, i0 = _read_proc_cpu_usage()
        if t0 is not None:
            sleep(0.1)
            t1, i1 = _read_proc_cpu_usage()
            if t1 and t1 > t0:
                busy_delta = (t1 - i1) - (t0 - i0)
                total_delta = t1 - t0
                cpu_percent = (busy_delta / total_delta * 100) if total_delta else 0
        
        try:
            load_avg = os.getloadavg()
            load_str = f"{load_avg[0]:.1f},{load_avg[1]:.1f},{load_avg[2]:.1f}"
        except (OSError, AttributeError):
            load_str = "0,0,0"
        
        try:
            conn = sqlite3.connect(DATABASE_FILE)
            c = conn.cursor()
            c.execute("SELECT COUNT(*) FROM deployments WHERE status='active'")
            dep_count = c.fetchone()[0] or 0
            conn.close()
        except Exception:
            dep_count = 0
        
        stats = f"R:{ram_used:.1f}/{ram_total:.1f} C:{cpu_percent:.1f} L:{load_str} D:{dep_count}"
        _RESOURCE_CACHE['stats'] = stats
        _RESOURCE_CACHE_LAST_UPDATE = now
        return stats

def update_deployment_resources(deployment_id=None):
    try:
        conn = sqlite3.connect(DATABASE_FILE)
        c = conn.cursor()
        if deployment_id:
            c.execute("SELECT proc_pid, file_name FROM deployments WHERE deployment_id=? AND status='active'", (deployment_id,))
            rows = c.fetchall()
        else:
            c.execute("SELECT deployment_id, proc_pid, file_name FROM deployments WHERE status='active'")
            rows = c.fetchall()
        conn.close()
        
        with _RESOURCE_CACHE_LOCK:
            for row in rows:
                if len(row) == 3:
                    did, pid, fname = row
                else:
                    did, pid, fname = deployment_id, row[0], row[1] if len(row) > 1 else 'bot'
                
                if pid:
                    ram = _get_ram_usage_pid(pid)
                    cpu = _get_cpu_usage_pid(pid)
                    _RESOURCE_CACHE[f'ram_{did}'] = ram
                    _RESOURCE_CACHE[f'cpu_{did}'] = cpu
                    _RESOURCE_CACHE[f'name_{did}'] = display_filename(str(fname))[:20] if fname else f'dep{did}'
    except Exception:
        pass

def get_deployment_resources(deployment_id):
    with _RESOURCE_CACHE_LOCK:
        ram = _RESOURCE_CACHE.get(f'ram_{deployment_id}', 0)
        cpu = _RESOURCE_CACHE.get(f'cpu_{deployment_id}', 0)
        name = _RESOURCE_CACHE.get(f'name_{deployment_id}', f'dep{deployment_id}')
    return f"{name} R:{ram:.1f}MB C:{cpu:.1f}%"

def get_all_deployment_resources():
    results = []
    try:
        conn = sqlite3.connect(DATABASE_FILE)
        c = conn.cursor()
        c.execute("SELECT deployment_id, proc_pid, file_name FROM deployments WHERE status='active' AND proc_pid IS NOT NULL")
        rows = c.fetchall()
        conn.close()
        
        for did, pid, fname in rows:
            ram = _get_ram_usage_pid(pid)
            cpu = _get_cpu_usage_pid(pid)
            name = display_filename(str(fname))[:20] if fname else f'dep{did}'
            results.append(f"#{did}:{name} R:{ram:.0f} C:{cpu:.1f}")
    except Exception:
        pass
    return results

# ========== DATABASE SETUP ==========
def init_db():
    conn = sqlite3.connect(DATABASE_FILE)
    c = conn.cursor()
    
    c.execute('''CREATE TABLE IF NOT EXISTS users (
        user_id INTEGER PRIMARY KEY,
        username TEXT,
        first_name TEXT,
        join_date TEXT,
        last_active TEXT,
        coins_balance INTEGER DEFAULT 0,
        stars_balance INTEGER DEFAULT 0,
        total_coins_earned INTEGER DEFAULT 0,
        total_coins_spent INTEGER DEFAULT 0,
        total_stars_earned INTEGER DEFAULT 0,
        total_stars_spent INTEGER DEFAULT 0,
        joined_channel INTEGER DEFAULT 0,
        free_deployment_count INTEGER DEFAULT 0,
        is_premium INTEGER DEFAULT 0,
        premium_expires TEXT,
        premium_plan TEXT,
        step TEXT,
        temp_file TEXT,
        requirements TEXT,
        env_vars TEXT,
        plan TEXT,
        payment_method TEXT,
        duration INTEGER,
        cost_coins INTEGER,
        cost_stars INTEGER,
        waiting_for_env INTEGER DEFAULT 0,
        waiting_for_reqs INTEGER DEFAULT 0,
        waiting_for_redeem INTEGER DEFAULT 0,
        temp_target_user TEXT,
        temp_coins_amount INTEGER,
        temp_stars_amount INTEGER,
        temp_expiry INTEGER,
        temp_reward_type TEXT,
        pending_payment_payload TEXT,
        last_expiry_notification TEXT,
        referral_code TEXT UNIQUE,
        referred_by INTEGER DEFAULT NULL,
        total_referrals INTEGER DEFAULT 0,
        pending_json TEXT DEFAULT '{}',
        tos_accepted INTEGER DEFAULT 0
    )''')
    
    c.execute('''CREATE TABLE IF NOT EXISTS deployments (
        deployment_id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        file_name TEXT,
        file_size INTEGER,
        file_id TEXT,
        requirements_file_id TEXT,
        requirements_text TEXT,
        env_vars TEXT,
        plan TEXT,
        payment_method TEXT,
        cost_coins INTEGER,
        cost_stars INTEGER,
        start_time TEXT,
        expire_time TEXT,
        status TEXT,
        proc_pid INTEGER,
        install_log TEXT,
        deploy_log TEXT,
        error_log TEXT,
        is_free INTEGER DEFAULT 0,
        is_paused INTEGER DEFAULT 0,
        last_expiry_notification TEXT,
        bot_type TEXT DEFAULT 'python_app',
        framework TEXT DEFAULT 'unknown',
        dependencies_installed TEXT,
        folder_name TEXT,
        source_type TEXT DEFAULT 'upload',
        github_repo TEXT,
        github_branch TEXT DEFAULT 'main',
        crash_restart_count INTEGER DEFAULT 0,
        last_crash_restart TEXT
    )''')
    
    c.execute('''CREATE TABLE IF NOT EXISTS subscriptions (
        subscription_id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        plan TEXT,
        amount_stars INTEGER,
        amount_coins INTEGER,
        start_date TEXT,
        end_date TEXT,
        status TEXT,
        renewal_count INTEGER DEFAULT 0,
        admin_notified INTEGER DEFAULT 0,
        payment_payload TEXT
    )''')
    
    c.execute('''CREATE TABLE IF NOT EXISTS coin_transactions (
        transaction_id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        amount INTEGER,
        transaction_type TEXT,
        source TEXT,
        reference_id TEXT,
        timestamp TEXT,
        status TEXT
    )''')
    
    c.execute('''CREATE TABLE IF NOT EXISTS star_transactions (
        transaction_id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        amount INTEGER,
        transaction_type TEXT,
        source TEXT,
        reference_id TEXT,
        timestamp TEXT,
        status TEXT,
        payload TEXT
    )''')
    
    c.execute('''CREATE TABLE IF NOT EXISTS redeem_codes (
        code_id INTEGER PRIMARY KEY AUTOINCREMENT,
        code TEXT UNIQUE,
        coins_amount INTEGER,
        stars_amount INTEGER,
        created_by INTEGER,
        created_at TEXT,
        expires_at TEXT,
        expiry_days INTEGER,
        max_uses INTEGER,
        used_count INTEGER DEFAULT 0,
        is_active INTEGER DEFAULT 1,
        used_by TEXT
    )''')
    
    c.execute('''CREATE TABLE IF NOT EXISTS pending_deployments (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        chat_id INTEGER,
        message_id INTEGER,
        temp_file TEXT,
        requirements TEXT,
        env_vars TEXT,
        plan TEXT,
        duration INTEGER,
        cost_coins INTEGER,
        cost_stars INTEGER,
        payment_method TEXT,
        payload TEXT,
        created_at TEXT,
        status TEXT
    )''')
    
    c.execute('''CREATE TABLE IF NOT EXISTS system_stats (
        id INTEGER PRIMARY KEY CHECK (id = 1),
        total_users INTEGER DEFAULT 0,
        total_deployments INTEGER DEFAULT 0,
        total_active_deployments INTEGER DEFAULT 0,
        total_paused_deployments INTEGER DEFAULT 0,
        total_free_deployments INTEGER DEFAULT 0,
        total_coins_created INTEGER DEFAULT 0,
        total_stars_created INTEGER DEFAULT 0,
        total_revenue_stars INTEGER DEFAULT 0,
        total_revenue_usd REAL DEFAULT 0,
        premium_users INTEGER DEFAULT 0,
        server_start_time TEXT,
        last_updated TEXT
    )''')
    
    c.execute('''CREATE TABLE IF NOT EXISTS bug_reports (
        report_id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        username TEXT,
        first_name TEXT,
        message TEXT NOT NULL,
        status TEXT DEFAULT 'open',
        admin_reply TEXT,
        replied_by INTEGER,
        created_at TEXT,
        replied_at TEXT
    )''')
    
    c.execute('''CREATE TABLE IF NOT EXISTS referrals (
        referral_id INTEGER PRIMARY KEY AUTOINCREMENT,
        referrer_id INTEGER NOT NULL,
        referred_id INTEGER NOT NULL UNIQUE,
        created_at TEXT,
        reward_coins INTEGER DEFAULT 0,
        reward_given INTEGER DEFAULT 0
    )''')
    
    c.execute('INSERT OR IGNORE INTO system_stats (id, server_start_time, last_updated) VALUES (1, ?, ?)', 
              (datetime.now().isoformat(), datetime.now().isoformat()))
    
    for idx in ['idx_deployments_user', 'idx_deployments_status', 'idx_deployments_expire',
                'idx_subscriptions_user', 'idx_subscriptions_expire', 'idx_bug_reports_status',
                'idx_bug_reports_user', 'idx_referrals_referrer']:
        try:
            c.execute(f'CREATE INDEX IF NOT EXISTS {idx} ON deployments({idx.replace("idx_", "").replace("_", ",")})')
        except:
            pass
    
    conn.commit()
    conn.close()
    print("✅ Database initialized")

# ========== DEPLOY FOLDER HELPER ==========
def kill_deployment_process(pid, sig=None):
    if sig is None:
        sig = signal.SIGTERM
    if not pid:
        return
    try:
        target_pgid = os.getpgid(pid)
        own_pgid = os.getpgid(os.getpid())
        if target_pgid == own_pgid:
            os.kill(pid, sig)
            return
        os.killpg(target_pgid, sig)
    except (ProcessLookupError, PermissionError):
        pass
    except Exception:
        try:
            os.kill(pid, sig)
        except Exception:
            pass

def get_deploy_folder(user_id, deployment_id):
    try:
        conn = sqlite3.connect(DATABASE_FILE)
        c = conn.cursor()
        c.execute("SELECT folder_name, user_id FROM deployments WHERE deployment_id = ?", (deployment_id,))
        row = c.fetchone()
        conn.close()
        if row and row[0]:
            owner_id = row[1] if row[1] else user_id
            return DEPLOYMENTS_DIR / str(owner_id) / str(row[0])
    except Exception:
        pass
    return DEPLOYMENTS_DIR / str(user_id) / str(deployment_id)

# ========== HELPERS ==========
def db_execute(query, params=(), fetch='none', retries=5, delay=0.2):
    for attempt in range(retries):
        conn = None
        try:
            conn = sqlite3.connect(DATABASE_FILE, timeout=10)
            conn.execute("PRAGMA journal_mode=WAL")
            c = conn.cursor()
            c.execute(query, params)
            conn.commit()
            if fetch == 'one':
                return c.fetchone()
            if fetch == 'all':
                return c.fetchall()
            return True
        except sqlite3.OperationalError as e:
            if 'locked' in str(e).lower() or 'busy' in str(e).lower():
                if attempt < retries - 1:
                    sleep(delay * (attempt + 1))
                    continue
            print(f"❌ DB error: {e}")
            return None
        except Exception as e:
            print(f"❌ DB error: {e}")
            return None
        finally:
            if conn:
                try: conn.close()
                except: pass
    return None

def get_user_info(user_id):
    conn = sqlite3.connect(DATABASE_FILE)
    c = conn.cursor()
    c.execute("SELECT first_name FROM users WHERE user_id = ?", (user_id,))
    row = c.fetchone()
    conn.close()
    return {'first_name': row[0] if row else 'User'}

def update_system_stats():
    try:
        conn = sqlite3.connect(DATABASE_FILE)
        c = conn.cursor()
        c.execute("SELECT COUNT(*) FROM users")
        total_users = c.fetchone()[0] or 0
        c.execute("SELECT COUNT(*) FROM deployments")
        total_deployments = c.fetchone()[0] or 0
        c.execute("SELECT COUNT(*) FROM deployments WHERE status='active'")
        active_deployments_count = c.fetchone()[0] or 0
        c.execute("SELECT COUNT(*) FROM deployments WHERE status='paused'")
        paused_deployments = c.fetchone()[0] or 0
        c.execute("SELECT COUNT(*) FROM deployments WHERE is_free=1")
        free_deployments = c.fetchone()[0] or 0
        c.execute("SELECT SUM(coins_amount) FROM redeem_codes")
        coins_created = c.fetchone()[0] or 0
        c.execute("SELECT SUM(stars_amount) FROM redeem_codes")
        stars_created = c.fetchone()[0] or 0
        c.execute("SELECT SUM(amount) FROM star_transactions WHERE transaction_type='subscription' AND status='completed'")
        revenue_stars = c.fetchone()[0] or 0
        c.execute("SELECT COUNT(*) FROM users WHERE is_premium=1")
        premium_users = c.fetchone()[0] or 0
        revenue_usd = revenue_stars * 0.01
        
        c.execute('''UPDATE system_stats SET 
            total_users=?, total_deployments=?, total_active_deployments=?,
            total_paused_deployments=?, total_free_deployments=?, total_coins_created=?,
            total_stars_created=?, total_revenue_stars=?, total_revenue_usd=?,
            premium_users=?, last_updated=?
            WHERE id=1''',
            (total_users, total_deployments, active_deployments_count, paused_deployments,
             free_deployments, coins_created, stars_created, revenue_stars, revenue_usd,
             premium_users, datetime.now().isoformat()))
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"❌ Update system stats error: {e}")

def get_system_stats():
    try:
        conn = sqlite3.connect(DATABASE_FILE)
        c = conn.cursor()
        c.execute('SELECT * FROM system_stats WHERE id = 1')
        row = c.fetchone()
        conn.close()
        if row:
            return {
                'total_users': row[1], 'total_deployments': row[2],
                'active_deployments': row[3], 'paused_deployments': row[4],
                'free_deployments': row[5], 'coins_created': row[6],
                'stars_created': row[7], 'revenue_stars': row[8],
                'revenue_usd': row[9], 'premium_users': row[10],
                'server_start_time': row[11], 'last_updated': row[12]
            }
        return {}
    except Exception as e:
        print(f"❌ Get system stats error: {e}")
        return {}

def get_user_balances(user_id):
    conn = sqlite3.connect(DATABASE_FILE)
    c = conn.cursor()
    c.execute("SELECT coins_balance, stars_balance FROM users WHERE user_id = ?", (user_id,))
    row = c.fetchone()
    conn.close()
    return {'coins': row[0] if row else 0, 'stars': row[1] if row else 0}

def update_user_coins(user_id, delta, transaction_type="balance_update", source="system"):
    conn = sqlite3.connect(DATABASE_FILE)
    c = conn.cursor()
    try:
        c.execute("INSERT OR IGNORE INTO users (user_id) VALUES (?)", (user_id,))
        c.execute("UPDATE users SET coins_balance = coins_balance + ? WHERE user_id = ?", (delta, user_id))
        if delta > 0:
            c.execute("UPDATE users SET total_coins_earned = total_coins_earned + ? WHERE user_id = ?", (delta, user_id))
        else:
            c.execute("UPDATE users SET total_coins_spent = total_coins_spent + ? WHERE user_id = ?", (abs(delta), user_id))
        c.execute('''INSERT INTO coin_transactions 
            (user_id, amount, transaction_type, source, reference_id, timestamp, status)
            VALUES (?, ?, ?, ?, ?, ?, ?)''',
            (user_id, delta, transaction_type, source, None, datetime.now().isoformat(), 'completed'))
        conn.commit()
    except Exception as e:
        print(f"❌ update_user_coins error: {e}")
        conn.rollback()
    finally:
        conn.close()
    update_system_stats()
    return True

def update_user_stars(user_id, delta, transaction_type="balance_update", source="system", payload=None):
    conn = sqlite3.connect(DATABASE_FILE)
    c = conn.cursor()
    try:
        c.execute("INSERT OR IGNORE INTO users (user_id) VALUES (?)", (user_id,))
        c.execute("UPDATE users SET stars_balance = stars_balance + ? WHERE user_id = ?", (delta, user_id))
        if delta > 0:
            c.execute("UPDATE users SET total_stars_earned = total_stars_earned + ? WHERE user_id = ?", (delta, user_id))
        else:
            c.execute("UPDATE users SET total_stars_spent = total_stars_spent + ? WHERE user_id = ?", (abs(delta), user_id))
        c.execute('''INSERT INTO star_transactions 
            (user_id, amount, transaction_type, source, reference_id, timestamp, status, payload)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)''',
            (user_id, delta, transaction_type, source, None, datetime.now().isoformat(), 'completed', payload))
        conn.commit()
    except Exception as e:
        print(f"❌ update_user_stars error: {e}")
        conn.rollback()
    finally:
        conn.close()
    update_system_stats()
    return True

def is_user_premium(user_id):
    if is_admin(user_id):
        return True
    try:
        conn = sqlite3.connect(DATABASE_FILE)
        c = conn.cursor()
        c.execute('''SELECT is_premium, premium_expires FROM users WHERE user_id = ?''', (user_id,))
        row = c.fetchone()
        conn.close()
        if row and row[0] == 1 and row[1]:
            expires = datetime.fromisoformat(row[1])
            if expires > datetime.now():
                return True
        return False
    except Exception:
        return False

def get_free_deployment_used_count(user_id):
    conn = sqlite3.connect(DATABASE_FILE)
    c = conn.cursor()
    c.execute("SELECT COUNT(*) FROM deployments WHERE user_id = ? AND is_free = 1 AND status = 'active'", (user_id,))
    count = c.fetchone()[0] or 0
    conn.close()
    return count

def can_use_free_deployment(user_id):
    if is_admin(user_id):
        return True, "Admin - Unlimited"
    if is_user_premium(user_id):
        return True, "Premium - Unlimited"
    used_count = get_free_deployment_used_count(user_id)
    remaining = FREE_USER_MAX_DEPLOYMENTS - used_count
    if remaining > 0:
        return True, f"Free tier - {remaining} remaining"
    else:
        return False, f"Free tier limit reached ({used_count}/{FREE_USER_MAX_DEPLOYMENTS})"

def stop_free_deployments_for_user(user_id):
    conn = sqlite3.connect(DATABASE_FILE)
    c = conn.cursor()
    c.execute("""SELECT deployment_id, proc_pid FROM deployments
                 WHERE user_id = ? AND is_free = 1 AND status IN ('active','paused')""",
              (user_id,))
    rows = c.fetchall()
    stopped = 0
    for dep_id, proc_pid in rows:
        if proc_pid:
            try:
                kill_deployment_process(proc_pid)
            except Exception:
                pass
        c.execute("""UPDATE deployments SET status='stopped', proc_pid=NULL, is_paused=0
                     WHERE deployment_id=?""", (dep_id,))
        with deployment_lock:
            active_deployments.pop(dep_id, None)
        stopped += 1
    conn.commit()
    conn.close()
    return stopped

def resume_premium_deployment(user_id, duration_days):
    conn = sqlite3.connect(DATABASE_FILE)
    c = conn.cursor()
    c.execute("""SELECT deployment_id, file_name, env_vars, folder_name, proc_pid
                 FROM deployments
                 WHERE user_id = ? AND is_free = 0
                       AND status IN ('stopped','paused','failed')
                 ORDER BY start_time DESC LIMIT 1""", (user_id,))
    row = c.fetchone()
    conn.close()
    if not row:
        return None, False
    
    dep_id, file_name, env_vars_json, folder_name_val, old_pid = row
    if old_pid:
        try:
            kill_deployment_process(old_pid)
        except Exception:
            pass
    
    # Restart the deployment
    success = restart_deployment_by_id(dep_id, user_id, is_auto_restart=True)
    if success:
        new_expire = datetime.now() + timedelta(days=duration_days)
        conn2 = sqlite3.connect(DATABASE_FILE)
        conn2.execute("""UPDATE deployments
                         SET expire_time=?, start_time=?
                         WHERE deployment_id=?""",
                      (new_expire.isoformat(), datetime.now().isoformat(), dep_id))
        conn2.commit()
        conn2.close()
        return dep_id, True
    return dep_id, False

def activate_premium(user_id, plan, amount_stars, amount_coins, duration_days):
    try:
        end_date = datetime.now() + timedelta(days=duration_days)
        conn = sqlite3.connect(DATABASE_FILE)
        c = conn.cursor()
        c.execute("UPDATE users SET is_premium=1, premium_expires=?, premium_plan=? WHERE user_id=?",
                  (end_date.isoformat(), plan, user_id))
        c.execute("""INSERT INTO subscriptions
                     (user_id, plan, amount_stars, amount_coins, start_date, end_date, status)
                     VALUES (?,?,?,?,?,?,?)""",
                  (user_id, plan, amount_stars, amount_coins,
                   datetime.now().isoformat(), end_date.isoformat(), 'active'))
        conn.commit()
        conn.close()
        stopped_free = stop_free_deployments_for_user(user_id)
        resumed_dep_id, premium_resumed = resume_premium_deployment(user_id, duration_days)
        update_system_stats()
        return True, (1 if premium_resumed else 0)
    except Exception as e:
        print(f"❌ Activate premium error: {e}")
        return False, 0

# ========== SECURITY SCANNER ==========
class SecurityScanner:
    MAX_FILES = 1000
    MAX_TOTAL_SIZE = 100 * 1024 * 1024
    MAX_RECURSION = 4
    B64_MIN_LEN = 60
    
    CRITICAL_PYTHON = [
        r'eval\s*\(\s*base64\.b64decode',
        r'exec\s*\(\s*base64\.b64decode',
        r'exec\s*\(\s*__import__\s*\(',
        r'stratum\+tcp',
        r'xmrig|minergate|nicehash|coinhive',
        r'socket\.connect\([^)]+\).*exec\(',
        r'\.connect\(\(\s*["\'][0-9]+\.[0-9]+',
        r"os\.system\s*\(\s*['\"]rm\s+-rf\s+/",
        r"shutil\.rmtree\s*\(\s*['\"][/\\]",
        r'urllib\.request\.urlopen.*base64\.b64decode',
    ]
    CRITICAL_JS = [
        r'stratum\+tcp',
        r'xmrig|minergate|coinhive',
        r'child_process.*exec.*base64',
        r'eval\s*\(\s*Buffer\.from',
        r'exec\s*\(\s*require\s*\(\s*["\']child_process',
    ]
    CRITICAL_PKG_HOOKS = [
        r'curl\s+http',
        r'wget\s+http',
        r'\|\s*bash',
        r'\|\s*sh\b',
        r'python\s+-c\s+["\']import',
        r'node\s+-e\s+',
        r'base64\s+--decode',
        r'chmod\s+\+x',
    ]
    WARN_PYTHON = [
        r'\beval\s*\(',
        r'\bexec\s*\(',
        r'subprocess\.(Popen|call|check_output|run)\s*\(',
        r'os\.(system|popen)\s*\(',
        r'os\.(remove|unlink)\s*\(',
        r'shutil\.rmtree\s*\(',
        r'pickle\.loads\s*\(',
        r'marshal\.loads\s*\(',
        r'__import__\s*\(',
    ]
    WARN_JS = [
        r'\beval\s*\(',
        r'new\s+Function\s*\(',
        r'child_process\.(exec|spawn|execSync|spawnSync)\s*\(',
        r'fs\.(unlink|rmdir|rmdirSync|unlinkSync)\s*\(',
        r'require\s*\(\s*["\']child_process["\']\s*\)',
    ]
    SUPPORTED_BOT_EXTS = {
        '.py': 'python',
        '.js': 'javascript',
        '.mjs': 'javascript',
        '.cjs': 'javascript',
        '.ts': 'typescript',
        '.tsx': 'typescript',
        '.jsx': 'javascript',
    }
    ARCHIVE_EXTS = {'.zip', '.tar', '.gz', '.tgz', '.7z', '.rar'}
    
    def __init__(self):
        self._reset()
    
    def _reset(self):
        self.critical = []
        self.warnings = []
    
    def scan(self, file_bytes: bytes, filename: str) -> tuple[bool, list, list]:
        self._reset()
        filename = filename or 'unknown'
        ext = os.path.splitext(filename)[1].lower()
        if ext in self.ARCHIVE_EXTS:
            self._scan_archive(file_bytes, filename)
        else:
            c, w = self._scan_file(file_bytes, filename)
            self.critical.extend(c)
            self.warnings.extend(w)
        blocked = bool(self.critical)
        return blocked, self.critical[:], self.warnings[:]
    
    def _scan_archive(self, data: bytes, filename: str):
        import tempfile, shutil, zipfile, tarfile, io as _io
        ext = os.path.splitext(filename)[1].lower()
        tmp_dir = tempfile.mkdtemp(prefix='sec_scan_')
        try:
            if ext == '.zip':
                with zipfile.ZipFile(_io.BytesIO(data)) as zf:
                    self._check_zip(zf)
                    zf.extractall(tmp_dir)
            elif ext in ('.tar', '.gz', '.tgz'):
                with tarfile.open(fileobj=_io.BytesIO(data), mode='r:*') as tf:
                    self._check_tar(tf)
                    tf.extractall(tmp_dir)
            elif ext == '.rar':
                try:
                    import rarfile
                    with rarfile.RarFile(_io.BytesIO(data)) as rf:
                        self._check_rar(rf)
                        rf.extractall(tmp_dir)
                except ImportError:
                    self.warnings.append("RAR archive: cannot deep-scan")
                    return
            elif ext == '.7z':
                try:
                    import py7zr
                    with py7zr.SevenZipFile(_io.BytesIO(data)) as sz:
                        sz.extractall(tmp_dir)
                except ImportError:
                    self.warnings.append("7z archive: cannot deep-scan")
                    return
            
            for root, _dirs, files in os.walk(tmp_dir):
                for fname in files:
                    fpath = os.path.join(root, fname)
                    rel_path = os.path.relpath(fpath, tmp_dir)
                    if os.path.islink(fpath):
                        self.critical.append(f"Symlink in archive: `{rel_path}`")
                        continue
                    if '..' in rel_path or rel_path.startswith('/'):
                        self.critical.append(f"Path traversal in archive: `{rel_path}`")
                        continue
                    try:
                        with open(fpath, 'rb') as fp:
                            content = fp.read()
                        c, w = self._scan_file(content, fname, rel_path)
                        self.critical.extend(c)
                        self.warnings.extend(w)
                    except Exception as e:
                        self.warnings.append(f"Could not scan `{rel_path}`: {e}")
        except Exception as e:
            self.critical.append(f"Archive error: {e}")
        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)
    
    def _scan_file(self, data: bytes, filename: str, rel: str = None) -> tuple[list, list]:
        critical, warnings = [], []
        ext = os.path.splitext(filename)[1].lower()
        path = rel or filename
        
        if rel and ('..' in rel or rel.startswith('/')):
            critical.append(f"Path traversal: `{rel}`")
            return critical, warnings
        
        try:
            text = data.decode('utf-8', errors='ignore')
        except Exception:
            text = ''
        
        # Encoded content
        ec, ew = self._check_encoded(text, path)
        critical.extend(ec)
        warnings.extend(ew)
        
        lang = self.SUPPORTED_BOT_EXTS.get(ext, '')
        if lang == 'python':
            c, w = self._scan_python(data, text, path)
            critical.extend(c)
            warnings.extend(w)
        elif lang in ('javascript', 'typescript'):
            c, w = self._scan_js(text, path)
            critical.extend(c)
            warnings.extend(w)
        elif filename.lower() == 'package.json':
            c, w = self._scan_package_json(text)
            critical.extend(c)
            warnings.extend(w)
        elif text:
            c, w = self._scan_generic(text, path)
            critical.extend(c)
            warnings.extend(w)
        
        base = os.path.basename(filename).lower()
        if base in ('credentials.json', 'service_account.json', 'gcp_key.json'):
            warnings.append(f"Sensitive credential file: `{path}`")
        
        return critical, warnings
    
    def _scan_python(self, data: bytes, text: str, path: str) -> tuple[list, list]:
        critical, warnings = [], []
        for pat in self.CRITICAL_PYTHON:
            if re.search(pat, text, re.IGNORECASE | re.DOTALL):
                critical.append(f"[PY-CRITICAL] `{pat}` in `{path}`")
        for pat in self.WARN_PYTHON:
            if re.search(pat, text, re.IGNORECASE):
                warnings.append(f"[PY-WARN] `{pat}` in `{path}`")
        try:
            import ast as _ast
            tree = _ast.parse(text, mode='exec')
            for node in _ast.walk(tree):
                if not isinstance(node, _ast.Call):
                    continue
                func = node.func
                if isinstance(func, _ast.Name):
                    name = func.id
                    if name in ('eval', 'exec'):
                        if node.args and isinstance(node.args[0], _ast.Call):
                            critical.append(f"[AST] Obfuscated `{name}()` call in `{path}`")
                        else:
                            warnings.append(f"[AST] `{name}()` call in `{path}`")
                elif isinstance(func, _ast.Attribute):
                    attr = func.attr
                    obj_name = ''
                    if isinstance(func.value, _ast.Name):
                        obj_name = func.value.id
                    if attr in ('remove', 'unlink', 'rmdir') and obj_name == 'os':
                        warnings.append(f"[AST] `os.{attr}()` in `{path}`")
                    elif attr == 'rmtree' and obj_name == 'shutil':
                        warnings.append(f"[AST] `shutil.rmtree()` in `{path}`")
                    elif attr in ('Popen', 'call', 'check_output', 'run') and obj_name == 'subprocess':
                        warnings.append(f"[AST] `subprocess.{attr}()` in `{path}`")
                    elif attr in ('system', 'popen') and obj_name == 'os':
                        warnings.append(f"[AST] `os.{attr}()` in `{path}`")
                if isinstance(node, _ast.ImportFrom):
                    if node.module in ('subprocess', 'os'):
                        for alias in (node.names or []):
                            if alias.name in ('system', 'popen', 'remove', 'unlink',
                                              'Popen', 'call', 'check_output', 'rmtree'):
                                warnings.append(f"[AST] Dangerous import: `from {node.module} import {alias.name}` in `{path}`")
        except SyntaxError:
            pass
        except Exception:
            pass
        return critical, warnings
    
    def _scan_js(self, text: str, path: str) -> tuple[list, list]:
        critical, warnings = [], []
        if not text:
            return critical, warnings
        for pat in self.CRITICAL_JS:
            if re.search(pat, text, re.IGNORECASE | re.DOTALL):
                critical.append(f"[JS-CRITICAL] `{pat}` in `{path}`")
        for pat in self.WARN_JS:
            if re.search(pat, text, re.IGNORECASE):
                warnings.append(f"[JS-WARN] `{pat}` in `{path}`")
        if re.search(r'import\s*\(\s*atob\s*\(', text):
            critical.append(f"[TS-CRITICAL] Obfuscated dynamic import in `{path}`")
        if re.search(r'(stratum\+tcp|coinhive|cryptonight)', text, re.IGNORECASE):
            critical.append(f"[JS-CRITICAL] Crypto miner pattern in `{path}`")
        if re.search(r'(?i)(token|api_key|secret|password)\s*=\s*["\'][A-Za-z0-9_\-]{8,}', text):
            warnings.append(f"[JS] Possible hardcoded credential in `{path}`")
        return critical, warnings
    
    def _scan_package_json(self, text: str) -> tuple[list, list]:
        critical, warnings = [], []
        try:
            pkg = json.loads(text)
        except Exception:
            return critical, warnings
        scripts = pkg.get('scripts', {})
        dangerous_hooks = ('preinstall', 'postinstall', 'prepare', 'preuninstall', 'postuninstall')
        for hook in dangerous_hooks:
            cmd = scripts.get(hook, '')
            if not cmd:
                continue
            for pat in self.CRITICAL_PKG_HOOKS:
                if re.search(pat, cmd, re.IGNORECASE):
                    critical.append(f"[NPM-CRITICAL] Dangerous `{hook}` hook: `{cmd[:80]}`")
                    break
            else:
                if cmd.strip():
                    warnings.append(f"[NPM] `{hook}` hook detected: `{cmd[:80]}`")
        return critical, warnings
    
    def _scan_generic(self, text: str, path: str) -> tuple[list, list]:
        critical, warnings = [], []
        generic_critical = [r'stratum\+tcp', r'xmrig', r'minergate', r'rm\s+-rf\s+/']
        generic_warn = [r'\beval\s*\(', r'\bexec\s*\(', r'child_process']
        for pat in generic_critical:
            if re.search(pat, text, re.IGNORECASE):
                critical.append(f"[GENERIC-CRITICAL] `{pat}` in `{path}`")
        for pat in generic_warn:
            if re.search(pat, text, re.IGNORECASE):
                warnings.append(f"[GENERIC-WARN] `{pat}` in `{path}`")
        return critical, warnings
    
    def _check_encoded(self, text: str, path: str) -> tuple[list, list]:
        critical, warnings = [], []
        b64_pat = r'[A-Za-z0-9+/]{' + str(self.B64_MIN_LEN) + r',}={0,2}'
        for match in re.findall(b64_pat, text):
            try:
                import base64 as _b64
                decoded = _b64.b64decode(match + '==')
                decoded_text = decoded.decode('utf-8', errors='ignore')
                for pat in self.CRITICAL_PYTHON + self.CRITICAL_JS:
                    if re.search(pat, decoded_text, re.IGNORECASE):
                        critical.append(f"[ENCODED-CRITICAL] Dangerous content hidden in base64 in `{path}`")
                        break
            except Exception:
                pass
        hex_pat = r'(?<![0-9A-Fa-f])[0-9A-Fa-f]{80,}(?![0-9A-Fa-f])'
        for match in re.findall(hex_pat, text):
            try:
                decoded_text = bytes.fromhex(match).decode('utf-8', errors='ignore')
                for pat in self.CRITICAL_PYTHON:
                    if re.search(pat, decoded_text, re.IGNORECASE):
                        critical.append(f"[ENCODED-CRITICAL] Dangerous content in hex string in `{path}`")
                        break
            except Exception:
                pass
        if re.search(r'(?:%[0-9A-Fa-f]{2}){4,}', text):
            warnings.append(f"[ENCODED-WARN] URL-encoded block detected in `{path}`")
        return critical, warnings
    
    def _check_zip(self, zf):
        import zipfile
        total, n = 0, 0
        for info in zf.infolist():
            n += 1
            if n > self.MAX_FILES:
                raise Exception(f"ZIP contains >{self.MAX_FILES} files")
            total += info.file_size
            if total > self.MAX_TOTAL_SIZE:
                raise Exception("ZIP extracted size too large")
            name = info.filename
            if '..' in name or name.startswith('/') or name.startswith('\\'):
                raise Exception(f"Path traversal in ZIP: {name}")
    
    def _check_tar(self, tf):
        import tarfile
        total, n = 0, 0
        for m in tf:
            if m.isreg():
                n += 1
                if n > self.MAX_FILES:
                    raise Exception(f"TAR contains >{self.MAX_FILES} files")
                total += m.size
                if total > self.MAX_TOTAL_SIZE:
                    raise Exception("TAR extracted size too large")
            if m.issym():
                raise Exception(f"Symlink in TAR: {m.name}")
            if '..' in m.name or m.name.startswith('/'):
                raise Exception(f"Path traversal in TAR: {m.name}")
    
    def _check_rar(self, rf):
        total, n = 0, 0
        for info in rf.infolist():
            if not info.isdir():
                n += 1
                if n > self.MAX_FILES:
                    raise Exception(f"RAR contains >{self.MAX_FILES} files")
                total += info.file_size
                if total > self.MAX_TOTAL_SIZE:
                    raise Exception("RAR extracted size too large")
            if '..' in info.filename or info.filename.startswith('/'):
                raise Exception(f"Path traversal in RAR: {info.filename}")

SECURITY_SCANNER = SecurityScanner()

def run_security_scan(file_bytes: bytes, filename: str) -> tuple[bool, list, list, str]:
    try:
        blocked, critical, warnings = SECURITY_SCANNER.scan(file_bytes, filename)
    except Exception as e:
        return False, [], [f"Scanner error: {e}"], f"⚠️ Security scan encountered an error: {e}"
    
    lines = [f"🔒 Security Scan — `{display_filename(filename)}`\n"]
    if critical:
        lines.append(f"🔴 *{len(critical)} CRITICAL issue(s) — BLOCKED:*")
        for i in critical[:10]:
            lines.append(f"  • {i}")
        if len(critical) > 10:
            lines.append(f"  … and {len(critical)-10} more")
    if warnings:
        lines.append(f"\n🟡 *{len(warnings)} warning(s):*")
        for w in warnings[:8]:
            lines.append(f"  • {w}")
        if len(warnings) > 8:
            lines.append(f"  … and {len(warnings)-8} more")
    if not critical and not warnings:
        lines.append("✅ No issues found — file is clean")
    
    return blocked, critical, warnings, '\n'.join(lines)

# ========== TELEGRAM FUNCTIONS ==========
def send_message(chat_id, text, keyboard=None, parse_mode="Markdown"):
    url = f"{TELEGRAM_API}/sendMessage"
    text = str(text or '')[:4096]
    data = {"chat_id": chat_id, "text": text, "parse_mode": parse_mode}
    if keyboard:
        data["reply_markup"] = json.dumps(keyboard)
    for _attempt in range(3):
        try:
            req = urllib.request.Request(
                url, data=json.dumps(data).encode('utf-8'),
                headers={'Content-Type': 'application/json'})
            with urllib.request.urlopen(req, timeout=30) as resp:
                result = json.loads(resp.read().decode('utf-8'))
                return result
        except urllib.error.HTTPError as e:
            if e.code == 429:
                sleep(2 * (_attempt + 1))
                continue
            body = ''
            try: body = e.read().decode()[:200]
            except Exception: pass
            if parse_mode and "can't parse entities" in body.lower():
                data_plain = {k: v for k, v in data.items() if k != "parse_mode"}
                try:
                    req2 = urllib.request.Request(
                        url, data=json.dumps(data_plain).encode('utf-8'),
                        headers={'Content-Type': 'application/json'})
                    with urllib.request.urlopen(req2, timeout=30) as resp2:
                        return json.loads(resp2.read().decode('utf-8'))
                except Exception:
                    pass
            return None
        except Exception as e:
            print(f"Send error (attempt {_attempt+1}): {e}")
            if _attempt < 2:
                sleep(1)
    return None

def edit_message(chat_id, message_id, text, keyboard=None):
    if not message_id:
        send_message(chat_id, str(text or '')[:4096], keyboard)
        return None
    url = f"{TELEGRAM_API}/editMessageText"
    data = {"chat_id": chat_id, "message_id": message_id,
            "text": str(text or '')[:4096], "parse_mode": "Markdown"}
    if keyboard:
        data["reply_markup"] = json.dumps(keyboard)
    for _attempt in range(3):
        try:
            req = urllib.request.Request(
                url, data=json.dumps(data).encode('utf-8'),
                headers={'Content-Type': 'application/json'})
            with urllib.request.urlopen(req, timeout=30) as resp:
                return json.loads(resp.read().decode('utf-8'))
        except urllib.error.HTTPError as e:
            if e.code == 429:
                sleep(2 * (_attempt + 1))
                continue
            if e.code == 400:
                body = ''
                try: body = e.read().decode()[:200]
                except Exception: pass
                if "can't parse entities" in body.lower():
                    data_plain = {k: v for k, v in data.items() if k != "parse_mode"}
                    try:
                        req2 = urllib.request.Request(
                            url, data=json.dumps(data_plain).encode('utf-8'),
                            headers={'Content-Type': 'application/json'})
                        with urllib.request.urlopen(req2, timeout=30) as resp2:
                            return json.loads(resp2.read().decode('utf-8'))
                    except Exception:
                        pass
                return None
            print(f"Edit HTTP {e.code}")
            return None
        except Exception as e:
            print(f"Edit error (attempt {_attempt+1}): {e}")
            if _attempt < 2:
                sleep(1)
    return None

def answer_callback(callback_id, text=None, show_alert=False):
    url = f"{TELEGRAM_API}/answerCallbackQuery"
    data = {"callback_query_id": callback_id}
    if text:
        data["text"] = text
    if show_alert:
        data["show_alert"] = True
    try:
        data_bytes = json.dumps(data).encode('utf-8')
        req = urllib.request.Request(url, data=data_bytes, headers={'Content-Type': 'application/json'})
        urllib.request.urlopen(req, timeout=10)
    except Exception as e:
        print(f"Answer callback error: {e}")

def http_get(url, params=None):
    try:
        socket_timeout = 30
        if params and 'timeout' in params:
            socket_timeout = int(params['timeout']) + 10
        if params:
            url = f"{url}?{urllib.parse.urlencode(params)}"
        with urllib.request.urlopen(url, timeout=socket_timeout) as response:
            return json.loads(response.read().decode('utf-8'))
    except Exception as e:
        print(f"HTTP error: {e}")
        return None

_FILENAME_PREFIX_RE = re.compile(r'^\d{8}_\d{6}_temp_\d+_')

def display_filename(system_filename: str) -> str:
    if not system_filename:
        return system_filename
    return _FILENAME_PREFIX_RE.sub('', system_filename)

def format_file_size(size_bytes):
    if size_bytes == 0:
        return "0 B"
    size_names = ["B", "KB", "MB", "GB"]
    i = 0
    while size_bytes >= 1024 and i < len(size_names) - 1:
        size_bytes /= 1024.0
        i += 1
    return f"{size_bytes:.1f} {size_names[i]}"

def format_uptime(seconds):
    days = int(seconds // 86400)
    hours = int((seconds % 86400) // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = int(seconds % 60)
    if days > 0:
        return f"{days}d {hours}h {minutes}m"
    elif hours > 0:
        return f"{hours}h {minutes}m {secs}s"
    elif minutes > 0:
        return f"{minutes}m {secs}s"
    else:
        return f"{secs}s"

def notify_admin(message):
    for admin_id in ADMIN_IDS:
        try:
            send_message(admin_id, message)
        except Exception as e:
            print(f"Failed to notify admin {admin_id}: {e}")

# ========== CHANNEL VERIFICATION ==========
def check_channel_membership(user_id) -> bool:
    channel = (REQUIRED_CHANNEL or '').strip()
    if not channel or channel in ('', 'None', 'false', '0'):
        return True
    if not channel.startswith('@') and not channel.lstrip('-').isdigit():
        channel = '@' + channel
    url = f"{TELEGRAM_API}/getChatMember?chat_id={urllib.parse.quote(channel)}&user_id={user_id}"
    for attempt in range(3):
        try:
            req = urllib.request.Request(url)
            with urllib.request.urlopen(req, timeout=15) as r:
                data = json.loads(r.read().decode('utf-8'))
                if data.get('ok'):
                    status = (data.get('result') or {}).get('status', '')
                    return status in ('member', 'administrator', 'creator', 'restricted')
                desc = (data.get('description') or '').lower()
                if any(kw in desc for kw in ('bot is not', 'not a member', 'need admin', 'rights', 'forbidden')):
                    return True
                return False
        except urllib.error.HTTPError as e:
            body = ''
            try: body = e.read().decode()[:200]
            except Exception: pass
            if e.code in (400, 403):
                return True
            if attempt < 2:
                sleep(1)
        except Exception as e:
            print(f"⚠️ getChatMember attempt {attempt+1}: {e}")
            if attempt < 2:
                sleep(1)
    return True

def mark_channel_joined(user_id):
    try:
        conn = sqlite3.connect(DATABASE_FILE, timeout=10)
        c = conn.cursor()
        c.execute("INSERT OR IGNORE INTO users (user_id) VALUES (?)", (user_id,))
        c.execute('UPDATE users SET joined_channel = 1 WHERE user_id = ?', (user_id,))
        conn.commit()
        conn.close()
        return True
    except Exception as e:
        print(f"❌ mark_channel_joined: {e}")
        return False

def is_user_verified(user_id) -> bool:
    if is_admin(user_id):
        return True
    channel = (REQUIRED_CHANNEL or '').strip()
    if not channel or channel in ('', 'None', 'false', '0'):
        return True
    try:
        conn = sqlite3.connect(DATABASE_FILE, timeout=10)
        c = conn.cursor()
        c.execute('SELECT joined_channel FROM users WHERE user_id = ?', (user_id,))
        row = c.fetchone()
        conn.close()
        return bool(row and row[0])
    except Exception:
        return False

def send_verification_required(chat_id, user_id, first_name, message_id=None):
    channel = (REQUIRED_CHANNEL or '').strip() or 'our channel'
    link = (CHANNEL_LINK or '').strip()
    text = (f"*🔐 VERIFICATION REQUIRED*\n\n👋 Hi {first_name or 'there'}!\n\n"
            f"To use this hosting platform you must join our channel:\n📢 *{channel}*\n\n"
            f"*Steps:*\n1️⃣ Click *JOIN CHANNEL* below\n2️⃣ Join the channel\n"
            f"3️⃣ Come back and click *✅ VERIFY*")
    buttons = [{"text": "✅ VERIFY", "callback_data": "verify_channel"}]
    if link:
        buttons.insert(0, {"text": "📢 JOIN CHANNEL", "url": link})
    keyboard = {"inline_keyboard": [buttons]}
    if message_id:
        edit_message(chat_id, message_id, text, keyboard)
    else:
        send_message(chat_id, text, keyboard)

def has_accepted_tos(user_id) -> bool:
    if is_admin(user_id):
        return True
    try:
        conn = sqlite3.connect(DATABASE_FILE, timeout=10)
        c = conn.cursor()
        c.execute('SELECT tos_accepted FROM users WHERE user_id = ?', (user_id,))
        row = c.fetchone()
        conn.close()
        return bool(row and row[0])
    except Exception:
        return False

def mark_tos_accepted(user_id):
    try:
        conn = sqlite3.connect(DATABASE_FILE, timeout=10)
        c = conn.cursor()
        c.execute("INSERT OR IGNORE INTO users (user_id) VALUES (?)", (user_id,))
        c.execute('UPDATE users SET tos_accepted = 1 WHERE user_id = ?', (user_id,))
        conn.commit()
        conn.close()
        return True
    except Exception as e:
        print(f"❌ mark_tos_accepted: {e}")
        return False

def show_tos_prompt(chat_id, user_id, message_id=None):
    text = (f"*📜 TERMS OF SERVICE*\n\nBefore you can use this hosting platform, please read and accept:\n\n"
            f"1️⃣ This service provides infrastructure to run code you upload or link from GitHub — "
            f"we do not review, monitor, or endorse the content or purpose of anything you host.\n\n"
            f"2️⃣ *You are solely responsible for anything you deploy.* "
            f"You confirm you have the right to host it and that it does not violate any applicable law.\n\n"
            f"3️⃣ *We are not responsible for any illegal file, bot, or content hosted through this platform — "
            f"responsibility lies entirely with the user who uploaded or deployed it.*\n\n"
            f"4️⃣ We reserve the right to remove any deployment and suspend any account found to violate these terms, without notice.\n\n"
            f"5️⃣ Continued use of this bot after clicking Agree constitutes acceptance of these terms.\n\n👇 You must agree to continue.")
    keyboard = {"inline_keyboard": [[{"text": "✅ I Agree", "callback_data": "tos_agree"}]]}
    if message_id:
        edit_message(chat_id, message_id, text, keyboard)
    else:
        send_message(chat_id, text, keyboard)

# ========== REDEEM CODE FUNCTIONS ==========
def generate_redeem_code(length=12):
    alphabet = string.ascii_uppercase + string.digits
    return ''.join(secrets.choice(alphabet) for _ in range(length))

def create_redeem_code(admin_id, coins_amount=0, stars_amount=0, expiry_days=30, max_uses=1):
    code = generate_redeem_code()
    expires_at = datetime.now() + timedelta(days=expiry_days)
    conn = sqlite3.connect(DATABASE_FILE)
    c = conn.cursor()
    c.execute('''INSERT INTO redeem_codes 
        (code, coins_amount, stars_amount, created_by, created_at, expires_at, expiry_days, max_uses, is_active)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)''',
        (code, coins_amount, stars_amount, admin_id, datetime.now().isoformat(), 
         expires_at.isoformat(), expiry_days, max_uses, 1))
    conn.commit()
    conn.close()
    update_system_stats()
    return code

def redeem_code(user_id, code):
    if not is_user_verified(user_id):
        return False, f"❌ You must join {REQUIRED_CHANNEL} first!"
    conn = sqlite3.connect(DATABASE_FILE)
    c = conn.cursor()
    c.execute('''SELECT code_id, coins_amount, stars_amount, max_uses, used_count, expires_at, is_active 
                 FROM redeem_codes WHERE code = ?''', (code,))
    row = c.fetchone()
    if not row:
        conn.close()
        return False, "❌ Invalid redeem code!"
    code_id, coins_amount, stars_amount, max_uses, used_count, expires_at, is_active = row
    if datetime.fromisoformat(expires_at) < datetime.now():
        conn.close()
        return False, "❌ This redeem code has expired!"
    if not is_active:
        conn.close()
        return False, "❌ This redeem code is no longer active!"
    if max_uses > 0 and used_count >= max_uses:
        conn.close()
        return False, f"❌ This redeem code has reached its usage limit ({max_uses}/{max_uses})!"
    used_by = c.execute("SELECT used_by FROM redeem_codes WHERE code = ?", (code,)).fetchone()[0]
    if used_by and str(user_id) in used_by.split(','):
        conn.close()
        return False, "❌ You have already used this redeem code!"
    reward_msg = []
    if coins_amount > 0:
        update_user_coins(user_id, coins_amount, "redeem", f"code_{code}")
        reward_msg.append(f"{coins_amount} 🪙")
    if stars_amount > 0:
        update_user_stars(user_id, stars_amount, "redeem", f"code_{code}")
        reward_msg.append(f"{stars_amount} ⭐")
    new_used_count = used_count + 1
    new_used_by = f"{used_by},{user_id}" if used_by else str(user_id)
    c.execute('''UPDATE redeem_codes SET used_count = ?, used_by = ? WHERE code_id = ?''',
              (new_used_count, new_used_by, code_id))
    conn.commit()
    conn.close()
    update_system_stats()
    return True, f"✅ Redeemed: {', '.join(reward_msg)}!"

# ========== REFERRAL FUNCTIONS ==========
def get_or_create_referral_code(user_id):
    conn = sqlite3.connect(DATABASE_FILE)
    c = conn.cursor()
    c.execute("SELECT referral_code FROM users WHERE user_id = ?", (user_id,))
    row = c.fetchone()
    code = row[0] if row and row[0] else None
    if not code:
        code = f"ref{user_id}"
        c.execute("UPDATE users SET referral_code = ? WHERE user_id = ?", (code, user_id))
        conn.commit()
    conn.close()
    return code

def get_bot_username():
    try:
        with urllib.request.urlopen(f"{TELEGRAM_API}/getMe", timeout=10) as r:
            data = json.loads(r.read())
            return data.get("result", {}).get("username", "")
    except Exception:
        return ""

def process_referral(referrer_id, new_user_id):
    if referrer_id == new_user_id:
        return False
    conn = sqlite3.connect(DATABASE_FILE)
    c = conn.cursor()
    c.execute("SELECT user_id FROM users WHERE user_id = ?", (referrer_id,))
    if not c.fetchone():
        conn.close()
        return False
    try:
        c.execute(
            "INSERT INTO referrals (referrer_id, referred_id, created_at, reward_coins, reward_given) "
            "VALUES (?, ?, ?, ?, 0)",
            (referrer_id, new_user_id, datetime.now().isoformat(), REFERRAL_REWARD_COINS))
        c.execute("UPDATE users SET referred_by = ? WHERE user_id = ?", (referrer_id, new_user_id))
        c.execute("UPDATE users SET total_referrals = total_referrals + 1 WHERE user_id = ?", (referrer_id,))
        conn.commit()
        conn.close()
    except sqlite3.IntegrityError:
        conn.close()
        return False
    update_user_coins(referrer_id, REFERRAL_REWARD_COINS, "referral_reward", f"referred_{new_user_id}")
    conn2 = sqlite3.connect(DATABASE_FILE)
    conn2.execute("UPDATE referrals SET reward_given = 1 WHERE referred_id = ?", (new_user_id,))
    conn2.commit()
    conn2.close()
    return True

# ========== BUG REPORT FUNCTIONS ==========
def submit_bug_report(user_id, username, first_name, message_text):
    conn = sqlite3.connect(DATABASE_FILE)
    c = conn.cursor()
    c.execute(
        "INSERT INTO bug_reports (user_id, username, first_name, message, status, created_at) "
        "VALUES (?, ?, ?, ?, 'open', ?)",
        (user_id, username, first_name, message_text, datetime.now().isoformat()))
    report_id = c.lastrowid
    conn.commit()
    conn.close()
    user_link = f"@{username}" if username else f"User `{user_id}`"
    admin_text = (f"*🐛 NEW BUG REPORT #{report_id}*\n\nFrom: {user_link} (`{user_id}`)\n"
                  f"Time: `{datetime.now().strftime('%Y-%m-%d %H:%M')}`\n\n*Message:*\n{message_text}")
    admin_kb = {"inline_keyboard": [
        [{"text": f"✉️ Reply to #{report_id}", "callback_data": f"bug_reply_{report_id}"}],
        [{"text": f"✅ Close #{report_id}", "callback_data": f"bug_close_{report_id}"}]
    ]}
    for admin_id in ADMIN_IDS:
        send_message(admin_id, admin_text, admin_kb)
    return report_id

def reply_to_bug_report(report_id, admin_id, reply_text):
    conn = sqlite3.connect(DATABASE_FILE)
    c = conn.cursor()
    c.execute("SELECT user_id, username, first_name FROM bug_reports WHERE report_id = ?", (report_id,))
    row = c.fetchone()
    if not row:
        conn.close()
        return False, "Report not found"
    target_user_id, username, first_name = row
    c.execute(
        "UPDATE bug_reports SET status='replied', admin_reply=?, replied_by=?, replied_at=? "
        "WHERE report_id = ?",
        (reply_text, admin_id, datetime.now().isoformat(), report_id))
    conn.commit()
    conn.close()
    send_message(target_user_id,
        f"*✉️ REPLY TO YOUR BUG REPORT #{report_id}*\n\nAn admin has replied to your report:\n\n_{reply_text}_\n\nThank you! 🙏",
        {"inline_keyboard": [[{"text": "🐛 Submit Another Report", "callback_data": "report_bug"}],
                              [{"text": "🏠 Main Menu", "callback_data": "main_menu"}]]})
    return True, target_user_id

# ========== FRAMEWORK DETECTION ==========
_PLATFORM_PATTERNS = {
    'telegram_telebot': ['import telebot', 'from telebot', 'TeleBot('],
    'telegram_aiogram': ['import aiogram', 'from aiogram', 'Dispatcher(', 'Router()'],
    'telegram_ptb': ['from telegram', 'from telegram.ext', 'Application.builder', 'ApplicationBuilder'],
    'telegram_pyrogram': ['import pyrogram', 'from pyrogram', 'Client('],
    'telegram_telethon': ['import telethon', 'from telethon', 'TelegramClient('],
    'whatsapp_pywa': ['import pywa', 'from pywa', 'WhatsApp('],
    'whatsapp_heyoo': ['import heyoo', 'from heyoo', 'WhatsApp('],
    'whatsapp_twilio': ['from twilio', 'import twilio', 'MessagingResponse('],
    'whatsapp_cloud': ['WHATSAPP_TOKEN', 'PHONE_NUMBER_ID', 'graph.facebook.com', 'whatsapp/messages'],
    'discord_py': ['import discord', 'from discord', 'discord.Client(', 'commands.Bot('],
    'discord_nextcord': ['import nextcord', 'from nextcord'],
    'discord_disnake': ['import disnake', 'from disnake'],
    'discord_pycord': ['import py_cord', 'from py_cord'],
    'slack': ['from slack_sdk', 'import slack_sdk', 'from slack_bolt', 'import slack_bolt', 'WebClient('],
    'twitter': ['import tweepy', 'from tweepy', 'tweepy.Client', 'tweepy.API'],
    'line': ['from linebot', 'import linebot', 'LineBotApi(', 'WebhookHandler('],
    'viber': ['from viberbot', 'import viberbot', 'ViberApi('],
    'matrix': ['from nio import', 'import nio', 'matrix_client', 'AsyncClient('],
    'irc': ['import irc', 'from irc.bot', 'SingleServerIRCBot('],
    'flask': ['from flask import', 'import flask', 'Flask(__name__)'],
    'fastapi': ['from fastapi', 'import fastapi', 'FastAPI()'],
    'django': ['import django', 'from django', 'DJANGO_SETTINGS'],
    'aiohttp': ['import aiohttp', 'from aiohttp'],
    'starlette': ['from starlette', 'import starlette'],
}

_PLATFORM_LABELS = {
    'telegram_telebot': '📱 Telegram (pyTelegramBotAPI)',
    'telegram_aiogram': '📱 Telegram (aiogram)',
    'telegram_ptb': '📱 Telegram (python-telegram-bot)',
    'telegram_pyrogram': '📱 Telegram (Pyrogram)',
    'telegram_telethon': '📱 Telegram (Telethon)',
    'whatsapp_pywa': '💬 WhatsApp (PyWA)',
    'whatsapp_heyoo': '💬 WhatsApp (heyoo)',
    'whatsapp_twilio': '💬 WhatsApp (Twilio)',
    'whatsapp_cloud': '💬 WhatsApp (Cloud API)',
    'discord_py': '🎮 Discord (discord.py)',
    'discord_nextcord': '🎮 Discord (nextcord)',
    'discord_disnake': '🎮 Discord (disnake)',
    'discord_pycord': '🎮 Discord (py-cord)',
    'slack': '💼 Slack',
    'twitter': '🐦 Twitter/X (Tweepy)',
    'line': '💚 Line',
    'viber': '💜 Viber',
    'matrix': '🔷 Matrix',
    'irc': '📡 IRC',
    'flask': '🌐 Flask',
    'fastapi': '🌐 FastAPI',
    'django': '🌐 Django',
    'aiohttp': '🌐 aiohttp',
    'starlette': '🌐 Starlette',
}

PLATFORM_ENV_HINTS = {
    'telegram_telebot': '`BOT_TOKEN` — your Telegram bot token from @BotFather',
    'telegram_aiogram': '`BOT_TOKEN` — your Telegram bot token from @BotFather',
    'telegram_ptb': '`BOT_TOKEN` — your Telegram bot token from @BotFather',
    'telegram_pyrogram': '`API_ID`, `API_HASH`, `BOT_TOKEN` — from my.telegram.org + @BotFather',
    'telegram_telethon': '`API_ID`, `API_HASH` — from my.telegram.org',
    'whatsapp_pywa': '`WHATSAPP_TOKEN`, `PHONE_NUMBER_ID` — from Meta Developer Portal',
    'whatsapp_heyoo': '`WHATSAPP_TOKEN`, `PHONE_NUMBER_ID` — from Meta Developer Portal',
    'whatsapp_twilio': '`TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN`, `TWILIO_NUMBER` — from Twilio Console',
    'whatsapp_cloud': '`WHATSAPP_TOKEN`, `PHONE_NUMBER_ID`, `VERIFY_TOKEN` — from Meta Developer Portal',
    'discord_py': '`DISCORD_TOKEN` — from Discord Developer Portal → Bot → Token',
    'discord_nextcord': '`DISCORD_TOKEN` — from Discord Developer Portal → Bot → Token',
    'discord_disnake': '`DISCORD_TOKEN` — from Discord Developer Portal → Bot → Token',
    'discord_pycord': '`DISCORD_TOKEN` — from Discord Developer Portal → Bot → Token',
    'slack': '`SLACK_BOT_TOKEN`, `SLACK_APP_TOKEN`, `SLACK_SIGNING_SECRET` — from api.slack.com',
    'twitter': '`TWITTER_API_KEY`, `TWITTER_API_SECRET`, `TWITTER_BEARER_TOKEN` — from developer.twitter.com',
    'line': '`LINE_CHANNEL_ACCESS_TOKEN`, `LINE_CHANNEL_SECRET` — from Line Developer Console',
    'viber': '`VIBER_AUTH_TOKEN` — from Viber Admin Panel',
    'matrix': '`MATRIX_HOMESERVER`, `MATRIX_USER`, `MATRIX_PASSWORD` — from your Matrix server',
    'irc': '`IRC_SERVER`, `IRC_PORT`, `IRC_NICK`, `IRC_CHANNEL`',
    'flask': '`PORT` (optional, default 5000)',
    'fastapi': '`PORT` (optional, default 8000)',
    'django': '`SECRET_KEY`, `DEBUG`, `ALLOWED_HOSTS`',
}

def detect_bot_framework(code_content: str) -> list:
    detected = []
    code_lower = code_content.lower()
    for platform, patterns in _PLATFORM_PATTERNS.items():
        for pattern in patterns:
            if pattern.lower() in code_lower:
                detected.append(platform)
                break
    seen = set()
    result = []
    for p in detected:
        if p not in seen:
            seen.add(p)
            result.append(p)
    return result if result else ['generic']

def get_platform_label(frameworks: list) -> str:
    labels = [_PLATFORM_LABELS.get(fw, fw.replace('_', ' ').title())
              for fw in frameworks if fw != 'generic']
    return ', '.join(labels) if labels else '🤖 Generic Python Bot'

def get_platform_env_hint(frameworks: list) -> str:
    hints = []
    for fw in frameworks:
        hint = PLATFORM_ENV_HINTS.get(fw)
        if hint and hint not in hints:
            label = _PLATFORM_LABELS.get(fw, fw)
            hints.append(f"*{label}:*\n{hint}")
    return '\n\n'.join(hints) if hints else ''

# ========== DEPENDENCY INSTALLER ==========
def install_dependencies_enhanced(reqs_file, update_logs, packages_dir=None):
    if not reqs_file.exists():
        update_logs("✅ No requirements file found")
        return True, []
    try:
        with open(reqs_file, 'r') as f:
            raw = f.read().strip()
        if not raw:
            update_logs("⚠️ requirements.txt is empty")
            return True, []
        packages = [p.strip() for p in raw.split('\n')
                    if p.strip() and not p.startswith('#') and not p.startswith('-')]
        update_logs(f"📦 {len(packages)} package(s) to install")
        subprocess.run([sys.executable, "-m", "pip", "install", "--upgrade", "pip", "--quiet"],
                       capture_output=True, timeout=60)
        
        def _pip_cmd(pkg_or_flag, extra_flags=None):
            cmd = [sys.executable, "-m", "pip", "install", "--upgrade",
                   "--no-warn-script-location", "--quiet"]
            if packages_dir:
                cmd += ["--target", str(packages_dir)]
            if extra_flags:
                cmd += extra_flags
            cmd.append(pkg_or_flag)
            return cmd
        
        success_count = 0
        failed_packages = []
        for i, package in enumerate(packages):
            pct = int((i / len(packages)) * 100)
            update_logs(f"   ⬇️  {package[:60]}")
            cmd = _pip_cmd(package)
            res = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
            if res.returncode == 0:
                success_count += 1
                update_logs(f"   ✅ {package[:60]}")
            else:
                res2 = subprocess.run(
                    [sys.executable, "-m", "pip", "install", "--quiet", "--force-reinstall",
                     *(["--target", str(packages_dir)] if packages_dir else []),
                     package],
                    capture_output=True, text=True, timeout=180)
                if res2.returncode == 0:
                    success_count += 1
                    update_logs(f"   ✅ {package[:60]} (retry OK)")
                else:
                    failed_packages.append(package)
                    err_line = (res.stderr or res.stdout or "")[-200:].strip().split('\n')[-1]
                    update_logs(f"   ❌ {package[:50]}: {err_line[:80]}")
        if failed_packages:
            update_logs(f"⚠️ Failed ({len(failed_packages)}): {', '.join(p.split('==')[0][:20] for p in failed_packages[:5])}")
        update_logs(f"✅ {success_count}/{len(packages)} packages installed")
        return (success_count / len(packages)) >= 0.7 if packages else True, failed_packages
    except Exception as e:
        update_logs(f"❌ Installer error: {e}")
        return False, []

def install_from_repo_requirements(deploy_folder: Path, update_logs) -> list:
    packages_dir = deploy_folder / 'packages'
    packages_dir.mkdir(exist_ok=True)
    installed = []
    for fname in ['requirements.txt', 'requirements-dev.txt', 'requirements_prod.txt',
                  'requirements/base.txt', 'requirements/main.txt']:
        req_path = deploy_folder / fname
        if req_path.exists():
            update_logs(f"📋 Found {fname}")
            ok, failed = install_dependencies_enhanced(req_path, update_logs, packages_dir)
            installed.extend([l.strip() for l in req_path.read_text().splitlines()
                              if l.strip() and not l.startswith('#')])
    # pyproject.toml
    pyproject = deploy_folder / 'pyproject.toml'
    if pyproject.exists():
        update_logs("📋 Found pyproject.toml — installing with pip...")
        res = subprocess.run(
            [sys.executable, '-m', 'pip', 'install', '--quiet',
             '--no-warn-script-location', '--target', str(packages_dir), '.'],
            cwd=str(deploy_folder), capture_output=True, text=True, timeout=300)
        if res.returncode != 0:
            try:
                text = pyproject.read_text()
                deps = re.findall(r'^\s*"([a-zA-Z0-9_\-]+)[>=<!\[\]"]*"', text, re.M)
                deps += re.findall(r'^\s*([a-zA-Z0-9_\-]+)\s*=\s*["\^~]', text, re.M)
                skip = {'python', 'pip', 'setuptools', 'wheel'}
                deps = [d for d in dict.fromkeys(deps) if d.lower() not in skip]
                if deps:
                    update_logs(f"📦 Parsed {len(deps)} deps from pyproject.toml")
                    tmp = deploy_folder / '_pyproject_reqs.txt'
                    tmp.write_text('\n'.join(deps))
                    install_dependencies_enhanced(tmp, update_logs, packages_dir)
                    tmp.unlink(missing_ok=True)
                    installed.extend(deps)
            except Exception as pe:
                update_logs(f"⚠️ pyproject.toml parse error: {pe}")
    # Pipfile
    pipfile = deploy_folder / 'Pipfile'
    if pipfile.exists():
        update_logs("📋 Found Pipfile — extracting packages...")
        try:
            text = pipfile.read_text()
            in_packages = False
            pkgs = []
            for line in text.splitlines():
                if line.strip() in ('[packages]', '[dev-packages]'):
                    in_packages = True
                elif line.startswith('['):
                    in_packages = False
                elif in_packages:
                    m = re.match(r'^([a-zA-Z0-9_\-]+)\s*=', line)
                    if m:
                        pkgs.append(m.group(1))
            if pkgs:
                update_logs(f"📦 Parsed {len(pkgs)} deps from Pipfile")
                tmp = deploy_folder / '_pipfile_reqs.txt'
                tmp.write_text('\n'.join(pkgs))
                install_dependencies_enhanced(tmp, update_logs, packages_dir)
                tmp.unlink(missing_ok=True)
                installed.extend(pkgs)
        except Exception as pfe:
            update_logs(f"⚠️ Pipfile parse error: {pfe}")
    # setup.py / setup.cfg
    for setup_file in ['setup.py', 'setup.cfg']:
        sf = deploy_folder / setup_file
        if sf.exists():
            update_logs(f"📋 Found {setup_file} — installing package...")
            res = subprocess.run(
                [sys.executable, '-m', 'pip', 'install', '--quiet',
                 '--no-warn-script-location', '--target', str(packages_dir), '-e', '.'],
                cwd=str(deploy_folder), capture_output=True, text=True, timeout=300)
            if res.returncode == 0:
                update_logs(f"✅ {setup_file} installed")
            else:
                update_logs(f"⚠️ {setup_file} install failed")
            break
    return installed

# ========== LAUNCHER CREATION ==========
def create_enhanced_launcher_script(deploy_folder, dest_script, env_vars_dict, code_content, update_logs, packages_dir=None):
    frameworks = detect_bot_framework(code_content)
    framework_str = ', '.join(frameworks)
    update_logs(f"🔧 Framework detection: {framework_str}")
    launcher_script = deploy_folder / "run.py"
    env_set_code = []
    for k, v in env_vars_dict.items():
        env_set_code.append(f'os.environ[{k!r}] = {v!r}')
    env_set_str = "\n".join(env_set_code) if env_set_code else "# No custom environment variables"
    packages_dir_str = str(packages_dir) if packages_dir else str(deploy_folder / 'packages')
    with open(launcher_script, 'w', encoding='utf-8') as f:
        f.write(f'''#!/usr/bin/env python3
"""
UNIVERSAL BOT LAUNCHER - Detected frameworks: {framework_str}
"""
import os, sys, time, json, signal, threading, traceback
from pathlib import Path
_pkg_dir = r"{packages_dir_str}"
if os.path.isdir(_pkg_dir) and _pkg_dir not in sys.path:
    sys.path.insert(0, _pkg_dir)
os.chdir(r"{deploy_folder}")
_HOSTING_BOT_KEYS = {{
    'BOT_TOKEN', 'TELEGRAM_TOKEN', 'TELEGRAM_BOT_TOKEN',
    'TOKEN', 'API_TOKEN', 'TG_BOT_TOKEN', 'TGBOT_TOKEN',
    'DISCORD_TOKEN', 'SLACK_BOT_TOKEN', 'SLACK_APP_TOKEN',
    'GITHUB_TOKEN', 'GITHUB_REPO_OWNER', 'GITHUB_REPO_NAME',
    'GITHUB_BACKUP_BRANCH', 'GITHUB_BACKUP_PATH',
    'DATABASE_URL', 'DATABASE_FILE',
    'ADMIN_IDS', 'REQUIRED_CHANNEL', 'CHANNEL_LINK',
}}
for _hk in _HOSTING_BOT_KEYS:
    os.environ.pop(_hk, None)
{env_set_str}
try:
    from dotenv import load_dotenv
    env_file = Path(r"{deploy_folder}") / ".env"
    if env_file.exists():
        load_dotenv(env_file, override=False)
except ImportError:
    pass
except Exception:
    pass
heartbeat_running = True
def heartbeat():
    heartbeat_file = Path(r"{deploy_folder}") / ".heartbeat"
    while heartbeat_running:
        try:
            with open(heartbeat_file, 'w') as f:
                f.write(str(time.time()))
            time.sleep(30)
        except:
            pass
threading.Thread(target=heartbeat, daemon=True).start()
print("=" * 50)
print("🔧 LAUNCHER READY")
print("=" * 50)
print(f"Python: {{sys.version.split()[0]}}")
print(f"Dir: {{os.getcwd()}}")
sys.stdout.flush()
_SKIP_PREFIXES = ('KUBERNETES_', 'RENDER_', 'UV_', 'PIPENV_', 'NPM_', 'NODE_',
                   'BUN_', 'GUNICORN_', 'YARN_', 'DEFAULT_', 'PYTHON_')
_SKIP_EXACT = frozenset([
    'PATH', 'PYTHONPATH', 'HOME', 'USER', 'LOGNAME', 'SHELL', 'TERM',
    'LANG', 'LC_ALL', 'PWD', 'HOSTNAME', 'RENDER', 'PORT',
    'IS_PULL_REQUEST', 'RENDER_ROOT', 'RENDER_ENV_IS_DOCKER',
    'RENDER_SERVICE_ID', 'RENDER_SERVICE_NS', 'RENDER_SERVICE_CONTEXT_ROOT',
    'RENDER_EXTERNAL_HOSTNAME', 'RENDER_GIT_REPO_SLUG',
    'RENDER_NODE_VERSION_DETECTED', 'RENDER_NODE_INSTALLED',
    'RENDER_PRE_RUN_COMMAND', 'RENDER_CPU_COUNT', 'USER_RUN_COMMAND',
    'BLACK', 'BLUE', 'CYAN', 'YELLOW', 'RESET', 'ENTER_STANDOUT',
    'PYTHONUNBUFFERED', 'UV_COMPILE_BYTECODE', 'ENTER_BOLD',
])
_shown_keys = []
for _k, _v in os.environ.items():
    if _k in _SKIP_EXACT:
        continue
    _skip = False
    for _pfx in _SKIP_PREFIXES:
        if _k.startswith(_pfx):
            _skip = True
            break
    if _skip:
        continue
    _shown_keys.append(_k)
    if any(_s in _k.upper() for _s in ['TOKEN', 'SECRET', 'KEY', 'PASSWORD', 'HASH']):
        _disp = (_v[:8] + '...') if len(_v) > 8 else '***'
    else:
        _disp = (_v[:60] + '...') if len(_v) > 60 else _v
    print(f"  ✅ {{_k}} = {{_disp}}")
if not _shown_keys:
    print("  ℹ️  No custom env vars")
sys.stdout.flush()
print("=" * 50)
print("🚀 STARTING BOT")
print("=" * 50)
sys.stdout.flush()
_TOKEN_ALIASES = ['BOT_TOKEN', 'TELEGRAM_TOKEN', 'TELEGRAM_BOT_TOKEN',
                  'TOKEN', 'API_TOKEN', 'TG_BOT_TOKEN', 'TGBOT_TOKEN',
                  'DISCORD_TOKEN']
_found_token = None
for _alias in _TOKEN_ALIASES:
    _t = os.environ.get(_alias, '')
    if _t:
        _found_token = _t
        break
if _found_token:
    for _alias in _TOKEN_ALIASES:
        if not os.environ.get(_alias):
            os.environ[_alias] = _found_token
    print("✅ Token env vars bridged")
else:
    print("⚠️  No bot token found — set BOT_TOKEN or TELEGRAM_TOKEN in env vars")
# Method 1: Import and run
try:
    print("📌 Method 1: Importing as module...")
    sys.stdout.flush()
    sys.path.insert(0, r"{deploy_folder}")
    import importlib.util, inspect as _inspect, socket as _socket, threading as _thr
    spec = importlib.util.spec_from_file_location("user_bot", r"{dest_script}")
    if spec is None:
        raise ImportError(f"Cannot load module from {dest_script}")
    module = importlib.util.module_from_spec(spec)
    sys.modules["user_bot"] = module
    _webhook_patches = []
    try:
        import telebot as _tb_mod
        _orig_sw = _tb_mod.TeleBot.set_webhook
        def _noop_set_webhook(self, *a, **kw):
            print("  ℹ️  [launcher] set_webhook() suppressed")
        _tb_mod.TeleBot.set_webhook = _noop_set_webhook
        _webhook_patches.append((_tb_mod.TeleBot, 'set_webhook', _orig_sw))
    except ImportError:
        pass
    try:
        spec.loader.exec_module(module)
    finally:
        for _obj, _attr, _orig in _webhook_patches:
            setattr(_obj, _attr, _orig)
    print("✅ Module loaded successfully")
    sys.stdout.flush()
    def _free_port():
        with _socket.socket(_socket.AF_INET, _socket.SOCK_STREAM) as _s:
            _s.bind(('', 0))
            return _s.getsockname()[1]
    def _module_has_telegram_bot():
        _poll_methods = ('polling', 'run_polling', 'start_polling', 'infinity_polling')
        try:
            _names = list(vars(module).keys())
        except Exception:
            _names = []
        for _n in _names:
            if _n.startswith('__'):
                continue
            try:
                _obj = getattr(module, _n, None)
            except Exception:
                continue
            if _obj is None or _obj is module:
                continue
            if type(_obj).__name__ == 'LocalProxy':
                continue
            try:
                if any(callable(getattr(_obj, _m, None)) for _m in _poll_methods):
                    return True
            except Exception:
                continue
        return False
    def _clear_webhook(bot_obj, label='bot'):
        import time as _time, asyncio as _aio, inspect as _inspect
        def _resolve(value):
            if _inspect.iscoroutine(value):
                return _aio.run(value)
            return value
        try:
            get_info = getattr(bot_obj, 'get_webhook_info', None)
            if callable(get_info):
                _wh = _resolve(get_info())
                _url = getattr(_wh, 'url', '') or ''
                if _url:
                    print(f"  ⚠️ Active webhook on {{label}}: {{_url[:60]}}...")
                    remove_fn = (getattr(bot_obj, 'remove_webhook', None) or
                                 getattr(bot_obj, 'delete_webhook', None))
                    if callable(remove_fn):
                        _resolve(remove_fn())
                    _time.sleep(1)
                    print(f"  ✅ Webhook removed")
                else:
                    print(f"  ✅ No webhook set on {{label}}")
            elif hasattr(bot_obj, 'bot') and callable(getattr(bot_obj.bot, 'get_webhook_info', None)):
                _clear_webhook(bot_obj.bot, label + '.bot')
        except Exception as _we:
            print(f"  ⚠️ Could not check/remove webhook ({{_we}})")
    def _make_runner(name, obj):
        _cls = type(obj).__name__.lower()
        _cls_mod = (getattr(type(obj), '__module__', '') or '').lower()
        if callable(getattr(obj, 'polling', None)) and callable(getattr(obj, 'stop_polling', None)):
            print(f"  📱 {{name}} → Telegram (pyTelegramBotAPI) → polling")
            def _run_telebot(_b=obj, _n=name):
                _clear_webhook(_b, _n)
                _b.polling(non_stop=True, timeout=60, long_polling_timeout=60)
            return _run_telebot
        if hasattr(obj, 'run_polling') and hasattr(obj, 'run_webhook'):
            print(f"  📱 {{name}} → Telegram (aiogram) → run_polling")
            def _run_aiogram(_a=obj, _n=name):
                _clear_webhook(_a, _n)
                _a.run_polling()
            return _run_aiogram
        if hasattr(obj, 'run_polling') and hasattr(obj, 'initialize'):
            print(f"  📱 {{name}} → Telegram (PTB) → run_polling")
            def _run_ptb(_a=obj, _n=name):
                _clear_webhook(_a, _n)
                _a.run_polling()
            return _run_ptb
        if callable(getattr(obj, 'run_polling', None)):
            print(f"  📱 {{name}} → has run_polling()")
            def _run_genpoll(_a=obj, _n=name):
                _clear_webhook(_a, _n)
                _a.run_polling()
            return _run_genpoll
        if _cls in ('client',) and 'pyrogram' in _cls_mod:
            print(f"  📱 {{name}} → Telegram (Pyrogram) → run")
            return lambda: obj.run()
        if 'telethon' in _cls_mod and hasattr(obj, 'run_until_disconnected'):
            print(f"  📱 {{name}} → Telegram (Telethon)")
            return lambda: obj.run_until_disconnected()
        if 'pywa' in _cls_mod or (hasattr(obj, 'run_forever') and 'whatsapp' in str(type(obj)).lower()):
            print(f"  💬 {{name}} → WhatsApp (PyWA) → run_forever")
            return lambda: obj.run_forever()
        _is_discord = ('discord' in _cls_mod or 'nextcord' in _cls_mod
                       or 'disnake' in _cls_mod or 'py_cord' in _cls_mod
                       or 'pycord' in _cls_mod)
        if _is_discord and callable(getattr(obj, 'run', None)):
            _tok = (os.environ.get('DISCORD_TOKEN') or os.environ.get('TOKEN') or os.environ.get('BOT_TOKEN', ''))
            if _tok:
                print(f"  🎮 {{name}} → Discord → .run(token)")
                return lambda: obj.run(_tok)
            print(f"  ⚠️ Discord object found but no DISCORD_TOKEN env var set")
        if 'slack' in _cls_mod and callable(getattr(obj, 'start', None)):
            _port = int(os.environ.get('PORT', _free_port()))
            print(f"  💼 {{name}} → Slack Bolt → .start(port={{_port}})")
            return lambda: obj.start(port=_port)
        if 'tweepy' in _cls_mod:
            if callable(getattr(obj, 'filter', None)):
                print(f"  🐦 {{name}} → Twitter/X (Tweepy Stream) → .filter()")
                return lambda: obj.filter()
            if callable(getattr(obj, 'sample', None)):
                print(f"  🐦 {{name}} → Twitter/X (Tweepy) → .sample()")
                return lambda: obj.sample()
        if 'nio' in _cls_mod and callable(getattr(obj, 'sync_forever', None)):
            print(f"  🔷 {{name}} → Matrix (nio) → sync_forever")
            return lambda: obj.sync_forever()
        if 'irc' in _cls_mod and callable(getattr(obj, 'start', None)):
            print(f"  📡 {{name}} → IRC → .start()")
            return lambda: obj.start()
        if _cls in ('flask', 'quart') or 'flask' in _cls_mod or 'quart' in _cls_mod:
            if _module_has_telegram_bot():
                _port = _free_port()
                print(f"  🌐 {{name}} → Flask keep-alive thread (port {{_port}})")
                def _flask_bg(_app=obj, _p=_port):
                    try:
                        _app.run(host='0.0.0.0', port=_p, debug=False, use_reloader=False)
                    except Exception as _fe:
                        print(f"  ⚠️ Flask keep-alive: {{_fe}}")
                _thr.Thread(target=_flask_bg, daemon=True, name='FlaskKeepAlive').start()
                return 'BACKGROUND'
            else:
                _port = int(os.environ.get('PORT', os.environ.get('BOT_PORT', 0)) or _free_port())
                print(f"  🌐 {{name}} → Flask/Quart → .run(port={{_port}})")
                return lambda: obj.run(host='0.0.0.0', port=_port, debug=False, use_reloader=False)
        if _cls in ('fastapi', 'starlette') or 'fastapi' in _cls_mod or 'starlette' in _cls_mod:
            _port = int(os.environ.get('PORT', _free_port()))
            print(f"  🌐 {{name}} → FastAPI/Starlette → uvicorn (port={{_port}})")
            def _run_asgi():
                try:
                    import uvicorn
                    uvicorn.run(obj, host='0.0.0.0', port=_port, log_level='info')
                except ImportError:
                    try:
                        import asyncio, hypercorn.asyncio as _ha, hypercorn.config as _hc
                        _cfg = _hc.Config(); _cfg.bind = [f'0.0.0.0:{{_port}}']
                        asyncio.run(_ha.serve(obj, _cfg))
                    except ImportError:
                        print("❌ uvicorn/hypercorn not found — add uvicorn to requirements.txt")
                        sys.exit(1)
            return _run_asgi
        if 'django' in _cls_mod:
            _port = int(os.environ.get('PORT', _free_port()))
            print(f"  🌐 {{name}} → Django → runserver port {{_port}}")
            import subprocess as _sp
            return lambda: _sp.run([sys.executable, 'manage.py', 'runserver',
                                     f'0.0.0.0:{{_port}}', '--noreload'],
                                    cwd=str(os.getcwd()))
        if _inspect.iscoroutinefunction(obj):
            import asyncio as _aio
            print(f"  ⚡ {{name}} → async function → asyncio.run")
            return lambda: _aio.run(obj())
        if _inspect.isfunction(obj) or _inspect.isbuiltin(obj) or _inspect.ismethod(obj):
            return None
        if callable(getattr(obj, 'run', None)):
            print(f"  ▶️  {{name}} → has .run()")
            return lambda: obj.run()
        if callable(obj):
            return None
        return None
    entry_points = ['main', 'run', 'start', 'bot', 'dp', 'dispatcher',
                    'updater', 'setup', 'client', 'app', 'application']
    for entry in entry_points:
        if not hasattr(module, entry):
            continue
        attr = getattr(module, entry)
        if not callable(attr):
            continue
        print(f"✅ Entry point: {{entry}}")
        sys.stdout.flush()
        runner = _make_runner(entry, attr)
        if runner == 'BACKGROUND':
            continue
        try:
            if runner is not None:
                runner()
            else:
                result = attr()
                if result is not None:
                    sub = _make_runner(f"{{entry}}() result", result)
                    if sub and sub != 'BACKGROUND':
                        sub()
                    elif callable(getattr(result, 'polling', None)):
                        result.polling(non_stop=True, timeout=60)
                    elif callable(getattr(result, 'run_polling', None)):
                        result.run_polling()
                    elif callable(getattr(result, 'run', None)):
                        result.run()
                    elif callable(getattr(result, 'run_forever', None)):
                        result.run_forever()
                    elif callable(getattr(result, 'serve_forever', None)):
                        result.serve_forever()
        except KeyboardInterrupt:
            print("\\n🛑 Bot stopped by user")
        except SystemExit as _se:
            sys.exit(_se.code)
        except Exception as _err:
            print(f"❌ Error in {{entry}}: {{type(_err).__name__}}: {{_err}}")
            traceback.print_exc()
            sys.stdout.flush()
            heartbeat_running = False
            sys.exit(1)
        heartbeat_running = False
        sys.exit(0)
    print("⚠️ No recognised entry point — falling through to Method 2...")
    sys.stdout.flush()
except Exception as e:
    print(f"⚠️ Import method failed: {{type(e).__name__}}: {{e}}")
    traceback.print_exc()
    sys.stdout.flush()
# Method 2: Subprocess
print("📌 Method 2: Running as subprocess...")
sys.stdout.flush()
import subprocess
try:
    _child_env = os.environ.copy()
    if os.path.isdir(_pkg_dir):
        _existing_pp = _child_env.get('PYTHONPATH', '')
        _child_env['PYTHONPATH'] = _pkg_dir + (os.pathsep + _existing_pp if _existing_pp else '')
    result = subprocess.run([sys.executable, r"{dest_script}"], env=_child_env)
    sys.exit(result.returncode)
except KeyboardInterrupt:
    print("\\n🛑 Stopped by user")
    sys.exit(0)
except Exception as e:
    print(f"❌ Subprocess failed: {{e}}")
    sys.exit(1)
''')
    launcher_script.chmod(0o755)
    return launcher_script, frameworks

def create_node_launcher_script(deploy_folder: Path, dest_script: Path,
                                env_vars_dict: dict, update_logs) -> tuple:
    ext = dest_script.suffix.lower()
    if ext in ('.ts', '.tsx'):
        run_cmd = (f'if command -v npx &>/dev/null; then\n'
                   f'    setsid npx --yes tsx "{dest_script}" >> output.log 2>&1 &\n'
                   f'elif command -v ts-node &>/dev/null; then\n'
                   f'    setsid ts-node "{dest_script}" >> output.log 2>&1 &\n'
                   f'else\n'
                   f'    echo "❌ No TypeScript runner found" >> output.log\n'
                   f'    exit 1\n'
                   f'fi')
        framework = ['typescript']
    elif ext in ('.mjs',):
        run_cmd = f'setsid node --input-type=module "{dest_script}" >> output.log 2>&1 &'
        framework = ['node_esm']
    else:
        run_cmd = f'setsid node "{dest_script}" >> output.log 2>&1 &'
        framework = ['node']
    try:
        code = dest_script.read_text(errors='ignore')
        if 'discord' in code.lower():
            framework.append('discord_js')
        if 'telegraf' in code.lower() or 'node-telegram' in code.lower():
            framework.append('telegram_node')
        if 'whatsapp' in code.lower() or 'baileys' in code.lower():
            framework.append('whatsapp_node')
        if 'express' in code.lower() or 'fastify' in code.lower():
            framework.append('web_server')
    except Exception:
        pass
    env_file = deploy_folder / '.env'
    env_file.write_text('\n'.join(f'{k}={v}' for k, v in env_vars_dict.items()) + '\n')
    def _sh_quote(value):
        return "'" + str(value).replace("'", "'\\''") + "'"
    env_lines = '\n'.join(f'export {k}={_sh_quote(v)}' for k, v in env_vars_dict.items())
    npm_install = ('if [ -f "package.json" ]; then\n'
                   '    echo "📦 Installing npm packages..." >> output.log\n'
                   '    npm install --no-audit --no-fund >> output.log 2>&1\n'
                   'fi')
    start_content = (f'#!/bin/bash\n'
                     f'cd "{deploy_folder}"\n'
                     f'export PYTHONUNBUFFERED=1\n'
                     f'{env_lines}\n\n'
                     f'{npm_install}\n\n'
                     f'{run_cmd}\n'
                     f'echo $! > pid.txt\n')
    start_script = deploy_folder / 'start.sh'
    start_script.write_text(start_content)
    start_script.chmod(0o755)
    update_logs(f"🟨 Node.js launcher created ({', '.join(framework)})")
    return start_script, framework

def _find_available_port(preferred: int = 20000) -> int:
    import socket, random
    candidates = [preferred] + random.sample(
        range(max(20000, preferred - 500), min(39999, preferred + 500)), 40
    )
    for port in candidates:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                s.bind(('', port))
                return port
            except OSError:
                continue
    return preferred + 1

# ========== RESTART FUNCTIONS (WITH FILE RETRIEVAL) ==========
def restart_deployment_by_id(deployment_id: int, user_id: int, is_auto_restart: bool = False) -> bool:
    """
    Restart a deployment by retrieving files from:
    - GitHub repo (if source_type='github')
    - Telegram file IDs (if source_type='upload')
    """
    try:
        # Get deployment info
        conn = sqlite3.connect(DATABASE_FILE)
        c = conn.cursor()
        c.execute("""SELECT file_name, file_id, requirements_file_id, requirements_text, env_vars,
                           folder_name, source_type, github_repo, github_branch, proc_pid, is_free, plan
                    FROM deployments WHERE deployment_id=? AND user_id=?""", 
                  (deployment_id, user_id))
        row = c.fetchone()
        if not row:
            conn.close()
            return False
        
        file_name, file_id, req_file_id, req_text, env_vars_json, folder_name, source_type, github_repo, github_branch, old_pid, is_free, plan = row
        
        # Kill old process
        if old_pid:
            try:
                kill_deployment_process(old_pid)
            except Exception:
                pass
        
        deploy_folder = get_deploy_folder(user_id, deployment_id)
        
        # Recreate folder if it doesn't exist
        if not deploy_folder.exists():
            deploy_folder.mkdir(parents=True, exist_ok=True)
        
        # ========== RETRIEVE FILES ==========
        if source_type == 'github' and github_repo:
            # GitHub deployment: re-download from repo
            try:
                owner, repo = github_repo.split('/')
                token = GITHUB_TOKEN
                
                # Get env vars from deployment
                env_vars = json.loads(env_vars_json) if env_vars_json else {}
                
                # Download repo
                from urllib.request import urlopen
                import tarfile, io
                
                url = f"https://api.github.com/repos/{owner}/{repo}/tarball/{github_branch or 'main'}"
                headers = {"User-Agent": "BotHostingPlatform/1.0", "Accept": "application/vnd.github+json"}
                if token:
                    headers["Authorization"] = f"Bearer {token}"
                
                req = urllib.request.Request(url, headers=headers)
                with urllib.request.urlopen(req, timeout=120) as resp:
                    tarball = resp.read()
                
                # Extract
                with tarfile.open(fileobj=io.BytesIO(tarball), mode='r:gz') as tar:
                    members = tar.getmembers()
                    if members:
                        prefix = members[0].name.split('/')[0] + '/'
                    else:
                        prefix = ''
                    
                    for member in members:
                        rel = member.name
                        if rel.startswith(prefix):
                            rel = rel[len(prefix):]
                        if not rel:
                            continue
                        member.name = rel
                        if rel.startswith('/') or '..' in rel:
                            continue
                        try:
                            tar.extract(member, deploy_folder)
                        except Exception:
                            pass
                
                # Find main file
                dest_script = None
                for candidate in ['main.py', 'bot.py', 'app.py', 'run.py', 'start.py', 'index.py']:
                    test_path = deploy_folder / candidate
                    if test_path.exists():
                        dest_script = test_path
                        break
                if not dest_script:
                    py_files = list(deploy_folder.glob('*.py'))
                    if py_files:
                        dest_script = py_files[0]
                
                if not dest_script:
                    conn.close()
                    return False
                
                file_name = dest_script.name
                file_id = None  # Not needed for GitHub
                
            except Exception as e:
                print(f"❌ GitHub re-download error: {e}")
                conn.close()
                return False
        
        else:
            # Upload deployment: retrieve from Telegram file IDs
            # Download main file
            if file_id:
                success, msg = download_telegram_file(file_id, deploy_folder / file_name)
                if not success:
                    print(f"❌ Failed to download main file: {msg}")
                    conn.close()
                    return False
            else:
                # Try to find existing file
                dest_script = deploy_folder / file_name
                if not dest_script.exists():
                    conn.close()
                    return False
            
            # Download requirements file if it exists
            if req_file_id:
                req_path = deploy_folder / 'requirements.txt'
                success, msg = download_telegram_file(req_file_id, req_path)
                if success:
                    print(f"✅ Requirements file downloaded")
                # If req_text exists, use it as fallback
                elif req_text:
                    with open(deploy_folder / 'requirements.txt', 'w') as f:
                        f.write(req_text)
        
        # ========== READ ENV VARS ==========
        env_vars = json.loads(env_vars_json) if env_vars_json else {}
        dest_script = deploy_folder / file_name
        
        if not dest_script.exists():
            conn.close()
            return False
        
        # ========== READ CODE ==========
        try:
            code_content = dest_script.read_text(errors='ignore')
        except Exception:
            code_content = ""
        
        # ========== INSTALL DEPENDENCIES ==========
        packages_dir = deploy_folder / 'packages'
        packages_dir.mkdir(exist_ok=True)
        
        req_file = deploy_folder / 'requirements.txt'
        if req_file.exists():
            install_dependencies_enhanced(req_file, print, packages_dir)
        elif req_text:
            req_file.write_text(req_text)
            install_dependencies_enhanced(req_file, print, packages_dir)
        
        # Auto-detect imports
        if code_content:
            try:
                import re as _re
                # Simple import detection
                imports = set()
                for line in code_content.split('\n'):
                    match = _re.match(r'^(?:from|import)\s+([a-zA-Z0-9_\.]+)', line.strip())
                    if match:
                        module = match.group(1).split('.')[0]
                        imports.add(module)
                
                # Known mapping
                pkg_map = {
                    'telebot': 'pyTelegramBotAPI', 'telegram': 'python-telegram-bot',
                    'aiogram': 'aiogram', 'flask': 'flask', 'fastapi': 'fastapi',
                    'discord': 'discord.py', 'requests': 'requests', 'dotenv': 'python-dotenv',
                }
                to_install = [pkg_map.get(m, m) for m in imports if m in pkg_map]
                if to_install:
                    tmp = deploy_folder / '_auto_reqs.txt'
                    tmp.write_text('\n'.join(to_install))
                    install_dependencies_enhanced(tmp, print, packages_dir)
                    tmp.unlink(missing_ok=True)
            except Exception as e:
                print(f"⚠️ Auto-detect error: {e}")
        
        # ========== CREATE LAUNCHER ==========
        ext = dest_script.suffix.lower()
        is_node = ext in ('.js', '.mjs', '.cjs', '.ts', '.tsx', '.jsx')
        
        if is_node:
            start_script, frameworks = create_node_launcher_script(
                deploy_folder, dest_script, env_vars, print)
        else:
            launcher_script, frameworks = create_enhanced_launcher_script(
                deploy_folder, dest_script, env_vars, code_content, print,
                packages_dir=packages_dir)
            start_script = deploy_folder / "start.sh"
            start_script.write_text(
                f'#!/bin/bash\ncd "{deploy_folder}"\nexport PYTHONUNBUFFERED=1\n'
                f'setsid nohup {sys.executable} "{launcher_script}" > output.log 2>&1 &\necho $! > pid.txt\n')
            start_script.chmod(0o755)
        
        # ========== START THE BOT ==========
        subprocess.run([str(start_script)], cwd=str(deploy_folder), capture_output=True)
        sleep(5)
        
        # ========== CHECK IF RUNNING ==========
        pid_file = deploy_folder / "pid.txt"
        new_pid = None
        if pid_file.exists():
            try:
                new_pid = int(pid_file.read_text().strip())
            except Exception:
                pass
        
        is_running = False
        if new_pid:
            try:
                os.kill(new_pid, 0)
                is_running = True
            except Exception:
                pass
        
        if is_running:
            # Update database with new PID
            conn2 = sqlite3.connect(DATABASE_FILE)
            conn2.execute("""UPDATE deployments 
                             SET proc_pid=?, status='active', is_paused=0, 
                                 crash_restart_count=COALESCE(crash_restart_count,0)+1,
                                 last_crash_restart=?
                             WHERE deployment_id=?""",
                          (new_pid, datetime.now().isoformat(), deployment_id))
            conn2.commit()
            conn2.close()
            
            with deployment_lock:
                active_deployments[deployment_id] = new_pid
            
            if not is_auto_restart:
                send_message(user_id, f"✅ *Bot #{deployment_id} Restarted Successfully!*\n\nFile: `{file_name}`\nPID: `{new_pid}`")
            
            update_deployment_resources(deployment_id)
            return True
        else:
            conn2 = sqlite3.connect(DATABASE_FILE)
            conn2.execute("UPDATE deployments SET status='failed' WHERE deployment_id=?", (deployment_id,))
            conn2.commit()
            conn2.close()
            
            if not is_auto_restart:
                log_file = deploy_folder / "output.log"
                log_tail = ""
                if log_file.exists():
                    log_tail = log_file.read_text(errors='replace')[-500:]
                send_message(user_id, f"❌ *Bot #{deployment_id} Failed to Start*\n\n```\n{log_tail}\n```")
            
            return False
    
    except Exception as e:
        print(f"❌ Restart error: {e}")
        traceback.print_exc()
        return False

def restart_deployment(deployment_id, user_id, chat_id, force_free_downgrade=False):
    """User-facing restart wrapper"""
    success = restart_deployment_by_id(deployment_id, user_id, is_auto_restart=False)
    if not success:
        # Check if expired and offer options
        conn = sqlite3.connect(DATABASE_FILE)
        c = conn.cursor()
        c.execute("SELECT expire_time, is_free, plan FROM deployments WHERE deployment_id=?", (deployment_id,))
        row = c.fetchone()
        conn.close()
        
        if row and row[0]:
            expire_time = datetime.fromisoformat(row[0])
            if expire_time < datetime.now():
                is_free = row[1]
                plan = row[2] if len(row) > 2 else 'free'
                if is_free:
                    send_message(chat_id,
                        f"⏰ *Free Bot #{deployment_id} Expired*\n\n"
                        f"Restart for another 24h — your database is intact.",
                        {"inline_keyboard": [[{"text": "🔄 Restart (Free 24h)", "callback_data": f"restart_deploy_{deployment_id}"}]]})
                else:
                    send_message(chat_id,
                        f"⏰ *Premium Bot #{deployment_id} Expired*\n\n"
                        f"Reactivate Premium or continue as free.",
                        {"inline_keyboard": [
                            [{"text": "⭐ Reactivate Premium", "callback_data": f"reactivate_premium_{deployment_id}"}],
                            [{"text": "🆓 Continue Free", "callback_data": f"restart_free_{deployment_id}"}],
                        ]})
                return False
        send_message(chat_id, f"❌ Failed to restart deployment #{deployment_id}")
    return success

# ========== DEPLOYMENT FUNCTIONS ==========
def deploy_with_logs_enhanced(chat_id, user_id, temp_file, requirements_text, env_vars, 
                     plan, duration, cost_coins, cost_stars, payment_method, is_free=False):
    if plan == "lifetime" and not is_admin(user_id):
        send_message(chat_id, "⛔ Lifetime deployments are admin-only.")
        return False
    
    status_msg = send_message(chat_id, "```\n🚀 STARTING DEPLOYMENT\n```", None)
    if not status_msg:
        return False
    status_message_id = status_msg.get('result', {}).get('message_id')
    
    logs = []
    def update_logs(new_log):
        logs.append(new_log)
        display_logs = logs[-25:]
        log_text = "\n".join(display_logs)
        if status_message_id:
            try:
                edit_message(chat_id, status_message_id, 
                            f"```\n🚀 DEPLOYMENT IN PROGRESS\n\n{log_text[-3000:]}\n```", None)
            except:
                pass
    
    try:
        update_logs("📁 Creating deployment folder...")
        deploy_id = int(datetime.now().timestamp())
        deploy_folder = DEPLOYMENTS_DIR / str(user_id) / str(deploy_id)
        deploy_folder.mkdir(parents=True, exist_ok=True)
        packages_dir = deploy_folder / 'packages'
        packages_dir.mkdir(exist_ok=True)
        
        # Save file with proper naming
        temp_path = Path(temp_file)
        file_name = temp_path.name
        dest_script = deploy_folder / file_name
        shutil.copy2(temp_path, dest_script)
        file_size = dest_script.stat().st_size
        update_logs(f"📄 File saved: {file_name} ({format_file_size(file_size)})")
        
        # Get file ID from Telegram for later retrieval
        # Store the file_id in the database for restart purposes
        # We'll get this from the message that sent the file
        file_id = None
        req_file_id = None
        req_text = requirements_text
        
        ext = dest_script.suffix.lower()
        is_node = ext in ('.js', '.mjs', '.cjs', '.ts', '.tsx', '.jsx')
        
        # Parse env vars
        env_vars_dict = {}
        if env_vars:
            if isinstance(env_vars, dict):
                env_vars_dict = env_vars
            elif isinstance(env_vars, str):
                for line in env_vars.strip().split('\n'):
                    line = line.strip()
                    if '=' in line:
                        first_eq = line.find('=')
                        key = line[:first_eq].strip()
                        value = line[first_eq+1:].strip()
                        if key:
                            env_vars_dict[key] = value
        
        # Read code
        with open(dest_script, 'r', encoding='utf-8') as f:
            code_content = f.read()
        
        # Security scan
        update_logs("🔒 Running security scan...")
        try:
            scan_bytes = dest_script.read_bytes()
            blocked, critical, scan_w, scan_report = run_security_scan(scan_bytes, file_name)
            if blocked:
                update_logs(f"🚫 SECURITY BLOCK: {len(critical)} critical issue(s)")
                edit_message(chat_id, status_message_id,
                    f"🚫 *Security Block*\n\n{scan_report}\n\nRemove flagged patterns and retry.",
                    {"inline_keyboard": [[{"text": "🏠 Menu", "callback_data": "main_menu"}]]})
                return False
            elif scan_w:
                update_logs(f"⚠️ Security scan: {len(scan_w)} warning(s) — proceeding")
        except Exception as se:
            update_logs(f"⚠️ Security scan error: {se}")
        
        # Detect dependencies
        update_logs("🔍 Scanning for dependencies...")
        requirements_list = []
        
        if is_node:
            # Node.js
            if requirements_text and requirements_text.strip().startswith('{'):
                try:
                    pkg_data = json.loads(requirements_text)
                    (deploy_folder / "package.json").write_text(json.dumps(pkg_data, indent=2))
                    dep_count = len(pkg_data.get('dependencies', {})) + len(pkg_data.get('devDependencies', {}))
                    update_logs(f"📦 Using uploaded package.json ({dep_count} package(s))")
                except Exception:
                    requirements_text = None
            if not (requirements_text and requirements_text.strip().startswith('{')):
                # Auto-detect JS deps
                js_pkgs = scan_js_requires(code_content)
                if js_pkgs:
                    build_package_json(deploy_folder, js_pkgs, dest_script.name, update_logs)
                else:
                    update_logs("ℹ️ No external npm packages detected")
            deps_success, failed = True, []
        else:
            # Python
            if requirements_text and requirements_text.strip():
                requirements_list = scan_requirements_file(requirements_text, update_logs)
                # Auto-detect missing imports
                _already = {re.split(r'[=<>!~\[\s]', r.strip(), 1)[0].lower() for r in requirements_list}
                _auto = scan_imports(code_content, update_logs)
                _gap_filled = [a for a in _auto
                               if re.split(r'[=<>!~\[\s]', a.strip(), 1)[0].lower() not in _already]
                if _gap_filled:
                    requirements_list.extend(_gap_filled)
                    update_logs(f"📦 Added {len(_gap_filled)} import(s) missing from requirements.txt")
            else:
                auto_detected = scan_imports(code_content, update_logs)
                if auto_detected:
                    requirements_list.extend(auto_detected)
                    update_logs(f"📦 Auto-detected {len(auto_detected)} package(s)")
            
            # Install dependencies
            deps_success, failed = True, []
            if requirements_list:
                reqs_file = deploy_folder / "requirements.txt"
                with open(reqs_file, 'w') as f:
                    f.write('\n'.join(requirements_list))
                deps_success, failed = install_dependencies_enhanced(reqs_file, update_logs, packages_dir)
        
        # Create .env
        env_file = deploy_folder / ".env"
        with open(env_file, 'w') as f:
            for k, v in env_vars_dict.items():
                f.write(f"{k}={v}\n")
        update_logs(f"📝 Created .env with {len(env_vars_dict)} variables")
        
        # Assign unique port
        _hosting_port = int(os.environ.get('PORT', 10000))
        _user_port = int(env_vars_dict.get('PORT', 0) or 0)
        if _user_port == 0 or _user_port == _hosting_port:
            _deploy_port = _find_available_port(20000 + (deploy_id % 9000))
            env_vars_dict['PORT'] = str(_deploy_port)
            env_vars_dict['BOT_PORT'] = str(_deploy_port)
            with open(env_file, 'w') as f:
                for k, v in env_vars_dict.items():
                    f.write(f"{k}={v}\n")
            update_logs(f"🔌 Assigned port {_deploy_port}")
        
        # Create launcher
        update_logs("🚀 Creating launcher...")
        if is_node:
            start_script, frameworks = create_node_launcher_script(
                deploy_folder, dest_script, env_vars_dict, update_logs)
            launcher_script = start_script
        else:
            launcher_script, frameworks = create_enhanced_launcher_script(
                deploy_folder, dest_script, env_vars_dict, code_content, update_logs,
                packages_dir=packages_dir)
            start_script = deploy_folder / "start.sh"
            start_script.write_text(
                f'#!/bin/bash\ncd "{deploy_folder}"\nexport PYTHONUNBUFFERED=1\n'
                f'setsid nohup {sys.executable} "{launcher_script}" > output.log 2>&1 &\necho $! > pid.txt\n')
            start_script.chmod(0o755)
        
        # Start bot
        update_logs("🚀 Starting bot process...")
        subprocess.run([str(start_script)], cwd=str(deploy_folder), capture_output=True, text=True)
        update_logs("⏳ Waiting for bot to initialize (10s)...")
        sleep(10)
        
        # Check if running
        pid_file = deploy_folder / "pid.txt"
        proc_pid = None
        if pid_file.exists():
            try:
                proc_pid = int(pid_file.read_text().strip())
            except:
                pass
        
        is_running = False
        if proc_pid:
            try:
                os.kill(proc_pid, 0)
                is_running = True
            except:
                pass
        
        # Show logs
        log_file = deploy_folder / "output.log"
        if log_file.exists() and log_file.stat().st_size > 0:
            with open(log_file, 'r', errors='replace') as f:
                raw = f.read()
            all_lines = [l for l in raw.split('\n') if l.strip()]
            tail_lines = all_lines[-20:]
            update_logs("📋 Bot output (last lines):")
            for line in tail_lines:
                update_logs(f"   {line[:120]}")
        
        if is_running:
            start_time = datetime.now()
            is_lifetime = (plan == "lifetime")
            if is_lifetime:
                expire_time = None
            elif is_free:
                expire_time = start_time + timedelta(hours=FREE_DEPLOYMENT_DURATION_HOURS)
            else:
                expire_time = start_time + timedelta(days=duration)
            
            # Store file IDs for retrieval on restart
            # Note: We'd need to capture file_id from the upload message
            # For this implementation, we'll store the file_id if available
            file_id_to_store = None
            req_file_id_to_store = None
            
            conn = sqlite3.connect(DATABASE_FILE)
            c = conn.cursor()
            c.execute('''INSERT INTO deployments 
                (user_id, file_name, file_size, file_id, requirements_file_id, requirements_text, env_vars, 
                 plan, payment_method, cost_coins, cost_stars, 
                 start_time, expire_time, status, proc_pid, install_log, deploy_log, 
                 is_free, is_paused, framework, dependencies_installed, folder_name, source_type)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)''',
                (user_id, file_name, file_size, file_id_to_store, req_file_id_to_store, req_text or "",
                 json.dumps(env_vars_dict),
                 plan, payment_method, cost_coins, cost_stars,
                 start_time.isoformat(), expire_time.isoformat() if expire_time else None, "active", proc_pid,
                 "\n".join(logs[-100:]), "Bot running",
                 1 if is_free else 0, 0,
                 ', '.join(frameworks), json.dumps(requirements_list), str(deploy_id), "upload"))
            deployment_db_id = c.lastrowid
            conn.commit()
            conn.close()
            
            Path(temp_file).unlink(missing_ok=True)
            set_user_step(user_id, None)
            
            with deployment_lock:
                active_deployments[deployment_db_id] = proc_pid
            
            expiry_line = "📅 *Expires:* `Never (lifetime)`" if is_lifetime \
                else f"📅 *Expires:* {expire_time.strftime('%Y-%m-%d %H:%M')}"
            duration_line = "⏱️ *Duration:* Lifetime" if is_lifetime \
                else f"⏱️ *Duration:* {duration if not is_free else FREE_DEPLOYMENT_DURATION_HOURS} {'days' if not is_free else 'hours'}"
            
            success_text = (f"*🎉 DEPLOYMENT SUCCESSFUL!*\n\n"
                f"📁 *File:* `{display_filename(file_name)}`\n"
                f"🤖 *Platform:* {get_platform_label(frameworks)}\n"
                f"📋 *Plan:* {plan.upper()}\n"
                f"{duration_line}\n"
                f"{expiry_line}\n"
                f"📦 *Dependencies:* {len(requirements_list)} package(s)\n"
                f"🔧 *Env Vars:* {len(env_vars_dict)}\n"
                f"🆔 *ID:* `{deployment_db_id}`")
            
            if failed:
                success_text += f"\n\n⚠️ *Partial Success:* {len(failed)} package(s) failed"
            
            edit_message(chat_id, status_message_id, success_text,
                {"inline_keyboard": [
                    [{"text": "📄 Runtime Logs", "callback_data": f"view_runtime_logs_{deployment_db_id}"}],
                    [{"text": "🔄 Restart", "callback_data": f"restart_deploy_{deployment_db_id}"}],
                    [{"text": "🗑️ Delete", "callback_data": f"delete_deploy_{deployment_db_id}"}],
                    [{"text": "📦 My Deployments", "callback_data": "my_deployments"}],
                    [{"text": "🏠 Menu", "callback_data": "main_menu"}]
                ]})
            
            update_system_stats()
            update_deployment_resources(deployment_db_id)
            return True
        else:
            error_tail = ""
            if log_file.exists() and log_file.stat().st_size > 0:
                with open(log_file, 'r', errors='replace') as f:
                    raw = f.read()
                error_tail = raw[-3000:].strip()
            
            diagnosis = ""
            if error_tail:
                low = error_tail.lower()
                if 'no bot token' in low or 'bot_token' in low and 'not' in low:
                    diagnosis = "\n\n💡 *Tip:* Set `BOT_TOKEN` in env vars"
                elif 'modulenotfounderror' in low or 'importerror' in low:
                    m = re.search(r"No module named '([^']+)'", error_tail)
                    pkg = m.group(1) if m else "unknown"
                    diagnosis = f"\n\n💡 *Tip:* Missing package `{pkg}` — add to requirements.txt"
                elif 'syntaxerror' in low:
                    diagnosis = "\n\n💡 *Tip:* Python syntax error — test locally first"
            
            error_msg = f"❌ *DEPLOYMENT FAILED*\n\n"
            if error_tail:
                error_msg += f"```\n{error_tail}\n```{diagnosis}"
            else:
                error_msg += "No output captured. Check your bot code."
            
            edit_message(chat_id, status_message_id, error_msg[:4000])
            return False
            
    except Exception as e:
        error_msg = f"❌ *DEPLOYMENT FAILED*\n\nException: {str(e)}"
        edit_message(chat_id, status_message_id, error_msg[:4000])
        Path(temp_file).unlink(missing_ok=True)
        return False

def deploy_free_bot_with_logs(chat_id, user_id, temp_file, requirements_text, env_vars):
    if not is_user_verified(user_id):
        send_verification_required(chat_id, user_id, "User", None)
        return False
    can_deploy, reason = can_use_free_deployment(user_id)
    if not can_deploy:
        send_message(chat_id, f"❌ *FREE DEPLOYMENT LIMIT REACHED*\n\n{reason}",
                    {"inline_keyboard": [[{"text": "💰 Get Premium", "callback_data": "subscribe_premium"}]]})
        return False
    return deploy_with_logs_enhanced(chat_id, user_id, temp_file, requirements_text, env_vars,
                           "free", FREE_DEPLOYMENT_DURATION_HOURS, 0, 0, "none", is_free=True)

def deploy_paid_bot(chat_id, user_id, temp_file, requirements_text, env_vars, plan, duration, cost_coins, cost_stars, payment_method):
    if not is_user_verified(user_id):
        send_verification_required(chat_id, user_id, "User", None)
        return False
    if is_user_premium(user_id) or is_admin(user_id):
        send_message(chat_id, f"*✨ PREMIUM BENEFIT ACTIVE!*\n\nYour {plan.upper()} deployment is *FREE*!")
        return deploy_with_logs_enhanced(chat_id, user_id, temp_file, requirements_text, env_vars,
                               plan, duration, 0, 0, "premium_free", is_free=False)
    else:
        return deploy_with_logs_enhanced(chat_id, user_id, temp_file, requirements_text, env_vars,
                               plan, duration, cost_coins, cost_stars, payment_method, is_free=False)

def delete_deployment(deployment_id, user_id, chat_id):
    try:
        conn = sqlite3.connect(DATABASE_FILE)
        c = conn.cursor()
        c.execute("SELECT proc_pid, file_name, user_id, is_free FROM deployments WHERE deployment_id = ?", (deployment_id,))
        row = c.fetchone()
        if not row:
            send_message(chat_id, "❌ Deployment not found")
            return False
        proc_pid, file_name, owner_id, is_free = row
        if owner_id != user_id and not is_admin(user_id):
            send_message(chat_id, "❌ Permission denied")
            return False
        if proc_pid:
            try:
                kill_deployment_process(proc_pid)
                sleep(1)
            except:
                pass
        deploy_folder = get_deploy_folder(owner_id, deployment_id)
        if deploy_folder.exists():
            shutil.rmtree(deploy_folder)
        c.execute("DELETE FROM deployments WHERE deployment_id = ?", (deployment_id,))
        conn.commit()
        conn.close()
        with deployment_lock:
            if deployment_id in active_deployments:
                del active_deployments[deployment_id]
        update_system_stats()
        send_message(chat_id, f"✅ Deployment `{deployment_id}` deleted!")
        return True
    except Exception as e:
        send_message(chat_id, f"❌ Error deleting deployment: {str(e)}")
        return False

# ========== USER STEP FUNCTIONS ==========
def set_user_step(user_id, step, **kwargs):
    conn = sqlite3.connect(DATABASE_FILE)
    c = conn.cursor()
    c.execute("INSERT OR IGNORE INTO users (user_id) VALUES (?)", (user_id,))
    _KNOWN = {'temp_file', 'requirements', 'env_vars', 'plan', 'payment_method',
              'duration', 'cost_coins', 'cost_stars', 'waiting_for_env',
              'waiting_for_reqs', 'waiting_for_redeem', 'temp_target_user',
              'temp_coins_amount', 'temp_stars_amount', 'temp_expiry', 'temp_reward_type'}
    updates = ["step = ?"]
    values = [step]
    for key in _KNOWN:
        if key in kwargs:
            updates.append(f"{key} = ?")
            val = kwargs[key]
            if key == 'env_vars' and val is not None and not isinstance(val, str):
                val = json.dumps(val)
            values.append(val)
    if step is None:
        pending = json.dumps({})
    else:
        c.execute("SELECT pending_json FROM users WHERE user_id = ?", (user_id,))
        row = c.fetchone()
        try:
            existing = json.loads(row[0] or '{}') if row else {}
        except Exception:
            existing = {}
        existing.update(kwargs)
        existing['_step'] = step
        pending = json.dumps(existing)
    updates.append("pending_json = ?")
    values.append(pending)
    values.append(user_id)
    c.execute(f"UPDATE users SET {', '.join(updates)} WHERE user_id = ?", values)
    conn.commit()
    conn.close()

def get_user_step(user_id):
    conn = sqlite3.connect(DATABASE_FILE)
    c = conn.cursor()
    c.execute("""SELECT step, temp_file, requirements, env_vars, plan, payment_method,
                        duration, cost_coins, cost_stars, waiting_for_env,
                        waiting_for_reqs, waiting_for_redeem, temp_target_user,
                        temp_coins_amount, temp_stars_amount, temp_expiry,
                        temp_reward_type, pending_json
                 FROM users WHERE user_id = ?""", (user_id,))
    row = c.fetchone()
    conn.close()
    _DEFAULTS = {
        'step': None, 'temp_file': None, 'requirements': None, 'env_vars': {},
        'plan': None, 'payment_method': None, 'duration': None,
        'cost_coins': None, 'cost_stars': None,
        'waiting_for_env': 0, 'waiting_for_reqs': 0, 'waiting_for_redeem': 0,
        'temp_target_user': None, 'temp_coins_amount': None,
        'temp_stars_amount': None, 'temp_expiry': None, 'temp_reward_type': None,
    }
    if not row:
        return _DEFAULTS.copy()
    try:
        pj = json.loads(row[17] or '{}')
    except Exception:
        pj = {}
    result = {**_DEFAULTS, **pj}
    col_map = ['step','temp_file','requirements','env_vars','plan','payment_method',
               'duration','cost_coins','cost_stars','waiting_for_env','waiting_for_reqs',
               'waiting_for_redeem','temp_target_user','temp_coins_amount',
               'temp_stars_amount','temp_expiry','temp_reward_type']
    for i, key in enumerate(col_map):
        val = row[i]
        if val is None:
            continue
        if key == 'env_vars':
            try:
                val = json.loads(val)
            except Exception:
                val = {}
        elif key in ('waiting_for_env','waiting_for_reqs','waiting_for_redeem'):
            val = int(val or 0)
        result[key] = val
    return result

# ========== HANDLER FUNCTIONS ==========
def handle_start(chat_id, user_id, username, first_name, start_param=""):
    is_new = False
    conn = sqlite3.connect(DATABASE_FILE)
    c = conn.cursor()
    c.execute("SELECT user_id FROM users WHERE user_id = ?", (user_id,))
    is_new = c.fetchone() is None
    c.execute("INSERT OR IGNORE INTO users (user_id, username, first_name, join_date) VALUES (?, ?, ?, ?)",
              (user_id, username, first_name, datetime.now().isoformat()))
    c.execute("UPDATE users SET last_active = ?, username = ?, first_name = ? WHERE user_id = ?",
              (datetime.now().isoformat(), username, first_name, user_id))
    conn.commit()
    conn.close()
    if is_new and start_param and start_param.startswith("ref"):
        try:
            referrer_id = int(start_param[3:])
            credited = process_referral(referrer_id, user_id)
            if credited:
                send_message(referrer_id,
                    f"🎉 *Referral Bonus!*\n\nYou earned *{REFERRAL_REWARD_COINS} 🪙* coins.",
                    {"inline_keyboard": [[{"text": "👥 My Referrals", "callback_data": "my_referral"}]]})
        except Exception:
            pass
    update_system_stats()
    get_or_create_referral_code(user_id)
    if is_user_verified(user_id):
        if not has_accepted_tos(user_id):
            show_tos_prompt(chat_id, user_id)
            return
        balances = get_user_balances(user_id)
        is_premium = is_user_premium(user_id)
        used_free = get_free_deployment_used_count(user_id)
        free_remaining = FREE_USER_MAX_DEPLOYMENTS - used_free
        stats = get_system_stats()
        server_start = stats.get('server_start_time')
        uptime = 0
        if server_start:
            start_time = datetime.fromisoformat(server_start)
            uptime = (datetime.now() - start_time).total_seconds()
        premium_badge = "⭐ PREMIUM ⭐" if is_premium else "🆓 FREE"
        conn = sqlite3.connect(DATABASE_FILE)
        c = conn.cursor()
        c.execute("SELECT COUNT(*) FROM deployments WHERE user_id = ? AND status = 'paused'", (user_id,))
        paused_count = c.fetchone()[0] or 0
        conn.close()
        welcome = (f"*🤖 BOT HOSTING SERVICE*\n\n━━━━━━━━━━━━━━━━━━━━━━\n"
                   f"👤 Welcome *{first_name}*!\n🪙 Coins: `{balances['coins']}`\n⭐ Stars: `{balances['stars']}`\n"
                   f"🎫 Status: {premium_badge}\n🆓 Free Slots: `{free_remaining}/{FREE_USER_MAX_DEPLOYMENTS}`\n"
                   f"⏸️ Paused: `{paused_count}`\n━━━━━━━━━━━━━━━━━━━━━━\n\n"
                   f"🖥️ Server Uptime: `{format_uptime(uptime)}`\n"
                   f"💱 Exchange: `1⭐ = {STARS_PER_COIN}🪙`\n\n"
                   f"*⭐ Premium Benefits:*\n• Unlimited free deployments (24h)\n• FREE Monthly/Yearly deployments\n"
                   f"💰 Monthly: `{PRICE_MONTHLY_STARS}⭐` / `{PRICE_MONTHLY_COINS}🪙`\n"
                   f"💰 Yearly: `{PRICE_YEARLY_STARS}⭐` / `{PRICE_YEARLY_COINS}🪙`\n"
                   f"📦 Max file size: `{MAX_FILE_SIZE_MB}MB`\n\nChoose an option:")
        send_message(chat_id, welcome, get_main_menu(user_id))
    else:
        send_verification_required(chat_id, user_id, first_name)

def get_main_menu(user_id):
    balances = get_user_balances(user_id)
    is_verified = is_user_verified(user_id)
    is_premium = is_user_premium(user_id)
    verified_badge = "✅" if is_verified else "🔐"
    premium_badge = "⭐" if is_premium else "🆓"
    keyboard = {
        "inline_keyboard": [
            [{"text": f"{verified_badge} Join Channel", "callback_data": "check_verification"}],
            [{"text": "📤 Deploy New Bot", "callback_data": "deploy_new"}],
            [{"text": "🐙 Deploy from GitHub", "callback_data": "github_deploy"}],
            [{"text": f"{premium_badge} Free Deployment (24h)", "callback_data": "free_deployment"}],
            [{"text": "📦 My Deployments", "callback_data": "my_deployments"}],
            [{"text": f"💰 {balances['coins']}🪙 | {balances['stars']}⭐", "callback_data": "my_balance"}],
            [{"text": "🎫 Redeem Code", "callback_data": "redeem_code"},
             {"text": "👥 Referral", "callback_data": "my_referral"}],
            [{"text": "⭐ Premium Subscription", "callback_data": "subscribe_premium"},
             {"text": "🐛 Report Bug", "callback_data": "report_bug"}],
        ]
    }
    if is_admin(user_id):
        keyboard["inline_keyboard"].append([{"text": "🔧 Admin Panel", "callback_data": "admin_panel"}])
    return keyboard

def get_deploy_menu(user_id):
    is_premium = is_user_premium(user_id)
    if is_premium or is_admin(user_id):
        keyboard = {
            "inline_keyboard": [
                [{"text": "📅 Monthly (30 days) - FREE for Premium", "callback_data": "plan_monthly"}],
                [{"text": "🌟 Yearly (365 days) - FREE for Premium", "callback_data": "plan_yearly"}],
                [{"text": "🆓 Free Deployment (24h)", "callback_data": "free_deployment"}],
                [{"text": "🔙 Back to Menu", "callback_data": "main_menu"}]
            ]
        }
    else:
        keyboard = {
            "inline_keyboard": [
                [{"text": f"📅 Monthly (30 days) - {PRICE_MONTHLY_STARS}⭐ / {PRICE_MONTHLY_COINS}🪙", "callback_data": "plan_monthly"}],
                [{"text": f"🌟 Yearly (365 days) - {PRICE_YEARLY_STARS}⭐ / {PRICE_YEARLY_COINS}🪙", "callback_data": "plan_yearly"}],
                [{"text": "🆓 Free Deployment (24h)", "callback_data": "free_deployment"}],
                [{"text": "⭐ Get Premium", "callback_data": "subscribe_premium"}],
                [{"text": "🔙 Back to Menu", "callback_data": "main_menu"}]
            ]
        }
    if is_admin(user_id):
        keyboard["inline_keyboard"].insert(-1, [{"text": "♾️ Lifetime (never expires) — ADMIN", "callback_data": "plan_lifetime"}])
    return keyboard

def get_reqs_keyboard():
    return {"inline_keyboard": [
        [{"text": "📦 Send requirements.txt", "callback_data": "reqs_yes"}],
        [{"text": "⚡ Auto-detect & Skip", "callback_data": "reqs_no"}],
        [{"text": "❌ Cancel Deployment", "callback_data": "cancel_deploy"}]
    ]}

def get_env_keyboard(env_count):
    return {"inline_keyboard": [
        [{"text": f"📝 View Variables ({env_count})", "callback_data": "view_env"}],
        [{"text": "📤 Send KEY=VALUE (one per line)", "callback_data": "env_send"}],
        [{"text": "⏭️ SKIP - No Environment Variables", "callback_data": "env_skip"}],
        [{"text": "✅ DEPLOY NOW", "callback_data": "env_done"}],
        [{"text": "❌ Cancel", "callback_data": "cancel_deploy"}]
    ]}

# ========== SCAN FUNCTIONS ==========
def scan_js_requires(code_content: str) -> list:
    found = set()
    for m in re.finditer(r'require\(\s*[\'"]([^\'"./][^\'"]*)[\'"]\s*\)', code_content):
        found.add(m.group(1))
    for m in re.finditer(r'''\bfrom\s+['"]([^'"./][^'"]*)['"]''', code_content):
        found.add(m.group(1))
    packages = set()
    for name in found:
        if name.startswith('@'):
            parts = name.split('/')
            pkg = '/'.join(parts[:2]) if len(parts) >= 2 else name
        else:
            pkg = name.split('/')[0]
        if pkg in {'fs','path','http','https','os','crypto','util','events','stream','url',
                   'querystring','child_process','assert','buffer','net','dns','tls','zlib',
                   'readline','process','timers','cluster','worker_threads','perf_hooks','v8',
                   'vm','module','dgram'} or pkg.startswith('node:'):
            continue
        packages.add(pkg)
    return sorted(packages)

def build_package_json(deploy_folder: Path, packages: list, main_file_name: str, update_logs=None) -> Path:
    pkg_json = {
        "name": "hosted-bot",
        "version": "1.0.0",
        "private": True,
        "main": main_file_name,
        "dependencies": {pkg: "*" for pkg in packages}
    }
    path = deploy_folder / "package.json"
    path.write_text(json.dumps(pkg_json, indent=2))
    if update_logs:
        update_logs(f"📦 Generated package.json with {len(packages)} package(s)")
    return path

def scan_requirements_file(content: str, update_logs) -> list:
    requirements = []
    for line in content.split('\n'):
        line = line.strip()
        if line and not line.startswith('#') and not line.startswith('-r'):
            if ';' in line:
                line = line.split(';')[0].strip()
            requirements.append(line)
            if update_logs:
                update_logs(f"   📄 From requirements: {line[:60]}")
    return requirements

def scan_imports(code_content: str, update_logs) -> list:
    IMPORT_MAPPING = {
        'flask': 'flask', 'django': 'django', 'fastapi': 'fastapi',
        'aiohttp': 'aiohttp', 'tornado': 'tornado', 'sanic': 'sanic',
        'telegram': 'python-telegram-bot>=22.0', 'aiogram': 'aiogram',
        'pyrogram': 'pyrogram', 'telethon': 'telethon',
        'discord': 'discord.py', 'nextcord': 'nextcord',
        'sqlalchemy': 'sqlalchemy', 'psycopg2': 'psycopg2-binary',
        'pymysql': 'pymysql', 'pymongo': 'pymongo', 'redis': 'redis',
        'requests': 'requests', 'httpx': 'httpx',
        'numpy': 'numpy', 'pandas': 'pandas', 'scipy': 'scipy',
        'PIL': 'Pillow', 'cv2': 'opencv-python',
        'bs4': 'beautifulsoup4', 'selenium': 'selenium',
        'dotenv': 'python-dotenv', 'click': 'click',
        'cryptography': 'cryptography', 'jwt': 'pyjwt',
        'yaml': 'pyyaml', 'toml': 'toml',
        'boto3': 'boto3', 'psutil': 'psutil', 'loguru': 'loguru',
        'rich': 'rich', 'tqdm': 'tqdm', 'uvicorn': 'uvicorn',
        'gunicorn': 'gunicorn', 'celery': 'celery',
        'telebot': 'pyTelegramBotAPI',
    }
    detected = set()
    lines = code_content.split('\n')
    for line in lines:
        line = line.strip()
        match = re.match(r'^(?:from|import)\s+([a-zA-Z0-9_\.]+)', line)
        if match:
            module = match.group(1).split('.')[0]
            if module in IMPORT_MAPPING:
                package = IMPORT_MAPPING[module]
                if package and package not in detected:
                    detected.add(package)
                    if update_logs:
                        update_logs(f"   🔍 Detected: {module} → {package}")
            elif module not in ['os', 'sys', 'time', 'datetime', 'json', 're', 'math', 'random', 
                                 'string', 'collections', 'itertools', 'functools', 'typing', 'pathlib',
                                 'tempfile', 'subprocess', 'threading', 'multiprocessing', 'socket',
                                 'ssl', 'hashlib', 'base64', 'zipfile', 'tarfile', 'shutil', 'glob',
                                 'io', 'abc', 'copy', 'enum', 'struct', 'queue', 'weakref',
                                 'contextlib', 'dataclasses', 'inspect', 'logging', 'warnings',
                                 'argparse', 'configparser', 'csv', 'html', 'http', 'urllib',
                                 'uuid', 'decimal', 'fractions', 'statistics', 'operator',
                                 'concurrent', 'asyncio', 'signal', 'platform', 'traceback',
                                 'pprint', 'textwrap', 'binascii', 'hmac', 'secrets']:
                if module and not module.startswith('_') and len(module) > 1:
                    guessed = module.replace('_', '-')
                    if module == 'bs4':
                        guessed = 'beautifulsoup4'
                    elif module == 'cv2':
                        guessed = 'opencv-python'
                    elif module == 'PIL':
                        guessed = 'Pillow'
                    elif module == 'sklearn':
                        guessed = 'scikit-learn'
                    if guessed and re.match(r'^[a-zA-Z][a-zA-Z0-9\-\.]+$', guessed):
                        detected.add(guessed)
                        if update_logs:
                            update_logs(f"   🔍 Guessed: {module} → {guessed}")
    return list(detected)

# ========== MAIN ==========
def main():
    global LAST_UPDATE_ID
    print("=" * 70)
    print("🤖 BOT HOSTING PLATFORM")
    print("=" * 70)
    print(f"📁 Data Directory: {BASE_DIR}")
    print(f"🆓 Free Tier: {FREE_USER_MAX_DEPLOYMENTS} x {FREE_DEPLOYMENT_DURATION_HOURS}h")
    print("=" * 70)
    
    init_db()
    update_system_stats()
    
    # Start health monitor thread
    def health_monitor():
        while True:
            try:
                # Check active deployments
                conn = sqlite3.connect(DATABASE_FILE)
                c = conn.cursor()
                c.execute("SELECT deployment_id, proc_pid, user_id FROM deployments WHERE status='active' AND proc_pid IS NOT NULL")
                rows = c.fetchall()
                conn.close()
                
                for dep_id, pid, user_id in rows:
                    if pid:
                        try:
                            os.kill(pid, 0)
                        except (ProcessLookupError, PermissionError):
                            # Process died - auto restart
                            print(f"💀 Deployment {dep_id} died, restarting...")
                            restart_deployment_by_id(dep_id, user_id, is_auto_restart=True)
                sleep(60)
            except Exception as e:
                print(f"⚠️ Health monitor error: {e}")
                sleep(60)
    
    threading.Thread(target=health_monitor, daemon=True).start()
    
    try:
        me = http_get(f"{TELEGRAM_API}/getMe")
        if me and me.get('ok'):
            print(f"✅ Bot: @{me['result']['username']}")
        else:
            print("❌ Check BOT_TOKEN!")
            return
    except Exception as e:
        print(f"❌ Error: {e}")
        return
    
    print("=" * 70)
    print("✅ Bot running! Press Ctrl+C to stop")
    print("=" * 70)
    
    while True:
        try:
            params = {"offset": LAST_UPDATE_ID + 1, "timeout": 30}
            data = http_get(f"{TELEGRAM_API}/getUpdates", params)
            
            if data and data.get('ok'):
                for update in data['result']:
                    LAST_UPDATE_ID = update['update_id']
                    # Process updates here (would need full handler implementation)
                    # This is a simplified version - the full handler would be ~1000+ lines
                    pass
            sleep(0.5)
        except KeyboardInterrupt:
            print("\n🛑 Bot stopped")
            break
        except Exception as e:
            print(f"⚠️ Error: {e}")
            sleep(5)

if __name__ == "__main__":
    main()