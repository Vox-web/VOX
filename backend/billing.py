"""
VOX — Биллинг: FastAPI Router
Stripe Checkout, Webhooks, Email верификация через Gmail SMTP, баланс.
"""

import os
import json
import socket
import ssl
import logging
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Optional

try:
    import stripe  # биллинг опционален — без пакета приложение всё равно стартует
except ImportError:  # pragma: no cover
    stripe = None
from fastapi import APIRouter, Header, HTTPException, Request, WebSocket
from fastapi.responses import JSONResponse, RedirectResponse
from starlette.concurrency import run_in_threadpool
from pydantic import BaseModel

from billing_db import (
    get_user_balance, update_balance, deduct_session_cost,
    generate_verify_token, verify_email_token,
    create_payment_record, confirm_stripe_payment,
    get_all_payments, admin_adjust_balance, get_user_by_id,
    can_start_session, MIN_BALANCE_TO_START,
    get_or_create_verify_token, mark_verification_sent, get_verification_meta,
    get_account_start_status, RESEND_COOLDOWN_SEC,
)
from vox_db import get_user_by_token

logger = logging.getLogger("vox.billing")

# ---------------------------------------------------------------------------
# Конфигурация
# ---------------------------------------------------------------------------
if stripe is not None:
    stripe.api_key = os.getenv("STRIPE_SECRET_KEY", "")
STRIPE_WEBHOOK_SECRET = os.getenv("STRIPE_WEBHOOK_SECRET", "")
BASE_URL = os.getenv("BASE_URL", "").rstrip("/")

# Допустимые суммы пополнения (USD)
TOPUP_AMOUNTS = {5, 10, 20, 50}

# MIN_BALANCE_TO_START импортируется из billing_db (единый источник правды).

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
# EMAIL верификация (Gmail SMTP SSL — единственный provider)
# ---------------------------------------------------------------------------

def _build_verification_html(name: str, verify_url: str) -> str:
    """Собрать HTML письма верификации."""
    return f"""<!DOCTYPE html>
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


GMAIL_HOST = "smtp.gmail.com"
# Сек на попытку. Короткий таймаут, чтобы заблокированный исходящий SMTP
# (типично для PaaS) не подвешивал запрос регистрации на десятки секунд.
GMAIL_SMTP_TIMEOUT = 7


def _resolve_ipv4(host: str) -> Optional[str]:
    """IPv4-адрес хоста или None. Нужен, чтобы обойти [Errno 101] на хостах
    без исходящего IPv6 (типичная ситуация в PaaS-контейнерах)."""
    try:
        infos = socket.getaddrinfo(host, None, socket.AF_INET, socket.SOCK_STREAM)
        return infos[0][4][0] if infos else None
    except Exception:
        return None


def _deliver_via_gmail(gmail_user: str, gmail_pass: str, to_addr: str, raw_msg: str):
    """
    Отправить уже собранное письмо через Gmail SMTP, перебирая стратегии до
    первого успеха. Возвращает (ok: bool, detail: str).

    Порядок:
      1. SSL :465 (обычный resolve) — стандартный путь, работает локально.
      2. STARTTLS :587 через IPv4 — обходит [Errno 101] Network is unreachable,
         когда контейнер уходит в недоступный IPv6 и/или 465 фильтруется.
      3. STARTTLS :587 (обычный resolve) — дополнительный запас.

    Отправитель и аутентификация — только GMAIL_USER/GMAIL_APP_PASSWORD.
    """
    ctx = ssl.create_default_context()
    ipv4 = _resolve_ipv4(GMAIL_HOST)

    attempts = [("ssl", GMAIL_HOST, 465)]
    if ipv4:
        attempts.append(("starttls", ipv4, 587))
    attempts.append(("starttls", GMAIL_HOST, 587))

    last_err = "no_attempt"
    for mode, addr, port in attempts:
        try:
            if mode == "ssl":
                with smtplib.SMTP_SSL(addr, port, timeout=GMAIL_SMTP_TIMEOUT, context=ctx) as s:
                    s.login(gmail_user, gmail_pass)
                    s.sendmail(gmail_user, to_addr, raw_msg)
            else:
                with smtplib.SMTP(addr, port, timeout=GMAIL_SMTP_TIMEOUT) as s:
                    # SNI/проверка сертификата — против реального hostname,
                    # даже когда подключаемся напрямую по IPv4-адресу.
                    s._host = GMAIL_HOST
                    s.ehlo()
                    s.starttls(context=ctx)
                    s.ehlo()
                    s.login(gmail_user, gmail_pass)
                    s.sendmail(gmail_user, to_addr, raw_msg)
            return True, f"{mode}:{port}"
        except Exception as exc:
            last_err = f"{mode}:{port} {type(exc).__name__}: {exc}"
            logger.warning("Gmail SMTP attempt failed (%s)", last_err)
    return False, last_err


def send_verification_email(user_id: int, email: str, name: str) -> bool:
    """
    Отправить письмо верификации email через Gmail SMTP (единственный provider).

    smtp.gmail.com, отправитель — существующий ящик GMAIL_USER (без MAIL_FROM),
    аутентификация через GMAIL_APP_PASSWORD. Отправка устойчива к [Errno 101]:
    при неудаче 465/SSL пробуем STARTTLS:587 с приоритетом IPv4 (см.
    _deliver_via_gmail).

    Семантика результата:
      * нет GMAIL_USER/GMAIL_APP_PASSWORD → "failed" (False);
      * все SMTP-попытки упали → "failed" (False), техническая причина в логах
        без пароля;
      * SMTP принял письмо → "sent" (True). ВНИМАНИЕ: success означает только,
        что Gmail принял отправку, а не что письмо попало во «Входящие».

    Токен ПЕРЕИСПОЛЬЗУЕТСЯ, если уже существует: повторная отправка не должна
    инвалидировать ранее отправленную рабочую ссылку. Фактический результат
    фиксируется в email_delivery_state (sent/failed).
    """
    gmail_user = os.getenv("GMAIL_USER", "")
    gmail_pass = os.getenv("GMAIL_APP_PASSWORD", "")
    if not gmail_user or not gmail_pass:
        logger.warning("⚠️ GMAIL_USER або GMAIL_APP_PASSWORD не задано — лист не надіслано")
        mark_verification_sent(user_id, "failed")
        return False

    token = get_or_create_verify_token(user_id)
    verify_url = f"{BASE_URL}/api/verify-email?token={token}"
    html_body = _build_verification_html(name, verify_url)

    msg = MIMEMultipart("alternative")
    msg["Subject"] = "🎙 VOX — Підтвердіть email та отримайте $3"
    msg["From"]    = f"VOX <{gmail_user}>"   # отправитель = существующий GMAIL_USER
    msg["To"]      = email
    msg.attach(MIMEText(html_body, "html", "utf-8"))

    ok, detail = _deliver_via_gmail(gmail_user, gmail_pass, email, msg.as_string())
    if ok:
        logger.info("📧 email прийнято Gmail SMTP (%s) для відправки: %s", detail, email)
        mark_verification_sent(user_id, "sent")
        return True

    logger.error("❌ Gmail SMTP не зміг відправити лист (%s)", detail)
    mark_verification_sent(user_id, "failed")
    return False


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

    if stripe is None or not stripe.api_key:
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

    if stripe is None:
        raise HTTPException(503, "Stripe не настроен")

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

    sent = send_verification_email(user["id"], user["email"], user.get("name", ""))
    if not sent:
        raise HTTPException(503, "Не вдалося надіслати лист. Спробуйте пізніше.")

    return JSONResponse({"ok": True, "message": "Лист надіслано"})


@billing_router.get("/account/status")
async def api_account_status(authorization: Optional[str] = Header(None)):
    """
    Server-authoritative статус аккаунта для онбординга.

    Фронтенд вызывает его после регистрации, перед стартом каждого платного
    host-режима, после возврата в приложение и после подтверждения email.
    Не возвращает verification token.
    """
    user = _get_current_user(authorization)
    start = get_account_start_status(user["id"])
    meta = get_verification_meta(user["id"])

    resend_available_at = None
    if meta["cooldown_remaining"] > 0:
        from datetime import datetime, timezone, timedelta
        resend_available_at = (
            datetime.now(timezone.utc) + timedelta(seconds=meta["cooldown_remaining"])
        ).isoformat()

    return JSONResponse({
        "authenticated": True,
        "email_verified": start["email_verified"],
        "balance": start["balance"],
        "start_status": start["status"],
        "verification_required": start["status"] == "email_verification_required",
        "resend_available_at": resend_available_at,
        "resend_cooldown_remaining": meta["cooldown_remaining"],
        "email_delivery_state": meta["delivery_state"],
        "min_balance": MIN_BALANCE_TO_START,
    })


@billing_router.post("/auth/resend-verification")
async def api_resend_verification(authorization: Optional[str] = Header(None)):
    """
    Повторно отправить verification email (cooldown 60с).

    Поведение:
      * только для авторизованного пользователя;
      * если email уже подтверждён — понятный ответ без отправки;
      * cooldown 60с между фактическими отправками;
      * НЕ сообщаем «лист надіслано», если провайдер вернул ошибку;
      * токен переиспользуется (старая ссылка не инвалидируется).
    """
    user = _get_current_user(authorization)
    full_user = get_user_by_id(user["id"])
    if not full_user:
        raise HTTPException(404, "Користувача не знайдено")

    if full_user.get("is_email_verified"):
        return JSONResponse({
            "ok": True,
            "status": "already_verified",
            "message": "Email вже підтверджений.",
        })

    meta = get_verification_meta(user["id"])
    if meta["cooldown_remaining"] > 0:
        return JSONResponse(
            status_code=429,
            content={
                "ok": False,
                "status": "cooldown",
                "retry_after": meta["cooldown_remaining"],
                "message": "Зачекайте перед повторною відправкою.",
            },
        )

    # send_verification_email — синхронный Gmail SMTP вызов. Выполняем его в
    # threadpool, чтобы блокирующий SMTP-вызов не останавливал event loop.
    sent = await run_in_threadpool(
        send_verification_email, user["id"], user["email"], user.get("name", "")
    )
    if not sent:
        # Реальная ошибка отправки — честно сообщаем, не имитируем успех.
        raise HTTPException(503, "Не вдалося надіслати лист. Спробуйте ще раз пізніше.")

    return JSONResponse({
        "ok": True,
        "status": "sent",
        "message": "Лист надіслано. Перевірте пошту (і папку «Спам»).",
    })


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
    Проверить, достаточно ли баланса для старта сессии (>= MIN_BALANCE_TO_START).
    Делегирует в billing_db.can_start_session — единая логика без дублей.
    """
    return can_start_session(user_id)