"""
param_list: wire list collect and ``cmd_param_list``.

param_edit: fetch list to ``output/``, edit under ``input/``, diff, ``param_set`` per row, verify.
"""

from __future__ import annotations

import asyncio
import difflib
import shutil
from collections import deque
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
from api.list_wire import (
    ListWireCollectionSession,
    ble_message_has_list_wire_response,
    feed_list_wire_collection_from_ble_text,
)
from api.output_paths import INPUT_DIR, OUTPUT_DIR, ensure_input_dir, ensure_output_dir
from api.protocol_utils import (
    WireCommand,
    WireListHeader,
    format_message,
    parse_message,
    unquote_field,
)

PARAM_LIST_WAIT_SECONDS = 3.0
PARAM_LIST_RESPONSE_PATH = OUTPUT_DIR / "param_list.txt"
PARAM_LIST_INPUT_PATH = INPUT_DIR / "param_list.txt"
DEFAULT_PARAM_LIST_WIRE = format_message([WireCommand.single_request("param_list", ())])

_param_list_ble_recent: deque[str] = deque(maxlen=128)
_active_param_list_session: Optional[ListWireCollectionSession] = None


@register_ble_capture
def capture_param_list_res_from_ble(message: str) -> None:
    for line in message.replace("\r\n", "\n").split("\n"):
        s = line.strip()
        if s and ble_message_has_list_wire_response("param_list", s):
            _param_list_ble_recent.append(s)


def parse_param_list_document(
    content: str,
) -> tuple[Optional[int], dict[int, tuple[str, str]], list[str]]:
    """
    Parse ``param_list.txt``: each non-empty line should contain wire ``param_list`` **response**
    commands (``h`` header and/or ``b`` body). Headers use the four-integer form
    ``param_list(h,s,T,C,B,j);`` (see :class:`api.protocol_utils.WireListHeader`); a single
    integer ``param_list(h,s,N);`` is still accepted when parsing old files.
    """
    errors: list[str] = []
    expected: Optional[int] = None
    rows: dict[int, tuple[str, str]] = {}
    if not content.strip():
        return None, {}, ["  (empty document)"]

    for lineno, line in enumerate(content.splitlines(), start=1):
        raw = line.strip()
        if not raw:
            continue
        wire = raw if raw.endswith(";") else raw + ";"
        got_param_list = False
        for cmd in parse_message(wire):
            if cmd.name.lower() != "param_list" or not cmd.is_response:
                continue
            if cmd.kind == "list_header":
                wh = WireListHeader.from_wire_command(cmd)
                if wh is None:
                    errors.append(f"  line {lineno}: param_list header invalid or empty")
                    continue
                expected = wh.total_row_count
                got_param_list = True
            elif cmd.kind == "list_body":
                if not cmd.arguments:
                    errors.append(f"  line {lineno}: param_list body missing arguments")
                    continue
                idx = cmd.index
                name = unquote_field(cmd.arguments[0])
                value = ", ".join(unquote_field(a) for a in cmd.arguments[1:])
                rows[idx] = (name, value)
                got_param_list = True
        if not got_param_list:
            errors.append(
                f"  line {lineno}: expected param_list(h,s,T,C,B,j) or param_list(b,s,…); got {raw[:120]!r}"
            )

    if expected is None and rows:
        positive = {k for k in rows if k > 0}
        if positive:
            mx = max(positive)
            if positive == set(range(1, mx + 1)):
                expected = mx

    return expected, rows, errors


def _log_param_list_collection_summary(
    session: ListWireCollectionSession, log, completed: bool
) -> None:
    status = "completo" if completed else "parcial (tempo esgotado)"
    log(f"📋 Parâmetros do dispositivo ({status}):", "YELLOW")
    if session.expected_row_total is not None:
        log(f"  Esperando {session.expected_row_total} entrada(s)", "CYAN")
    for idx in sorted(session.rows.keys()):
        if idx == 0:
            continue
        name, value = session.rows[idx]
        log(f"  [{idx}] {name} = {value}", "WHITE")


@register_ble_try_feed
def try_feed_param_list_session(message: str) -> bool:
    if _active_param_list_session is None:
        return False
    return feed_list_wire_collection_from_ble_text(_active_param_list_session, message)


async def collect_param_list_to_file(
    ctx: CliHandlerContext, send_wire: str | None = None
) -> bool:
    global _active_param_list_session
    wire = send_wire or DEFAULT_PARAM_LIST_WIRE
    session = ListWireCollectionSession("param_list", record_raw_wire_lines=True)
    _active_param_list_session = session
    try:
        for msg in list(_param_list_ble_recent):
            feed_list_wire_collection_from_ble_text(session, msg)
        if not await ctx.send_wire(wire):
            return False
        completed = await session.wait_until_done(PARAM_LIST_WAIT_SECONDS)
        if not completed:
            ctx.log(
                f"⏱ Coleta de param_list excedeu {PARAM_LIST_WAIT_SECONDS:g}s; "
                "mostrando resultados parciais.",
                "YELLOW",
            )
        ensure_output_dir()
        if session.write_file_if_non_empty(PARAM_LIST_RESPONSE_PATH):
            ctx.log(
                f"💾 Lista de parâmetros salva em {PARAM_LIST_RESPONSE_PATH}",
                "GREEN",
            )
        else:
            ctx.log(
                "⚠ Nenhuma lista de parâmetros coletada; arquivo não gravado.",
                "YELLOW",
            )
        _log_param_list_collection_summary(session, ctx.log, completed)
        return completed
    finally:
        if _active_param_list_session is session:
            _active_param_list_session = None
        _param_list_ble_recent.clear()


@cli_command
async def cmd_param_list(inv: WireCommand, ctx: CliHandlerContext) -> None:
    _ = inv
    await collect_param_list_to_file(ctx)


@cli_command
async def cmd_param_edit(inv: WireCommand, ctx: CliHandlerContext) -> None:
    _ = inv

    await collect_param_list_to_file(ctx)

    if (
        not PARAM_LIST_RESPONSE_PATH.is_file()
        or not PARAM_LIST_RESPONSE_PATH.read_text(encoding="utf-8").strip()
    ):
        ctx.log("⚠ output/param_list.txt ausente ou vazio após param_list.", "YELLOW")
        return

    ensure_output_dir()
    ensure_input_dir()
    shutil.copy2(PARAM_LIST_RESPONSE_PATH, PARAM_LIST_INPUT_PATH)
    rel_out = PARAM_LIST_RESPONSE_PATH.relative_to(OUTPUT_DIR.parent)
    rel_in = PARAM_LIST_INPUT_PATH.relative_to(OUTPUT_DIR.parent)
    ctx.log(
        f"💾 Gravado {rel_out} e copiado para {rel_in} — edite o arquivo em input/ e salve.",
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

    original_text = PARAM_LIST_RESPONSE_PATH.read_text(encoding="utf-8")
    edited_text = PARAM_LIST_INPUT_PATH.read_text(encoding="utf-8")
    orig_lines = original_text.splitlines(keepends=True)
    edit_lines = edited_text.splitlines(keepends=True)
    diff_lines = list(
        difflib.unified_diff(
            orig_lines,
            edit_lines,
            fromfile="output/param_list.txt",
            tofile="input/param_list.txt",
        )
    )
    ctx.log(
        "--- Diferenças (output/param_list.txt → input/param_list.txt) ---", "YELLOW"
    )
    if not diff_lines:
        ctx.log("(sem diferenças)", "WHITE")
        ctx.log("Nada a aplicar.", "YELLOW")
        return
    for line in diff_lines:
        ctx.log(line.rstrip("\n"), "WHITE")

    ok = await loop.run_in_executor(
        None,
        lambda: confirm(
            "Aplicar essas mudanças? Será enviado param_set para cada linha param_list(b,s,…) de input/param_list.txt."
        ),
    )
    if not ok:
        ctx.log("Cancelado — nenhum comando param_set enviado.", "YELLOW")
        return

    expected, edited_rows, edit_errors = parse_param_list_document(
        PARAM_LIST_INPUT_PATH.read_text(encoding="utf-8")
    )
    if edit_errors:
        ctx.log(
            "⚠ O arquivo editado tem linhas inválidas (cada linha: param_list(h,s,T,C,B,j); ou param_list(b,s,i,...);):",
            "RED",
        )
        for e in edit_errors:
            ctx.log(e, "RED")
        ctx.log("Corrija o arquivo e execute param_edit novamente.", "YELLOW")
        return
    if expected is None:
        ctx.log(
            "⚠ O arquivo editado deve incluir o cabeçalho da lista param_list (param_list(h,s,T,C,B,j);) "
            "ou linhas completas param_list(b,s,…) para os índices 1..N.",
            "RED",
        )
        return
    missing = [i for i in range(1, expected + 1) if i not in edited_rows]
    if missing:
        ctx.log(
            f"⚠ Falta(m) índice(s) de parâmetro (esperados 1..{expected}): {missing!r}",
            "RED",
        )
        return
    extra = [i for i in edited_rows if i < 1 or i > expected]
    if extra:
        ctx.log(f"⚠ Índice fora do intervalo 1..{expected}: {extra!r}", "RED")
        return

    to_apply = [
        (i, edited_rows[i][0], edited_rows[i][1]) for i in range(1, expected + 1)
    ]
    if not to_apply:
        ctx.log("Nenhuma linha de parâmetro para enviar.", "YELLOW")
        return

    param_set_rows = [
        WireCommand("param_set", "list_body", False, idx, (name, value))
        for idx, name, value in to_apply
    ]
    if not await send_homogeneous_list_body_requests_batched(
        ctx,
        param_set_rows,
        max_messages_before_ack=DEFAULT_LIST_BATCH_MESSAGES_BEFORE_ACK,
        ack_timeout=DEFAULT_LIST_BATCH_ACK_TIMEOUT_SECONDS,
    ):
        ctx.log("⚠ Falha ao enviar; interrompendo.", "RED")
        return

    ctx.log(
        f"✅ Enviado(s) {len(to_apply)} comando(s) param_set. Solicitando param_list para verificar…",
        "GREEN",
    )

    await collect_param_list_to_file(ctx)
    if PARAM_LIST_RESPONSE_PATH.is_file():
        ctx.log("✅ output/param_list.txt atualizado a partir do dispositivo.", "GREEN")
    else:
        ctx.log(
            "⚠ param_list após aplicar não produziu output/param_list.txt.", "YELLOW"
        )
