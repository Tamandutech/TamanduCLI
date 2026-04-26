"""
List wire protocol: batched ``list_body`` sends with OK pacing, and multi-BLE list collection.

List headers on the wire always use four integers ``T, C, B, j`` (see :class:`WireListHeader`).
When parsing, a single trailing integer is still normalized to ``(T, 1, 1, 0)`` for old
transcripts or firmware that has not upgraded yet.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Callable, Mapping, Optional, Protocol, Sequence, runtime_checkable

from api.incoming import IncomingRouter
from api.protocol_utils import (
    DEFAULT_WIRE_MESSAGE_MAX_BYTES,
    WireCommand,
    WireListHeader,
    format_wire_command,
    pack_list_body_requests_into_batched_wire_messages,
    parse_message,
    unquote_field,
)

DEFAULT_LIST_BATCH_MESSAGES_BEFORE_ACK = 5
DEFAULT_LIST_BATCH_ACK_TIMEOUT_SECONDS = 30.0


def list_response_header_row_total(cmd: WireCommand) -> Optional[int]:
    """
    Expected list row count ``T`` from a ``list_header`` **response** (``WireListHeader``).
    """
    if not cmd.is_response:
        return None
    header = WireListHeader.from_wire_command(cmd)
    if header is None:
        return None
    return header.total_row_count


def ble_message_has_list_wire_response(command_name: str, text: str) -> bool:
    """True if ``text`` parses to at least one matching ``list_header`` / ``list_body`` response."""
    key = command_name.lower()
    for c in parse_message(text):
        if c.name.lower() != key or not c.is_response:
            continue
        if c.kind in ("list_header", "list_body"):
            return True
    return False


def feed_list_wire_collection_from_ble_text(
    session: "ListWireCollectionSession", message: str
) -> bool:
    """
    Split ``message`` on lines, parse wire commands, feed matching responses into ``session``.

    Returns whether any relevant command was fed.
    """
    fed = False
    seen: set[str] = set()
    for part in [message] + message.replace("\r\n", "\n").split("\n"):
        key = part.strip()
        if not key or key in seen:
            continue
        seen.add(key)
        for cmd in parse_message(key):
            if (
                cmd.name.lower() == session.command_key
                and cmd.is_response
                and cmd.kind in ("list_header", "list_body")
            ):
                session.feed_wire(cmd)
                fed = True
    return fed


class ListWireCollectionSession:
    """
    Collect ``list_header`` / ``list_body`` responses for one command until row ``1..N`` exist.

    Row ``0`` stores ``("header", str(N))`` when the expected count ``N`` is known.
    """

    def __init__(
        self,
        command_name: str,
        *,
        record_raw_wire_lines: bool = True,
    ) -> None:
        self._wire_name = command_name
        self.command_key = command_name.lower()
        self._record_raw = record_raw_wire_lines
        self._expected: Optional[int] = None
        self._rows: dict[int, tuple[str, str]] = {}
        self._wire_lines: list[str] = []
        self._done = asyncio.Event()

    @property
    def expected_row_total(self) -> Optional[int]:
        return self._expected

    @property
    def rows(self) -> Mapping[int, tuple[str, str]]:
        return self._rows

    def feed_wire(self, cmd: WireCommand) -> None:
        if cmd.name.lower() != self.command_key or not cmd.is_response:
            return
        if cmd.kind == "list_header":
            header = WireListHeader.from_wire_command(cmd)
            if header is None:
                return
            n = header.total_row_count
            self._rows[0] = ("header", str(n))
            self._expected = n
            if self._record_raw:
                self._wire_lines.append(format_wire_command(cmd))
        elif cmd.kind == "list_body":
            if not cmd.arguments:
                return
            idx = cmd.index
            name = unquote_field(cmd.arguments[0])
            value = ", ".join(unquote_field(a) for a in cmd.arguments[1:])
            self._rows[idx] = (name, value)
            if self._record_raw:
                self._wire_lines.append(format_wire_command(cmd))
        if self._is_complete():
            self._done.set()

    def _is_complete(self) -> bool:
        if self._expected is None or 0 not in self._rows:
            return False
        name0, _ = self._rows[0]
        if name0.lower() != "header":
            return False
        n = self._expected
        return all(i in self._rows for i in range(1, n + 1))

    async def wait_until_done(self, timeout: float) -> bool:
        try:
            await asyncio.wait_for(self._done.wait(), timeout=timeout)
            return True
        except asyncio.TimeoutError:
            return False

    def render_file_body(self) -> str:
        """UTF-8 text for a saved list file (raw captured lines or reconstructed wire)."""
        if self._record_raw:
            body = "\n".join(self._wire_lines)
            return body + ("\n" if self._wire_lines else "")
        lines_out: list[str] = []
        for idx in sorted(self._rows.keys()):
            name, value = self._rows[idx]
            if idx == 0 and name.lower() == "header":
                try:
                    n = int(value.strip())
                    lines_out.append(
                        format_wire_command(
                            WireListHeader.single_message(n).to_wire_command(
                                self._wire_name, is_response=True
                            )
                        )
                    )
                except ValueError:
                    n0 = self._expected if self._expected is not None else 0
                    lines_out.append(
                        format_wire_command(
                            WireListHeader.single_message(n0).to_wire_command(
                                self._wire_name, is_response=True
                            )
                        )
                    )
            else:
                lines_out.append(
                    format_wire_command(
                        WireCommand(
                            self._wire_name, "list_body", True, idx, (name, value)
                        )
                    )
                )
        return "\n".join(lines_out) + ("\n" if lines_out else "")

    def write_file_if_non_empty(self, path: Path) -> bool:
        """Write :meth:`render_file_body` to ``path`` if non-blank; return whether written."""
        body = self.render_file_body()
        if not body.strip():
            return False
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body, encoding="utf-8")
        return True


def _list_batch_ack_predicate(
    min_message_index: int, max_message_index: int
) -> Callable[[WireCommand], bool]:
    """
    Match ``name(s,s,<min>,<max>,"ok")`` acknowledgments for a sent batch of wire messages.

    ``min_message_index`` / ``max_message_index`` are inclusive global indices of batched
    wire messages (same numbering as the batch header's message index).
    """

    def pred(c: WireCommand) -> bool:
        if c.kind != "single" or not c.is_response:
            return False
        if len(c.arguments) < 3:
            return False
        try:
            lo = int(unquote_field(c.arguments[0]))
            hi = int(unquote_field(c.arguments[1]))
        except ValueError:
            return False
        if lo != min_message_index or hi != max_message_index:
            return False
        if unquote_field(c.arguments[2]).strip().lower() != "ok":
            return False
        return True

    return pred


@runtime_checkable
class ListBatchSendContext(Protocol):
    """Minimal context for :func:`send_homogeneous_list_body_requests_batched`."""

    incoming: IncomingRouter

    async def send_wire(self, message: str) -> bool: ...

    def log(self, message: str, color: str) -> None: ...


async def send_homogeneous_list_body_requests_batched(
    ctx: ListBatchSendContext,
    list_bodies: Sequence[WireCommand],
    *,
    max_bytes: int = DEFAULT_WIRE_MESSAGE_MAX_BYTES,
    max_messages_before_ack: Optional[int] = None,
    ack_timeout: float = DEFAULT_LIST_BATCH_ACK_TIMEOUT_SECONDS,
    log_prefix: str = "📤",
) -> bool:
    """
    Send ``list_body`` requests sharing one command name using wire batch headers.

    If ``max_messages_before_ack`` is a positive integer, after that many BLE wire
    messages are sent the client waits for
    ``name(s,s,<min_message_index>,<max_message_index>,"ok")`` (inclusive indices
    matching the batched message order) before sending the next group.
    ``None`` or ``<= 0`` disables acknowledgment pacing.
    """
    if not list_bodies:
        return True
    cmd_name = list_bodies[0].name.lower()
    packed = pack_list_body_requests_into_batched_wire_messages(
        list_bodies, max_bytes=max_bytes
    )
    if not packed:
        return True

    burst = max_messages_before_ack
    if burst is None or burst < 1:
        for msg in packed:
            ctx.log(f"{log_prefix} {msg}", "CYAN")
            if not await ctx.send_wire(msg):
                return False
        return True

    offset = 0
    while offset < len(packed):
        window = packed[offset : offset + burst]
        win_lo = offset
        win_hi = offset + len(window) - 1
        for msg in window:
            ctx.log(f"{log_prefix} {msg}", "CYAN")
            if not await ctx.send_wire(msg):
                return False
        try:
            await ctx.incoming.wait_for(
                cmd_name,
                is_response=True,
                timeout=ack_timeout,
                predicate=_list_batch_ack_predicate(win_lo, win_hi),
            )
        except TimeoutError:
            ctx.log(
                f"⏱ Resposta OK ausente para {cmd_name!r} índices de mensagem {win_lo}…{win_hi} "
                f"após {ack_timeout:g}s.",
                "RED",
            )
            return False
        ctx.log(
            f'✅ {cmd_name}(s,s,{win_lo},{win_hi},"ok") — lote processado; enviando próximo grupo.',
            "GREEN",
        )
        offset += len(window)
    return True
