"""
Block B — бонус за верификацию email, гейт баланса, room billing.
"""

import threading

import pytest

import vox_db
import billing_db


@pytest.fixture(autouse=True)
def _init_db():
    vox_db.init_db()
    billing_db.migrate()


def _register(email: str) -> int:
    res = vox_db.register_user(email, "U", "secret123")
    assert res["ok"], res
    return res["user"]["id"]


# ── Бонус ───────────────────────────────────────────────────────────────────

def test_registration_gives_no_bonus():
    uid = _register("nobonus@x.com")
    assert billing_db.get_user_balance(uid) == 0.0
    u = billing_db.get_user_by_id(uid)
    assert int(u["bonus_given"]) == 0


def test_verification_grants_single_bonus():
    uid = _register("bonus1@x.com")
    token = billing_db.generate_verify_token(uid)
    res = billing_db.verify_email_token(token)
    assert res["ok"] and res["bonus"] is True
    assert billing_db.get_user_balance(uid) == billing_db.EMAIL_VERIFY_BONUS


def test_second_verification_no_second_bonus():
    uid = _register("bonus2@x.com")
    token = billing_db.generate_verify_token(uid)
    billing_db.verify_email_token(token)
    # повторно генерируем токен и верифицируем снова — бонус уже выдан
    token2 = billing_db.generate_verify_token(uid)
    res2 = billing_db.verify_email_token(token2)
    assert res2["ok"] and res2["bonus"] is False
    assert billing_db.get_user_balance(uid) == billing_db.EMAIL_VERIFY_BONUS  # ровно один бонус


def test_parallel_verification_grants_exactly_one_bonus():
    uid = _register("race@x.com")
    token = billing_db.generate_verify_token(uid)

    results = []

    def _worker():
        results.append(billing_db.verify_email_token(token))

    threads = [threading.Thread(target=_worker) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    bonus_count = sum(1 for r in results if r.get("bonus"))
    assert bonus_count == 1, results
    # баланс увеличился ровно на один бонус
    assert billing_db.get_user_balance(uid) == billing_db.EMAIL_VERIFY_BONUS


# ── Гейт баланса ─────────────────────────────────────────────────────────────

def test_can_start_session_threshold():
    uid = _register("gate@x.com")
    assert billing_db.can_start_session(uid) is False  # баланс 0
    billing_db.update_balance(uid, billing_db.MIN_BALANCE_TO_START - 0.01)
    assert billing_db.can_start_session(uid) is False
    billing_db.update_balance(uid, 0.01)
    assert billing_db.can_start_session(uid) is True


# ── Room billing ─────────────────────────────────────────────────────────────

@pytest.mark.parametrize("guests,expected", [(0, False), (1, True), (5, True)])
def test_room_billable(guests, expected):
    assert billing_db.room_billable(guests) is expected


def test_room_deduct_scales_with_guests():
    uid = _register("room@x.com")
    billing_db.update_balance(uid, 10.0)
    before = billing_db.get_user_balance(uid)
    billing_db.deduct_session_cost(uid, "room", 3)  # 3 гостя × 0.05
    after = billing_db.get_user_balance(uid)
    assert abs((before - after) - 3 * billing_db.DEFAULT_PRICE_PER_MIN) < 1e-9
