"""
VOX — Абстракция почтового провайдера.

ПРОБЛЕМА (production-аудит): Gmail SMTP недоступен из Railway-контейнера
(`[Errno 101] Network is unreachable` — исходящий SMTP-порт заблокирован).
Из-за этого verification email просто не отправлялся, хотя пользователь видел
«успех».

Решение: единая функция send_email() с выбором транспорта через переменную
окружения EMAIL_PROVIDER:

    EMAIL_PROVIDER=resend   → Resend HTTP API (рекомендовано для Railway)
    EMAIL_PROVIDER=gmail    → Gmail SMTP (локальный / dev / legacy fallback)

Правила:
  * никогда не логируем API key / пароль;
  * при неполной конфигурации НЕ имитируем успех — возвращаем state="failed";
  * у каждого ответа есть state: "sent" | "failed" — это и есть
    email_delivery_state, который видит фронтенд;
  * Resend вызывается с таймаутом, non-2xx считается ошибкой;
  * сетевые вызовы Resend мокаются в тестах (email_provider.httpx.post).
"""

import os
import smtplib
import logging
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

import httpx

logger = logging.getLogger("vox.email")

RESEND_API_URL = "https://api.resend.com/emails"
RESEND_TIMEOUT_SEC = 10.0
GMAIL_TIMEOUT_SEC = 15.0


def get_provider_name() -> str:
    """Активный провайдер (lowercase). По умолчанию gmail (legacy)."""
    return (os.getenv("EMAIL_PROVIDER", "gmail") or "gmail").strip().lower()


def is_configured(provider: str | None = None) -> bool:
    """True, если у выбранного провайдера есть все нужные переменные."""
    provider = provider or get_provider_name()
    if provider == "resend":
        return bool(os.getenv("RESEND_API_KEY") and os.getenv("MAIL_FROM"))
    if provider == "gmail":
        return bool(os.getenv("GMAIL_USER") and os.getenv("GMAIL_APP_PASSWORD"))
    return False


def log_provider_on_startup() -> None:
    """Логировать выбранный провайдер на старте — без секретов."""
    provider = get_provider_name()
    if provider in ("resend", "gmail"):
        logger.info(
            "📧 Email provider: %s (configured=%s)", provider, is_configured(provider)
        )
    else:
        logger.warning(
            "📧 Email provider: невідомий '%s' — листи не надсилатимуться", provider
        )


def send_email(to: str, subject: str, html_body: str) -> dict:
    """
    Отправить письмо через активный провайдер.

    Возвращает dict:
        {"ok": bool, "state": "sent"|"failed", "provider": str, "error": str|None}
    """
    provider = get_provider_name()
    if provider == "resend":
        return _send_resend(to, subject, html_body)
    if provider == "gmail":
        return _send_gmail(to, subject, html_body)
    logger.error("Unknown EMAIL_PROVIDER=%r — лист не надіслано", provider)
    return {"ok": False, "state": "failed", "provider": provider, "error": "unknown_provider"}


def _send_resend(to: str, subject: str, html_body: str) -> dict:
    api_key = os.getenv("RESEND_API_KEY", "")
    mail_from = os.getenv("MAIL_FROM", "")
    if not api_key or not mail_from:
        # Не имитируем успех при неполной конфигурации.
        logger.warning("Resend не налаштовано (RESEND_API_KEY/MAIL_FROM) — лист не надіслано")
        return {"ok": False, "state": "failed", "provider": "resend", "error": "not_configured"}

    try:
        resp = httpx.post(
            RESEND_API_URL,
            headers={
                "Authorization": f"Bearer {api_key}",  # ключ НЕ логируем
                "Content-Type": "application/json",
            },
            json={"from": mail_from, "to": [to], "subject": subject, "html": html_body},
            timeout=RESEND_TIMEOUT_SEC,
        )
    except Exception as exc:  # таймаут, DNS, сеть — без утечки секрета в лог
        logger.error("Resend transport error: %s", type(exc).__name__)
        return {"ok": False, "state": "failed", "provider": "resend", "error": "transport_error"}

    if 200 <= resp.status_code < 300:
        logger.info("📧 email надіслано через Resend: %s", to)
        return {"ok": True, "state": "sent", "provider": "resend", "error": None}

    logger.error("Resend non-2xx: status=%s", resp.status_code)
    return {"ok": False, "state": "failed", "provider": "resend", "error": f"http_{resp.status_code}"}


def _send_gmail(to: str, subject: str, html_body: str) -> dict:
    gmail_user = os.getenv("GMAIL_USER", "")
    gmail_pass = os.getenv("GMAIL_APP_PASSWORD", "")
    if not gmail_user or not gmail_pass:
        logger.warning("Gmail не налаштовано (GMAIL_USER/GMAIL_APP_PASSWORD) — лист не надіслано")
        return {"ok": False, "state": "failed", "provider": "gmail", "error": "not_configured"}

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = f"VOX <{gmail_user}>"
    msg["To"] = to
    msg.attach(MIMEText(html_body, "html", "utf-8"))

    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=GMAIL_TIMEOUT_SEC) as server:
            server.login(gmail_user, gmail_pass)
            server.sendmail(gmail_user, to, msg.as_string())
        logger.info("📧 email надіслано через Gmail: %s", to)
        return {"ok": True, "state": "sent", "provider": "gmail", "error": None}
    except Exception as exc:
        logger.error("Gmail SMTP error: %s", exc)
        return {"ok": False, "state": "failed", "provider": "gmail", "error": "smtp_error"}
