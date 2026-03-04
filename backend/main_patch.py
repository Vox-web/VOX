"""
VOX — API маршруты для аутентификации и отзывов
================================================
ИНСТРУКЦИЯ: Добавь этот код в main.py

1. В начало файла (импорты):
   from vox_db import (init_db, register_user, login_user,
                        get_user_by_token, add_review, get_reviews,
                        approve_review, delete_review, get_all_users)
   from fastapi import Header
   from fastapi.middleware.cors import CORSMiddleware

2. После создания app = FastAPI(...), добавь CORS:
   app.add_middleware(
       CORSMiddleware,
       allow_origins=["*"],
       allow_methods=["*"],
       allow_headers=["*"],
   )

3. В функцию startup (или сразу после app = FastAPI()):
   init_db()

4. Вставь все маршруты ниже в main.py
"""

# ─── Вставь в main.py ────────────────────────────────────────────────────────

from fastapi import Request, Header, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from typing import Optional

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

ADMIN_LOGIN    = "admin"
ADMIN_PASSWORD = "kozerog"

# ─── Auth endpoints ───────────────────────────────────────────────────────────

@app.post("/api/register")
async def api_register(body: RegisterBody):
    result = register_user(body.email, body.name, body.password)
    if not result["ok"]:
        errors = {
            "email_exists":     "Цей email вже зареєстровано",
            "password_too_short": "Пароль занадто короткий (мін. 6 символів)",
        }
        raise HTTPException(400, errors.get(result["error"], result["error"]))
    # Убираем лишнее из ответа
    result["user"].pop("password_hash", None)
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


# ─── Admin endpoints ──────────────────────────────────────────────────────────

def _check_admin(authorization: Optional[str]):
    """Простая проверка admin токена (Basic-like)."""
    if not authorization:
        raise HTTPException(401, "Unauthorized")
    # Формат: "Basic admin:kozerog" (base64) ИЛИ просто "admin:kozerog"
    import base64
    try:
        scheme, creds = authorization.split(" ", 1)
        if scheme.lower() == "basic":
            decoded = base64.b64decode(creds).decode()
        else:
            decoded = creds
        login, pwd = decoded.split(":", 1)
        if login != ADMIN_LOGIN or pwd != ADMIN_PASSWORD:
            raise HTTPException(403, "Forbidden")
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(401, "Unauthorized")


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


@app.get("/api/admin/users")
async def admin_get_users(authorization: Optional[str] = Header(None)):
    _check_admin(authorization)
    return get_all_users()
