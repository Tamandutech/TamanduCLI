"""
BLE param_list flow: collect param_list_res(...) and write output/param_list.txt.
"""

from __future__ import annotations

import asyncio
from collections import deque
from typing import TYPE_CHECKING, Optional

from commands.command_handlers import cli_command
from output_paths import OUTPUT_DIR, ensure_output_dir
from protocol_utils import parse_command_message

if TYPE_CHECKING:
    from commands.command_handlers import NusPort
    from protocol_utils import CommandInvocation

PARAM_LIST_WAIT_SECONDS = 3.0
PARAM_LIST_RESPONSE_PATH = OUTPUT_DIR / "param_list.txt"

_param_list_res_recent: deque[str] = deque(maxlen=64)

_active_param_list_session: Optional["ParamListCollectionSession"] = None


def _terminal():
    import main as main_module

    return main_module.Terminal


def parse_param_list_res(line: str) -> Optional[tuple[int, str, str]]:
    """
    Expects the whole stripped line to be only ``param_list_res(...)`` (see ``parse_command_message``).

    Three fields per line:

    - Header: param_list_res(0,"header",N)
    - Rows: param_list_res(index, param_name, value)
    """
    inv = parse_command_message(line)
    if inv is None or inv.name != "param_list_res":
        return None
    args = inv.params
    if len(args) != 3:
        return None
    try:
        idx = int(args[0].strip())
    except ValueError:
        return None
    return idx, args[1], args[2]


def capture_param_list_res_from_ble(message: str) -> None:
    for line in message.replace("\r\n", "\n").split("\n"):
        s = line.strip()
        if s and parse_param_list_res(s) is not None:
            _param_list_res_recent.append(s)


class ParamListCollectionSession:
    def __init__(self) -> None:
        self._rows: dict[int, tuple[str, str]] = {}
        self._expected_count: Optional[int] = None
        self._done = asyncio.Event()

    def feed_parsed(self, parsed: tuple[int, str, str]) -> None:
        idx, name, value = parsed
        self._rows[idx] = (name, value)
        if idx == 0 and name.lower() == "header":
            try:
                self._expected_count = int(value.strip())
            except ValueError:
                _terminal().log(
                    f"⚠ param_list_res(0,\"header\",…): entry count is not an integer: {value!r}",
                    "YELLOW",
                )
        if self._is_complete():
            self._done.set()

    def _is_complete(self) -> bool:
        if self._expected_count is None or 0 not in self._rows:
            return False
        name0, _ = self._rows[0]
        if name0.lower() != "header":
            return False
        n = self._expected_count
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
                    lines_out.append(f"param_list_res(0,{q(name)},{n})")
                except ValueError:
                    lines_out.append(f"param_list_res(0,{q(name)},{q(value)})")
            else:
                lines_out.append(f"param_list_res({idx},{q(name)},{q(value)})")
        body = "\n".join(lines_out)
        if body:
            ensure_output_dir()
            PARAM_LIST_RESPONSE_PATH.write_text(body + "\n", encoding="utf-8")
            t.log(f"💾 Parameter list saved to {PARAM_LIST_RESPONSE_PATH}", "GREEN")
        else:
            t.log("⚠ No param_list_res lines collected; file not written.", "YELLOW")

        status = "complete" if completed else "partial (timeout)"
        t.log(f"📋 Device parameters ({status}):", "YELLOW")
        for idx in sorted(self._rows.keys()):
            name, value = self._rows[idx]
            if idx == 0:
                n = self._expected_count
                t.log(
                    f"  [0] header — expecting {n if n is not None else value} row(s)",
                    "CYAN",
                )
            else:
                t.log(f"  [{idx}] {name} = {value}", "WHITE")


def try_feed_param_list_session(message: str) -> bool:
    if _active_param_list_session is None:
        return False
    fed = False
    for line in message.replace("\r\n", "\n").split("\n"):
        parsed = parse_param_list_res(line.strip())
        if parsed:
            _active_param_list_session.feed_parsed(parsed)
            fed = True
    return fed


@cli_command
async def cmd_param_list(inv: "CommandInvocation", nus: "NusPort") -> None:
    """Send param_list(...) over BLE, collect param_list_res, write output/param_list.txt."""
    global _active_param_list_session
    t = _terminal()
    session = ParamListCollectionSession()
    _active_param_list_session = session
    try:
        for line in list(_param_list_res_recent):
            p = parse_param_list_res(line)
            if p:
                session.feed_parsed(p)
        if not await nus.send_message(inv.line):
            return
        completed = await session.wait_until_done(PARAM_LIST_WAIT_SECONDS)
        if not completed:
            t.log(
                f"⏱ param_list collection timed out after {PARAM_LIST_WAIT_SECONDS:g}s; "
                "showing partial results.",
                "YELLOW",
            )
        session.write_file_and_log(completed)
    finally:
        if _active_param_list_session is session:
            _active_param_list_session = None
        _param_list_res_recent.clear()
