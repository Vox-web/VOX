"""
Block — единый account activation gate + onboarding endpoints.

Покрывает (по ТЗ):
  1  новый пользователь: email_verified=false, balance=0;
  2  неподтверждённый → email_verification_required (приоритет над балансом);
  3  подтверждённый с малым балансом → insufficient_balance;
  4  подтверждённый с достаточным балансом → ready;
  5-8 общий gate в Solo / Duo one-device / Duo remote host / Room host (WS);
  9-10 Room/Duo remote guest НЕ блокируются по account state;
  11-14 resend: success / cooldown / already verified / provider failure;
  + /api/account/status, token reuse, billing_unavailable (fail-closed).
"""

import pytest

import vox_db
import billing_db
import billing

main = pytest.importorskip("main", reason="full app deps not installed")
from fastapi.testclient import TestClient  # noqa: E402

client = TestClient(main.app)


@pytest.fixture(autouse=True)
def _init_db():
    vox_db.init_db()
    billing_db.migrate()


_counter = {"n": 0}


def _email(prefix):
    _counter["n"] += 1
    return f"{prefix}{_counter['n']}@x.com"


def _register(email):
    res = vox_db.register_user(email, "U", "secret123")
    assert res["ok"], res
    return res["token"], res["user"]["id"]


def _verify(uid):
    token = billing_db.generate_verify_token(uid)
    billing_db.verify_email_token(token)


def _auth(token):
    return {"Authorization": "Bearer " + token}


# ── 1-4: статус gate ──────────────────────────────────────────────────────────

def test_new_user_unverified_zero_balance():
    _tok, uid = _register(_email("new"))
    u = billing_db.get_user_by_id(uid)
    assert int(u["is_email_verified"]) == 0
    assert billing_db.get_user_balance(uid) == 0.0


def test_unverified_returns_email_verification_required_not_balance():
    _tok, uid = _register(_email("unverif"))
    # Баланс 0, но приоритет — подтверждение email.
    status = billing_db.get_account_start_status(uid)
    assert status["status"] == "email_verification_required"


def test_verified_low_balance_returns_insufficient():
    _tok, uid = _register(_email("lowbal"))
    _verify(uid)  # начисляет $3 бонус
    billing_db.admin_adjust_balance(uid, 0.10)  # ниже MIN_BALANCE_TO_START
    status = billing_db.get_account_start_status(uid)
    assert status["status"] == "insufficient_balance"
    assert status["email_verified"] is True


def test_verified_sufficient_balance_returns_ready():
    _tok, uid = _register(_email("ready"))
    _verify(uid)  # $3 бонус >= MIN
    status = billing_db.get_account_start_status(uid)
    assert status["status"] == "ready"


def test_billing_unavailable_fails_closed(monkeypatch):
    _tok, uid = _register(_email("dberr"))

    def boom(_uid):
        raise OSError("db down")

    monkeypatch.setattr(billing_db, "get_user_by_id", boom)
    status = billing_db.get_account_start_status(uid)
    assert status["status"] == "billing_unavailable"


# ── /api/account/status ───────────────────────────────────────────────────────

def test_account_status_endpoint_unverified():
    tok, _uid = _register(_email("statusapi"))
    r = client.get("/api/account/status", headers=_auth(tok))
    assert r.status_code == 200
    d = r.json()
    assert d["authenticated"] is True
    assert d["email_verified"] is False
    assert d["start_status"] == "email_verification_required"
    assert d["verification_required"] is True
    assert "token" not in d  # токен не утекает
    assert "email_verify_token" not in d


def test_account_status_endpoint_requires_auth():
    assert client.get("/api/account/status").status_code == 401


# ── 5-8: общий gate в WS host-сценариях ───────────────────────────────────────

def _expect_verification_terminal(ws_path, send_setup=None):
    tok, _uid = _register(_email("ws"))
    with client.websocket_connect(ws_path) as ws:
        if send_setup:
            send_setup(ws)
        ws.send_json({"type": "auth", "token": tok})
        msg = ws.receive_json()
    assert msg.get("code") == "email_verification_required", (ws_path, msg)


def test_gate_solo_ws():
    _expect_verification_terminal("/ws/solo")


def test_gate_duo_one_device_ws():
    pair = client.get("/api/languages").json()["duo_one_device_default"]

    def setup(ws):
        ws.send_json({"type": "config", "lang_a": pair["lang_a"], "lang_b": pair["lang_b"]})

    _expect_verification_terminal("/ws/duo", send_setup=setup)


def test_gate_duo_remote_host_ws():
    r = client.post("/duo/create", json={"lang_a": "uk", "lang_b": "en"})
    duo_id = r.json()["duo_id"]
    _expect_verification_terminal(f"/ws/duo/{duo_id}/host")


def test_gate_room_host_ws():
    # Room требует room_manager из lifespan → используем context-managed client.
    tok, _uid = _register(_email("wsroom"))
    with TestClient(main.app) as c:
        room_id = c.post("/room/create", json={"host_language": "uk"}).json()["room_id"]
        with c.websocket_connect(f"/ws/room/{room_id}/host") as ws:
            ws.send_json({"type": "auth", "token": tok})
            msg = ws.receive_json()
    assert msg.get("code") == "email_verification_required", msg


def test_gate_is_used_in_exactly_four_host_paths():
    """Один общий механизм: gate вызывается ровно в 4 host-обработчиках."""
    src = (main.__file__)
    text = open(src, encoding="utf-8").read()
    assert text.count("await _enforce_account_start(ws, _user_id)") == 4


# ── 9-10: гости НЕ блокируются по своему account state ────────────────────────

def test_duo_remote_guest_not_blocked():
    r = client.post("/duo/create", json={"lang_a": "uk", "lang_b": "en"})
    duo_id = r.json()["duo_id"]
    # Гость без верификации/баланса всё равно получает duo_ready.
    with client.websocket_connect(f"/ws/duo/{duo_id}/guest") as ws:
        msg = ws.receive_json()
    assert msg["type"] == "duo_ready"
    assert msg.get("code") != "email_verification_required"


def test_guest_ws_handlers_have_no_account_gate():
    """Платит host: guest-обработчики не вызывают account gate."""
    text = open(main.__file__, encoding="utf-8").read()
    # Вырезаем участок guest-обработчиков и проверяем отсутствие gate.
    guest_marker = '@app.websocket("/ws/duo/{duo_id}/guest")'
    assert guest_marker in text
    after = text.split(guest_marker, 1)[1].split("@app.websocket", 1)[0]
    assert "_enforce_account_start" not in after


# ── 11-14: resend verification ────────────────────────────────────────────────

def test_resend_success(monkeypatch):
    tok, _uid = _register(_email("resok"))
    monkeypatch.setattr(
        billing.email_provider, "send_email",
        lambda **k: {"ok": True, "state": "sent", "provider": "resend", "error": None},
    )
    r = client.post("/api/auth/resend-verification", headers=_auth(tok))
    assert r.status_code == 200 and r.json()["status"] == "sent"


def test_resend_cooldown(monkeypatch):
    tok, _uid = _register(_email("rescool"))
    monkeypatch.setattr(
        billing.email_provider, "send_email",
        lambda **k: {"ok": True, "state": "sent", "provider": "resend", "error": None},
    )
    assert client.post("/api/auth/resend-verification", headers=_auth(tok)).status_code == 200
    r2 = client.post("/api/auth/resend-verification", headers=_auth(tok))
    assert r2.status_code == 429
    assert r2.json()["status"] == "cooldown"


def test_resend_already_verified_does_not_send(monkeypatch):
    tok, uid = _register(_email("resverif"))
    _verify(uid)

    def must_not_call(**k):
        raise AssertionError("should not send for verified user")

    monkeypatch.setattr(billing.email_provider, "send_email", must_not_call)
    r = client.post("/api/auth/resend-verification", headers=_auth(tok))
    assert r.status_code == 200 and r.json()["status"] == "already_verified"


def test_resend_provider_failure_reports_error(monkeypatch):
    tok, _uid = _register(_email("resfail"))
    monkeypatch.setattr(
        billing.email_provider, "send_email",
        lambda **k: {"ok": False, "state": "failed", "provider": "resend", "error": "http_500"},
    )
    r = client.post("/api/auth/resend-verification", headers=_auth(tok))
    assert r.status_code == 503  # не имитируем успех


# ── token reuse ───────────────────────────────────────────────────────────────

def test_verify_token_is_reused_not_invalidated():
    _tok, uid = _register(_email("reuse"))
    t1 = billing_db.get_or_create_verify_token(uid)
    t2 = billing_db.get_or_create_verify_token(uid)
    assert t1 == t2 and t1  # повторная отправка не ломает рабочую ссылку
