"""Keep the newest live frame while preserving session/track message order."""
import asyncio
from collections import deque
import contextlib
import json


async def telemetry_messages(socket):
    pending = deque()
    ready = asyncio.Event()
    finished = False

    async def receive():
        nonlocal finished
        live = False
        try:
            async for raw in socket:
                msg = json.loads(raw)
                if msg.get("type") == "session_reset":
                    pending.clear()
                    live = False
                elif msg.get("type") == "track":
                    live = msg.get("source") == "live"
                if live and msg.get("type") == "frames" and pending and pending[-1].get("type") == "frames":
                    pending[-1] = msg
                else:
                    pending.append(msg)
                ready.set()
        finally:
            finished = True
            ready.set()

    reader = asyncio.create_task(receive())
    try:
        while True:
            if pending:
                yield pending.popleft()
            elif finished:
                await reader
                return
            else:
                ready.clear()
                try:
                    await asyncio.wait_for(ready.wait(), .1)
                except asyncio.TimeoutError:
                    yield {}
    finally:
        reader.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await reader
