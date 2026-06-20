"""
Общая настройка тестов VOX.

ВАЖНО: VOX_DB_PATH выставляется в изолированный временный файл ДО любого
импорта backend-модулей, потому что db_config.DB_PATH вычисляется один раз
при импорте. Так тесты никогда не трогают реальную БД.
"""

import os
import sys
import tempfile
from pathlib import Path

import pytest

# 1) Изолированная тестовая БД — до импортов backend.
_TEST_DB_DIR = tempfile.mkdtemp(prefix="vox_test_db_")
os.environ.setdefault("VOX_DB_PATH", str(Path(_TEST_DB_DIR) / "vox_test.db"))

# 2) Делаем backend импортируемым как пакетные модули верхнего уровня
#    (так же, как при запуске `cd backend && uvicorn main:app`).
_BACKEND_DIR = Path(__file__).resolve().parent.parent / "backend"
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))


# 3) ГЛОБАЛЬНАЯ блокировка реального email-транспорта во всех тестах.
#
# ПРИЧИНА (production-инцидент): test_api_smoke регистрировал реального
# пользователя через /api/register, который запускал фоновый поток отправки
# письма. Если в окружении были GMAIL_USER/GMAIL_APP_PASSWORD, поток реально
# слал письмо через Gmail SMTP (отсюда bounce на apismoke@x.com — «Address not
# found»).
#
# Этот autouse-fixture делает невозможным любой реальный SMTP-вызов из тестов:
# smtplib.SMTP_SSL и smtplib.SMTP (465/SSL и 587/STARTTLS) поднимают
# RuntimeError. Тесты, которым нужен транспорт (Gmail-путь), ставят СВОЙ
# FakeSMTP поверх (см. fixture fake_gmail) и работают; всё остальное физически
# не может уйти в сеть. Патчим сам модуль smtplib — это покрывает и
# billing.send_verification_email, и main (/api/contact), т.к. это один и тот же
# объект модуля.
@pytest.fixture(autouse=True)
def _block_real_email_transport(monkeypatch):
    import smtplib
    import httpx

    def _blocked_smtp(*args, **kwargs):
        raise RuntimeError("Real SMTP transport is blocked during tests")

    def _blocked_post(url, *args, **kwargs):
        # Покрывает Gmail API (oauth2.googleapis.com / gmail.googleapis.com) и
        # любой исходящий email-HTTP. Тесты Gmail API ставят свой mock поверх.
        raise RuntimeError("Real outbound HTTP is blocked during tests: %s" % url)

    monkeypatch.setattr(smtplib, "SMTP_SSL", _blocked_smtp)
    monkeypatch.setattr(smtplib, "SMTP", _blocked_smtp)
    monkeypatch.setattr(httpx, "post", _blocked_post)
    yield


# 4) Управляемый FakeSMTP для тестов Gmail-пути.
#
# Ставит валидные creds и подменяет smtplib.SMTP_SSL и smtplib.SMTP
# контролируемой заглушкой поверх глобальной блокировки. Управление поведением:
# sink["fail_on"] in (None, "login", "send"). sink["sent"] — захваченные письма
# (наружу не уходят).
@pytest.fixture
def fake_gmail(monkeypatch):
    import smtplib

    sink = {"sent": [], "fail_on": None}

    class FakeSMTP:
        def __init__(self, host, port, timeout=None, **kwargs):
            self.host, self.port = host, port

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        # Методы STARTTLS-пути (587) — для SSL-пути (465) не вызываются.
        def ehlo(self, *a):
            return (250, b"ok")

        def starttls(self, *a, **k):
            return (220, b"ready")

        def login(self, user, pwd):
            if sink["fail_on"] == "login":
                raise RuntimeError("login failed")

        def sendmail(self, frm, to, msg):
            if sink["fail_on"] == "send":
                raise RuntimeError("sendmail failed")
            sink["sent"].append({"from": frm, "to": to})

    monkeypatch.setenv("GMAIL_USER", "vox@gmail.com")
    monkeypatch.setenv("GMAIL_APP_PASSWORD", "app pass word")
    monkeypatch.setattr(smtplib, "SMTP_SSL", FakeSMTP)
    monkeypatch.setattr(smtplib, "SMTP", FakeSMTP)
    return sink
