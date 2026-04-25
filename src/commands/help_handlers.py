"""
BLE help flow: collect ``help(h,...)`` / ``help(b,...)`` wire responses and ``cmd_help``.
"""

from __future__ import annotations

import asyncio
from collections import deque
from typing import Callable, Optional

from api.command_handlers import (
    CliHandlerContext,
    cli_command,
    register_ble_capture,
    register_ble_try_feed,
)
from api.output_paths import OUTPUT_DIR, ensure_output_dir
from api.protocol_utils import WireCommand, format_message, format_wire_command, parse_message, unquote_field

HELP_WAIT_SECONDS = 3.0
HELP_RESPONSE_PATH = OUTPUT_DIR / "help_response.txt"

_help_recent: deque[str] = deque(maxlen=64)
_active_help_session: Optional["HelpCollectionSession"] = None


def _message_has_help_list_response(text: str) -> bool:
    for c in parse_message(text):
        if c.name.lower() != "help" or not c.is_response:
            continue
        if c.kind in ("list_header", "list_body"):
            return True
    return False


@register_ble_capture
def capture_help_from_ble(message: str) -> None:
    """Buffer notifications that contain help list wire responses (replay when session starts)."""
    for line in message.replace("\r\n", "\n").split("\n"):
        s = line.strip()
        if s and _message_has_help_list_response(s):
            _help_recent.append(s)


capture_help_res_from_ble = capture_help_from_ble


class HelpCollectionSession:
    def __init__(self) -> None:
        self._rows: dict[int, tuple[str, str]] = {}
        self._expected_command_count: Optional[int] = None
        self._done = asyncio.Event()

    def feed_wire(self, cmd: WireCommand) -> None:
        if cmd.name.lower() != "help" or not cmd.is_response:
            return
        if cmd.kind == "list_header":
            if not cmd.arguments:
                return
            try:
                n = int(unquote_field(cmd.arguments[0]))
            except ValueError:
                return
            self._rows[0] = ("header", str(n))
            self._expected_command_count = n
        elif cmd.kind == "list_body":
            idx = cmd.index
            if not cmd.arguments:
                return
            name = unquote_field(cmd.arguments[0])
            value = ", ".join(unquote_field(a) for a in cmd.arguments[1:])
            self._rows[idx] = (name, value)
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

    def write_file_and_log(self, completed: bool, log: Callable[[str, str], None]) -> None:
        lines_out: list[str] = []
        for idx in sorted(self._rows.keys()):
            name, value = self._rows[idx]
            if idx == 0 and name.lower() == "header":
                try:
                    n = int(value.strip())
                    lines_out.append(format_wire_command(WireCommand("help", "list_header", True, 0, (str(n),))))
                except ValueError:
                    lines_out.append(format_wire_command(WireCommand("help", "list_header", True, 0, (value,))))
            else:
                lines_out.append(
                    format_wire_command(WireCommand("help", "list_body", True, idx, (name, value)))
                )

        body = "\n".join(lines_out)
        if body:
            ensure_output_dir()
            HELP_RESPONSE_PATH.write_text(body + "\n", encoding="utf-8")
            log(f"💾 Lista de help salva em {HELP_RESPONSE_PATH}", "GREEN")
        else:
            log("⚠ Nenhuma linha wire de help coletada; arquivo não gravado.", "YELLOW")

        status = "completo" if completed else "parcial (tempo esgotado)"
        log(f"📋 Help do dispositivo ({status}):", "YELLOW")
        for idx in sorted(self._rows.keys()):
            name, value = self._rows[idx]
            if idx == 0:
                n = self._expected_command_count
                log(
                    f"  [0] cabeçalho — esperando {n if n is not None else value} linha(s) de comando",
                    "CYAN",
                )
            else:
                log(f"  [{idx}] {name}: {value}", "WHITE")


@register_ble_try_feed
def try_feed_help_session(message: str) -> bool:
    if _active_help_session is None:
        return False
    fed = False
    seen: set[str] = set()
    parts = [message] + message.replace("\r\n", "\n").split("\n")
    for part in parts:
        key = part.strip()
        if not key or key in seen:
            continue
        seen.add(key)
        for cmd in parse_message(key):
            if cmd.name.lower() == "help" and cmd.is_response and cmd.kind in ("list_header", "list_body"):
                _active_help_session.feed_wire(cmd)
                fed = True
    return fed


@cli_command
async def cmd_help(inv: WireCommand, ctx: CliHandlerContext) -> None:
    global _active_help_session
    session = HelpCollectionSession()
    _active_help_session = session
    try:
        for buffered in list(_help_recent):
            for cmd in parse_message(buffered):
                session.feed_wire(cmd)
        wire = format_message([WireCommand.single_request("help", inv.arguments)])
        if not await ctx.send_wire(wire):
            return
        completed = await session.wait_until_done(HELP_WAIT_SECONDS)
        if not completed:
            ctx.log(
                f"⏱ Coleta de help excedeu {HELP_WAIT_SECONDS:g}s; mostrando resultados parciais.",
                "YELLOW",
            )
        session.write_file_and_log(completed, ctx.log)
    finally:
        if _active_help_session is session:
            _active_help_session = None
        _help_recent.clear()
