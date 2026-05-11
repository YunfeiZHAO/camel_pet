"""Screen monitor — periodic capture + vision analysis.

Runs a background async loop that captures the screen at a configurable
interval, sends the screenshot to a vision LLM for classification, and
stores the result in the ActivityStore.
"""
from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime
from typing import Awaitable, Callable, Optional

from .activity_store import ActivityStore
from .tools.screen import capture_screenshot, _screenshots_dir
from .vision_agent import ActivityResult, ActivityStatus, VisionAgent

log = logging.getLogger("camel-pet.monitor")


class ScreenMonitor:
    """Periodically captures the screen and classifies user activity."""

    def __init__(
        self,
        activity_store: ActivityStore,
        analyze_fn: Callable[[str], Awaitable[ActivityResult]],
        on_suggestion: Optional[Callable[[str], Awaitable[None]]] = None,
        interval_seconds: float = 300.0,  # 5 minutes default
    ):
        self._store = activity_store
        self._analyze_fn = analyze_fn  # async fn(image_b64) -> ActivityResult
        self._on_suggestion = on_suggestion
        self._interval = interval_seconds
        self._enabled = False
        self._task: asyncio.Task | None = None

        # Per-session directory for images and prediction log
        session_ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        self._session_dir = _screenshots_dir() / f"session_{session_ts}"
        self._session_dir.mkdir(parents=True, exist_ok=True)
        self._predictions_path = self._session_dir / "predictions.jsonl"

    @property
    def enabled(self) -> bool:
        return self._enabled

    @property
    def interval(self) -> float:
        return self._interval

    def set_interval(self, seconds: float) -> None:
        self._interval = max(10.0, seconds)  # minimum 10 seconds

    def enable(self, enabled: bool) -> None:
        if enabled and not self._enabled:
            self._enabled = True
            self._task = asyncio.create_task(self._run())
            log.info("screen monitor enabled (interval=%.0fs)", self._interval)
        elif not enabled and self._enabled:
            self._enabled = False
            if self._task:
                self._task.cancel()
                self._task = None
            log.info("screen monitor disabled")

    async def capture_and_analyze(self) -> ActivityResult | None:
        """Single capture + analysis cycle. Returns a validated ActivityResult or None."""
        try:
            image_b64 = await asyncio.to_thread(capture_screenshot, save_dir=self._session_dir)
        except Exception as e:
            log.error("screenshot capture failed: %s", e)
            return None

        try:
            result = await self._analyze_fn(image_b64)
        except Exception as e:
            log.error("activity analysis failed: %s", e)
            return None

        # Store the validated result
        self._store.insert(
            status=result.status.value,
            app=result.app,
            details=result.details,
        )
        log.info("activity recorded: status=%s app=%s", result.status.value, result.app)

        # Append prediction to per-session JSONL for debugging
        try:
            record = {
                "timestamp": datetime.now().isoformat(),
                "status": result.status.value,
                "app": result.app,
                "details": result.details,
            }
            with open(self._predictions_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(record, ensure_ascii=False) + "\n")
        except Exception as e:
            log.warning("failed to write prediction log: %s", e)

        return result

    async def _run(self) -> None:
        """Background loop: capture → analyze → store → sleep."""
        try:
            while self._enabled:
                await self.capture_and_analyze()
                await asyncio.sleep(self._interval)
        except asyncio.CancelledError:
            return
        except Exception:
            log.exception("screen monitor loop crashed")

    def shutdown(self) -> None:
        self.enable(False)
