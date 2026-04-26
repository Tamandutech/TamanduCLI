"""
BLE help flow: collect ``help(h,...)`` / ``help(b,...)`` wire responses and ``cmd_help``.
"""

from __future__ import annotations

from collections import deque
from typing import Callable, Optional

from api.command_handlers import (
    CliHandlerContext,
    cli_command,
    register_ble_capture,
    register_ble_try_feed,
)
from api.list_wire import (
    ListWireCollectionSession,
    ble_message_has_list_wire_response,
    feed_list_wire_collection_from_ble_text,
)
from api.output_paths import OUTPUT_DIR, ensure_output_dir
from api.protocol_utils import WireCommand, format_message

HELP_WAIT_SECONDS = 3.0
HELP_RESPONSE_PATH = OUTPUT_DIR / "help_response.txt"

_help_recent: deque[str] = deque(maxlen=64)
_active_help_session: Optional[ListWireCollectionSession] = None


@register_ble_capture
def capture_help_from_ble(message: str) -> None:
    """Buffer notifications that contain help list wire responses (replay when session starts)."""
    for line in message.replace("\r\n", "\n").split("\n"):
        s = line.strip()
        if s and ble_message_has_list_wire_response("help", s):
            _help_recent.append(s)


capture_help_res_from_ble = capture_help_from_ble


def _log_help_collection_summary(
    session: ListWireCollectionSession, log: Callable[[str, str], None], completed: bool
) -> None:
    status = "completo" if completed else "parcial (tempo esgotado)"
    log(f"📋 Help do dispositivo ({status}):", "YELLOW")
    for idx in sorted(session.rows.keys()):
        name, value = session.rows[idx]
        if idx == 0:
            n = session.expected_row_total
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
    return feed_list_wire_collection_from_ble_text(_active_help_session, message)


@cli_command
async def cmd_help(inv: WireCommand, ctx: CliHandlerContext) -> None:
    global _active_help_session
    session = ListWireCollectionSession("help", record_raw_wire_lines=False)
    _active_help_session = session
    try:
        for buffered in list(_help_recent):
            feed_list_wire_collection_from_ble_text(session, buffered)
        wire = format_message([WireCommand.single_request("help", inv.arguments)])
        if not await ctx.send_wire(wire):
            return
        completed = await session.wait_until_done(HELP_WAIT_SECONDS)
        if not completed:
            ctx.log(
                f"⏱ Coleta de help excedeu {HELP_WAIT_SECONDS:g}s; mostrando resultados parciais.",
                "YELLOW",
            )
        ensure_output_dir()
        if session.write_file_if_non_empty(HELP_RESPONSE_PATH):
            ctx.log(f"💾 Lista de help salva em {HELP_RESPONSE_PATH}", "GREEN")
        else:
            ctx.log(
                "⚠ Nenhuma linha wire de help coletada; arquivo não gravado.", "YELLOW"
            )
        _log_help_collection_summary(session, ctx.log, completed)
    finally:
        if _active_help_session is session:
            _active_help_session = None
        _help_recent.clear()
