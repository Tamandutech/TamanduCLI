"""
map_filter: filter ``input/map.txt``, write ``input/filtered_map.txt``, show it, and send after confirmation.

Invocation: ``map_filter``. A selectable list of :data:`MAP_FILTERS` opens immediately.

Add a filter by defining ``def nome(rows: list[MapRow]) -> list[MapRow]`` and appending
that function to :data:`MAP_FILTERS`. The command reindexes kept rows from 1 so the file
and the ``map_add`` slots stay contiguous. Float wire text is preserved when the numeric
value is unchanged; if a filter edits a float, update the matching ``*_wire`` field or the
command rewrites it with three decimal places.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Optional

from prompt_toolkit.shortcuts import confirm
from prompt_toolkit.shortcuts.choice_input import ChoiceInput

from api.command_handlers import CliHandlerContext, WireCommand, cli_command
from api.output_paths import OUTPUT_DIR, ensure_input_dir
from commands.map_edit_handlers import (
    MAP_CSV_HEADER,
    MAP_INPUT_PATH,
    MapRow,
    _parse_float,
    _parse_map_rows,
    apply_map_rows,
)

FILTERED_MAP_PATH = MAP_INPUT_PATH.with_name("filtered_map.txt")

MapFilter = Callable[[list[MapRow]], list[MapRow]]


def keep_all_points(rows: list[MapRow]) -> list[MapRow]:
    """Return every row. Copy this function, rename it, and append it to MAP_FILTERS."""
    return list(rows)


# Register filters here. ``map_filter`` opens this list for selection.
MAP_FILTERS: list[MapFilter] = [
    keep_all_points,
]


def _duplicate_filter_names(filters: list[MapFilter]) -> list[str]:
    seen: set[str] = set()
    duplicates: list[str] = []
    for fn in filters:
        name = fn.__name__
        if name in seen and name not in duplicates:
            duplicates.append(name)
        seen.add(name)
    return duplicates


async def _select_filter(filters: list[MapFilter]) -> Optional[MapFilter]:
    try:
        return await ChoiceInput(
            message="Selecione um filtro:",
            options=[(fn, fn.__name__) for fn in filters],
            default=filters[0],
        ).prompt_async()
    except (KeyboardInterrupt, EOFError):
        return None


def _float_wire(value: float, original: str) -> str:
    parsed = _parse_float(original)
    if parsed is not None and parsed == value:
        return original
    return f"{value:.3f}"


def _reindex_rows(rows: list[MapRow]) -> list[MapRow]:
    numbered: list[MapRow] = []
    for index, row in enumerate(rows, start=1):
        numbered.append(
            row._replace(
                index=index,
                encoder_derivative_wire=_float_wire(
                    row.encoder_derivative, row.encoder_derivative_wire
                ),
                encoder_derivative_average_wire=_float_wire(
                    row.encoder_derivative_average,
                    row.encoder_derivative_average_wire,
                ),
                speed_wire=_float_wire(row.speed, row.speed_wire),
            )
        )
    return numbered


def _format_map_csv(rows: list[MapRow]) -> str:
    lines = [MAP_CSV_HEADER]
    for row in rows:
        lines.append(
            ",".join(
                (
                    str(row.index),
                    str(row.encoder_left),
                    str(row.encoder_right),
                    row.encoder_derivative_wire,
                    row.encoder_derivative_average_wire,
                    row.speed_wire,
                    str(row.point_type),
                )
            )
        )
    return "\n".join(lines) + "\n"


def _checked_filter_rows(fn: MapFilter, rows: list[MapRow]) -> Optional[list[MapRow]]:
    result = fn(list(rows))
    if isinstance(result, tuple):
        result = list(result)
    if not isinstance(result, list) or any(not isinstance(row, MapRow) for row in result):
        return None
    return result


@cli_command
async def cmd_map_filter(inv: WireCommand, ctx: CliHandlerContext) -> None:
    _ = inv
    duplicates = _duplicate_filter_names(MAP_FILTERS)
    if duplicates:
        ctx.log(
            "⚠ MAP_FILTERS tem nomes repetidos: " + ", ".join(duplicates) + ".",
            "RED",
        )
        return
    if not MAP_FILTERS:
        ctx.log(
            "Nenhum filtro em MAP_FILTERS. Defina uma função "
            "(rows: list[MapRow]) -> list[MapRow] em src/commands/map_filter_handlers.py "
            "e acrescente-a à lista.",
            "YELLOW",
        )
        return

    fn = await _select_filter(MAP_FILTERS)
    if fn is None:
        ctx.log("Cancelado — nenhum filtro selecionado.", "YELLOW")
        return
    filter_name = fn.__name__

    if not MAP_INPUT_PATH.is_file():
        ctx.log(f"⚠ Arquivo não encontrado: {MAP_INPUT_PATH}", "RED")
        return

    rows, errors = _parse_map_rows(MAP_INPUT_PATH)
    if errors:
        ctx.log(
            "⚠ input/map.txt tem linhas que não são "
            f"{MAP_CSV_HEADER} válidos:",
            "RED",
        )
        for error in errors:
            ctx.log(error, "RED")
        return

    try:
        filtered = _checked_filter_rows(fn, rows)
    except Exception as exc:
        ctx.log(f"⚠ Filtro {filter_name} falhou: {exc}", "RED")
        return
    if filtered is None:
        ctx.log(
            f"⚠ Filtro {filter_name} precisa devolver uma lista de MapRow.",
            "RED",
        )
        return

    numbered = _reindex_rows(filtered)
    ensure_input_dir()
    FILTERED_MAP_PATH.write_text(_format_map_csv(numbered), encoding="utf-8")
    rel = FILTERED_MAP_PATH.relative_to(OUTPUT_DIR.parent)
    ctx.log(
        f"💾 {rel}: {len(numbered)} ponto(s) de {len(rows)} "
        f"com o filtro {filter_name}.",
        "GREEN",
    )
    ctx.log(f"--- {rel} ---", "YELLOW")
    for line in FILTERED_MAP_PATH.read_text(encoding="utf-8").splitlines():
        ctx.log(line, "WHITE")
    if not numbered:
        ctx.log(
            "⚠ Filtro removeu todos os pontos. Arquivo gravado; nada enviado ao robô.",
            "YELLOW",
        )
        return

    loop = asyncio.get_running_loop()
    ok = await loop.run_in_executor(
        None,
        lambda: confirm(
            "Enviar este mapa ao robô? Serão enviados map_clear, map_add para cada linha de input/filtered_map.txt e depois map_save."
        ),
    )
    if not ok:
        ctx.log("Cancelado — nada enviado.", "YELLOW")
        return
    await apply_map_rows(ctx, numbered)
