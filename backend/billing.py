"""
VOX — Биллинг: FastAPI Router
Stripe Checkout, Webhooks, Email верификация через Gmail SMTP, баланс.
"""

import os
import json
import logging
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Optional

import stripe
from fastapi import APIRouter, Header, HTTPException, Request, WebSocket
from fastapi.responses import JSONResponse, RedirectResponse
from pydantic import BaseModel

from billing_db import (
    get_user_balance, update_balance, deduct_session_cost,
    generate_verify_token, verify_email_token,
    create_payment_record, confirm_stripe_payment,
    get_all_payments, admin_adjust_balance, get_user_by_id,
)
from vox_db import get_user_by_token

logger = logging.getLogger("vox.billing")

# ---------------------------------------------------------------------------
# Конфигурация
# ---------------------------------------------------------------------------
stripe.api_key = os.getenv("STRIPE_SECRET_KEY", "")
STRIPE_WEBHOOK_SECRET = os.getenv("STRIPE_WEBHOOK_SECRET", "")
BASE_URL = os.getenv("BASE_URL", "").rstrip("/")

# Допустимые суммы пополнения (USD)
TOPUP_AMOUNTS = {5, 10, 20, 50}

# Стоимость запуска сессии минимум $0.25
MIN_BALANCE_TO_START = 0.25

billing_router = APIRouter(prefix="/api")

# ---------------------------------------------------------------------------
# Pydantic модели
# ---------------------------------------------------------------------------
class CheckoutBody(BaseModel):
    amount: int  # 5 | 10 | 20 | 50

class AdjustBalanceBody(BaseModel):
    user_id: int
    new_balance: float

# ---------------------------------------------------------------------------
# Вспомогательные функции
# ---------------------------------------------------------------------------

def _get_current_user(authorization: Optional[str]):
    """Получить текущего пользователя из Bearer токена или выбросить 401."""
    token = (authorization or "").replace("Bearer ", "").strip()
    user = get_user_by_token(token) if token else None
    if not user:
        raise HTTPException(401, "Не авторизовано")
    return user


def _check_admin(authorization: Optional[str]):
    """Проверить Basic-авторизацию админа (идентична main.py)."""
    import base64
    admin_login = os.getenv("ADMIN_LOGIN", "admin")
    admin_password = os.getenv("ADMIN_PASSWORD", "kozerog")
    if not authorization:
        raise HTTPException(401, "Unauthorized")
    try:
        scheme, creds = authorization.split(" ", 1)
        decoded = base64.b64decode(creds).decode() if scheme.lower() == "basic" else creds
        login, pwd = decoded.split(":", 1)
        if login != admin_login or pwd != admin_password:
            raise HTTPException(403, "Forbidden")
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(401, "Unauthorized")


# ---------------------------------------------------------------------------
# EMAIL верификация (Gmail SMTP)
# ---------------------------------------------------------------------------

def send_verification_email(user_id: int, email: str, name: str):
    """
    Генерировать токен и отправить HTML-письмо верификации через Gmail SMTP.
    Вызывается после регистрации пользователя.
    Переменные среды: GMAIL_USER, GMAIL_APP_PASSWORD
    """
    gmail_user = os.getenv("GMAIL_USER", "")
    gmail_pass = os.getenv("GMAIL_APP_PASSWORD", "")

    if not gmail_user or not gmail_pass:
        logger.warning("⚠️ GMAIL_USER або GMAIL_APP_PASSWORD не задано — email не відправлено")
        return

    token = generate_verify_token(user_id)
    verify_url = f"{BASE_URL}/api/verify-email?token={token}"

    html_body = f"""<!DOCTYPE html>
<html lang="uk">
<head>
<meta charset="UTF-8"/>
<meta name="viewport" content="width=device-width,initial-scale=1"/>
<title>VOX — Підтвердіть email</title>
</head>
<body style="margin:0;padding:0;background:#09080f;font-family:Arial,sans-serif">
  <table width="100%" cellpadding="0" cellspacing="0" style="background:#09080f;padding:40px 20px">
    <tr><td align="center">
      <table width="100%" style="max-width:480px;background:#15121f;border:1px solid #2a2340;border-radius:20px;overflow:hidden">
        <!-- Шапка -->
        <tr>
          <td style="background:linear-gradient(135deg,#1a1530,#0f0d1a);padding:36px 40px;text-align:center;border-bottom:1px solid #2a2340">
            <div style="font-size:30px;font-weight:800;color:#7c6aff;letter-spacing:6px">VOX</div>
            <div style="color:#5e5880;font-size:13px;margin-top:6px">Real-Time AI Translation</div>
          </td>
        </tr>
        <!-- Тело -->
        <tr>
          <td style="padding:36px 40px">
            <h2 style="margin:0 0 16px;font-size:22px;font-weight:700;color:#f0eeff">
              Привіт, {name}! 👋
            </h2>
            <p style="margin:0 0 12px;color:#9b93c4;font-size:15px;line-height:1.6">
              Дякуємо за реєстрацію у <strong style="color:#a594ff">VOX</strong>.<br/>
              Підтвердіть свій email — і отримаєте <strong style="color:#2dd4a0">$3 бонусу</strong> на баланс!
            </p>
            <div style="background:#0f0d1a;border:1px solid #2a2340;border-radius:12px;padding:16px 20px;margin:24px 0;text-align:center">
              <div style="font-size:28px;margin-bottom:6px">🎁</div>
              <div style="color:#f0eeff;font-size:16px;font-weight:600">Бонус $3.00</div>
              <div style="color:#5e5880;font-size:12px;margin-top:4px">~60 хвилин Solo-перекладу</div>
            </div>
            <div style="text-align:center;margin:28px 0">
              <a href="{verify_url}" style="display:inline-block;background:linear-gradient(135deg,#9b8aff,#7c6aff);color:#fff;font-size:16px;font-weight:700;text-decoration:none;padding:16px 40px;border-radius:12px;box-shadow:0 8px 24px rgba(124,106,255,.4)">
                ✅ Підтвердити email
              </a>
            </div>
            <p style="color:#5e5880;font-size:12px;text-align:center;margin:0">
              Якщо ви не реєструвались у VOX — просто проігноруйте цей лист.
            </p>
          </td>
        </tr>
        <!-- Підвал -->
        <tr>
          <td style="padding:20px 40px;border-top:1px solid #2a2340;text-align:center">
            <div style="color:#3d3560;font-size:12px">© 2025 VOX AI Translation. Усі права захищені.</div>
          </td>
        </tr>
      </table>
    </td></tr>
  </table>
</body>
</html>"""

    msg = MIMEMultipart("alternative")
    msg["Subject"] = "🎙 VOX — Підтвердіть email та отримайте $3"
    msg["From"]    = f"VOX <{gmail_user}>"
    msg["To"]      = email
    msg.attach(MIMEText(html_body, "html", "utf-8"))

    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
            server.login(gmail_user, gmail_pass)
            server.sendmail(gmail_user, email, msg.as_string())
        logger.info(f"📧 email відправлено через Gmail: {email}")
    except Exception as e:
        logger.error(f"❌ Gmail SMTP error: {e}")


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@billing_router.get("/balance")
async def api_get_balance(authorization: Optional[str] = Header(None)):
    """Вернуть текущий баланс авторизованного пользователя."""
    user = _get_current_user(authorization)
    balance = get_user_balance(user["id"])
    from billing_db import _conn as _bconn
    try:
        bc = _bconn(); cur = bc.cursor()
        cur.execute("SELECT price_per_min FROM user_finance_settings WHERE user_id=?", (user["id"],))
        row = cur.fetchone()
        ppm = float(row["price_per_min"]) if row and row["price_per_min"] else 0.05
        bc.close()
    except Exception:
        ppm = 0.05
    est_minutes = int(balance / ppm) if balance > 0 else 0
    return JSONResponse({"ok": True, "balance": round(balance, 4), "est_minutes": est_minutes, "price_per_min": ppm})


@billing_router.post("/create-checkout")
async def api_create_checkout(
    body: CheckoutBody,
    authorization: Optional[str] = Header(None)
):
    """Создать Stripe Checkout Session для пополнения баланса."""
    user = _get_current_user(authorization)

    if body.amount not in TOPUP_AMOUNTS:
        raise HTTPException(400, f"Допустимые суммы: {sorted(TOPUP_AMOUNTS)}")

    if not stripe.api_key:
        raise HTTPException(503, "Stripe не настроен")

    try:
        session = stripe.checkout.Session.create(
            payment_method_types=["card"],
            line_items=[{
                "price_data": {
                    "currency": "usd",
                    "product_data": {
                        "name": f"VOX Balance — ${body.amount}",
                        "description": f"Поповнення балансу VOX на ${body.amount}.00",
                    },
                    "unit_amount": body.amount * 100,
                },
                "quantity": 1,
            }],
            mode="payment",
            success_url=f"{BASE_URL}/host?payment=success",
            cancel_url=f"{BASE_URL}/host?payment=cancel",
            metadata={
                "user_id": str(user["id"]),
                "amount_usd": str(body.amount),
            },
            customer_email=user.get("email"),
        )

        create_payment_record(user["id"], session.id, float(body.amount))
        logger.info(f"💳 checkout created: user={user['id']} amount=${body.amount} session={session.id}")

        return JSONResponse({"ok": True, "url": session.url})

    except stripe.error.StripeError as e:
        logger.error(f"❌ Stripe error: {e}")
        raise HTTPException(500, "Помилка Stripe")


@billing_router.post("/webhook")
async def api_stripe_webhook(request: Request):
    """
    Stripe Webhook — принимает события и подтверждает платежи.
    Важно: тело читается сырым (без парсинга) для верификации подписи.
    """
    payload = await request.body()
    sig_header = request.headers.get("stripe-signature", "")

    if not STRIPE_WEBHOOK_SECRET:
        logger.warning("⚠️ STRIPE_WEBHOOK_SECRET не задан — webhook не верифицируется")
        event = json.loads(payload)
    else:
        try:
            event = stripe.Webhook.construct_event(payload, sig_header, STRIPE_WEBHOOK_SECRET)
        except stripe.error.SignatureVerificationError:
            logger.warning("❌ Webhook: неверная подпись Stripe")
            raise HTTPException(400, "Invalid signature")

    if event["type"] == "checkout.session.completed":
        session_obj = event["data"]["object"]
        session_id = session_obj["id"]
        confirmed = confirm_stripe_payment(session_id)
        if confirmed:
            logger.info(f"✅ Webhook: payment confirmed session={session_id}")
        else:
            logger.info(f"ℹ️ Webhook: payment already processed session={session_id}")

    return JSONResponse({"ok": True})


@billing_router.post("/send-verification")
async def api_send_verification(authorization: Optional[str] = Header(None)):
    """Отправить (повторно) письмо верификации email."""
    user = _get_current_user(authorization)
    full_user = get_user_by_id(user["id"])
    if not full_user:
        raise HTTPException(404, "Пользователь не найден")
    if full_user.get("is_email_verified"):
        return JSONResponse({"ok": True, "message": "Email вже підтверджений"})

    send_verification_email(user["id"], user["email"], user.get("name", ""))
    return JSONResponse({"ok": True, "message": "Лист надіслано"})


@billing_router.get("/verify-email")
async def api_verify_email(token: str = ""):
    """Верифицировать email по токену из письма. Редирект на /host."""
    result = verify_email_token(token)
    if result["ok"]:
        return RedirectResponse(url="/host?verified=1", status_code=303)
    return RedirectResponse(url="/host?verified=0", status_code=303)


# ---------------------------------------------------------------------------
# Admin endpoints
# ---------------------------------------------------------------------------

@billing_router.get("/admin/payments")
async def admin_get_payments(authorization: Optional[str] = Header(None)):
    """Список всех платежей для админки."""
    _check_admin(authorization)
    payments = get_all_payments(limit=500)
    return JSONResponse(payments)


@billing_router.post("/admin/adjust-balance")
async def admin_adjust_balance_endpoint(
    body: AdjustBalanceBody,
    authorization: Optional[str] = Header(None)
):
    """Вручную установить баланс пользователя."""
    _check_admin(authorization)
    if body.new_balance < 0:
        raise HTTPException(400, "Баланс не може бути від'ємним")
    ok = admin_adjust_balance(body.user_id, body.new_balance)
    if not ok:
        raise HTTPException(404, "Пользователь не найден")
    return JSONResponse({"ok": True, "new_balance": body.new_balance})


# ---------------------------------------------------------------------------
# Биллинг-логика для сессий (экспортируется в main.py)
# ---------------------------------------------------------------------------

async def check_balance_for_start(user_id: int) -> bool:
    """
    Проверить достаточно ли баланса для старта сессии.
    Минимум MIN_BALANCE_TO_START ($0.25).
    """
    balance = get_user_balance(user_id)
    return balance >= MIN_BALANCE_TO_START


async def billing_tick(user_id: int, mode: str, guests: int, ws: WebSocket):
    """
    Вызывается каждую минуту активной сессии:
    - Списывает стоимость за минуту
    - За 2 минуты до обнуления — предупреждение
    - При нулевом балансе — завершает сессию
    """
    new_balance = deduct_session_cost(user_id, mode, guests)
    from billing_db import _conn as _bconn
    try:
        bc = _bconn(); cur = bc.cursor()
        cur.execute("SELECT price_per_min FROM user_finance_settings WHERE user_id=?", (user_id,))
        row = cur.fetchone()
        _ppm = float(row["price_per_min"]) if row and row["price_per_min"] else 0.05
        bc.close()
    except Exception:
        _ppm = 0.05
    cost_per_min = _ppm * max(1, guests)

    if new_balance <= 0:
        try:
            await ws.send_json({
                "type": "session_ended",
                "reason": "no_balance",
                "message": "Баланс вичерпано. Поповніть для продовження."
            })
        except Exception:
            pass
        logger.info(f"🔴 billing_tick: user={user_id} баланс исчерпан, сессия завершена")
        return False

    minutes_left = int(new_balance / cost_per_min)
    if minutes_left <= 2:
        try:
            await ws.send_json({
                "type": "balance_warning",
                "minutes_left": minutes_left,
                "balance": round(new_balance, 4),
                "message": f"⚠️ Баланс закінчується! Залишилось ~{minutes_left} хв."
            })
        except Exception:
            pass
        logger.info(f"⚠️ billing_tick: user={user_id} minutes_left={minutes_left}")

    return True