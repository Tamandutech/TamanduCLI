"""
Command registry and dispatch.

Extending:
  1. Create a module (e.g. my_commands.py under ``commands/`` or elsewhere on ``sys.path``).
  2. Implement async def my_cmd(inv: CommandInvocation, nus: NusPort) -> None.
  3. Call register_cli_command("my_cmd", my_cmd) at import time.
  4. Add ``import my_commands`` at the bottom of this file (or your app entry).

Incoming BLE lines use the same command_name(...) shape; register with register_incoming_command.
"""

from __future__ import annotations

from typing import Awaitable, Callable, Protocol, runtime_checkable

from commands.help_handlers import (
    capture_help_res_from_ble,
    cmd_help,
    try_feed_help_session,
)
from commands.param.param_list_handlers import (
    capture_param_list_res_from_ble,
    cmd_param_list,
    try_feed_param_list_session,
)
from protocol_utils import (
    CommandInvocation,
    digest_invocation_parameters,
    parse_command_invocation,
    parse_command_line,
    split_top_level_commas,
    unquote_field,
)

__all__ = [
    "CLI_COMMAND_HANDLERS",
    "INCOMING_MESSAGE_HANDLERS",
    "CommandInvocation",
    "NusPort",
    "capture_help_res_from_ble",
    "capture_param_list_res_from_ble",
    "digest_invocation_parameters",
    "dispatch_cli_command",
    "handle_incoming_message",
    "is_command_invocation",
    "is_registered_command",
    "parse_command_invocation",
    "parse_command_line",
    "register_cli_command",
    "register_incoming_command",
    "split_top_level_commas",
    "try_feed_help_session",
    "try_feed_param_list_session",
    "unquote_field",
]


@runtime_checkable
class NusPort(Protocol):
    """BLE serial bridge to the robot (or device): anything with ``send_message`` works for handlers."""

    async def send_message(self, message: str) -> bool: ...


IncomingMessageHandler = Callable[[CommandInvocation], None]
CLICommandHandler = Callable[[CommandInvocation, NusPort], Awaitable[None]]


def _terminal():
    import main as main_module

    return main_module.Terminal


def is_command_invocation(message: str) -> bool:
    return parse_command_invocation(message) is not None


async def cmd_echo(inv: CommandInvocation, nus: NusPort) -> None:
    _ = nus
    _terminal().log(f"🔊 Echo: {inv.raw_arguments}", "CYAN")


async def cmd_ping(inv: CommandInvocation, nus: NusPort) -> None:
    _ = inv
    _ = nus
    _terminal().log("🏓 pong", "GREEN")


def cmd_default(inv: CommandInvocation) -> None:
    _ = inv


CLI_COMMAND_HANDLERS: dict[str, CLICommandHandler] = {
    "help": cmd_help,
    "param_list": cmd_param_list,
    "echo": cmd_echo,
    "ping": cmd_ping,
}


def register_cli_command(name: str, handler: CLICommandHandler) -> None:
    """Register or replace a CLI (async) handler. Name is stored lowercased."""
    CLI_COMMAND_HANDLERS[name.lower()] = handler


def _incoming_echo(inv: CommandInvocation) -> None:
    _terminal().log(f"🔊 Echo: {inv.raw_arguments}", "CYAN")


def _incoming_ping(inv: CommandInvocation) -> None:
    _ = inv
    _terminal().log("🏓 pong", "GREEN")


INCOMING_MESSAGE_HANDLERS: dict[str, IncomingMessageHandler] = {
    "echo": _incoming_echo,
    "ping": _incoming_ping,
}


def register_incoming_command(name: str, handler: IncomingMessageHandler) -> None:
    """Register or replace a sync handler for incoming BLE lines (same command shape)."""
    INCOMING_MESSAGE_HANDLERS[name.lower()] = handler


def is_registered_command(message: str) -> bool:
    inv = parse_command_line(message)
    return inv is not None and inv.name in CLI_COMMAND_HANDLERS


async def dispatch_cli_command(message: str, nus: NusPort) -> bool:
    inv = parse_command_line(message)
    if inv is None:
        return False
    handler = CLI_COMMAND_HANDLERS.get(inv.name)
    if handler is None:
        return False
    await handler(inv, nus)
    return True


def handle_incoming_message(message: str) -> None:
    inv = parse_command_line(message)
    if inv is None:
        return
    handler = INCOMING_MESSAGE_HANDLERS.get(inv.name, cmd_default)
    handler(inv)


# --- Optional local plugins (add your own: ``import my_robot_commands``) ---
