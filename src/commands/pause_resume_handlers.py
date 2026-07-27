"""
pause / resume: send ``name(s,r)`` and wait for ``name(s,s,ok)``.
"""

from __future__ import annotations

from api.command_handlers import CliHandlerContext, WireCommand, cli_command
from api.protocol_utils import format_message


async def _send_single_ok_command(name: str, ctx: CliHandlerContext) -> None:
    wire = format_message([WireCommand.single_request(name, ())])
    ctx.log(f"📤 {name}", "CYAN")
    if not await ctx.send_wire(wire):
        ctx.log(f"⚠ Falha ao enviar {name}.", "RED")
        return


@cli_command
async def cmd_pause(inv: WireCommand, ctx: CliHandlerContext) -> None:
    _ = inv
    await _send_single_ok_command("pause", ctx)


@cli_command
async def cmd_resume(inv: WireCommand, ctx: CliHandlerContext) -> None:
    _ = inv
    await _send_single_ok_command("resume", ctx)
