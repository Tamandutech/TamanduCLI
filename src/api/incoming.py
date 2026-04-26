"""Buffer incoming :class:`WireCommand` instances for ``wait_for``-style scripts."""

from __future__ import annotations

import asyncio
from typing import Callable, Optional

from api.protocol_utils import WireCommand


class IncomingRouter:
    """Keeps a FIFO of parsed commands from the device (same event loop as async handlers)."""

    def __init__(self, loop: Optional[asyncio.AbstractEventLoop] = None) -> None:
        self._loop = loop or asyncio.get_event_loop()
        self._items: list[WireCommand] = []

    def record(self, cmd: WireCommand) -> None:
        self._items.append(cmd)

    def record_many(self, cmds: list[WireCommand]) -> None:
        self._items.extend(cmds)

    def clear(self) -> None:
        self._items.clear()

    async def wait_for(
        self,
        name: str,
        *,
        is_response: bool = True,
        timeout: float = 5.0,
        predicate: Optional[Callable[[WireCommand], bool]] = None,
    ) -> WireCommand:
        """Pop the first matching command, or raise ``TimeoutError``."""
        deadline = self._loop.time() + timeout
        want = name.lower()
        while True:
            for i, c in enumerate(self._items):
                if c.name.lower() != want or c.is_response != is_response:
                    continue
                if predicate is not None and not predicate(c):
                    continue
                return self._items.pop(i)
            if self._loop.time() > deadline:
                raise TimeoutError(name)
            await asyncio.sleep(0.02)
