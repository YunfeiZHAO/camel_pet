"""Vision analysis agent — uses CAMEL ChatAgent with vision to classify screen activity."""
from __future__ import annotations

import logging
import os
from enum import Enum
from typing import Any, Optional

from camel.agents import ChatAgent
from camel.messages import BaseMessage
from camel.models import ModelFactory
from camel.types import ModelPlatformType, RoleType
import re as _re

from pydantic import BaseModel, Field

log = logging.getLogger("camel-pet.vision")

_VISION_SYSTEM_PROMPT = """\
You are an activity classifier. You analyze screenshots of a user's desktop \
and determine what they are currently doing.

You MUST respond with a single JSON object and nothing else. No markdown, no explanation.

JSON schema:
{"status": "<category>", "app": "<app name or null>", "details": {}}

Categories (pick exactly one for "status"):
- "coding" — writing or reading code in an IDE/editor (VS Code, IntelliJ, Vim, etc.)
- "working" — productive work that isn't coding (documents, spreadsheets, email, terminal)
- "browsing" — web browsing (reading articles, searching, shopping)
- "reading" — focused reading (PDFs, ebooks, documentation, research papers)
- "design" — creative/design work (Figma, Photoshop, Illustrator, Blender, CAD)
- "video" — watching video content (YouTube, streaming, media player)
- "gaming" — playing a game
- "communication" — chat apps, social media messaging (Slack, Discord, Teams, WeChat)
- "meeting" — video/audio calls and conferences (Zoom, Teams call, Google Meet)
- "social_media" — scrolling social feeds (Twitter/X, Reddit, Instagram, TikTok)
- "music" — music player in foreground (Spotify, Apple Music, NetEase Music)
- "learning" — online courses, tutorials, educational platforms (Coursera, Udemy)
- "idle" — desktop visible but no active work (empty desktop, lock screen, screensaver)
- "other" — anything that doesn't fit above

Hints for classification:
- If you see code or a code editor interface, choose "coding"
- If you see a browser with a video playing, choose "video" not "browsing"
- Terminal/command line doing development tasks counts as "coding"
- Video call window (Zoom, Meet, Teams with camera) → "meeting", not "communication"
- Chat/messaging without video → "communication"
- Scrolling a social feed (Reddit, Twitter) → "social_media", not "browsing"
- Reading an article/documentation focused in browser → "reading" if no other tabs/activity
- If unsure between two categories, pick the more specific one

Example response:
{"status": "coding", "app": "Visual Studio Code", "details": {"language": "python"}}
"""


class ActivityStatus(str, Enum):
    coding = "coding"
    working = "working"
    browsing = "browsing"
    reading = "reading"
    design = "design"
    video = "video"
    gaming = "gaming"
    communication = "communication"
    meeting = "meeting"
    social_media = "social_media"
    music = "music"
    learning = "learning"
    idle = "idle"
    other = "other"


class ActivityResult(BaseModel):
    """Structured result from the vision activity classifier."""
    status: ActivityStatus = Field(description="The detected activity category")
    app: Optional[str] = Field(
        default=None, description="Foreground application name, if identifiable")
    details: dict[str, Any] = Field(
        default_factory=dict, description="Extra context like project name or URL topic")


class VisionAgent:
    """CAMEL-based vision agent for screen activity classification."""

    def __init__(
        self,
        model_name: Optional[str] = None,
        api_key: Optional[str] = None,
        platform: str = "minmax",
        url: Optional[str] = None,
    ):
        platform_norm = (platform or "minmax").lower()
        self._platform = platform_norm

        if platform_norm == "openai_compatible":
            key = api_key or os.environ.get("OPENAI_COMPATIBLE_API_KEY")
            base_url = url or os.environ.get("OPENAI_COMPATIBLE_BASE_URL")
            model = ModelFactory.create(
                model_platform=ModelPlatformType.OPENAI_COMPATIBLE_MODEL,
                model_type=model_name or "gpt-4o-mini",
                api_key=key,
                url=base_url,
            )
        elif platform_norm in ("minmax", "minimax"):
            key = api_key or os.environ.get("MINIMAX_API_KEY")
            base_url = url or os.environ.get("ANTHROPIC_BASE_URL", "https://api.minimax.io/anthropic")
            model = ModelFactory.create(
                model_platform=ModelPlatformType.ANTHROPIC,
                model_type=model_name or "MiniMax-M2.7",
                api_key=key,
                url=base_url,
            )
        elif platform_norm == "anthropic":
            key = api_key or os.environ.get("ANTHROPIC_API_KEY")
            base_url = url or None
            model = ModelFactory.create(
                model_platform=ModelPlatformType.ANTHROPIC,
                model_type=model_name or "claude-haiku-4-5",
                api_key=key,
                url=base_url,
            )
        else:
            # fallback: treat as anthropic
            key = api_key or os.environ.get("ANTHROPIC_API_KEY")
            base_url = url or os.environ.get("ANTHROPIC_BASE_URL")
            model = ModelFactory.create(
                model_platform=ModelPlatformType.ANTHROPIC,
                model_type=model_name or "claude-haiku-4-5",
                api_key=key,
                url=base_url,
            )

        self.system_msg = BaseMessage(
            role_name="VisionClassifier",
            role_type=RoleType.ASSISTANT,
            meta_dict={},
            content=_VISION_SYSTEM_PROMPT,
        )

        self._agent = ChatAgent(
            system_message=self.system_msg,
            model=model,
            output_language=None,
        )

    def analyze(self, image_b64: str) -> ActivityResult:
        """Analyze a base64-encoded screenshot and return a validated ActivityResult.

        Args:
            image_b64: Base64-encoded JPEG image data.

        Returns:
            Validated ActivityResult with status, app, and details.
        """
        # clear the history memory to ensure a fresh analysis each time;
        # we don't want previous screenshots influencing the result
        self._agent.memory.clear()

        # MiniMax expects image as inline data URL in content string
        if self._platform in ("minmax", "minimax"):
            content = (
                "Analyze this screenshot and classify the user's current activity.\n\n"
                f"data:image/jpeg;base64,{image_b64}"
            )
            user_msg = BaseMessage(
                role_name="User",
                role_type=RoleType.USER,
                meta_dict={},
                content=content,
            )
        else:
            # Anthropic / OpenAI use image_list
            user_msg = BaseMessage(
                role_name="User",
                role_type=RoleType.USER,
                meta_dict={},
                content="Analyze this screenshot and classify the user's current activity.",
                image_list=[f"data:image/jpeg;base64,{image_b64}"],
            )

        response = self._agent.step(user_msg, response_format=ActivityResult)

        # CAMEL returns parsed_dict or we parse from content
        if response.msgs and hasattr(response.msgs[0], "parsed"):
            parsed = response.msgs[0].parsed
            if isinstance(parsed, ActivityResult):
                return parsed

        # Fallback: extract JSON from raw content
        raw = response.msgs[0].content if response.msgs else ""
        log.debug("Vision raw response: %s", raw[:500])
        return self._parse_response(raw)

    def _parse_response(self, raw: str) -> ActivityResult:
        """Best-effort parse of the LLM response into ActivityResult."""
        # Try direct JSON parse
        try:
            return ActivityResult.model_validate_json(raw)
        except Exception:
            pass

        # Try to extract JSON object from surrounding text/markdown
        match = _re.search(r'\{[^{}]*"status"\s*:\s*"[^"]+"[^{}]*\}', raw)
        if match:
            try:
                return ActivityResult.model_validate_json(match.group())
            except Exception:
                pass

        # Last resort: look for a known status keyword in the text
        raw_lower = raw.lower()
        for status in ActivityStatus:
            if status.value in raw_lower:
                log.info("Extracted status '%s' from free-text response", status.value)
                return ActivityResult(status=status)

        log.warning("Failed to parse vision response, returning 'other': %s", raw[:200])
        return ActivityResult(status=ActivityStatus.other)
