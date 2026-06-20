"""
VOX — Биллинг: модуль базы данных
Все новые таблицы и функции для биллинга.
Не изменяет vox_db.py — только добавляет новое через ALTER TABLE.
"""

import sqlite3
import secrets
import logging

logger = logging.getLogger("vox.billing_db")

# Используем ту же БД, что и vox_db.py — единый источник пути (db_config.py).
# Раньше здесь был отдельный дефолт "/data/vox.db", из-за чего billing мог
# работать с другим файлом, чем auth. Теперь путь общий.
from db_config import DB_PATH

# Единые билинговые константы (один источник правды).
MIN_BALANCE_TO_START = 0.25   # минимальный баланс для старта платной сессии
DEFAULT_PRICE_PER_MIN = 0.05  # дефолтный тариф $/мин
EMAIL_VERIFY_BONUS = 3.0      # бонус за подтверждение email (один раз на пользователя)


def _conn():
    """Создать соединение с БД."""
    con = sqlite3.connect(DB_PATH, timeout=5.0)
    con.row_factory = sqlite3.Row
    # Ждать освобождения блокировки до 5с вместо мгновенного "database is locked".
    con.execute("PRAGMA busy_timeout=5000")
    return con


def room_billable(guest_count: int) -> bool:
    """Списывать ли за комнату: тариф = цена × гости, при 0 гостях — нет."""
    return guest_count > 0


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
        # Onboarding: состояние доставки verification email и cooldown повторной
        # отправки. Безопасная миграция — только ADD COLUMN, без потери данных.
        "email_delivery_state":      "TEXT DEFAULT 'unknown'",  # sent | failed | unknown
        "last_verification_sent_at": "TEXT",                    # ISO-время последней отправки
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
    Верифицировать токен email и начислить бонус РОВНО ОДИН РАЗ.

    Идемпотентность и защита от гонки: бонус начисляется атомарным
    UPDATE с условием bonus_given=0. Даже при параллельных запросах
    (двойной клик/двойная доставка письма) только один UPDATE изменит
    строку — повторный вернёт rowcount=0 и второй бонус не выдаст.

    Возвращает {"ok": bool, "user_id": int|None, "bonus": bool}
    """
    if not token:
        return {"ok": False, "user_id": None, "bonus": False}

    con = _conn()
    try:
        # Сериализуем запись (SQLite write-lock на время транзакции).
        con.execute("BEGIN IMMEDIATE")
        cur = con.cursor()
        cur.execute(
            "SELECT id FROM users WHERE email_verify_token=?",
            (token,)
        )
        row = cur.fetchone()
        if not row:
            con.rollback()
            return {"ok": False, "user_id": None, "bonus": False}

        user_id = row["id"]

        # Помечаем email подтверждённым и гасим токен.
        con.execute(
            "UPDATE users SET is_email_verified=1, email_verify_token=NULL WHERE id=?",
            (user_id,)
        )

        # Атомарно начисляем бонус только если он ещё не выдавался.
        cur.execute(
            "UPDATE users SET balance = ROUND(balance + ?, 6), bonus_given=1 "
            "WHERE id=? AND bonus_given=0",
            (EMAIL_VERIFY_BONUS, user_id)
        )
        bonus_applied = cur.rowcount > 0

        con.commit()
        if bonus_applied:
            logger.info(f"🎁 bonus ${EMAIL_VERIFY_BONUS:.0f} начислен (verify): user_id={user_id}")
        return {"ok": True, "user_id": user_id, "bonus": bonus_applied}
    except Exception as e:
        con.rollback()
        logger.error(f"verify_email_token: {e}")
        return {"ok": False, "user_id": None, "bonus": False}
    finally:
        con.close()


def can_start_session(user_id: int) -> bool:
    """Достаточно ли баланса для старта платной сессии (>= MIN_BALANCE_TO_START)."""
    return get_user_balance(user_id) >= MIN_BALANCE_TO_START


def grant_signup_bonus(user_id: int) -> bool:
    """
    ТЕСТОВЫЙ РЕЖИМ: начислить $3 и пометить email подтверждённым СРАЗУ при
    регистрации, без верификации по письму.

    Идемпотентно: бонус выдаётся ровно один раз (guard bonus_given=0). Это та же
    атомарная логика, что и в verify_email_token, но без токена. Чтобы вернуть
    верификацию — перестать вызывать эту функцию из /api/register и снова
    включить отправку письма.

    Возвращает True, если бонус был начислен этим вызовом.
    """
    con = _conn()
    try:
        con.execute("BEGIN IMMEDIATE")
        con.execute(
            "UPDATE users SET is_email_verified=1, email_verify_token=NULL WHERE id=?",
            (user_id,),
        )
        cur = con.cursor()
        cur.execute(
            "UPDATE users SET balance = ROUND(balance + ?, 6), bonus_given=1 "
            "WHERE id=? AND bonus_given=0",
            (EMAIL_VERIFY_BONUS, user_id),
        )
        applied = cur.rowcount > 0
        con.commit()
        if applied:
            logger.info(f"🎁 signup bonus ${EMAIL_VERIFY_BONUS:.0f} начислен (test-mode): user_id={user_id}")
        return applied
    except Exception as exc:
        con.rollback()
        logger.error(f"grant_signup_bonus: {exc}")
        return False
    finally:
        con.close()


# ---------------------------------------------------------------------------
# Единый account activation gate
# ---------------------------------------------------------------------------

# Cooldown повторной отправки verification email (секунды).
RESEND_COOLDOWN_SEC = 60


def get_or_create_verify_token(user_id: int) -> str:
    """
    Вернуть существующий verification-токен пользователя или создать новый.

    Важно: НЕ инвалидируем рабочий старый verification link. Если токен уже
    есть (email ещё не подтверждён) — переиспользуем его, чтобы повторная
    отправка письма не ломала ранее отправленную ссылку.
    """
    con = _conn()
    try:
        row = con.execute(
            "SELECT email_verify_token FROM users WHERE id=?", (user_id,)
        ).fetchone()
        if row and row["email_verify_token"]:
            return row["email_verify_token"]
    finally:
        con.close()
    return generate_verify_token(user_id)


def mark_verification_sent(user_id: int, state: str) -> None:
    """
    Зафиксировать факт попытки отправки verification email.

    state: 'sent' | 'failed' | 'unknown'. Время обновляем только при успешной
    отправке — cooldown должен ограничивать реальные отправленные письма, а не
    неудачные попытки (чтобы пользователь мог сразу повторить после сбоя).
    """
    con = _conn()
    try:
        if state == "sent":
            con.execute(
                "UPDATE users SET email_delivery_state=?, "
                "last_verification_sent_at=datetime('now') WHERE id=?",
                (state, user_id),
            )
        else:
            con.execute(
                "UPDATE users SET email_delivery_state=? WHERE id=?",
                (state, user_id),
            )
        con.commit()
    finally:
        con.close()


def get_verification_meta(user_id: int) -> dict:
    """Вернуть состояние доставки и cooldown повторной отправки."""
    con = _conn()
    try:
        row = con.execute(
            "SELECT email_delivery_state, last_verification_sent_at, "
            "CAST((julianday('now') - julianday(last_verification_sent_at)) * 86400.0 "
            "AS INTEGER) AS elapsed_sec "
            "FROM users WHERE id=?",
            (user_id,),
        ).fetchone()
    finally:
        con.close()

    if not row:
        return {"delivery_state": "unknown", "cooldown_remaining": 0, "last_sent_at": None}

    elapsed = row["elapsed_sec"]
    last_sent = row["last_verification_sent_at"]
    if last_sent is None or elapsed is None:
        remaining = 0
    else:
        remaining = max(0, RESEND_COOLDOWN_SEC - int(elapsed))
    return {
        "delivery_state": row["email_delivery_state"] or "unknown",
        "cooldown_remaining": remaining,
        "last_sent_at": last_sent,
    }


def get_account_start_status(user_id: int) -> dict:
    """
    Единый серверный gate для старта любой платной host-сессии.

    Возвращает dict со статусом start_status (строго в этом порядке приоритета):
      1. email_verification_required — email не подтверждён (важнее баланса!);
      2. insufficient_balance        — email ок, но баланс < MIN_BALANCE_TO_START;
      3. ready                       — email подтверждён и баланса достаточно;
      4. billing_unavailable         — техническая ошибка доступа к БД биллинга.

    Используется ОДИНАКОВО в Solo / Duo one-device / Duo remote host / Room host,
    чтобы поведение не расходилось между режимами.
    """
    try:
        user = get_user_by_id(user_id)
        if user is None:
            # Аккаунт не найден в billing-БД — это не «нет денег», а техническая
            # рассинхронизация. Не разрешаем платную сессию молча.
            return {
                "status": "billing_unavailable",
                "email_verified": False,
                "balance": 0.0,
            }
        email_verified = bool(user.get("is_email_verified"))
        balance = float(user.get("balance") or 0.0)
    except Exception as exc:
        logger.error("get_account_start_status failed for user=%s: %s", user_id, exc)
        return {"status": "billing_unavailable", "email_verified": False, "balance": 0.0}

    if not email_verified:
        status = "email_verification_required"
    elif balance < MIN_BALANCE_TO_START:
        status = "insufficient_balance"
    else:
        status = "ready"

    return {"status": status, "email_verified": email_verified, "balance": round(balance, 4)}


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


# ---------------------------------------------------------------------------
# Пороги предупреждений о балансе
# ---------------------------------------------------------------------------

BALANCE_WARN_LOW      = 1.50   # мягкое предупреждение
BALANCE_WARN_CRITICAL = 0.50   # жёсткое предупреждение

def get_balance_warning(balance: float) -> str | None:
    """Вернуть уровень предупреждения: 'low' | 'critical' | 'zero' | None."""
    if balance <= 0:
        return "zero"
    elif balance <= BALANCE_WARN_CRITICAL:
        return "critical"
    elif balance <= BALANCE_WARN_LOW:
        return "low"
    return None