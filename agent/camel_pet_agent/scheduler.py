"""Proactive message scheduler.

Watches for user inactivity and periodically fires a nudge event. The
server receives the callback and turns it into a WebSocket `nudge`
message (or a full LLM-generated proactive turn — up to the caller).
"""
from __future__ import annotations

import asyncio
import time
from typing import Awaitable, Callable


NudgeFn = Callable[[], Awaitable[None]]


class IdleScheduler:
    def __init__(
        self,
        on_nudge: NudgeFn,
        idle_seconds: float = 30 * 60,
        poll_seconds: float = 30.0,
    ):
        self._on_nudge = on_nudge
        self._idle_seconds = idle_seconds
        self._poll_seconds = poll_seconds
        self._last_activity = time.time()
        self._enabled = False
        self._task: asyncio.Task | None = None

    def touch(self) -> None:
        """Mark the user as active. Call whenever a user message arrives."""
        self._last_activity = time.time()

    def enable(self, enabled: bool) -> None:
        if enabled and not self._enabled:
            self._enabled = True
            self._task = asyncio.create_task(self._run())
        elif not enabled and self._enabled:
            self._enabled = False
            if self._task:
                self._task.cancel()
                self._task = None

    async def _run(self) -> None:
        try:
            while self._enabled:
                await asyncio.sleep(self._poll_seconds)
                if time.time() - self._last_activity >= self._idle_seconds:
                    try:
                        await self._on_nudge()
                    except Exception:
                        pass
                    self._last_activity = time.time()
        except asyncio.CancelledError:
            return

    def shutdown(self) -> None:
        self.enable(False)
