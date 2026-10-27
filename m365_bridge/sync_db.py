import sqlite3
from pathlib import Path
from typing import Optional, Dict, Any, List
from m365_bridge.config import DATA_DIR

DB_PATH = DATA_DIR / "sync_state.db"

def get_db():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    with conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS files (
                rel_path TEXT PRIMARY KEY,
                local_mtime REAL,
                local_size INTEGER,
                remote_mtime REAL,
                remote_size INTEGER,
                last_sync_direction TEXT,
                last_synced_at REAL
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS kv_store (
                key TEXT PRIMARY KEY,
                value TEXT
            )
        """)
    return conn

def get_kv(key: str) -> Optional[str]:
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT value FROM kv_store WHERE key = ?", (key,))
    row = cur.fetchone()
    conn.close()
    return row["value"] if row else None

def set_kv(key: str, value: str):
    conn = get_db()
    with conn:
        conn.execute("""
            INSERT INTO kv_store (key, value) VALUES (?, ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value
        """, (key, value))
    conn.close()

def get_file_state(rel_path: str) -> Optional[Dict[str, Any]]:
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT * FROM files WHERE rel_path = ?", (rel_path,))
    row = cur.fetchone()
    conn.close()
    if row:
        return dict(row)
    return None

def record_synced_file(
    rel_path: str,
    local_mtime: float,
    local_size: int,
    remote_mtime: float,
    remote_size: int,
    direction: str = "down"
):
    import time
    conn = get_db()
    with conn:
        conn.execute("""
            INSERT INTO files (rel_path, local_mtime, local_size, remote_mtime, remote_size, last_sync_direction, last_synced_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(rel_path) DO UPDATE SET
                local_mtime = excluded.local_mtime,
                local_size = excluded.local_size,
                remote_mtime = excluded.remote_mtime,
                remote_size = excluded.remote_size,
                last_sync_direction = excluded.last_sync_direction,
                last_synced_at = excluded.last_synced_at
        """, (rel_path, local_mtime, local_size, remote_mtime, remote_size, direction, time.time()))
    conn.close()

def remove_file_record(rel_path: str):
    conn = get_db()
    with conn:
        conn.execute("DELETE FROM files WHERE rel_path = ?", (rel_path,))
    conn.close()

def list_all_tracked_files() -> Dict[str, Dict[str, Any]]:
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT * FROM files")
    rows = cur.fetchall()
    conn.close()
    return {r["rel_path"]: dict(r) for r in rows}
