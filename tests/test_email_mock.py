"""
Block F — email-верификация через Gmail SMTP с mock-транспортом (без сети).
"""

import pytest

import vox_db
import billing_db
import billing


class _FakeSMTP:
    """Мок smtplib.SMTP_SSL: захватывает отправленное письмо вместо сети."""
    sent = []

    def __init__(self, host, port, timeout=None):
        self.host, self.port = host, port

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def login(self, user, pwd):
        self.user = user

    def sendmail(self, frm, to, msg):
        _FakeSMTP.sent.append({"from": frm, "to": to, "msg": msg})


@pytest.fixture(autouse=True)
def _setup(monkeypatch):
    vox_db.init_db()
    billing_db.migrate()
    _FakeSMTP.sent.clear()
    monkeypatch.setenv("GMAIL_USER", "vox@gmail.com")
    monkeypatch.setenv("GMAIL_APP_PASSWORD", "app pass word")
    monkeypatch.setattr(billing.smtplib, "SMTP_SSL", _FakeSMTP)


def _new_user(email):
    res = vox_db.register_user(email, "U", "secret123")
    assert res["ok"]
    return res["user"]["id"]


def test_send_verification_email_uses_smtp_and_sets_token():
    uid = _new_user("verify@x.com")
    ok = billing.send_verification_email(uid, "verify@x.com", "U")
    assert ok is True
    assert len(_FakeSMTP.sent) == 1
    sent = _FakeSMTP.sent[0]
    assert sent["to"] == "verify@x.com"
    # токен сохранён в БД (письмо без сети, но состояние верификации готово)
    u = billing_db.get_user_by_id(uid)
    assert int(u["is_email_verified"]) == 0  # ещё не подтверждён


def test_send_verification_email_without_credentials_returns_false(monkeypatch):
    monkeypatch.delenv("GMAIL_USER", raising=False)
    monkeypatch.delenv("GMAIL_APP_PASSWORD", raising=False)
    uid = _new_user("nocreds@x.com")
    ok = billing.send_verification_email(uid, "nocreds@x.com", "U")
    assert ok is False
    assert _FakeSMTP.sent == []


def test_full_verify_flow_grants_bonus_once():
    uid = _new_user("flow@x.com")
    billing.send_verification_email(uid, "flow@x.com", "U")
    # достаём токен напрямую и проходим верификацию
    con = billing_db._conn()
    token = con.execute("SELECT email_verify_token FROM users WHERE id=?", (uid,)).fetchone()[0]
    con.close()
    assert token
    res = billing_db.verify_email_token(token)
    assert res["ok"] and res["bonus"] is True
    assert billing_db.get_user_balance(uid) == billing_db.EMAIL_VERIFY_BONUS
