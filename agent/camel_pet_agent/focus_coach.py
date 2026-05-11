"""Focus Coach — proactive, activity-aware nudges.

Runs a background loop that inspects recent screen-activity records
(captured by ScreenMonitor into ActivityStore). When the user has been
distracted for longer than a threshold inside the analysis window, the
coach composes a short nudge and fires it as a proactive message. A
cooldown prevents re-firing too soon.

Message composition is hybrid: a templated line by default, escalated to
an LLM-generated line on "big drift" (heavy or prolonged distraction).
The LLM helper is optional — if missing or failing, the template is
used.
"""
from __future__ import annotations

import asyncio
import logging
import random
import time
from typing import Awaitable, Callable, Iterable, Optional

from .activity_store import ActivityRecord, ActivityStore

log = logging.getLogger("camel-pet.focus")


DEFAULT_FOCUS: tuple[str, ...] = (
    "coding",
    "working",
    "reading",
    "learning",
    "design",
    "meeting",
)
DEFAULT_DISTRACTION: tuple[str, ...] = (
    "video",
    "gaming",
    "social_media",
    "browsing",
)

# Per-record duration cap, same as ActivityStore._compute_summary — keeps
# a single stale record from dominating when captures are missed.
_MAX_RECORD_SECONDS = 10 * 60


AdviceFn = Callable[[str], Awaitable[None]]
ComposeLLMFn = Callable[[dict], Awaitable[str]]


_TEMPLATES: dict[str, list[str]] = {
    "video": [
        "that video's been rolling a while. stretch, sip water, then back to it?",
        "the dunes say: one video turns into ten. pause here?",
    ],
    "gaming": [
        "the game'll still be there in five minutes. stand up first?",
        "quest log says: hydrate. then return.",
    ],
    "social_media": [
        "the feed is endless. close the loop and come back?",
        "scrolling sand. blink twice, refocus.",
    ],
    "browsing": [
        "tabs piling up. thirty seconds of eyes-closed, then refocus?",
        "lot of wandering. what were we actually looking for?",
    ],
}
_FALLBACK_TEMPLATES: list[str] = [
    "been drifting a bit. stand up, stretch, then back to the oasis.",
    "small pause? shoulders down, breath out, then back to work.",
]


def _pick_template(dominant: str | None) -> str:
    if dominant and dominant in _TEMPLATES:
        return random.choice(_TEMPLATES[dominant])
    return random.choice(_FALLBACK_TEMPLATES)


def _bucketize(
    records: list[ActivityRecord],
    focus_set: set[str],
    distraction_set: set[str],
    now: float | None = None,
) -> tuple[float, float, dict[str, float]]:
    """Return (distracted_minutes, focus_minutes, by_category_minutes)."""
    by_cat: dict[str, float] = {}
    if not records:
        return 0.0, 0.0, by_cat

    tnow = now if now is not None else time.time()
    for i, rec in enumerate(records):
        if i + 1 < len(records):
            duration = records[i + 1].timestamp - rec.timestamp
        else:
            duration = tnow - rec.timestamp
        duration = max(0.0, min(duration, _MAX_RECORD_SECONDS))
        minutes = duration / 60.0
        by_cat[rec.status] = by_cat.get(rec.status, 0.0) + minutes

    distracted = sum(m for c, m in by_cat.items() if c in distraction_set)
    focus = sum(m for c, m in by_cat.items() if c in focus_set)
    return distracted, focus, by_cat


class FocusCoach:
    """Periodically evaluates recent activity and fires proactive nudges."""

    def __init__(
        self,
        activity_store: ActivityStore,
        on_advice: AdviceFn,
        compose_llm: Optional[ComposeLLMFn] = None,
        check_interval_seconds: float = 300.0,
        window_minutes: float = 30.0,
        distracted_threshold_minutes: float = 15.0,
        cooldown_seconds: float = 900.0,
        focus_categories: Iterable[str] = DEFAULT_FOCUS,
        distraction_categories: Iterable[str] = DEFAULT_DISTRACTION,
    ):
        self._store = activity_store
        self._on_advice = on_advice
        self._compose_llm = compose_llm
        self._check_interval = max(30.0, float(check_interval_seconds))
        self._window_minutes = max(1.0, float(window_minutes))
        self._threshold_minutes = max(1.0, float(distracted_threshold_minutes))
        self._cooldown = max(0.0, float(cooldown_seconds))
        self._focus = set(focus_categories)
        self._distraction = set(distraction_categories)

        self._enabled = False
        self._task: asyncio.Task | None = None
        self._last_fired: float = 0.0

    # ── lifecycle ──────────────────────────────────────────────────

    @property
    def enabled(self) -> bool:
        return self._enabled

    def enable(self, enabled: bool) -> None:
        if enabled and not self._enabled:
            self._enabled = True
            self._task = asyncio.create_task(self._run())
            log.info(
                "focus coach enabled (check=%.0fs window=%.0fm thr=%.0fm cooldown=%.0fs)",
                self._check_interval,
                self._window_minutes,
                self._threshold_minutes,
                self._cooldown,
            )
        elif not enabled and self._enabled:
            self._enabled = False
            if self._task:
                self._task.cancel()
                self._task = None
            log.info("focus coach disabled")

    def shutdown(self) -> None:
        self.enable(False)

    def set_compose_llm(self, fn: Optional[ComposeLLMFn]) -> None:
        self._compose_llm = fn

    def reconfigure(
        self,
        *,
        check_interval_seconds: float | None = None,
        window_minutes: float | None = None,
        distracted_threshold_minutes: float | None = None,
        cooldown_seconds: float | None = None,
        focus_categories: Iterable[str] | None = None,
        distraction_categories: Iterable[str] | None = None,
    ) -> None:
        if check_interval_seconds is not None:
            self._check_interval = max(30.0, float(check_interval_seconds))
        if window_minutes is not None:
            self._window_minutes = max(1.0, float(window_minutes))
        if distracted_threshold_minutes is not None:
            self._threshold_minutes = max(1.0, float(distracted_threshold_minutes))
        if cooldown_seconds is not None:
            self._cooldown = max(0.0, float(cooldown_seconds))
        if focus_categories is not None:
            self._focus = set(focus_categories)
        if distraction_categories is not None:
            self._distraction = set(distraction_categories)

    # ── core evaluation ────────────────────────────────────────────

    async def _run(self) -> None:
        try:
            while self._enabled:
                await asyncio.sleep(self._check_interval)
                if not self._enabled:
                    return
                try:
                    await self._tick()
                except Exception:
                    log.exception("focus coach tick crashed")
        except asyncio.CancelledError:
            return

    async def _tick(self) -> None:
        now = time.time()
        if now - self._last_fired < self._cooldown:
            return

        records = self._store.get_recent(self._window_minutes * 60.0)
        distracted, focus, by_cat = _bucketize(
            records, self._focus, self._distraction, now=now
        )

        total = distracted + focus + sum(
            m for c, m in by_cat.items() if c not in self._focus and c not in self._distraction
        )
        log.debug(
            "focus tick: distracted=%.1fm focus=%.1fm total=%.1fm cats=%s",
            distracted, focus, total, by_cat,
        )

        if distracted < self._threshold_minutes:
            return

        text = await self._compose(distracted, focus, by_cat)
        if not text:
            return

        try:
            await self._on_advice(text)
        except Exception:
            log.exception("focus coach on_advice failed")
            return

        self._last_fired = time.time()
        log.info(
            "focus coach fired (distracted=%.1fm focus=%.1fm): %s",
            distracted, focus, text[:80],
        )

    async def _compose(
        self,
        distracted: float,
        focus: float,
        by_cat: dict[str, float],
    ) -> str:
        # Dominant distraction category (largest bucket inside distraction set).
        distraction_only = {c: m for c, m in by_cat.items() if c in self._distraction}
        dominant = max(distraction_only, key=distraction_only.get) if distraction_only else None

        share = (
            distracted / (distracted + focus)
            if (distracted + focus) > 0
            else 1.0
        )
        escalate = (
            distracted >= 2.0 * self._threshold_minutes
            or (distracted >= self._threshold_minutes and share >= 0.8)
        )

        if escalate and self._compose_llm is not None:
            try:
                context = {
                    "window_minutes": self._window_minutes,
                    "distracted_minutes": round(distracted, 1),
                    "focus_minutes": round(focus, 1),
                    "dominant_distraction": dominant,
                    "by_category_minutes": {c: round(m, 1) for c, m in by_cat.items()},
                }
                text = await self._compose_llm(context)
                if text and text.strip():
                    return text.strip()
                log.warning("focus coach LLM returned empty; falling back to template")
            except Exception:
                log.exception("focus coach LLM compose failed; falling back to template")

        return _pick_template(dominant)
