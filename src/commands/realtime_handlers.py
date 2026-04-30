"""
open_realtime: fullscreen read-only realtime monitor panel.
"""

from __future__ import annotations

import asyncio
import re
from collections.abc import Awaitable
from contextlib import suppress

from prompt_toolkit.application import Application
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.layout import Layout, Window
from prompt_toolkit.layout.controls import FormattedTextControl
from prompt_toolkit.styles import Style

from api.command_handlers import CliHandlerContext, WireCommand, cli_command
from api.protocol_utils import format_message
from api.realtime import REALTIME_VARIABLES, register_realtime_variable

BATTERY_GET_TIMEOUT_SECONDS = 2.0
_VOLTAGE_RE = re.compile(r"(-?\d+(?:\.\d+)?)")


@register_realtime_variable("battery", refresh_seconds=1.0, order=0)
async def get_realtime_battery_from_device(ctx: CliHandlerContext) -> str:
    wire = format_message([WireCommand.single_request("battery_get", ())])
    if not await ctx.send_wire(wire):
        raise RuntimeError("failed to send battery_get")
    resp = await ctx.incoming.wait_for(
        "battery_get",
        timeout=BATTERY_GET_TIMEOUT_SECONDS,
        predicate=lambda c: c.kind == "single",
    )
    payload = " ".join(resp.arguments).strip()
    if not payload:
        payload = "unknown"
    match = _VOLTAGE_RE.search(payload)
    if match is None:
        return payload
    return f"{match.group(1)} V"


async def _resolve_value(ctx: CliHandlerContext, name: str) -> str:
    spec = REALTIME_VARIABLES[name]
    value = spec.getter(ctx)
    if isinstance(value, Awaitable):
        value = await value
    return str(value)


@cli_command("open_realtime")
async def cmd_open_realtime(inv: WireCommand, ctx: CliHandlerContext) -> None:
    _ = inv
    if not REALTIME_VARIABLES:
        ctx.log("⚠ Nenhuma variável realtime registrada.", "YELLOW")
        return

    ordered = sorted(
        REALTIME_VARIABLES.values(), key=lambda s: (s.order, s.name.lower())
    )
    values: dict[str, str] = {spec.name: "loading..." for spec in ordered}
    errors: dict[str, str] = {}
    stop_event = asyncio.Event()

    def render_lines() -> list[tuple[str, str]]:
        rows: list[tuple[str, str]] = [
            ("class:title", " Realtime monitor (read-only) ")
        ]
        rows.append(("class:hint", " Press q or Ctrl-C to close.\n\n"))
        for spec in ordered:
            rows.append(("class:key", f" {spec.name:<18} "))
            rows.append(("class:sep", " : "))
            rows.append(("class:value", f"{values.get(spec.name, '...')}\n"))
            err = errors.get(spec.name)
            if err:
                rows.append(("class:error", f" {'':<18}   ({err})\n"))
        return rows

    control = FormattedTextControl(text=render_lines)
    kb = KeyBindings()

    @kb.add("q")
    @kb.add("c-c")
    @kb.add("escape")
    def _exit_panel(event) -> None:
        stop_event.set()
        event.app.exit()

    app = Application(
        layout=Layout(Window(content=control, wrap_lines=False)),
        key_bindings=kb,
        full_screen=True,
        mouse_support=False,
        style=Style.from_dict(
            {
                "value": "fg:ansicyan",
            }
        ),
    )

    async def worker(variable_name: str) -> None:
        refresh = max(0.1, REALTIME_VARIABLES[variable_name].refresh_seconds)
        while not stop_event.is_set():
            try:
                values[variable_name] = await _resolve_value(ctx, variable_name)
                errors.pop(variable_name, None)
            except Exception as exc:
                # Keep last successful value while monitor is open.
                errors[variable_name] = f"stale: {exc!s}"
            app.invalidate()
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=refresh)
            except asyncio.TimeoutError:
                pass

    workers = [asyncio.create_task(worker(spec.name)) for spec in ordered]
    ctx.log(
        "🖥 Realtime monitor aberto (somente leitura). Pressione q, Esc ou Ctrl-C para fechar.",
        "CYAN",
    )
    try:
        await app.run_async()
    finally:
        stop_event.set()
        for task in workers:
            task.cancel()
        for task in workers:
            with suppress(asyncio.CancelledError):
                await task
        ctx.log("🛑 Realtime monitor fechado.", "CYAN")
