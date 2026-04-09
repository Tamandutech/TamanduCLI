"""
BLE help flow: help_res(...) parsing, collection session, and cmd_help.
"""

from __future__ import annotations

import asyncio
from collections import deque
from typing import TYPE_CHECKING, Optional

from output_paths import OUTPUT_DIR, ensure_output_dir
from protocol_utils import split_top_level_commas, unquote_field

if TYPE_CHECKING:
    from commands.command_handlers import NusPort
    from protocol_utils import CommandInvocation

HELP_WAIT_SECONDS = 3.0
HELP_RESPONSE_PATH = OUTPUT_DIR / "help_response.txt"

_help_res_recent: deque[str] = deque(maxlen=64)

_active_help_session: Optional["HelpCollectionSession"] = None


def _terminal():
    import main as main_module

    return main_module.Terminal


def _help_res_inner(line: str) -> Optional[str]:
    s = line.strip()
    low = s.lower()
    key = "help_res("
    if key not in low:
        return None
    idx = low.find(key)
    if idx < 0:
        return None
    i = idx + len(key)
    depth = 1
    j = i
    while j < len(s) and depth:
        if s[j] == "(":
            depth += 1
        elif s[j] == ")":
            depth -= 1
        j += 1
    if depth:
        return None
    return s[i : j - 1]


def parse_help_res(line: str) -> Optional[tuple[int, str, str]]:
    """
    Wire shapes:

    - Header: help_res(0,"header",N) — N is the number of command rows (indices 1..N).
    - Rows (3 fields): help_res(index, name, value) — legacy.
    - Rows (4 fields): help_res(index, name, params, description) — params e.g. \"none\" or
      \"reference,value\"; stored value is \"params, description\" for display/file.
    """
    inner = _help_res_inner(line)
    if inner is None:
        return None
    args = split_top_level_commas(inner)
    if len(args) not in (3, 4):
        return None
    try:
        idx = int(args[0].strip())
    except ValueError:
        return None
    name = unquote_field(args[1])
    if len(args) == 3:
        return idx, name, unquote_field(args[2])
    params = unquote_field(args[2])
    description = unquote_field(args[3])
    return idx, name, f"{params}, {description}"


def capture_help_res_from_ble(message: str) -> None:
    """Remember help_res lines so a burst that starts before the session is active is not lost."""
    for line in message.replace("\r\n", "\n").split("\n"):
        s = line.strip()
        if s and parse_help_res(s) is not None:
            _help_res_recent.append(s)


class HelpCollectionSession:
    """Collects help_res lines until header count is satisfied or timeout."""

    def __init__(self) -> None:
        self._rows: dict[int, tuple[str, str]] = {}
        self._expected_command_count: Optional[int] = None
        self._done = asyncio.Event()

    def feed_parsed(self, parsed: tuple[int, str, str]) -> None:
        idx, name, value = parsed
        self._rows[idx] = (name, value)
        if idx == 0 and name.lower() == "header":
            try:
                self._expected_command_count = int(value.strip())
            except ValueError:
                _terminal().log(
                    f"⚠ help_res(0,\"header\",…): entry count is not an integer: {value!r}",
                    "YELLOW",
                )
        if self._is_complete():
            self._done.set()

    def _is_complete(self) -> bool:
        if self._expected_command_count is None or 0 not in self._rows:
            return False
        name0, _ = self._rows[0]
        if name0.lower() != "header":
            return False
        n = self._expected_command_count
        return all(i in self._rows for i in range(1, n + 1))

    async def wait_until_done(self, timeout: float) -> bool:
        try:
            await asyncio.wait_for(self._done.wait(), timeout=timeout)
            return True
        except asyncio.TimeoutError:
            return False

    def write_file_and_log(self, completed: bool) -> None:
        t = _terminal()
        lines_out: list[str] = []
        for idx in sorted(self._rows.keys()):
            name, value = self._rows[idx]

            def q(x: str) -> str:
                return '"' + x.replace("\\", "\\\\").replace('"', '\\"') + '"'

            if idx == 0 and name.lower() == "header":
                try:
                    n = int(value.strip())
                    lines_out.append(f"help_res(0,{q(name)},{n})")
                except ValueError:
                    lines_out.append(f"help_res(0,{q(name)},{q(value)})")
            else:
                lines_out.append(f"help_res({idx},{q(name)},{q(value)})")
        body = "\n".join(lines_out)
        if body:
            ensure_output_dir()
            HELP_RESPONSE_PATH.write_text(body + "\n", encoding="utf-8")
            t.log(f"💾 Help list saved to {HELP_RESPONSE_PATH}", "GREEN")
        else:
            t.log("⚠ No help_res lines collected; file not written.", "YELLOW")

        status = "complete" if completed else "partial (timeout)"
        t.log(f"📋 Device help ({status}):", "YELLOW")
        for idx in sorted(self._rows.keys()):
            name, value = self._rows[idx]
            if idx == 0:
                n = self._expected_command_count
                t.log(
                    f"  [0] header — expecting {n if n is not None else value} command row(s)",
                    "CYAN",
                )
            else:
                t.log(f"  [{idx}] {name}: {value}", "WHITE")


def try_feed_help_session(message: str) -> bool:
    """Consume help_res lines during an active help collection session."""
    if _active_help_session is None:
        return False
    fed = False
    for line in message.replace("\r\n", "\n").split("\n"):
        parsed = parse_help_res(line.strip())
        if parsed:
            _active_help_session.feed_parsed(parsed)
            fed = True
    return fed


async def cmd_help(inv: "CommandInvocation", nus: "NusPort") -> None:
    """Send help(...) over BLE, wait/collect help_res rows, then persist and print."""
    global _active_help_session
    t = _terminal()
    session = HelpCollectionSession()
    _active_help_session = session
    try:
        for line in list(_help_res_recent):
            p = parse_help_res(line)
            if p:
                session.feed_parsed(p)
        if not await nus.send_message(inv.line):
            return
        completed = await session.wait_until_done(HELP_WAIT_SECONDS)
        if not completed:
            t.log(
                f"⏱ Help collection timed out after {HELP_WAIT_SECONDS:g}s; showing partial results.",
                "YELLOW",
            )
        session.write_file_and_log(completed)
    finally:
        if _active_help_session is session:
            _active_help_session = None
        _help_res_recent.clear()
