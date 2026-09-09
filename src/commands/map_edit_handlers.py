"""
map_edit: map_get (wire), collect ``map_get(b,s,…)`` body rows as CSV, save map.txt, edit, apply wire map_add / map_clear / map_save.

CSV columns (``index`` is the array slot; the other six fields are ``MapPoint``):

    index,encoder_left,encoder_right,encoder_derivative,encoder_derivative_average,speed,point_type
"""

from __future__ import annotations

import asyncio
import difflib
import shutil
from collections import deque
from pathlib import Path
from typing import NamedTuple, Optional

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
from api.list_wire import ble_message_has_list_wire_response
from api.output_paths import INPUT_DIR, OUTPUT_DIR, ensure_input_dir, ensure_output_dir
from api.protocol_utils import (
    WireCommand,
    WireListHeader,
    format_message,
    parse_message,
    unquote_field,
)

MAP_GET_TIMEOUT_SECONDS = 60.0
MAP_GET_NO_HEADER_IDLE_SECONDS = 3.0
MAP_GET_STALL_SECONDS = 10.0
MAP_SAVE_TIMEOUT_SECONDS = 5.0
MAP_OUTPUT_PATH = OUTPUT_DIR / "map.txt"
MAP_INPUT_PATH = INPUT_DIR / "map.txt"
MAP_CSV_COLUMNS = (
    "index",
    "encoder_left",
    "encoder_right",
    "encoder_derivative",
    "encoder_derivative_average",
    "speed",
    "point_type",
)
MAP_CSV_HEADER = ",".join(MAP_CSV_COLUMNS)
MAP_POINT_FIELD_COUNT = 6
_INT32_MIN = -2_147_483_648
_INT32_MAX = 2_147_483_647
POINT_TYPE_NAMES = {
    "AUTO_MARK": 0,
    "MANUAL_MARK": 1,
    "STOP_COMMAND_MARK": 2,
    "UNKNOWN_MARK": 3,
    "CURVE_START_MARK": 4,
    "CURVE_END_MARK": 5,
}
POINT_TYPE_VALUES = frozenset(POINT_TYPE_NAMES.values())

_map_get_wire_recent: deque[str] = deque(maxlen=64)
_active_map_get_session: Optional["MapGetSession"] = None


def _map_get_body_to_csv_line(cmd: WireCommand) -> Optional[str]:
    """
    One map row from ``map_get(b,s,<idx>, <MapPoint fields>)``.

    Prefer seven arguments (index plus MapPoint). If only six arguments are sent
    (encoder_left, encoder_right, encoder_derivative, encoder_derivative_average,
    speed, point_type), ``cmd.index`` is prepended as the first CSV column.
    """
    if cmd.name.lower() != "map_get" or not cmd.is_response or cmd.kind != "list_body":
        return None
    args = [unquote_field(a) for a in cmd.arguments]
    if len(args) >= 7:
        return ",".join(args[:7])
    if len(args) == MAP_POINT_FIELD_COUNT:
        return f"{cmd.index},{','.join(args)}"
    return None


@register_ble_capture
def capture_map_get_res_from_ble(message: str) -> None:
    for line in message.replace("\r\n", "\n").split("\n"):
        s = line.strip()
        if s and ble_message_has_list_wire_response("map_get", s):
            _map_get_wire_recent.append(s)


class MapGetSession:
    """
    Collect ``map_get`` list responses until header ``T`` rows are present.

    Completes when bodies ``1..T`` (or ``0..T-1``) are stored. The host does not
    write ACKs while the device is notifying: overlapping GATT writes collide
    with NUS RX (Bleak error 15 / pending tasks). A stall without new rows or
    the overall timeout may still end the session with a partial map.
    """

    def __init__(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop
        self._done = asyncio.Event()
        self._rows: dict[int, str] = {}
        self._expected_rows: Optional[int] = None
        self._expected_messages: Optional[int] = None
        self._received_message_indices: set[int] = set()
        self._idle_handle: Optional[asyncio.TimerHandle] = None
        self._dead = False
        self._finished = False

    @property
    def expected_row_total(self) -> Optional[int]:
        return self._expected_rows

    @property
    def expected_message_total(self) -> Optional[int]:
        return self._expected_messages

    @property
    def received_message_indices(self) -> frozenset[int]:
        return frozenset(self._received_message_indices)

    @property
    def row_count(self) -> int:
        return len(self._rows)

    def feed_ble(self, message: str) -> bool:
        cmds = [
            c
            for c in parse_message(message)
            if c.name.lower() == "map_get"
            and c.is_response
            and c.kind in ("list_header", "list_body")
        ]
        if not cmds:
            return False
        self._loop.call_soon_threadsafe(self._on_commands, tuple(cmds))
        return True

    def _on_commands(self, cmds: tuple[WireCommand, ...]) -> None:
        if self._dead or self._finished:
            return
        for cmd in cmds:
            if cmd.kind == "list_header":
                header = WireListHeader.from_wire_command(cmd)
                if header is None:
                    continue
                self._expected_rows = header.total_row_count
                self._expected_messages = header.total_messages
                self._received_message_indices.add(header.message_index)
                self._cancel_idle()
            else:
                row = _map_get_body_to_csv_line(cmd)
                if row is None:
                    continue
                try:
                    idx = int(row.split(",", 1)[0], 10)
                except ValueError:
                    idx = cmd.index
                self._rows[idx] = row
        if self._is_complete():
            self._finish()
            return
        self._schedule_stall()

    def _schedule_stall(self) -> None:
        if self._dead or self._finished:
            return
        self._cancel_idle()
        delay = (
            MAP_GET_NO_HEADER_IDLE_SECONDS
            if self._expected_rows is None
            else MAP_GET_STALL_SECONDS
        )
        self._idle_handle = self._loop.call_later(delay, self._complete_after_idle)

    def _cancel_idle(self) -> None:
        if self._idle_handle is not None:
            self._idle_handle.cancel()
            self._idle_handle = None

    def _complete_after_idle(self) -> None:
        self._idle_handle = None
        if self._dead or self._finished:
            return
        self._finish()

    def _is_complete(self) -> bool:
        n = self._expected_rows
        if n is None:
            return False
        if n <= 0:
            return True
        if all(i in self._rows for i in range(1, n + 1)):
            return True
        return all(i in self._rows for i in range(0, n))

    def _finish(self) -> None:
        self._finished = True
        self._cancel_idle()
        self._done.set()

    def close(self) -> None:
        self._dead = True
        self._cancel_idle()

    async def wait_until_done(self, timeout: float) -> bool:
        try:
            await asyncio.wait_for(self._done.wait(), timeout=timeout)
        except TimeoutError:
            return False
        return self._is_complete()

    @property
    def data(self) -> Optional[str]:
        if not self._rows:
            return None
        return "\n".join(self._rows[i] for i in sorted(self._rows)) + "\n"

    def last_point_type(self) -> Optional[int]:
        if not self._rows:
            return None
        last = self._rows[max(self._rows)]
        parts = [p.strip() for p in last.split(",")]
        if not parts:
            return None
        return _parse_point_type(parts[-1])


@register_ble_try_feed
def try_feed_map_get_session(message: str) -> bool:
    if _active_map_get_session is None:
        return False
    return _active_map_get_session.feed_ble(message)


class MapRow(NamedTuple):
    """CSV row: array ``index`` plus ``MapPoint`` fields (index is not stored on device in MapPoint)."""

    index: int
    encoder_left: int
    encoder_right: int
    encoder_derivative: float
    encoder_derivative_average: float
    speed: float
    point_type: int
    encoder_derivative_wire: str
    encoder_derivative_average_wire: str
    speed_wire: str


def _is_map_csv_header(s: str) -> bool:
    parts = [p.strip().lower() for p in s.split(",")]
    return parts == [c.lower() for c in MAP_CSV_COLUMNS]


def _parse_int32(s: str) -> Optional[int]:
    try:
        value = int(s, 10)
    except ValueError:
        return None
    if value < _INT32_MIN or value > _INT32_MAX:
        return None
    return value


def _parse_float(s: str) -> Optional[float]:
    try:
        return float(s)
    except ValueError:
        return None


def _parse_point_type(s: str) -> Optional[int]:
    name = s.strip()
    mapped = POINT_TYPE_NAMES.get(name)
    if mapped is None:
        mapped = POINT_TYPE_NAMES.get(name.upper())
    if mapped is not None:
        return mapped
    try:
        value = int(name, 10)
    except ValueError:
        return None
    if value not in POINT_TYPE_VALUES:
        return None
    return value


def _parse_map_rows(path: Path) -> tuple[list[MapRow], list[str]]:
    rows: list[MapRow] = []
    errors: list[str] = []
    text = path.read_text(encoding="utf-8")
    for lineno, line in enumerate(text.splitlines(), start=1):
        s = line.strip()
        if not s or _is_map_csv_header(s):
            continue
        parsed = _parse_map_row(s)
        if parsed is None:
            errors.append(f"  line {lineno}: {s[:120]!r}")
            continue
        rows.append(parsed)
    return rows, errors


def _parse_map_row(s: str) -> Optional[MapRow]:
    parts = [p.strip() for p in s.split(",")]
    if len(parts) != len(MAP_CSV_COLUMNS):
        return None
    try:
        index = int(parts[0], 10)
    except ValueError:
        return None
    encoder_left = _parse_int32(parts[1])
    encoder_right = _parse_int32(parts[2])
    encoder_derivative = _parse_float(parts[3])
    encoder_derivative_average = _parse_float(parts[4])
    speed = _parse_float(parts[5])
    if (
        encoder_left is None
        or encoder_right is None
        or encoder_derivative is None
        or encoder_derivative_average is None
        or speed is None
    ):
        return None
    point_type = _parse_point_type(parts[6])
    if point_type is None:
        return None
    return MapRow(
        index=index,
        encoder_left=encoder_left,
        encoder_right=encoder_right,
        encoder_derivative=encoder_derivative,
        encoder_derivative_average=encoder_derivative_average,
        speed=speed,
        point_type=point_type,
        encoder_derivative_wire=parts[3],
        encoder_derivative_average_wire=parts[4],
        speed_wire=parts[5],
    )


def _map_row_to_add_arguments(row: MapRow) -> tuple[str, ...]:
    return (
        str(row.encoder_left),
        str(row.encoder_right),
        row.encoder_derivative_wire,
        row.encoder_derivative_average_wire,
        row.speed_wire,
        str(row.point_type),
    )


def _map_save_status(cmd: WireCommand) -> str:
    if not cmd.arguments:
        return ""
    return unquote_field(cmd.arguments[0]).strip().lower()


async def _send_map_save(ctx: CliHandlerContext) -> bool:
    """Send ``map_save(s,r);`` and wait for ``map_save(s,s,ok)`` or ``map_save(s,s,error)``."""
    wire = format_message([WireCommand.single_request("map_save", ())])
    ctx.log("📤 map_save", "CYAN")
    if not await ctx.send_wire(wire):
        ctx.log("⚠ Falha ao enviar map_save.", "RED")
        return False
    try:
        resp = await ctx.incoming.wait_for(
            "map_save",
            timeout=MAP_SAVE_TIMEOUT_SECONDS,
            predicate=lambda c: c.kind == "single",
        )
    except TimeoutError:
        ctx.log(
            f"⏱ map_save: sem resposta ok/error em {MAP_SAVE_TIMEOUT_SECONDS:g}s.",
            "RED",
        )
        return False
    status = _map_save_status(resp)
    if status == "ok":
        ctx.log("✅ map_save: mapa gravado na flash.", "GREEN")
        return True
    if status == "error":
        ctx.log("⚠ map_save: escrita na flash falhou.", "RED")
        return False
    ctx.log(f"⚠ map_save: resposta inesperada ({status or 'vazia'}).", "RED")
    return False


def _log_map_get_collection(
    ctx: CliHandlerContext, session: MapGetSession, completed: bool
) -> None:
    expected = session.expected_row_total
    got = session.row_count
    messages = session.expected_message_total
    received_j = ",".join(str(i) for i in sorted(session.received_message_indices))
    if completed:
        ctx.log(f"✅ map_get completo: {got} ponto(s).", "GREEN")
        return
    if expected is None:
        ctx.log(
            f"⏱ map_get: tempo esgotado após {MAP_GET_TIMEOUT_SECONDS:g}s "
            f"({got} ponto(s), sem header T).",
            "YELLOW",
        )
        return
    ctx.log(
        f"⚠ map_get incompleto: {got}/{expected} ponto(s) "
        f"(header T={expected}, B={messages if messages is not None else '?'}"
        f", mensagens j={received_j or '—'}).",
        "RED",
    )
    last_type = session.last_point_type()
    stop = POINT_TYPE_NAMES["STOP_COMMAND_MARK"]
    if last_type != stop:
        ctx.log(
            f"⚠ Último ponto tem point_type={last_type}; "
            f"um mapa completo termina em STOP_COMMAND_MARK ({stop}).",
            "RED",
        )


@cli_command
async def cmd_map_save(inv: WireCommand, ctx: CliHandlerContext) -> None:
    _ = inv
    await _send_map_save(ctx)


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
        for buffered in list(_map_get_wire_recent):
            session.feed_ble(buffered)
        completed = await session.wait_until_done(MAP_GET_TIMEOUT_SECONDS)
        raw = session.data
        if raw is None:
            ctx.log(
                f"⏱ map_get: nenhuma linha ``map_get(b,s,…)`` em "
                f"{MAP_GET_TIMEOUT_SECONDS:g}s após o pedido.",
                "YELLOW",
            )
            return
        _log_map_get_collection(ctx, session, completed)
        ensure_output_dir()
        ensure_input_dir()
        body = raw if raw.endswith("\n") else raw + "\n"
        MAP_OUTPUT_PATH.write_text(f"{MAP_CSV_HEADER}\n{body}", encoding="utf-8")
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
                "Essas mudanças são intencionais? Serão enviados map_clear, map_add para cada linha de input/map.txt e depois map_save."
            ),
        )
        if not ok:
            ctx.log("Cancelado — nada enviado.", "YELLOW")
            return

        rows, edit_errors = _parse_map_rows(MAP_INPUT_PATH)
        if edit_errors:
            ctx.log(
                "⚠ O arquivo editado tem linhas que não são "
                f"{MAP_CSV_HEADER} válidos:",
                "RED",
            )
            for e in edit_errors:
                ctx.log(e, "RED")
            ctx.log("Corrija o arquivo e execute map_edit novamente.", "YELLOW")
            return

        ctx.log("📤 map_clear", "CYAN")
        if not await ctx.send_wire(
            format_message([WireCommand.single_request("map_clear", ())])
        ):
            ctx.log("⚠ Falha ao enviar map_clear; interrompendo.", "RED")
            return
        await asyncio.sleep(1.0)

        map_add_rows = [
            WireCommand(
                "map_add", "list_body", False, row.index, _map_row_to_add_arguments(row)
            )
            for row in rows
        ]
        if not await send_homogeneous_list_body_requests_batched(
            ctx,
            map_add_rows,
            max_messages_before_ack=DEFAULT_LIST_BATCH_MESSAGES_BEFORE_ACK,
            ack_timeout=DEFAULT_LIST_BATCH_ACK_TIMEOUT_SECONDS,
        ):
            ctx.log("⚠ Falha ao enviar; interrompendo.", "RED")
            return

        ctx.log(
            f"✅ Enviados map_clear e {len(map_add_rows)} linha(s) map_add.", "GREEN"
        )
        await asyncio.sleep(1.0)
        if not await _send_map_save(ctx):
            return
    finally:
        session.close()
        if _active_map_get_session is session:
            _active_map_get_session = None
        _map_get_wire_recent.clear()
