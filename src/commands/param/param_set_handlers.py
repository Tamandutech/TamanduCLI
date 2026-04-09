"""
BLE param_set: send param_set(ref, value) with no response handling.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from commands.command_handlers import NusPort
    from protocol_utils import CommandInvocation


def _terminal():
    import main as main_module

    return main_module.Terminal


async def cmd_param_set(inv: "CommandInvocation", nus: "NusPort") -> None:
    if len(inv.params) < 2:
        _terminal().log(
            '⚠ param_set needs two arguments, e.g. param_set("Class.param","value")',
            "YELLOW",
        )
        return
    await nus.send_message(inv.line)
