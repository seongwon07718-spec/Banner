import sqlite3
import datetime as dt
from .config import DB_PATH

def init_db():
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("""CREATE TABLE IF NOT EXISTS license_codes(
        code TEXT PRIMARY KEY, type TEXT, created_at TEXT, used_by INTEGER, used_at TEXT)""")
    cur.execute("""CREATE TABLE IF NOT EXISTS licenses(
        user_id INTEGER PRIMARY KEY, code TEXT, type TEXT, activated_at TEXT, expires_at TEXT)""")
    cur.execute("""CREATE TABLE IF NOT EXISTS banner_settings(
        user_id INTEGER PRIMARY KEY, emoji TEXT, banner_name TEXT, updated_at TEXT)""")
    cur.execute("""CREATE TABLE IF NOT EXISTS banner_channels(
        user_id INTEGER PRIMARY KEY, guild_id INTEGER, channel_id INTEGER, UNIQUE(user_id, guild_id))""")
    cur.execute("""CREATE TABLE IF NOT EXISTS license_cleanup(
        user_id INTEGER PRIMARY KEY, cleaned_at TEXT)""")
    conn.commit(); conn.close()

def get_license_row(user_id: int):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("SELECT type, activated_at, expires_at FROM licenses WHERE user_id=?", (user_id,))
    row = cur.fetchone()
    conn.close()
    return row

def has_active_license(user_id: int):
    row = get_license_row(user_id)
    if not row:
        return False, None, None
    lic_type, _, expires_at = row
    if lic_type == "영구":
        return True, lic_type, None
    if not expires_at:
        return False, lic_type, None
    try:
        exp = dt.datetime.fromisoformat(expires_at)
        return (exp > dt.datetime.utcnow()), lic_type, exp
    except Exception:
        return False, lic_type, None
