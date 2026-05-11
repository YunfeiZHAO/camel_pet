"""Backend smoke-test CLI. Chat with the agent without the frontend.

Modes:
  direct (default) — build CamelPetAgent in-process and chat. No server needed.
  ws               — connect to a running server (uvicorn) over WebSocket.

Usage (from agent/):
  python test_cli.py
  python test_cli.py --mode ws --url ws://127.0.0.1:8765/ws
  python test_cli.py --once "hello camel"
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env", override=True)


def run_direct(once: str | None) -> None:
    from camel_pet_agent.agent import CamelPetAgent

    platform = os.environ.get("CAMEL_PET_PLATFORM", "minmax").lower()
    model_name = os.environ.get("CAMEL_PET_MODEL", "MiniMax-M2.7")
    api_key = os.environ.get("MINIMAX_API_KEY")
    url = os.environ.get("ANTHROPIC_BASE_URL")

    print(f"[direct] platform={platform} model={model_name} url={url or '-'}")
    agent = CamelPetAgent(
        model_name=model_name,
        api_key=api_key,
        platform=platform,
        url=url,
    )

    if once is not None:
        print("camel>", agent.chat(once))
        return

    print("type /quit to exit, /reset to clear history")
    while True:
        try:
            text = input("you> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return
        if not text:
            continue
        if text == "/quit":
            return
        if text == "/reset":
            agent.reset()
            print("[reset]")
            continue
        try:
            reply = agent.chat(text)
        except Exception as e:
            print(f"[error] {e}")
            continue
        print("camel>", reply)


async def run_ws(url: str, once: str | None) -> None:
    try:
        import websockets
    except ImportError:
        print("websockets not installed. Try: pip install websockets", file=sys.stderr)
        sys.exit(2)

    async with websockets.connect(url) as ws:
        ready = json.loads(await ws.recv())
        print(f"[ws] ready: {ready}")

        async def send_and_collect(text: str) -> str:
            await ws.send(json.dumps({"type": "user", "text": text}))
            buf: list[str] = []
            while True:
                msg = json.loads(await ws.recv())
                t = msg.get("type")
                if t == "token":
                    buf.append(msg.get("text", ""))
                elif t == "done":
                    return "".join(buf)
                elif t == "error":
                    return f"[error] {msg.get('message')}"

        if once is not None:
            reply = await send_and_collect(once)
            print("camel>", reply)
            return

        print("type /quit to exit")
        loop = asyncio.get_event_loop()
        while True:
            try:
                text = (await loop.run_in_executor(None, input, "you> ")).strip()
            except (EOFError, KeyboardInterrupt):
                print()
                return
            if not text:
                continue
            if text == "/quit":
                return
            reply = await send_and_collect(text)
            print("camel>", reply)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=["direct", "ws"], default="direct")
    ap.add_argument("--url", default="ws://127.0.0.1:8765/ws")
    ap.add_argument("--once", default=None, help="send a single message and exit")
    args = ap.parse_args()

    if args.mode == "direct":
        run_direct(args.once)
    else:
        asyncio.run(run_ws(args.url, args.once))


if __name__ == "__main__":
    main()
