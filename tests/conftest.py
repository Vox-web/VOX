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
# слал письмо (отсюда bounce на apismoke@x.com — «Address not found»).
#
# Этот autouse-fixture делает невозможным любой реальный сетевой email-вызов из
# тестов: smtplib.SMTP_SSL и httpx.post (Resend) поднимают RuntimeError. Тесты,
# которым нужен транспорт (test_email_mock — Gmail, test_email_provider —
# Resend), ставят СВОЙ fake поверх и работают; всё остальное физически не может
# уйти в сеть. Патчим сами модули smtplib/httpx — это покрывает и
# email_provider, и main (/api/contact), т.к. это один и тот же объект модуля.
@pytest.fixture(autouse=True)
def _block_real_email_transport(monkeypatch):
    import smtplib
    import httpx

    def _blocked_smtp(*args, **kwargs):
        raise RuntimeError("Real SMTP transport is blocked during tests")

    def _blocked_post(url, *args, **kwargs):
        raise RuntimeError("Real HTTP email transport is blocked during tests: %s" % url)

    monkeypatch.setattr(smtplib, "SMTP_SSL", _blocked_smtp)
    monkeypatch.setattr(httpx, "post", _blocked_post)
    yield
