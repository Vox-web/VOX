"""
VOX — Биллинг: модуль базы данных
Все новые таблицы и функции для биллинга.
Не изменяет vox_db.py — только добавляет новое через ALTER TABLE.
"""

import sqlite3
import secrets
import logging
from pathlib import Path
import os

logger = logging.getLogger("vox.billing_db")

# Используем ту же БД, что и vox_db.py
DB_PATH = Path(os.environ.get("DB_PATH", "/data/vox.db"))


def _conn():
    """Создать соединение с БД."""
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    return con


# ---------------------------------------------------------------------------
# Миграция
# ---------------------------------------------------------------------------

def migrate():
    """
    Безопасная миграция: добавляет новые колонки в таблицу users
    и создаёт таблицу payments если их ещё нет.
    """
    con = _conn()
    cur = con.cursor()

    # Получаем список текущих колонок users
    cur.execute("PRAGMA table_info(users)")
    existing_cols = {row["name"] for row in cur.fetchall()}

    # Добавляем колонки только если их нет
    new_cols = {
        "balance":            "REAL DEFAULT 0.0",
        "is_email_verified":  "INTEGER DEFAULT 0",
        "bonus_given":        "INTEGER DEFAULT 0",
        "email_verify_token": "TEXT",
    }
    for col, definition in new_cols.items():
        if col not in existing_cols:
            cur.execute(f"ALTER TABLE users ADD COLUMN {col} {definition}")
            logger.info(f"✅ billing_db: добавлена колонка users.{col}")

    # Таблица платежей
    cur.execute("""
        CREATE TABLE IF NOT EXISTS payments (
            id               INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id          INTEGER NOT NULL,
            stripe_session_id TEXT UNIQUE,
            amount           REAL NOT NULL,
            status           TEXT DEFAULT 'pending',
            created_at       DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    """)

    con.commit()
    con.close()
    logger.info("✅ billing_db: миграция завершена")


# ---------------------------------------------------------------------------
# Баланс
# ---------------------------------------------------------------------------

def get_user_balance(user_id: int) -> float:
    """Вернуть текущий баланс пользователя."""
    con = _conn()
    cur = con.cursor()
    cur.execute("SELECT balance FROM users WHERE id=?", (user_id,))
    row = cur.fetchone()
    con.close()
    return float(row["balance"]) if row and row["balance"] is not None else 0.0


def update_balance(user_id: int, delta: float) -> float:
    """
    Атомарно изменить баланс на delta (может быть отрицательным).
    Возвращает новый баланс.
    """
    con = _conn()
    cur = con.cursor()
    cur.execute(
        "UPDATE users SET balance = ROUND(balance + ?, 6) WHERE id=?",
        (delta, user_id)
    )
    con.commit()
    cur.execute("SELECT balance FROM users WHERE id=?", (user_id,))
    row = cur.fetchone()
    con.close()
    new_bal = float(row["balance"]) if row else 0.0
    logger.info(f"💰 balance update: user_id={user_id} delta={delta:+.4f} new={new_bal:.4f}")
    return new_bal


def deduct_session_cost(user_id: int, mode: str, guests: int) -> float:
    """
    Списать стоимость одной минуты сессии.
    Цена берётся из user_finance_settings.price_per_min (если задана),
    иначе дефолт $0.05. Умножается на max(1, guests).
    Возвращает остаток баланса.
    """
    try:
        con = _conn()
        cur = con.cursor()
        cur.execute(
            "SELECT price_per_min FROM user_finance_settings WHERE user_id=?",
            (user_id,)
        )
        row = cur.fetchone()
        price_per_min = float(row["price_per_min"]) if row and row["price_per_min"] else 0.05
        con.close()
    except Exception:
        price_per_min = 0.05

    cost = price_per_min * max(1, guests)
    new_balance = update_balance(user_id, -cost)
    logger.info(f"💸 deduct: user={user_id} mode={mode} guests={guests} price={price_per_min:.4f} cost={cost:.4f} left={new_balance:.4f}")
    return new_balance


# ---------------------------------------------------------------------------
# Верификация email
# ---------------------------------------------------------------------------

def generate_verify_token(user_id: int) -> str:
    """Генерировать и сохранить токен верификации email."""
    token = secrets.token_urlsafe(32)
    con = _conn()
    con.execute(
        "UPDATE users SET email_verify_token=?, is_email_verified=0 WHERE id=?",
        (token, user_id)
    )
    con.commit()
    con.close()
    return token


def verify_email_token(token: str) -> dict:
    """
    Верифицировать токен.
    Если bonus_given=False — начислить $3 бонус.
    Возвращает {"ok": bool, "user_id": int|None, "bonus": bool}
    """
    if not token:
        return {"ok": False, "user_id": None, "bonus": False}

    con = _conn()
    cur = con.cursor()
    cur.execute(
        "SELECT id, is_email_verified, bonus_given FROM users WHERE email_verify_token=?",
        (token,)
    )
    row = cur.fetchone()
    if not row:
        con.close()
        return {"ok": False, "user_id": None, "bonus": False}

    user_id = row["id"]
    bonus_applied = False

    # Помечаем email как подтверждённый
    con.execute(
        "UPDATE users SET is_email_verified=1, email_verify_token=NULL WHERE id=?",
        (user_id,)
    )

    # Начисляем $3 бонус только если ещё не давали
    if not row["bonus_given"]:
        con.execute(
            "UPDATE users SET balance = ROUND(balance + 3.0, 6), bonus_given=1 WHERE id=?",
            (user_id,)
        )
        bonus_applied = True
        logger.info(f"🎁 bonus $3 начислен: user_id={user_id}")

    con.commit()
    con.close()
    return {"ok": True, "user_id": user_id, "bonus": bonus_applied}


# ---------------------------------------------------------------------------
# Платежи (Stripe)
# ---------------------------------------------------------------------------

def create_payment_record(user_id: int, session_id: str, amount_usd: float) -> int:
    """Создать запись о платеже со статусом 'pending'."""
    con = _conn()
    cur = con.cursor()
    cur.execute(
        "INSERT INTO payments (user_id, stripe_session_id, amount, status) VALUES (?,?,?,'pending')",
        (user_id, session_id, amount_usd)
    )
    con.commit()
    row_id = cur.lastrowid
    con.close()
    return row_id


def confirm_stripe_payment(session_id: str) -> bool:
    """
    Подтвердить платёж по stripe_session_id:
    - Обновить статус на 'completed'
    - Начислить баланс пользователю
    Возвращает True если платёж найден и ещё не был подтверждён.
    """
    con = _conn()
    cur = con.cursor()
    cur.execute(
        "SELECT id, user_id, amount, status FROM payments WHERE stripe_session_id=?",
        (session_id,)
    )
    row = cur.fetchone()
    if not row:
        con.close()
        logger.warning(f"⚠️ confirm_stripe: session_id={session_id} не найден")
        return False

    if row["status"] == "completed":
        con.close()
        logger.info(f"ℹ️ confirm_stripe: session_id={session_id} уже подтверждён")
        return False

    # Обновляем статус
    con.execute(
        "UPDATE payments SET status='completed' WHERE stripe_session_id=?",
        (session_id,)
    )
    # Начисляем баланс
    con.execute(
        "UPDATE users SET balance = ROUND(balance + ?, 6) WHERE id=?",
        (row["amount"], row["user_id"])
    )
    con.commit()
    con.close()
    logger.info(f"✅ confirm_stripe: user={row['user_id']} +${row['amount']:.2f}")
    return True


def get_all_payments(limit: int = 200) -> list:
    """Вернуть список всех платежей для админки."""
    con = _conn()
    cur = con.cursor()
    cur.execute("""
        SELECT p.id, p.user_id, u.email, u.name,
               p.stripe_session_id, p.amount, p.status, p.created_at
        FROM payments p
        LEFT JOIN users u ON u.id = p.user_id
        ORDER BY p.created_at DESC
        LIMIT ?
    """, (limit,))
    rows = [dict(r) for r in cur.fetchall()]
    con.close()
    return rows


def admin_adjust_balance(user_id: int, new_balance: float) -> bool:
    """Установить баланс вручную (для админки)."""
    con = _conn()
    cur = con.cursor()
    cur.execute("UPDATE users SET balance=? WHERE id=?", (round(new_balance, 4), user_id))
    changed = cur.rowcount > 0
    con.commit()
    con.close()
    logger.info(f"🔧 admin_adjust: user={user_id} new_balance={new_balance}")
    return changed


def get_user_by_id(user_id: int) -> dict | None:
    """Получить пользователя по id."""
    con = _conn()
    cur = con.cursor()
    cur.execute("SELECT id, email, name, balance, is_email_verified, bonus_given FROM users WHERE id=?", (user_id,))
    row = cur.fetchone()
    con.close()
    return dict(row) if row else None