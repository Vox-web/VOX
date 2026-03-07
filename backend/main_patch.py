"""
VOX — API маршруты: аутентификация, отзывы, управление пользователями
=====================================================================
ИНСТРУКЦИЯ: Добавь этот код в main.py

1. В начало файла — импорты (если ещё не добавлены):

   from vox_db import (init_db, register_user, login_user,
                       get_user_by_token, add_review, get_reviews,
                       approve_review, delete_review, get_all_users)
   from fastapi import Header
   from fastapi.middleware.cors import CORSMiddleware

2. После создания app = FastAPI(...):

   app.add_middleware(
       CORSMiddleware,
       allow_origins=["*"],
       allow_methods=["*"],
       allow_headers=["*"],
   )

3. Инициализация БД + биллинг (после app = FastAPI(...)):

   init_db()
   from billing_db import migrate as billing_migrate
   billing_migrate()

   from billing import billing_router, send_verification_email
   app.include_router(billing_router)

4. Вставь все маршруты ниже в main.py

ВАЖНО: Если маршруты /api/register, /api/login, /api/me, /api/reviews,
       /api/admin/reviews и /api/admin/users уже есть в main.py —
       НЕ добавляй их повторно. Добавляй только те, которых ещё нет.
       Новые маршруты в этом файле: PATCH и DELETE /api/admin/users/{id}
"""

# ─── Вставь в main.py ────────────────────────────────────────────────────────

from fastapi import Request, Header, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from typing import Optional
import sqlite3

# ─── Pydantic-модели ──────────────────────────────────────────────────────────

class RegisterBody(BaseModel):
    email: str
    name: str
    password: str

class LoginBody(BaseModel):
    email: str
    password: str

class ReviewBody(BaseModel):
    rating: int
    text: str
    source: str = "landing"   # 'landing' | 'host'
    # Для анонимных отзывов:
    guest_name: Optional[str] = None
    guest_email: Optional[str] = None

class UpdateUserBody(BaseModel):
    """Тело запроса для редактирования пользователя."""
    user_id: int
    name: Optional[str] = None
    email: Optional[str] = None
    is_active: Optional[bool] = None
    new_password: Optional[str] = None
    new_balance: Optional[float] = None

ADMIN_LOGIN    = os.getenv("ADMIN_LOGIN",    "admin")
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "kozerog")


# ─── Auth endpoints ───────────────────────────────────────────────────────────

@app.post("/api/register")
async def api_register(body: RegisterBody):
    result = register_user(body.email, body.name, body.password)
    if not result["ok"]:
        errors = {
            "email_exists":       "Цей email вже зареєстровано",
            "password_too_short": "Пароль занадто короткий (мін. 6 символів)",
        }
        raise HTTPException(400, errors.get(result["error"], result["error"]))
    result["user"].pop("password_hash", None)

    # Отправить письмо верификации + $3 бонус (если биллинг подключён)
    try:
        from billing import send_verification_email
        send_verification_email(
            result["user"]["id"],
            result["user"]["email"],
            result["user"]["name"]
        )
    except Exception as _e:
        logger.warning(f"send_verification_email failed: {_e}")

    return result


@app.post("/api/login")
async def api_login(body: LoginBody):
    result = login_user(body.email, body.password)
    if not result["ok"]:
        raise HTTPException(401, "Невірний email або пароль")
    result["user"].pop("password_hash", None)
    return result


@app.get("/api/me")
async def api_me(authorization: Optional[str] = Header(None)):
    token = (authorization or "").replace("Bearer ", "").strip()
    user = get_user_by_token(token) if token else None
    if not user:
        raise HTTPException(401, "Не авторизовано")
    return user


# ─── Reviews endpoints ────────────────────────────────────────────────────────

@app.post("/api/reviews")
async def api_add_review(
    body: ReviewBody,
    authorization: Optional[str] = Header(None)
):
    token = (authorization or "").replace("Bearer ", "").strip()
    user = get_user_by_token(token) if token else None

    result = add_review(
        rating=body.rating,
        text=body.text,
        source=body.source,
        user_id=user["id"] if user else None,
        user_name=user["name"] if user else body.guest_name,
        user_email=user["email"] if user else body.guest_email,
    )
    if not result["ok"]:
        raise HTTPException(400, result["error"])
    return {"ok": True, "message": "Відгук отримано, дякуємо!"}


@app.get("/api/reviews/public")
async def api_public_reviews():
    """Одобренные отзывы для лендинга."""
    return get_reviews(approved_only=True, limit=50)


# ─── Admin helper ─────────────────────────────────────────────────────────────

def _check_admin(authorization: Optional[str]):
    """Проверка Basic-аутентификации для Admin API."""
    if not authorization:
        raise HTTPException(401, "Unauthorized")
    import base64
    try:
        scheme, creds = authorization.split(" ", 1)
        decoded = base64.b64decode(creds).decode() if scheme.lower() == "basic" else creds
        login, pwd = decoded.split(":", 1)
        if login != ADMIN_LOGIN or pwd != ADMIN_PASSWORD:
            raise HTTPException(403, "Forbidden")
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(401, "Unauthorized")


# ─── Admin: Reviews ───────────────────────────────────────────────────────────

@app.get("/api/admin/reviews")
async def admin_get_reviews(authorization: Optional[str] = Header(None)):
    _check_admin(authorization)
    return get_reviews(approved_only=False, limit=500)


@app.patch("/api/admin/reviews/{review_id}/approve")
async def admin_approve_review(
    review_id: int,
    approved: bool = True,
    authorization: Optional[str] = Header(None)
):
    _check_admin(authorization)
    approve_review(review_id, approved)
    return {"ok": True}


@app.delete("/api/admin/reviews/{review_id}")
async def admin_delete_review(
    review_id: int,
    authorization: Optional[str] = Header(None)
):
    _check_admin(authorization)
    delete_review(review_id)
    return {"ok": True}


# ─── Admin: Users (GET + новые PATCH / DELETE) ───────────────────────────────

@app.get("/api/admin/users")
async def admin_get_users(authorization: Optional[str] = Header(None)):
    _check_admin(authorization)
    # get_all_users() из vox_db — возвращает базовые поля
    # Дополняем balance и is_email_verified из billing_db
    users = get_all_users()
    try:
        from billing_db import _conn as _billing_conn
        con = _billing_conn()
        cur = con.cursor()
        for u in users:
            cur.execute(
                "SELECT balance, is_email_verified FROM users WHERE id=?", (u["id"],)
            )
            row = cur.fetchone()
            if row:
                u["balance"]           = float(row["balance"] or 0)
                u["is_email_verified"] = bool(row["is_email_verified"])
            else:
                u["balance"]           = 0.0
                u["is_email_verified"] = False
        con.close()
    except Exception as _e:
        logger.warning(f"admin_get_users: billing fields unavailable: {_e}")
        for u in users:
            u.setdefault("balance", 0.0)
            u.setdefault("is_email_verified", False)
    return users


@app.patch("/api/admin/users/{user_id}")
async def admin_update_user(
    user_id: int,
    body: UpdateUserBody,
    authorization: Optional[str] = Header(None)
):
    """
    Редактировать пользователя: имя, email, статус, пароль, баланс.
    Прямые UPDATE через SQLite (не трогает vox_db.py).
    """
    _check_admin(authorization)

    from billing_db import DB_PATH
    import hashlib

    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    cur = con.cursor()

    # Проверяем что пользователь существует
    cur.execute("SELECT id FROM users WHERE id=?", (user_id,))
    if not cur.fetchone():
        con.close()
        raise HTTPException(404, "Користувач не знайдений")

    # Обновляем только переданные поля
    if body.name is not None:
        cur.execute("UPDATE users SET name=? WHERE id=?", (body.name.strip(), user_id))

    if body.email is not None:
        # Проверка уникальности email
        cur.execute("SELECT id FROM users WHERE email=? AND id!=?", (body.email.strip(), user_id))
        if cur.fetchone():
            con.close()
            raise HTTPException(400, "Цей email вже використовується іншим користувачем")
        cur.execute("UPDATE users SET email=? WHERE id=?", (body.email.strip(), user_id))

    if body.is_active is not None:
        cur.execute("UPDATE users SET is_active=? WHERE id=?", (int(body.is_active), user_id))

    if body.new_password is not None and body.new_password.strip():
        if len(body.new_password) < 6:
            con.close()
            raise HTTPException(400, "Пароль занадто короткий (мін. 6 символів)")
        pw_hash = hashlib.sha256(body.new_password.encode()).hexdigest()
        cur.execute("UPDATE users SET password_hash=? WHERE id=?", (pw_hash, user_id))

    if body.new_balance is not None:
        if body.new_balance < 0:
            con.close()
            raise HTTPException(400, "Баланс не може бути від'ємним")
        cur.execute("UPDATE users SET balance=? WHERE id=?", (round(body.new_balance, 4), user_id))

    con.commit()
    con.close()
    logger.info(f"✏️ admin_update_user: id={user_id}")
    return {"ok": True}


@app.delete("/api/admin/users/{user_id}")
async def admin_delete_user(
    user_id: int,
    authorization: Optional[str] = Header(None)
):
    """
    Удалить пользователя и все его данные (отзывы, платежи).
    Прямые DELETE через SQLite.
    """
    _check_admin(authorization)

    from billing_db import DB_PATH

    con = sqlite3.connect(DB_PATH)
    cur = con.cursor()

    # Проверяем что пользователь существует
    cur.execute("SELECT id, name FROM users WHERE id=?", (user_id,))
    row = cur.fetchone()
    if not row:
        con.close()
        raise HTTPException(404, "Користувач не знайдений")

    # Удаляем связанные данные
    cur.execute("DELETE FROM payments WHERE user_id=?", (user_id,))
    cur.execute("DELETE FROM reviews WHERE user_id=?",  (user_id,))  # если поле есть
    cur.execute("DELETE FROM users WHERE id=?",         (user_id,))

    con.commit()
    con.close()
    logger.info(f"🗑 admin_delete_user: id={user_id}")
    return {"ok": True}