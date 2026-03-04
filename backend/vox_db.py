"""
VOX — База данных (SQLite → PostgreSQL later)

Таблицы:
  - users     : регистрация / аутентификация
  - reviews   : отзывы пользователей
  - sessions  : токены сессий

Для продакшна замени:
  DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///vox.db")
  и подключи asyncpg / databases
"""

import os
import sqlite3
import hashlib
import secrets
import logging
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

logger = logging.getLogger("vox.db")

DB_PATH = os.getenv("VOX_DB_PATH", "vox.db")


def get_db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db():
    """Создать таблицы если не существуют."""
    with get_db() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS users (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                email       TEXT    UNIQUE NOT NULL,
                name        TEXT    NOT NULL,
                password_hash TEXT  NOT NULL,
                created_at  TEXT    DEFAULT (datetime('now')),
                is_active   INTEGER DEFAULT 1
            );

            CREATE TABLE IF NOT EXISTS sessions (
                token       TEXT    PRIMARY KEY,
                user_id     INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                created_at  TEXT    DEFAULT (datetime('now')),
                expires_at  TEXT    NOT NULL
            );

            CREATE TABLE IF NOT EXISTS reviews (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id     INTEGER REFERENCES users(id) ON DELETE SET NULL,
                user_name   TEXT,
                user_email  TEXT,
                rating      INTEGER NOT NULL CHECK(rating BETWEEN 1 AND 5),
                text        TEXT    NOT NULL,
                source      TEXT    DEFAULT 'landing',  -- 'landing' | 'host'
                is_approved INTEGER DEFAULT 0,
                created_at  TEXT    DEFAULT (datetime('now'))
            );

            CREATE INDEX IF NOT EXISTS idx_sessions_token   ON sessions(token);
            CREATE INDEX IF NOT EXISTS idx_reviews_approved ON reviews(is_approved);
        """)
    logger.info(f"✅ БД инициализирована: {DB_PATH}")


def hash_password(password: str) -> str:
    salt = secrets.token_hex(16)
    h = hashlib.sha256(f"{salt}{password}".encode()).hexdigest()
    return f"{salt}:{h}"


def verify_password(password: str, password_hash: str) -> bool:
    try:
        salt, h = password_hash.split(":", 1)
        return hashlib.sha256(f"{salt}{password}".encode()).hexdigest() == h
    except Exception:
        return False


def create_session_token(user_id: int, days: int = 30) -> str:
    token = secrets.token_urlsafe(32)
    expires = (datetime.utcnow() + timedelta(days=days)).isoformat()
    with get_db() as conn:
        conn.execute(
            "INSERT INTO sessions (token, user_id, expires_at) VALUES (?,?,?)",
            (token, user_id, expires)
        )
    return token


# ─── API функции ─────────────────────────────────────────────────────────────

def register_user(email: str, name: str, password: str) -> dict:
    """Зарегистрировать нового пользователя."""
    if len(password) < 6:
        return {"ok": False, "error": "password_too_short"}
    try:
        ph = hash_password(password)
        with get_db() as conn:
            conn.execute(
                "INSERT INTO users (email, name, password_hash) VALUES (?,?,?)",
                (email.lower().strip(), name.strip(), ph)
            )
        user = get_user_by_email(email)
        token = create_session_token(user["id"])
        return {"ok": True, "token": token, "user": dict(user)}
    except sqlite3.IntegrityError:
        return {"ok": False, "error": "email_exists"}
    except Exception as e:
        logger.error(f"register_user: {e}")
        return {"ok": False, "error": "server_error"}


def login_user(email: str, password: str) -> dict:
    """Войти в аккаунт."""
    user = get_user_by_email(email)
    if not user:
        return {"ok": False, "error": "not_found"}
    if not verify_password(password, user["password_hash"]):
        return {"ok": False, "error": "wrong_password"}
    token = create_session_token(user["id"])
    return {"ok": True, "token": token, "user": dict(user)}


def get_user_by_token(token: str) -> Optional[dict]:
    """Получить пользователя по токену сессии."""
    with get_db() as conn:
        row = conn.execute("""
            SELECT u.id, u.email, u.name, u.created_at
            FROM sessions s JOIN users u ON s.user_id = u.id
            WHERE s.token = ? AND s.expires_at > datetime('now')
        """, (token,)).fetchone()
    return dict(row) if row else None


def get_user_by_email(email: str) -> Optional[dict]:
    with get_db() as conn:
        row = conn.execute(
            "SELECT * FROM users WHERE email = ?", (email.lower().strip(),)
        ).fetchone()
    return dict(row) if row else None


def add_review(
    rating: int, text: str, source: str,
    user_id: Optional[int] = None,
    user_name: Optional[str] = None,
    user_email: Optional[str] = None,
) -> dict:
    """Добавить отзыв."""
    if not text.strip():
        return {"ok": False, "error": "empty_text"}
    if not (1 <= rating <= 5):
        return {"ok": False, "error": "invalid_rating"}
    try:
        with get_db() as conn:
            conn.execute("""
                INSERT INTO reviews (user_id, user_name, user_email, rating, text, source)
                VALUES (?,?,?,?,?,?)
            """, (user_id, user_name, user_email, rating, text.strip(), source))
        return {"ok": True}
    except Exception as e:
        logger.error(f"add_review: {e}")
        return {"ok": False, "error": "server_error"}


def get_reviews(approved_only: bool = True, limit: int = 100) -> list:
    """Получить отзывы."""
    with get_db() as conn:
        q = "SELECT * FROM reviews"
        if approved_only:
            q += " WHERE is_approved = 1"
        q += " ORDER BY created_at DESC LIMIT ?"
        rows = conn.execute(q, (limit,)).fetchall()
    return [dict(r) for r in rows]


def approve_review(review_id: int, approved: bool = True):
    with get_db() as conn:
        conn.execute("UPDATE reviews SET is_approved=? WHERE id=?", (int(approved), review_id))


def delete_review(review_id: int):
    with get_db() as conn:
        conn.execute("DELETE FROM reviews WHERE id=?", (review_id,))


def get_all_users(limit: int = 500) -> list:
    with get_db() as conn:
        rows = conn.execute(
            "SELECT id, email, name, created_at, is_active FROM users ORDER BY created_at DESC LIMIT ?",
            (limit,)
        ).fetchall()
    return [dict(r) for r in rows]
