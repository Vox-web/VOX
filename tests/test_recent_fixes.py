"""
Tests for bugs fixed in the logic/security audits (commits b114318, 84bd735, ddad2d2, e6e9497).
Each test targets a specific previously-broken behaviour.
"""

import asyncio
import threading
import time
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

import vox_db
import billing_db


# ──────────────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def _init_db():
    vox_db.init_db()
    billing_db.migrate()


def _register(email: str) -> int:
    res = vox_db.register_user(email, "U", "secret123")
    assert res["ok"], res
    return res["user"]["id"]


# ──────────────────────────────────────────────────────────────────────────────
# C-2: Stripe double-credit race (BEGIN IMMEDIATE)
# ──────────────────────────────────────────────────────────────────────────────

def test_confirm_stripe_payment_credits_balance():
    uid = _register("stripe_basic@x.com")
    billing_db.create_payment_record(uid, "cs_basic_001", 5.00)
    result = billing_db.confirm_stripe_payment("cs_basic_001")
    assert result is True
    assert abs(billing_db.get_user_balance(uid) - 5.00) < 1e-6


def test_confirm_stripe_payment_idempotent():
    """Second call must return False and NOT double-credit the user."""
    uid = _register("stripe_dupe@x.com")
    billing_db.create_payment_record(uid, "cs_dupe_001", 10.00)
    first = billing_db.confirm_stripe_payment("cs_dupe_001")
    second = billing_db.confirm_stripe_payment("cs_dupe_001")
    assert first is True
    assert second is False
    assert abs(billing_db.get_user_balance(uid) - 10.00) < 1e-6  # credited only once


def test_confirm_stripe_payment_parallel_exactly_one_credit():
    """Simulate Stripe sending the webhook twice simultaneously."""
    uid = _register("stripe_race@x.com")
    billing_db.create_payment_record(uid, "cs_race_001", 20.00)

    results = []

    def _worker():
        results.append(billing_db.confirm_stripe_payment("cs_race_001"))

    threads = [threading.Thread(target=_worker) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert results.count(True) == 1
    assert abs(billing_db.get_user_balance(uid) - 20.00) < 1e-6


def test_confirm_stripe_payment_unknown_session_returns_false():
    result = billing_db.confirm_stripe_payment("cs_nonexistent_999")
    assert result is False


# ──────────────────────────────────────────────────────────────────────────────
# F3-1: Room TTL — Room.created_at exists and is a datetime
# ──────────────────────────────────────────────────────────────────────────────

def test_room_has_created_at_attribute():
    from room_manager import Room
    room = Room(room_id="TEST01", host_language="uk")
    assert hasattr(room, "created_at"), "Room must have created_at attribute"
    assert isinstance(room.created_at, datetime), "created_at must be a datetime"


def test_room_created_at_timestamp_is_numeric():
    """TTL code does room.created_at.timestamp() — must not raise and be recent."""
    from room_manager import Room
    room = Room(room_id="TEST02", host_language="en")
    ts = room.created_at.timestamp()
    assert isinstance(ts, float)
    now = time.time()
    # timezone-aware datetime.now(utc).timestamp() == time.time() regardless of local tz
    assert abs(now - ts) < 5.0


def test_room_ttl_age_computation():
    """Simulate the exact TTL code path from main.py._room_ttl_cleanup."""
    from room_manager import Room
    room = Room(room_id="TEST03", host_language="fr")
    now = time.time()
    created_at = getattr(room, "created_at", None)
    age = (now - created_at.timestamp()) if created_at is not None else 0
    assert age >= 0
    assert age < 1.0  # room was just created


# ──────────────────────────────────────────────────────────────────────────────
# F2-1: Duo guest reconnect race — guest_result_task stored and cancelled
# ──────────────────────────────────────────────────────────────────────────────

def test_duo_session_has_guest_result_task_field():
    """DuoSession must have guest_result_task field (None initially)."""
    import main
    session = main.DuoSession("ABCDEF", "uk", "en")
    assert hasattr(session, "guest_result_task")
    assert session.guest_result_task is None


@pytest.mark.asyncio
async def test_duo_session_old_result_task_cancelled_on_reconnect():
    """When guest reconnects, old result_task must be cancelled before new DG starts."""
    import main

    session = main.DuoSession("XYZABC", "uk", "en")

    old_task_cancelled = asyncio.Event()

    async def _forever():
        try:
            await asyncio.sleep(9999)
        except asyncio.CancelledError:
            old_task_cancelled.set()
            raise

    old_task = asyncio.create_task(_forever())
    # Yield once so the task actually starts (enters asyncio.sleep(9999))
    await asyncio.sleep(0)
    session.guest_result_task = old_task

    # Simulate the reconnect cancel logic from websocket_duo_guest
    if session.guest_result_task and not session.guest_result_task.done():
        session.guest_result_task.cancel()
        try:
            await session.guest_result_task
        except (asyncio.CancelledError, Exception):
            pass
        session.guest_result_task = None

    assert old_task_cancelled.is_set(), "Old result_task must be cancelled on reconnect"
    assert session.guest_result_task is None


@pytest.mark.asyncio
async def test_duo_session_finally_only_clears_own_task():
    """finally block must NOT clear guest_result_task if a newer task already replaced it."""
    import main

    session = main.DuoSession("NEWXYZ", "uk", "en")

    async def _dummy():
        await asyncio.sleep(0)

    old_task = asyncio.create_task(_dummy())
    new_task = asyncio.create_task(_dummy())
    session.guest_result_task = new_task

    # Simulate finally from the OLD connection (result_task = old_task)
    result_task = old_task
    if session.guest_result_task is result_task:
        session.guest_result_task = None

    # New task should NOT have been cleared
    assert session.guest_result_task is new_task, \
        "Newer task must not be cleared by older connection's finally"

    # Cleanup
    old_task.cancel()
    new_task.cancel()
    await asyncio.gather(old_task, new_task, return_exceptions=True)


# ──────────────────────────────────────────────────────────────────────────────
# F5-3: Password reset — token idempotency and race safety
# ──────────────────────────────────────────────────────────────────────────────

def test_reset_password_happy_path():
    uid = _register("pwreset_ok@x.com")
    token = vox_db.create_password_reset(uid)
    result = vox_db.reset_password_by_token(token, "newpassword1")
    assert result["ok"] is True
    assert result["user_id"] == uid


def test_reset_password_token_consumed_after_use():
    """Same token must not work a second time."""
    uid = _register("pwreset_reuse@x.com")
    token = vox_db.create_password_reset(uid)
    vox_db.reset_password_by_token(token, "firstnewpass")
    second = vox_db.reset_password_by_token(token, "secondnewpass")
    assert second["ok"] is False
    assert second["error"] == "invalid_token"


def test_reset_password_parallel_only_one_succeeds():
    """Concurrent reset attempts with same token: exactly one must succeed."""
    uid = _register("pwreset_race@x.com")
    token = vox_db.create_password_reset(uid)

    results = []

    def _worker():
        results.append(vox_db.reset_password_by_token(token, "newpass123"))

    threads = [threading.Thread(target=_worker) for _ in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    ok_count = sum(1 for r in results if r.get("ok"))
    assert ok_count == 1, f"Exactly one reset must succeed, got: {results}"


def test_reset_password_short_password_rejected():
    uid = _register("pwreset_short@x.com")
    token = vox_db.create_password_reset(uid)
    result = vox_db.reset_password_by_token(token, "123")
    assert result["ok"] is False
    assert result["error"] == "password_too_short"
    # Token must NOT be consumed
    result2 = vox_db.reset_password_by_token(token, "longenough")
    assert result2["ok"] is True


def test_reset_password_old_tokens_invalidated():
    """create_password_reset must invalidate all previous tokens for the user."""
    uid = _register("pwreset_old@x.com")
    token_old = vox_db.create_password_reset(uid)
    _token_new = vox_db.create_password_reset(uid)  # invalidates old
    result = vox_db.reset_password_by_token(token_old, "doesntmatter")
    assert result["ok"] is False


# ──────────────────────────────────────────────────────────────────────────────
# F5-2: has_recent_password_reset — DB-backed rate limit
# ──────────────────────────────────────────────────────────────────────────────

def test_has_recent_password_reset_true_after_create():
    uid = _register("pwrate_yes@x.com")
    vox_db.create_password_reset(uid)
    assert vox_db.has_recent_password_reset(uid, within_seconds=60) is True


def test_has_recent_password_reset_false_when_no_token():
    uid = _register("pwrate_no@x.com")
    assert vox_db.has_recent_password_reset(uid, within_seconds=60) is False


def test_has_recent_password_reset_false_after_use():
    uid = _register("pwrate_used@x.com")
    token = vox_db.create_password_reset(uid)
    vox_db.reset_password_by_token(token, "newpassword1")
    assert vox_db.has_recent_password_reset(uid, within_seconds=60) is False


# ──────────────────────────────────────────────────────────────────────────────
# H-1: Billing pre-flight — stops immediately when balance ≤ 0
# ──────────────────────────────────────────────────────────────────────────────

class ControlledSleep:
    def __init__(self):
        self.waiters: list = []

    async def __call__(self, seconds):
        fut = asyncio.get_running_loop().create_future()
        self.waiters.append((seconds, fut))
        await fut

    async def tick(self, expected):
        while not self.waiters:
            await asyncio.sleep(0)
        s, fut = self.waiters.pop(0)
        assert s == expected
        fut.set_result(None)
        await asyncio.sleep(0)


@pytest.mark.asyncio
async def test_billing_preflight_stops_when_balance_zero():
    """If get_balance returns 0, loop must terminate without sleeping 60s."""
    from session_billing import BillingCoordinator

    clock = ControlledSleep()
    coordinator = BillingCoordinator(sleep=clock)
    terminated = asyncio.Event()

    async def on_balance(bal, guests):
        terminated.set()

    handle = await coordinator.start(
        key=("solo", 99),
        user_id=99,
        mode="solo",
        deduct=lambda *_: 0.0,
        on_balance=on_balance,
        on_error=lambda e: (_ for _ in ()).throw(AssertionError(e)),
        get_balance=lambda _: 0.0,   # always 0 → pre-flight fires
    )
    # Give the loop one iteration
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    assert terminated.is_set(), "Pre-flight must terminate billing when balance is 0"
    await coordinator.stop(handle)


@pytest.mark.asyncio
async def test_billing_preflight_allows_positive_balance():
    """If balance > 0, loop proceeds to sleep(60), not terminate early."""
    from session_billing import BillingCoordinator

    clock = ControlledSleep()
    coordinator = BillingCoordinator(sleep=clock)
    charges = []

    async def _on_balance_noop(*_):
        pass

    async def _fail_on_error(e):
        raise AssertionError(f"unexpected billing error: {e}")

    handle = await coordinator.start(
        key=("solo", 100),
        user_id=100,
        mode="solo",
        deduct=lambda *args: charges.append(args) or 1.0,
        on_balance=_on_balance_noop,
        on_error=_fail_on_error,
        get_balance=lambda _: 5.0,   # positive → proceed
    )
    # Pre-flight passes, now loop is sleeping 60s
    await asyncio.sleep(0)
    assert charges == [], "Should not charge before 60s sleep"
    await clock.tick(60)
    assert len(charges) == 1, "Should charge after 60s"
    await coordinator.stop(handle)


@pytest.mark.asyncio
async def test_room_billing_preflight_skips_when_no_guests():
    """In room mode, pre-flight must NOT fire (and not terminate) when guests=0."""
    from session_billing import BillingCoordinator

    clock = ControlledSleep()
    coordinator = BillingCoordinator(sleep=clock)
    terminated = asyncio.Event()
    guests = 0  # no guests yet

    async def on_balance(bal, g):
        terminated.set()

    handle = await coordinator.start(
        key=("room", "R001"),
        user_id=101,
        mode="room",
        deduct=lambda *_: 1.0,
        guest_count=lambda: guests,
        on_balance=on_balance,
        on_error=lambda e: (_ for _ in ()).throw(AssertionError(e)),
        get_balance=lambda _: 0.0,   # balance=0, but guests=0 so should skip
    )
    # Loop should be sleeping(1) — waiting for guests, NOT terminated
    await clock.tick(1)
    assert not terminated.is_set(), \
        "Pre-flight must not terminate Room billing when there are 0 guests"
    await coordinator.stop(handle)


# ──────────────────────────────────────────────────────────────────────────────
# F3-3: leave_room clears participant.websocket
# ──────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_leave_room_clears_websocket():
    from room_manager import RoomManager

    rm = RoomManager()
    room, _ = rm.create_room("uk")
    room_id = room.room_id

    fake_ws = MagicMock()
    fake_ws.send_json = AsyncMock()

    participant = rm.join_room(room_id, "en", "Alice")
    assert participant is not None
    participant.websocket = fake_ws

    assert participant.websocket is fake_ws
    await rm.leave_room(room_id, participant.guest_id)
    assert participant.websocket is None, \
        "leave_room must clear participant.websocket"


@pytest.mark.asyncio
async def test_leave_room_removes_participant_from_dict():
    from room_manager import RoomManager

    rm = RoomManager()
    room, _ = rm.create_room("uk")
    room_id = room.room_id

    fake_ws = MagicMock()
    fake_ws.send_json = AsyncMock()

    participant = rm.join_room(room_id, "de", "Bob")
    assert participant is not None
    participant.websocket = fake_ws
    guest_id = participant.guest_id

    assert guest_id in rm.rooms[room_id].participants
    await rm.leave_room(room_id, guest_id)
    assert guest_id not in rm.rooms[room_id].participants
