"""FastAPI + WebSocket sidecar for Camel Pet."""
from __future__ import annotations

import asyncio
import json
import logging
import os
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware

from .activity_store import ActivityStore
from .agent import CamelPetAgent
from .focus_coach import DEFAULT_DISTRACTION, DEFAULT_FOCUS, FocusCoach
from .memory import ChatStore
from .scheduler import IdleScheduler
from .screen_monitor import ScreenMonitor
from .tools.clipboard import get_clipboard
from .tools.timer import TimerService, make_set_timer
from .tools.activity import make_get_activity_summary

load_dotenv(Path(__file__).parent.parent / ".env", override=True)
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("camel-pet")

app = FastAPI(title="Camel Pet Agent")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


class Runtime:
    """Server-wide state that outlives a single WebSocket connection."""

    def __init__(self) -> None:
        self.store = ChatStore()
        self.activity_store = ActivityStore()
        self.agent: CamelPetAgent | None = None
        self.timers: TimerService | None = None
        self.scheduler: IdleScheduler | None = None
        self.monitor: ScreenMonitor | None = None
        self.coach: FocusCoach | None = None
        self.active_ws: WebSocket | None = None
        self.platform = os.environ.get("CAMEL_PET_PLATFORM", "anthropic").lower()
        if self.platform == "openai_compatible":
            self.model_name = os.environ.get("CAMEL_PET_MODEL", "gpt-4o-mini")
            self.api_key = os.environ.get("OPENAI_COMPATIBLE_API_KEY")
            self.base_url = os.environ.get("OPENAI_COMPATIBLE_BASE_URL")
        else:
            self.model_name = os.environ.get("CAMEL_PET_MODEL", "claude-haiku-4-5")
            self.api_key = os.environ.get("ANTHROPIC_API_KEY")
            self.base_url = None
        self.clipboard_enabled = False
        self.nudges_enabled = False
        self.screen_monitor_enabled = False
        self.monitor_interval_seconds = 300
        # Focus coach defaults
        self.focus_coach_enabled = False
        self.focus_coach_interval_seconds = 300
        self.focus_coach_window_minutes = 30
        self.distracted_threshold_minutes = 15
        self.focus_coach_cooldown_seconds = 900
        self.focus_categories: list[str] = list(DEFAULT_FOCUS)
        self.distraction_categories: list[str] = list(DEFAULT_DISTRACTION)
        # Vision model — must support image inputs (separate from chat model)
        self.vision_platform = os.environ.get("CAMEL_PET_VISION_PLATFORM", "minmax").lower()
        self.vision_model = os.environ.get("CAMEL_PET_VISION_MODEL", "MiniMax-M2.7")
        self.vision_api_key = os.environ.get("CAMEL_PET_VISION_API_KEY") or os.environ.get("MINIMAX_API_KEY") or os.environ.get("ANTHROPIC_API_KEY")
        self.vision_base_url = os.environ.get("CAMEL_PET_VISION_BASE_URL") or os.environ.get("ANTHROPIC_BASE_URL")

    def build_tools(self) -> list:
        tools: list = [
            make_set_timer(self.timers) if self.timers else None,
        ]
        if self.clipboard_enabled:
            tools.append(get_clipboard)
        if self.screen_monitor_enabled:
            tools.append(make_get_activity_summary(self.activity_store))
        return [t for t in tools if t is not None]

    def build_agent(self) -> CamelPetAgent:
        return CamelPetAgent(
            model_name=self.model_name,
            api_key=self.api_key,
            tools=self.build_tools(),
            store=self.store,
            platform=self.platform,
            url=self.base_url,
        )

    def rebuild_agent(self) -> None:
        self.agent = self.build_agent()
        # Invalidate vision agent so it picks up new config
        self._vision_agent = None


RT = Runtime()


async def _send(ws: WebSocket | None, payload: dict[str, Any]) -> None:
    if ws is None:
        return
    try:
        await ws.send_text(json.dumps(payload))
    except Exception:
        pass


async def _focus_llm_compose(context: dict) -> str:
    """One-shot ChatAgent call for a 'big drift' focus nudge.

    Builds a throwaway ChatAgent (separate memory) so the main chat
    history stays clean. Matches CamelPetAgent's current model wiring
    (ANTHROPIC platform, which serves both real Anthropic and MiniMax).
    Any failure is swallowed by FocusCoach which falls back to a template.
    """
    from camel.agents import ChatAgent
    from camel.messages import BaseMessage
    from camel.models import ModelFactory
    from camel.types import ModelPlatformType, RoleType

    key = RT.api_key or os.environ.get("MINIMAX_API_KEY") or os.environ.get("ANTHROPIC_API_KEY")
    base_url = RT.base_url or os.environ.get("ANTHROPIC_BASE_URL")
    model = ModelFactory.create(
        model_platform=ModelPlatformType.ANTHROPIC,
        model_type=RT.model_name,
        api_key=key,
        url=base_url,
    )
    system = BaseMessage(
        role_name="Camel",
        role_type=RoleType.ASSISTANT,
        meta_dict={},
        content=(
            "You are a small desert camel desktop pet. Voice: curious, "
            "gently sarcastic, warm, economical. Occasional light "
            "desert / dune / stars / oasis reference is fine but not every "
            "time. The user has been distracted for a while. Write ONE "
            "short sentence (max 20 words), in your voice, gently nudging "
            "them to refocus, stretch, hydrate, or take a real break. "
            "No quotes, no lists, no preamble."
        ),
    )
    agent = ChatAgent(system_message=system, model=model, message_window_size=1)

    cats = context.get("by_category_minutes", {})
    top = context.get("dominant_distraction") or "distractions"
    user_prompt = (
        f"Last {context['window_minutes']:.0f} min: "
        f"{context['distracted_minutes']:.0f} min distracted "
        f"({top} dominant), {context['focus_minutes']:.0f} min focused. "
        f"Breakdown: {cats}. One sentence."
    )
    user_msg = BaseMessage(
        role_name="User",
        role_type=RoleType.USER,
        meta_dict={},
        content=user_prompt,
    )

    def _call() -> str:
        resp = agent.step(user_msg)
        return resp.msgs[0].content if resp.msgs else ""

    text = await asyncio.to_thread(_call)
    return (text or "").strip()


async def _vision_analyze(image_b64: str):
    """Send a screenshot to the VisionAgent for activity classification."""
    from .vision_agent import ActivityResult, VisionAgent

    # Lazy-init a VisionAgent using vision-specific config (not chat model)
    if not hasattr(RT, "_vision_agent") or RT._vision_agent is None:
        RT._vision_agent = VisionAgent(
            model_name=RT.vision_model,
            api_key=RT.vision_api_key,
            platform=RT.vision_platform,
            url=RT.vision_base_url,
        )
    return await asyncio.to_thread(RT._vision_agent.analyze, image_b64)


@app.on_event("startup")
async def _startup() -> None:
    loop = asyncio.get_event_loop()

    async def on_timer_fire(tid: int, message: str) -> None:
        log.info("timer #%s fired: %s", tid, message)
        await _send(
            RT.active_ws,
            {"type": "timer_fired", "id": tid, "message": message},
        )

    async def on_nudge() -> None:
        log.info("idle nudge")
        await _send(RT.active_ws, {"type": "nudge"})

    async def on_focus_advice(text: str) -> None:
        await _send(RT.active_ws, {"type": "proactive", "text": text})

    RT.timers = TimerService(on_timer_fire, loop=loop)
    RT.scheduler = IdleScheduler(on_nudge)
    RT.monitor = ScreenMonitor(
        activity_store=RT.activity_store,
        analyze_fn=_vision_analyze,
        interval_seconds=RT.monitor_interval_seconds,
    )
    RT.coach = FocusCoach(
        activity_store=RT.activity_store,
        on_advice=on_focus_advice,
        compose_llm=_focus_llm_compose,
        check_interval_seconds=RT.focus_coach_interval_seconds,
        window_minutes=RT.focus_coach_window_minutes,
        distracted_threshold_minutes=RT.distracted_threshold_minutes,
        cooldown_seconds=RT.focus_coach_cooldown_seconds,
        focus_categories=RT.focus_categories,
        distraction_categories=RT.distraction_categories,
    )


@app.on_event("shutdown")
async def _shutdown() -> None:
    if RT.timers:
        RT.timers.shutdown()
    if RT.scheduler:
        RT.scheduler.shutdown()
    if RT.monitor:
        RT.monitor.shutdown()
    if RT.coach:
        RT.coach.shutdown()
    RT.activity_store.close()
    RT.store.close()


@app.get("/health")
async def health():
    return {"status": "ok", "model": RT.model_name}


# ── Activity REST endpoints ────────────────────────────────────────────

@app.get("/activity/today")
async def activity_today():
    """Return today's activity summary."""
    summary = RT.activity_store.today_summary()
    return {
        "date": summary.date,
        "breakdown": summary.breakdown,
        "screen_time_minutes": round(summary.screen_time, 1),
    }


@app.get("/activity/range")
async def activity_range(start: str, end: str):
    """Return daily summaries for a date range (YYYY-MM-DD)."""
    summaries = RT.activity_store.get_daily_summaries(start, end)
    return [
        {
            "date": s.date,
            "breakdown": s.breakdown,
            "screen_time_minutes": round(s.screen_time, 1),
        }
        for s in summaries
    ]


@app.get("/activity/records")
async def activity_records(start: str, end: str):
    """Return raw activity records for a date range."""
    records = RT.activity_store.get_range(start, end)
    return [
        {
            "id": r.id,
            "timestamp": r.timestamp,
            "status": r.status,
            "app": r.app,
            "details": r.details,
        }
        for r in records
    ]


@app.delete("/activity/day/{target_date}")
async def activity_delete_day(target_date: str):
    """Delete all activity records for a specific day (YYYY-MM-DD)."""
    deleted = RT.activity_store.delete_day(target_date)
    return {"deleted": deleted, "date": target_date}


@app.delete("/activity/all")
async def activity_delete_all():
    """Delete all activity data."""
    RT.activity_store.clear()
    return {"deleted": "all"}


@app.post("/activity/capture")
async def activity_capture_now():
    """Manually trigger a single screen capture + analysis cycle."""
    if RT.monitor is None:
        return {"error": "Screen monitor not initialized"}
    result = await RT.monitor.capture_and_analyze()
    if result is None:
        return {"error": "Capture or analysis failed — check server logs"}
    return {
        "status": result.status.value,
        "app": result.app,
        "details": result.details,
    }


async def _stream_reply(ws: WebSocket, reply: str, chunk_size: int = 6, delay: float = 0.02):
    for i in range(0, len(reply), chunk_size):
        await ws.send_text(json.dumps({"type": "token", "text": reply[i : i + chunk_size]}))
        await asyncio.sleep(delay)
    await ws.send_text(json.dumps({"type": "done"}))


async def _handle_user(ws: WebSocket, text: str) -> None:
    if RT.agent is None:
        RT.rebuild_agent()
    assert RT.agent is not None
    if RT.scheduler:
        RT.scheduler.touch()
    log.info("user: %s", text[:80])
    try:
        reply = await asyncio.to_thread(RT.agent.chat, text)
    except Exception as e:
        log.exception("agent.chat failed")
        await _send(ws, {"type": "error", "message": str(e)})
        return
    log.info("camel: %s", reply[:80])
    await _stream_reply(ws, reply)


def _apply_config(msg: dict[str, Any]) -> tuple[bool, str | None]:
    """Apply a runtime config message. Returns (changed, error)."""
    changed = False
    if "platform" in msg and msg["platform"] and str(msg["platform"]).lower() != RT.platform:
        RT.platform = str(msg["platform"]).lower()
        changed = True
    if "model" in msg and msg["model"] and msg["model"] != RT.model_name:
        RT.model_name = str(msg["model"])
        changed = True
    if "api_key" in msg and msg["api_key"]:
        RT.api_key = str(msg["api_key"])
        changed = True
    if "base_url" in msg and msg["base_url"]:
        RT.base_url = str(msg["base_url"])
        changed = True
    if "clipboard_enabled" in msg:
        new = bool(msg["clipboard_enabled"])
        if new != RT.clipboard_enabled:
            RT.clipboard_enabled = new
            changed = True
    if "nudges_enabled" in msg and RT.scheduler is not None:
        RT.scheduler.enable(bool(msg["nudges_enabled"]))
        RT.nudges_enabled = bool(msg["nudges_enabled"])
    if "screen_monitor_enabled" in msg:
        new_mon = bool(msg["screen_monitor_enabled"])
        if new_mon != RT.screen_monitor_enabled:
            RT.screen_monitor_enabled = new_mon
            if RT.monitor:
                RT.monitor.enable(new_mon)
            changed = True
    if "monitor_interval_seconds" in msg:
        new_interval = max(10, int(msg["monitor_interval_seconds"]))
        if new_interval != RT.monitor_interval_seconds:
            RT.monitor_interval_seconds = new_interval
            if RT.monitor:
                RT.monitor.set_interval(new_interval)

    # ── focus coach config ────────────────────────────────────
    coach_reconf: dict = {}
    if "focus_coach_interval_seconds" in msg:
        v = max(30, int(msg["focus_coach_interval_seconds"]))
        if v != RT.focus_coach_interval_seconds:
            RT.focus_coach_interval_seconds = v
            coach_reconf["check_interval_seconds"] = v
    if "focus_coach_window_minutes" in msg:
        v = max(1, int(msg["focus_coach_window_minutes"]))
        if v != RT.focus_coach_window_minutes:
            RT.focus_coach_window_minutes = v
            coach_reconf["window_minutes"] = v
    if "distracted_threshold_minutes" in msg:
        v = max(1, int(msg["distracted_threshold_minutes"]))
        if v != RT.distracted_threshold_minutes:
            RT.distracted_threshold_minutes = v
            coach_reconf["distracted_threshold_minutes"] = v
    if "focus_coach_cooldown_seconds" in msg:
        v = max(0, int(msg["focus_coach_cooldown_seconds"]))
        if v != RT.focus_coach_cooldown_seconds:
            RT.focus_coach_cooldown_seconds = v
            coach_reconf["cooldown_seconds"] = v
    if "focus_categories" in msg and isinstance(msg["focus_categories"], list):
        new_focus = [str(x) for x in msg["focus_categories"]]
        if new_focus != RT.focus_categories:
            RT.focus_categories = new_focus
            coach_reconf["focus_categories"] = new_focus
    if "distraction_categories" in msg and isinstance(msg["distraction_categories"], list):
        new_dist = [str(x) for x in msg["distraction_categories"]]
        if new_dist != RT.distraction_categories:
            RT.distraction_categories = new_dist
            coach_reconf["distraction_categories"] = new_dist
    if coach_reconf and RT.coach is not None:
        RT.coach.reconfigure(**coach_reconf)
    if "focus_coach_enabled" in msg and RT.coach is not None:
        new_en = bool(msg["focus_coach_enabled"])
        if new_en != RT.focus_coach_enabled:
            RT.focus_coach_enabled = new_en
            RT.coach.enable(new_en)

    if changed:
        try:
            RT.rebuild_agent()
        except Exception as e:
            return False, str(e)
    return changed, None


@app.websocket("/ws")
async def ws_endpoint(ws: WebSocket):
    await ws.accept()
    log.info("client connected")
    RT.active_ws = ws

    # Try to build an agent now so the UI can surface init errors early.
    init_error: str | None = None
    if RT.agent is None:
        try:
            RT.rebuild_agent()
        except Exception as e:
            init_error = str(e)
            log.warning("agent deferred init: %s", e)

    await _send(
        ws,
        {
            "type": "ready",
            "model": RT.model_name,
            "clipboard_enabled": RT.clipboard_enabled,
            "nudges_enabled": RT.nudges_enabled,
            "screen_monitor_enabled": RT.screen_monitor_enabled,
            "monitor_interval_seconds": RT.monitor_interval_seconds,
            "platform": RT.platform,
            "has_api_key": bool(RT.api_key),
            "init_error": init_error,
            "focus_coach_enabled": RT.focus_coach_enabled,
            "focus_coach_interval_seconds": RT.focus_coach_interval_seconds,
            "focus_coach_window_minutes": RT.focus_coach_window_minutes,
            "distracted_threshold_minutes": RT.distracted_threshold_minutes,
            "focus_coach_cooldown_seconds": RT.focus_coach_cooldown_seconds,
            "focus_categories": RT.focus_categories,
            "distraction_categories": RT.distraction_categories,
        },
    )

    try:
        while True:
            raw = await ws.receive_text()
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                await _send(ws, {"type": "error", "message": "bad json"})
                continue

            mtype = msg.get("type")

            if mtype == "user":
                text = (msg.get("text") or "").strip()
                if not text:
                    continue
                await _handle_user(ws, text)

            elif mtype == "config":
                changed, err = _apply_config(msg)
                if err:
                    await _send(ws, {"type": "error", "message": f"config: {err}"})
                else:
                    await _send(
                        ws,
                        {
                            "type": "config_ack",
                            "model": RT.model_name,
                            "clipboard_enabled": RT.clipboard_enabled,
                            "nudges_enabled": RT.nudges_enabled,
                            "screen_monitor_enabled": RT.screen_monitor_enabled,
                            "monitor_interval_seconds": RT.monitor_interval_seconds,
                            "platform": RT.platform,
                            "has_api_key": bool(RT.api_key),
                            "changed": changed,
                            "focus_coach_enabled": RT.focus_coach_enabled,
                            "focus_coach_interval_seconds": RT.focus_coach_interval_seconds,
                            "focus_coach_window_minutes": RT.focus_coach_window_minutes,
                            "distracted_threshold_minutes": RT.distracted_threshold_minutes,
                            "focus_coach_cooldown_seconds": RT.focus_coach_cooldown_seconds,
                            "focus_categories": RT.focus_categories,
                            "distraction_categories": RT.distraction_categories,
                        },
                    )

            elif mtype == "clear_history":
                RT.store.clear()
                try:
                    RT.rebuild_agent()
                except Exception as e:
                    await _send(ws, {"type": "error", "message": str(e)})
                    continue
                await _send(ws, {"type": "history_cleared"})

            elif mtype == "cancel_timer":
                tid = int(msg.get("id", 0))
                ok = bool(RT.timers and RT.timers.cancel(tid))
                await _send(ws, {"type": "timer_cancelled", "id": tid, "ok": ok})

            elif mtype == "list_timers":
                pending = RT.timers.pending() if RT.timers else []
                await _send(ws, {"type": "timers", "items": pending})

            else:
                await _send(ws, {"type": "error", "message": f"unknown type: {mtype}"})

    except WebSocketDisconnect:
        log.info("client disconnected")
    except Exception:
        log.exception("ws handler crashed")
        try:
            await ws.close()
        except Exception:
            pass
    finally:
        if RT.active_ws is ws:
            RT.active_ws = None


def main():
    import uvicorn

    port = int(os.environ.get("CAMEL_PET_PORT", "8765"))
    uvicorn.run("camel_pet_agent.server:app", host="127.0.0.1", port=port, reload=False)


if __name__ == "__main__":
    main()
