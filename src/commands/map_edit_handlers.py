"""
map_edit: map_get (wire), collect ``map_get(b,s,…)`` body rows as CSV, save map.txt, edit, apply wire map_add / map_clear / map_SaveRuntime.
"""

from __future__ import annotations

import asyncio
import difflib
import shutil
from collections import deque
from pathlib import Path
from typing import Optional

from prompt_toolkit.shortcuts import confirm

from api.command_handlers import (
    DEFAULT_LIST_BATCH_ACK_TIMEOUT_SECONDS,
    DEFAULT_LIST_BATCH_MESSAGES_BEFORE_ACK,
    CliHandlerContext,
    cli_command,
    register_ble_capture,
    register_ble_try_feed,
    send_homogeneous_list_body_requests_batched,
)
from api.output_paths import INPUT_DIR, OUTPUT_DIR, ensure_input_dir, ensure_output_dir
from api.protocol_utils import WireCommand, format_message, parse_message, unquote_field

MAP_GET_IDLE_SECONDS = 3.0
MAP_OUTPUT_PATH = OUTPUT_DIR / "map.txt"
MAP_INPUT_PATH = INPUT_DIR / "map.txt"

_map_get_wire_recent: deque[str] = deque(maxlen=8)
_active_map_get_session: Optional["MapGetSession"] = None


def _map_get_body_to_csv_line(cmd: WireCommand) -> Optional[str]:
    """
    One map row from ``map_get(b,s,<idx>, <5 fields>)``.

    Prefer five arguments (index, time, encMedia, trackStatus, offset). If only four
    arguments are sent, ``cmd.index`` is prepended as the first CSV column.
    """
    if cmd.name.lower() != "map_get" or not cmd.is_response or cmd.kind != "list_body":
        return None
    args = [unquote_field(a) for a in cmd.arguments]
    if len(args) >= 5:
        return ",".join(args[:5])
    if len(args) == 4:
        return f"{cmd.index},{','.join(args)}"
    return None


def _message_has_map_get_body(text: str) -> bool:
    for c in parse_message(text):
        if _map_get_body_to_csv_line(c) is not None:
            return True
    return False


@register_ble_capture
def capture_map_get_res_from_ble(message: str) -> None:
    for line in message.replace("\r\n", "\n").split("\n"):
        s = line.strip()
        if s and _message_has_map_get_body(s):
            _map_get_wire_recent.append(s)


def iter_map_get_wire_csv_lines(message: str) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for part in [message.strip()] + message.replace("\r\n", "\n").split("\n"):
        key = part.strip()
        if not key or key in seen:
            continue
        seen.add(key)
        for cmd in parse_message(key):
            row = _map_get_body_to_csv_line(cmd)
            if row is not None:
                out.append(row)
    return out


class MapGetSession:
    def __init__(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop
        self._done = asyncio.Event()
        self._parts: list[str] = []
        self._idle_handle: Optional[asyncio.TimerHandle] = None
        self._dead = False
        self._finished = False

    def start_idle_watch(self) -> None:
        self._schedule_idle()

    def feed(self, data: str) -> None:
        self._loop.call_soon_threadsafe(self._on_row, data)

    def _schedule_idle(self) -> None:
        if self._dead or self._finished:
            return
        if self._idle_handle is not None:
            self._idle_handle.cancel()
            self._idle_handle = None
        self._idle_handle = self._loop.call_later(MAP_GET_IDLE_SECONDS, self._complete_after_idle)

    def _on_row(self, data: str) -> None:
        if self._dead or self._finished:
            return
        row = data.strip()
        if not row:
            return
        self._parts.append(row)
        self._schedule_idle()

    def _complete_after_idle(self) -> None:
        self._idle_handle = None
        if self._dead or self._finished:
            return
        self._finished = True
        self._done.set()

    def close(self) -> None:
        self._dead = True
        if self._idle_handle is not None:
            self._idle_handle.cancel()
            self._idle_handle = None

    async def wait_until_done(self) -> None:
        await self._done.wait()

    @property
    def data(self) -> Optional[str]:
        if not self._parts:
            return None
        return "\n".join(self._parts) + "\n"


@register_ble_try_feed
def try_feed_map_get_session(message: str) -> bool:
    if _active_map_get_session is None:
        return False
    fed = False
    for row in iter_map_get_wire_csv_lines(message):
        _active_map_get_session.feed(row)
        fed = True
    return fed


def _parse_map_rows(path: Path) -> tuple[dict[int, tuple[int, int, int, int, int]], list[str]]:
    rows: dict[int, tuple[int, int, int, int, int]] = {}
    errors: list[str] = []
    text = path.read_text(encoding="utf-8")
    for lineno, line in enumerate(text.splitlines(), start=1):
        s = line.strip()
        if not s:
            continue
        parsed = _parse_map_row(s)
        if parsed is None:
            errors.append(f"  line {lineno}: {s[:120]!r}")
            continue
        idx = parsed[0]
        rows[idx] = parsed
    return rows, errors


def _parse_map_row(s: str) -> Optional[tuple[int, int, int, int, int]]:
    parts = [p.strip() for p in s.split(",")]
    if len(parts) != 5:
        return None
    try:
        a, b, c, d, e = (int(p) for p in parts)
    except ValueError:
        return None
    return a, b, c, d, e


@cli_command
async def cmd_map_edit(inv: WireCommand, ctx: CliHandlerContext) -> None:
    _ = inv
    global _active_map_get_session
    _map_get_wire_recent.clear()
    session = MapGetSession(asyncio.get_running_loop())
    _active_map_get_session = session
    try:
        wire_get = format_message([WireCommand.single_request("map_get", ())])
        if not await ctx.send_wire(wire_get):
            return
        session.start_idle_watch()
        for buffered in list(_map_get_wire_recent):
            for row in iter_map_get_wire_csv_lines(buffered):
                session.feed(row)
        await session.wait_until_done()
        raw = session.data
        if raw is None:
            ctx.log(
                f"⏱ map_get: nenhuma linha ``map_get(b,s,…)`` em {MAP_GET_IDLE_SECONDS:g}s de inatividade após o pedido.",
                "YELLOW",
            )
            return
        ensure_output_dir()
        ensure_input_dir()
        MAP_OUTPUT_PATH.write_text(raw, encoding="utf-8")
        shutil.copy2(MAP_OUTPUT_PATH, MAP_INPUT_PATH)
        rel_out = MAP_OUTPUT_PATH.relative_to(OUTPUT_DIR.parent)
        rel_in = MAP_INPUT_PATH.relative_to(OUTPUT_DIR.parent)
        ctx.log(
            f"💾 Salvo {rel_out} e copiado para {rel_in} — edite o arquivo em input/ e salve.",
            "GREEN",
        )

        loop = asyncio.get_running_loop()
        done = await loop.run_in_executor(
            None,
            lambda: confirm(
                "Terminou a edição? Continuar para ver diferenças e aplicar?"
            ),
        )
        if not done:
            ctx.log("Cancelado (sem diff ou aplicar).", "YELLOW")
            return

        original_text = MAP_OUTPUT_PATH.read_text(encoding="utf-8")
        edited_text = MAP_INPUT_PATH.read_text(encoding="utf-8")
        orig_lines = original_text.splitlines(keepends=True)
        edit_lines = edited_text.splitlines(keepends=True)
        diff_lines = list(
            difflib.unified_diff(
                orig_lines,
                edit_lines,
                fromfile="output/map.txt",
                tofile="input/map.txt",
            )
        )
        ctx.log("--- Diferenças (output/map.txt → input/map.txt) ---", "YELLOW")
        if not diff_lines:
            ctx.log("(sem diferenças)", "WHITE")
            ctx.log("Nada a aplicar.", "YELLOW")
            return
        for line in diff_lines:
            ctx.log(line.rstrip("\n"), "WHITE")

        ok = await loop.run_in_executor(
            None,
            lambda: confirm(
                "Essas mudanças são intencionais? Serão enviados map_clear, map_add para cada linha de input/map.txt e depois map_SaveRuntime."
            ),
        )
        if not ok:
            ctx.log("Cancelado — nada enviado.", "YELLOW")
            return

        _, edit_errors = _parse_map_rows(MAP_INPUT_PATH)
        if edit_errors:
            ctx.log(
                "⚠ O arquivo editado tem linhas que não são index,time,encMedia,trackStatus,offset válidos:",
                "RED",
            )
            for e in edit_errors:
                ctx.log(e, "RED")
            ctx.log("Corrija o arquivo e execute map_edit novamente.", "YELLOW")
            return

        ctx.log("📤 map_clear", "CYAN")
        if not await ctx.send_wire(format_message([WireCommand.single_request("map_clear", ())])):
            ctx.log("⚠ Falha ao enviar map_clear; interrompendo.", "RED")
            return
        await asyncio.sleep(1.0)

        map_input_body = MAP_INPUT_PATH.read_text(encoding="utf-8")
        map_add_rows: list[WireCommand] = []
        for raw_line in map_input_body.splitlines():
            line = raw_line.strip()
            if not line:
                continue
            parts = [p.strip() for p in line.split(",")]
            row_idx = int(parts[0])
            map_add_rows.append(
                WireCommand("map_add", "list_body", False, row_idx, tuple(parts[1:]))
            )
        if not await send_homogeneous_list_body_requests_batched(
            ctx,
            map_add_rows,
            max_messages_before_ack=DEFAULT_LIST_BATCH_MESSAGES_BEFORE_ACK,
            ack_timeout=DEFAULT_LIST_BATCH_ACK_TIMEOUT_SECONDS,
        ):
            ctx.log("⚠ Falha ao enviar; interrompendo.", "RED")
            return

        ctx.log(f"✅ Enviados map_clear e {len(map_add_rows)} linha(s) map_add.", "GREEN")
        await asyncio.sleep(1.0)
        if not await ctx.send_wire(format_message([WireCommand.single_request("map_SaveRuntime", ())])):
            ctx.log("⚠ Falha ao enviar map_SaveRuntime.", "RED")
            return
        ctx.log("📤 map_SaveRuntime enviado.", "GREEN")
    finally:
        session.close()
        if _active_map_get_session is session:
            _active_map_get_session = None
        _map_get_wire_recent.clear()
