"""
BLE param_get flow: send param_get(ref), wait for param_get_res(ref, value).
"""

from __future__ import annotations

import asyncio
from collections import deque
from typing import TYPE_CHECKING, Optional

from commands.command_handlers import cli_command
from protocol_utils import parse_command_message

if TYPE_CHECKING:
    from commands.command_handlers import NusPort
    from protocol_utils import CommandInvocation

PARAM_GET_WAIT_SECONDS = 3.0

_param_get_res_recent: deque[str] = deque(maxlen=32)

_active_param_get_session: Optional["ParamGetSession"] = None


def _terminal():
    import main as main_module

    return main_module.Terminal


def parse_param_get_res(line: str) -> Optional[tuple[str, str]]:
    """
    Expects the whole stripped line to be only ``param_get_res(...)`` (see ``parse_command_message``).

    Device → host, after param_get(Class.ref):

    - Two fields: param_get_res("Class.param","value")
    - Three fields (same shape as list rows): param_get_res(0,"Class.param","value") — index ignored.
    """
    inv = parse_command_message(line)
    if inv is None or inv.name != "param_get_res":
        return None
    args = inv.params
    if len(args) == 2:
        return args[0], args[1]
    if len(args) == 3:
        return args[1], args[2]
    return None


def capture_param_get_res_from_ble(message: str) -> None:
    for line in message.replace("\r\n", "\n").split("\n"):
        s = line.strip()
        if s and parse_param_get_res(s) is not None:
            _param_get_res_recent.append(s)


def _refs_match(requested: str, responded: str) -> bool:
    return requested.strip() == responded.strip()


class ParamGetSession:
    def __init__(self, requested_param_ref: str) -> None:
        self._requested = requested_param_ref.strip()
        self._done = asyncio.Event()
        self._name: Optional[str] = None
        self._value: Optional[str] = None

    def try_feed(self, parsed: tuple[str, str]) -> None:
        name, value = parsed
        if not _refs_match(self._requested, name):
            return
        self._name = name
        self._value = value
        self._done.set()

    async def wait_until_done(self, timeout: float) -> bool:
        try:
            await asyncio.wait_for(self._done.wait(), timeout=timeout)
            return True
        except asyncio.TimeoutError:
            return False

    @property
    def result(self) -> Optional[tuple[str, str]]:
        if self._name is None or self._value is None:
            return None
        return self._name, self._value


def try_feed_param_get_session(message: str) -> bool:
    if _active_param_get_session is None:
        return False
    fed = False
    for line in message.replace("\r\n", "\n").split("\n"):
        parsed = parse_param_get_res(line.strip())
        if parsed:
            _active_param_get_session.try_feed(parsed)
            fed = True
    return fed


@cli_command
async def cmd_param_get(inv: "CommandInvocation", nus: "NusPort") -> None:
    """Send param_get(ref) over BLE; wait for param_get_res with matching ref and print value."""
    global _active_param_get_session
    t = _terminal()
    if not inv.params:
        t.log(
            '⚠ param_get needs one argument, e.g. param_get("Class.param") or param_get(Class.param)',
            "YELLOW",
        )
        return
    requested = inv.params[0].strip()
    session = ParamGetSession(requested)
    _active_param_get_session = session
    try:
        for line in list(_param_get_res_recent):
            p = parse_param_get_res(line)
            if p:
                session.try_feed(p)
        if session.result:
            n, v = session.result
            t.log(f"📎 param_get (buffered): {n} = {v}", "GREEN")
            return
        if not await nus.send_message(inv.line):
            return
        completed = await session.wait_until_done(PARAM_GET_WAIT_SECONDS)
        if not completed:
            t.log(
                f"⏱ param_get timed out after {PARAM_GET_WAIT_SECONDS:g}s "
                f"(no param_get_res for {requested!r})",
                "YELLOW",
            )
            return
        res = session.result
        if res:
            n, v = res
            t.log(f"📎 {n} = {v}", "GREEN")
    finally:
        if _active_param_get_session is session:
            _active_param_get_session = None
        _param_get_res_recent.clear()
