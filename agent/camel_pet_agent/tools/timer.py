"""Timer tool.

The agent calls `set_timer(seconds, message)` to schedule a nudge. The
TimerService (owned by the server) holds an asyncio loop reference and
pushes events back to the client when timers fire.
"""
from __future__ import annotations

import asyncio
import itertools
import time
from dataclasses import dataclass
from typing import Awaitable, Callable, Optional


OnFire = Callable[[int, str], Awaitable[None]]


@dataclass
class _Timer:
    id: int
    fire_at: float
    message: str
    task: asyncio.Task


class TimerService:
    """Tracks outstanding timers and invokes a callback when they fire.

    The callback runs on the service's event loop, so it's safe to send
    WebSocket messages from inside it.
    """

    def __init__(self, on_fire: OnFire, loop: asyncio.AbstractEventLoop | None = None):
        self._on_fire = on_fire
        self._loop = loop or asyncio.get_event_loop()
        self._ids = itertools.count(1)
        self._timers: dict[int, _Timer] = {}

    def schedule(self, seconds: float, message: str) -> int:
        tid = next(self._ids)
        fire_at = time.time() + max(1.0, float(seconds))

        async def _run():
            try:
                await asyncio.sleep(max(0.0, fire_at - time.time()))
                await self._on_fire(tid, message)
            finally:
                self._timers.pop(tid, None)

        task = self._loop.create_task(_run())
        self._timers[tid] = _Timer(tid, fire_at, message, task)
        return tid

    def cancel(self, tid: int) -> bool:
        t = self._timers.pop(tid, None)
        if not t:
            return False
        t.task.cancel()
        return True

    def pending(self) -> list[dict]:
        now = time.time()
        return [
            {"id": t.id, "message": t.message, "remaining_s": max(0, int(t.fire_at - now))}
            for t in self._timers.values()
        ]

    def shutdown(self) -> None:
        for t in list(self._timers.values()):
            t.task.cancel()
        self._timers.clear()


# Thread-safe "set_timer" factory bound to the server's TimerService.
# The agent runs in a worker thread (via asyncio.to_thread), so we need
# to hop back to the service's loop when scheduling.
def make_set_timer(service: TimerService) -> Callable[[int, str], str]:
    loop = service._loop

    def set_timer(seconds: int, message: str) -> str:
        """Schedule a timer that notifies the user after `seconds` seconds.

        Args:
            seconds: Delay before the timer fires, in seconds.
            message: A short message the camel will say when the timer fires.

        Returns:
            A human-readable confirmation string including the timer id.
        """
        future = asyncio.run_coroutine_threadsafe(
            _schedule_on_loop(service, seconds, message), loop
        )
        tid = future.result(timeout=5)
        return f"Timer #{tid} set for {seconds}s — I'll let you know."

    return set_timer


async def _schedule_on_loop(service: TimerService, seconds: int, message: str) -> int:
    return service.schedule(seconds, message)
