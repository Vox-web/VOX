"""Runtime coordination for per-minute session billing."""

from __future__ import annotations

import asyncio
import contextlib
from dataclasses import dataclass
from typing import Awaitable, Callable, Hashable


@dataclass(frozen=True)
class BillingHandle:
    key: Hashable
    owner: object
    task: asyncio.Task


class BillingCoordinator:
    """Keep at most one billing loop for a logical paid session."""

    def __init__(self, sleep: Callable[[float], Awaitable[None]] = asyncio.sleep):
        self._sleep = sleep
        self._active: dict[Hashable, BillingHandle] = {}

    async def start(
        self,
        *,
        key: Hashable,
        user_id: int,
        mode: str,
        deduct: Callable[[int, str, int], float],
        guest_count: Callable[[], int] = lambda: 0,
        on_balance: Callable[[float, int], Awaitable[None]],
        on_error: Callable[[Exception], Awaitable[None]],
        get_balance: Callable[[int], float] | None = None,
    ) -> BillingHandle:
        previous = self._active.get(key)
        if previous:
            previous.task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await previous.task

        owner = object()
        task = asyncio.create_task(
            self._run(user_id, mode, deduct, guest_count, on_balance, on_error, get_balance)
        )
        handle = BillingHandle(key=key, owner=owner, task=task)
        self._active[key] = handle
        task.add_done_callback(lambda _task: self._discard_if_owner(key, owner))
        return handle

    async def stop(self, handle: BillingHandle | None) -> None:
        if handle is None:
            return
        current = self._active.get(handle.key)
        if current is None or current.owner is not handle.owner:
            return
        self._active.pop(handle.key, None)
        handle.task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await handle.task

    def _discard_if_owner(self, key: Hashable, owner: object) -> None:
        current = self._active.get(key)
        if current is not None and current.owner is owner:
            self._active.pop(key, None)

    async def _run(self, user_id, mode, deduct, guest_count, on_balance, on_error, get_balance=None):
        while True:
            try:
                if mode == "room" and max(0, int(guest_count())) == 0:
                    # A billable minute starts only while somebody is listening.
                    await self._sleep(1)
                    continue
                # Pre-flight: если баланс уже нулевой — завершить сразу, не ждать минуту.
                if get_balance is not None:
                    current = get_balance(user_id)
                    if current <= 0:
                        await on_balance(current, max(0, int(guest_count())))
                        return
                # Billing is postpaid: no charge until a full minute has elapsed.
                await self._sleep(60)
                guests = max(0, int(guest_count()))
                if mode == "room" and guests == 0:
                    continue
                new_balance = deduct(user_id, mode, guests)
                await on_balance(new_balance, guests)
                if new_balance <= 0:
                    return
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                await on_error(exc)
                return


def count_billable_room_guests(room) -> int:
    """Count guests defensively even if a future room map also stores the host."""
    return sum(
        1
        for guest_id, participant in room.participants.items()
        if guest_id != "host" and not getattr(participant, "is_host", False)
    )


billing_coordinator = BillingCoordinator()
