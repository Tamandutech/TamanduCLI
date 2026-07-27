"""
pause / resume: send ``name(s,r)`` and wait for ``name(s,s,ok)``.
"""

from __future__ import annotations

from api.command_handlers import CliHandlerContext, WireCommand, cli_command
from api.protocol_utils import format_message, unquote_field

PAUSE_RESUME_TIMEOUT_SECONDS = 5.0


def _is_single_ok_response(cmd: WireCommand) -> bool:
    return (
        cmd.kind == "single"
        and cmd.is_response
        and len(cmd.arguments) >= 1
        and unquote_field(cmd.arguments[0]).strip().lower() == "ok"
    )


async def _send_single_ok_command(name: str, ctx: CliHandlerContext) -> None:
    wire = format_message([WireCommand.single_request(name, ())])
    ctx.log(f"📤 {name}", "CYAN")
    if not await ctx.send_wire(wire):
        ctx.log(f"⚠ Falha ao enviar {name}.", "RED")
        return
    try:
        await ctx.incoming.wait_for(
            name,
            timeout=PAUSE_RESUME_TIMEOUT_SECONDS,
            predicate=_is_single_ok_response,
        )
    except TimeoutError:
        ctx.log(
            f"⏱ Resposta {name}(s,s,ok) ausente após {PAUSE_RESUME_TIMEOUT_SECONDS:g}s.",
            "RED",
        )
        return
    ctx.log(f"✅ {name}(s,s,ok) — comando executado.", "GREEN")


@cli_command
async def cmd_pause(inv: WireCommand, ctx: CliHandlerContext) -> None:
    _ = inv
    await _send_single_ok_command("pause", ctx)


@cli_command
async def cmd_resume(inv: WireCommand, ctx: CliHandlerContext) -> None:
    _ = inv
    await _send_single_ok_command("resume", ctx)
