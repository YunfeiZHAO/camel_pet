"""CAMEL-AI ChatAgent wrapper.

Hackathon v0: non-streaming CAMEL call; server chunks the reply to
simulate streaming. Real token streaming is a later pass.
"""
from __future__ import annotations

import logging
import os
from typing import Callable, Optional

from camel.agents import ChatAgent
from camel.messages import BaseMessage
from camel.models import ModelFactory
from camel.types import ModelPlatformType, RoleType, ModelType

from .memory import ChatStore
from .personality import SYSTEM_PROMPT

log = logging.getLogger("camel-pet.agent")


class CamelPetAgent:
    def __init__(
        self,
        model_name: str,
        api_key: Optional[str] = None,
        tools: Optional[list[Callable]] = None,
        store: Optional[ChatStore] = None,
        platform: str = "anthropic",
        url: Optional[str] = None,
    ):
        platform_norm = (platform or "anthropic").lower()

        if platform_norm == "minmax":
            base_url = url or os.environ.get("ANTHROPIC_BASE_URL")
            key = api_key or os.environ.get("MINIMAX_API_KEY")
            if not key:
                raise RuntimeError(
                    "ANTHROPIC_API_KEY not set. Export it or put it in agent/.env"
                )
            model = ModelFactory.create(
                model_platform=ModelPlatformType.ANTHROPIC,
                model_type=model_name or ModelType.MINIMAX_M2_7,
                api_key=key,
                url=base_url,
            )
        else:
            raise RuntimeError(
                f"unknown platform '{platform}': expected 'anthropic', 'minimax', or 'openai_compatible'"
            )

        system_msg = BaseMessage(
            role_name="Camel",
            role_type=RoleType.ASSISTANT,
            meta_dict={},
            content=SYSTEM_PROMPT,
        )

        kwargs: dict = {
            "system_message": system_msg,
            "model": model,
            "message_window_size": 20,
        }
        if tools:
            kwargs["tools"] = tools
        self._agent = ChatAgent(**kwargs)

        self.store = store
        if store is not None:
            self._preload_history(store)

    def _preload_history(self, store: ChatStore, limit: int = 20) -> None:
        try:
            from camel.types import OpenAIBackendRole
        except ImportError:
            log.warning("OpenAIBackendRole not available; skipping memory preload")
            return

        for turn in store.recent(limit=limit):
            try:
                if turn.role == "user":
                    msg = BaseMessage(
                        role_name="User",
                        role_type=RoleType.USER,
                        meta_dict={},
                        content=turn.content,
                    )
                    role = OpenAIBackendRole.USER
                else:
                    msg = BaseMessage(
                        role_name="Camel",
                        role_type=RoleType.ASSISTANT,
                        meta_dict={},
                        content=turn.content,
                    )
                    role = OpenAIBackendRole.ASSISTANT
                self._agent.update_memory(msg, role)
            except Exception as e:
                log.warning("memory preload failed on turn (role=%s): %s", turn.role, e)
                return

    def chat(self, text: str) -> str:
        user_msg = BaseMessage(
            role_name="User",
            role_type=RoleType.USER,
            meta_dict={},
            content=text,
        )
        response = self._agent.step(user_msg)
        reply = (
            response.msgs[0].content
            if response.msgs
            else "(the camel squints at you in silence)"
        )
        if self.store is not None:
            self.store.append("user", text)
            self.store.append("assistant", reply)
        return reply

    def reset(self) -> None:
        self._agent.reset()
        if self.store is not None:
            self.store.clear()
