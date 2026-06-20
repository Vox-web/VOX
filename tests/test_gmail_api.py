"""
Block — отправка через Gmail API по HTTPS (основной путь на Railway, где
исходящий SMTP заблокирован). Все HTTP-вызовы мокаются: реальная сеть не
используется.
"""

import pytest

import billing


class _FakeResp:
    def __init__(self, status_code, json_data=None):
        self.status_code = status_code
        self._j = json_data or {}

    def json(self):
        return self._j


@pytest.fixture
def gmail_api_env(monkeypatch):
    """OAuth2-креды Gmail API заданы (HTTPS-путь активен)."""
    monkeypatch.setenv("GMAIL_USER", "vox@gmail.com")
    monkeypatch.setenv("GMAIL_CLIENT_ID", "cid.apps.googleusercontent.com")
    monkeypatch.setenv("GMAIL_CLIENT_SECRET", "secret")
    monkeypatch.setenv("GMAIL_REFRESH_TOKEN", "1//refresh")
    # На всякий случай: SMTP-креды отсутствуют, чтобы тестировать именно API-путь.
    monkeypatch.delenv("GMAIL_APP_PASSWORD", raising=False)


def _install_http(monkeypatch, token_resp, send_resp, captured=None):
    def fake_post(url, *a, **k):
        if "oauth2.googleapis.com" in url:
            return token_resp() if callable(token_resp) else token_resp
        if "gmail.googleapis.com" in url:
            if captured is not None:
                captured["headers"] = k.get("headers")
                captured["json"] = k.get("json")
            return send_resp() if callable(send_resp) else send_resp
        raise AssertionError("unexpected url " + url)

    monkeypatch.setattr(billing.httpx, "post", fake_post)


def test_gmail_api_configured_detection(gmail_api_env):
    assert billing.gmail_api_configured() is True


def test_gmail_api_send_success(monkeypatch, gmail_api_env):
    captured = {}
    _install_http(
        monkeypatch,
        token_resp=_FakeResp(200, {"access_token": "ya29.token"}),
        send_resp=_FakeResp(200, {"id": "msg-1"}),
        captured=captured,
    )
    ok, detail = billing._deliver_via_gmail_api(b"RAW-MIME-BYTES", "user@x.com")
    assert ok is True and detail == "gmail_api"
    # access_token уходит в заголовок Authorization, тело несёт raw.
    assert captured["headers"]["Authorization"] == "Bearer ya29.token"
    assert "raw" in captured["json"]


def test_gmail_api_oauth_failure(monkeypatch, gmail_api_env):
    _install_http(
        monkeypatch,
        token_resp=_FakeResp(400, {"error": "invalid_grant"}),
        send_resp=_FakeResp(200, {"id": "msg-1"}),
    )
    ok, detail = billing._deliver_via_gmail_api(b"RAW", "user@x.com")
    assert ok is False and detail == "oauth_failed"


def test_gmail_api_send_non_2xx(monkeypatch, gmail_api_env):
    _install_http(
        monkeypatch,
        token_resp=_FakeResp(200, {"access_token": "ya29.token"}),
        send_resp=_FakeResp(403, {"error": "forbidden"}),
    )
    ok, detail = billing._deliver_via_gmail_api(b"RAW", "user@x.com")
    assert ok is False and detail == "http_403"


def test_gmail_api_send_timeout(monkeypatch, gmail_api_env):
    def fake_post(url, *a, **k):
        if "oauth2.googleapis.com" in url:
            return _FakeResp(200, {"access_token": "ya29.token"})
        raise billing.httpx.TimeoutException("timed out")

    monkeypatch.setattr(billing.httpx, "post", fake_post)
    ok, detail = billing._deliver_via_gmail_api(b"RAW", "user@x.com")
    assert ok is False and detail.startswith("transport:")


def test_send_verification_prefers_api_over_smtp(monkeypatch, gmail_api_env):
    """Когда настроен Gmail API — используем его, SMTP не трогаем."""
    import vox_db
    import billing_db

    vox_db.init_db()
    billing_db.migrate()

    # SMTP должен быть недоступен — если код к нему обратится, тест упадёт.
    def _smtp_must_not_be_used(*a, **k):
        raise AssertionError("SMTP must not be used when Gmail API is configured")

    monkeypatch.setattr(billing.smtplib, "SMTP_SSL", _smtp_must_not_be_used)
    monkeypatch.setattr(billing.smtplib, "SMTP", _smtp_must_not_be_used)
    _install_http(
        monkeypatch,
        token_resp=_FakeResp(200, {"access_token": "ya29.token"}),
        send_resp=_FakeResp(200, {"id": "msg-1"}),
    )

    res = vox_db.register_user("apiuser@x.com", "U", "secret123")
    uid = res["user"]["id"]
    assert billing.send_verification_email(uid, "apiuser@x.com", "U") is True
    assert billing_db.get_verification_meta(uid)["delivery_state"] == "sent"
