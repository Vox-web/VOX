"""
Block — email_provider: Resend HTTP API через mock (без реальной сети).

Покрывает: success / non-2xx / timeout / неполная конфигурация / неизвестный
провайдер. Реальные HTTP-вызовы Resend замоканы (email_provider.httpx.post).
"""

import pytest

import email_provider


class _FakeResp:
    def __init__(self, status_code):
        self.status_code = status_code


@pytest.fixture(autouse=True)
def _resend_env(monkeypatch):
    monkeypatch.setenv("EMAIL_PROVIDER", "resend")
    monkeypatch.setenv("RESEND_API_KEY", "re_test_key")
    monkeypatch.setenv("MAIL_FROM", "VOX <noreply@vox.test>")


def test_resend_success(monkeypatch):
    captured = {}

    def fake_post(url, headers=None, json=None, timeout=None):
        captured["url"] = url
        captured["headers"] = headers
        captured["json"] = json
        return _FakeResp(200)

    monkeypatch.setattr(email_provider.httpx, "post", fake_post)
    res = email_provider.send_email("u@x.com", "Subj", "<b>hi</b>")
    assert res["ok"] is True and res["state"] == "sent" and res["provider"] == "resend"
    assert captured["url"] == email_provider.RESEND_API_URL
    assert captured["json"]["to"] == ["u@x.com"]
    # API key передаётся в заголовке, но не должен утекать в state/ошибку.
    assert "re_test_key" in captured["headers"]["Authorization"]


def test_resend_non_2xx_is_failure(monkeypatch):
    monkeypatch.setattr(email_provider.httpx, "post", lambda *a, **k: _FakeResp(422))
    res = email_provider.send_email("u@x.com", "S", "<b>x</b>")
    assert res["ok"] is False and res["state"] == "failed"
    assert res["error"] == "http_422"


def test_resend_timeout_is_failure(monkeypatch):
    def boom(*a, **k):
        raise email_provider.httpx.TimeoutException("timeout")

    monkeypatch.setattr(email_provider.httpx, "post", boom)
    res = email_provider.send_email("u@x.com", "S", "<b>x</b>")
    assert res["ok"] is False and res["state"] == "failed"
    assert res["error"] == "transport_error"


def test_resend_not_configured_does_not_fake_success(monkeypatch):
    monkeypatch.delenv("RESEND_API_KEY", raising=False)
    # httpx не должен вызываться при неполной конфигурации.
    monkeypatch.setattr(
        email_provider.httpx, "post",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("must not call httpx")),
    )
    res = email_provider.send_email("u@x.com", "S", "<b>x</b>")
    assert res["ok"] is False and res["state"] == "failed"
    assert res["error"] == "not_configured"


def test_unknown_provider_is_failure(monkeypatch):
    monkeypatch.setenv("EMAIL_PROVIDER", "carrier_pigeon")
    res = email_provider.send_email("u@x.com", "S", "<b>x</b>")
    assert res["ok"] is False and res["state"] == "failed"
    assert res["error"] == "unknown_provider"


def test_is_configured_reflects_env(monkeypatch):
    assert email_provider.is_configured("resend") is True
    monkeypatch.delenv("MAIL_FROM", raising=False)
    assert email_provider.is_configured("resend") is False
