import asyncio
from types import SimpleNamespace

import pytest

import billing_db
import main
from session_billing import BillingCoordinator, count_billable_room_guests


class ControlledSleep:
    def __init__(self):
        self.waiters = []

    async def __call__(self, seconds):
        assert seconds in (1, 60)
        future = asyncio.get_running_loop().create_future()
        self.waiters.append((seconds, future))
        await future

    async def advance(self, expected_seconds):
        while True:
            while not self.waiters:
                await asyncio.sleep(0)
            seconds, future = self.waiters.pop(0)
            if not future.done():
                assert seconds == expected_seconds
                future.set_result(None)
                break
        await asyncio.sleep(0)

    async def minute(self):
        await self.advance(60)


@pytest.mark.asyncio
async def test_no_deduction_before_first_real_minute_then_one_charge():
    clock = ControlledSleep()
    coordinator = BillingCoordinator(sleep=clock)
    charges = []
    balances = []

    handle = await coordinator.start(
        key=("solo", 7), user_id=7, mode="solo",
        deduct=lambda *args: charges.append(args) or 0.95,
        on_balance=lambda balance, guests: _record(balances, (balance, guests)),
        on_error=_fail,
    )
    await asyncio.sleep(0)
    assert charges == []

    await clock.minute()
    assert charges == [(7, "solo", 0)]
    assert balances == [(0.95, 0)]
    await coordinator.stop(handle)


@pytest.mark.asyncio
async def test_room_without_guests_skips_charge_but_guest_is_charged():
    clock = ControlledSleep()
    coordinator = BillingCoordinator(sleep=clock)
    guests = 0
    charges = []
    handle = await coordinator.start(
        key=("room", "ABC"), user_id=9, mode="room",
        deduct=lambda *args: charges.append(args) or 1.0,
        guest_count=lambda: guests,
        on_balance=lambda *_: _record([], None), on_error=_fail,
    )
    await clock.advance(1)
    assert charges == []
    guests = 1
    await clock.advance(1)
    assert charges == []
    await clock.minute()
    assert charges == [(9, "room", 1)]
    await coordinator.stop(handle)


@pytest.mark.asyncio
async def test_reconnect_replaces_loop_without_parallel_charge():
    clock = ControlledSleep()
    coordinator = BillingCoordinator(sleep=clock)
    charges = []
    kwargs = dict(
        key=("duo", 4), user_id=4, mode="duo",
        deduct=lambda *args: charges.append(args) or 1.0,
        on_balance=lambda *_: _record([], None), on_error=_fail,
    )
    old = await coordinator.start(**kwargs)
    await asyncio.sleep(0)
    new = await coordinator.start(**kwargs)
    await coordinator.stop(old)  # stale handler cleanup must not stop replacement
    await clock.minute()
    assert charges == [(4, "duo", 0)]
    await coordinator.stop(new)


def _patch_start_status(monkeypatch, status):
    monkeypatch.setattr(
        billing_db, "get_account_start_status",
        lambda _user_id: {"status": status, "email_verified": status != "email_verification_required", "balance": 0.0},
    )


@pytest.mark.asyncio
async def test_start_gate_requires_email_verification(monkeypatch):
    ws = FakeWebSocket()
    _patch_start_status(monkeypatch, "email_verification_required")
    assert await main._enforce_account_start(ws, 11) is False
    # Приоритет: неподтверждённый email, НЕ «недостатньо коштів».
    assert ws.messages[-1]["code"] == "email_verification_required"


@pytest.mark.asyncio
async def test_start_gate_rejects_insufficient_balance(monkeypatch):
    ws = FakeWebSocket()
    _patch_start_status(monkeypatch, "insufficient_balance")
    assert await main._enforce_account_start(ws, 11) is False
    assert ws.messages[-1]["code"] == "insufficient_balance"


@pytest.mark.asyncio
async def test_start_gate_allows_ready_account(monkeypatch):
    ws = FakeWebSocket()
    _patch_start_status(monkeypatch, "ready")
    assert await main._enforce_account_start(ws, 11) is True
    assert ws.messages == []


@pytest.mark.asyncio
async def test_start_gate_fails_closed_when_billing_is_unavailable(monkeypatch):
    ws = FakeWebSocket()
    _patch_start_status(monkeypatch, "billing_unavailable")
    assert await main._enforce_account_start(ws, 11) is False
    assert ws.messages[-1]["code"] == "billing_unavailable"


def test_room_guest_count_excludes_host_entry():
    room = SimpleNamespace(participants={
        "host": SimpleNamespace(),
        "g1": SimpleNamespace(),
        "g2": SimpleNamespace(is_host=True),
    })
    assert count_billable_room_guests(room) == 1


async def _record(target, value):
    target.append(value)


async def _fail(exc):
    raise AssertionError(f"unexpected billing error: {exc}")


class FakeWebSocket:
    def __init__(self):
        self.messages = []

    async def send_json(self, message):
        self.messages.append(message)
