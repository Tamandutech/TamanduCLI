"""
Command registry and dispatch.

Extending:
  1. Create a module (e.g. my_commands.py under ``commands/`` or elsewhere on ``sys.path``).
  2. Implement async def my_cmd(inv: CommandInvocation, nus: NusPort) -> None.
  3. Call register_cli_command("my_cmd", my_cmd) at import time.
  4. Add ``import my_commands`` at the bottom of this file (or your app entry).

Built-in commands use the same ``register_cli_command`` / ``register_incoming_command`` API as plugins.

Incoming BLE lines use the same command_name(...) shape; register with register_incoming_command.
"""

from __future__ import annotations

from typing import Awaitable, Callable, Protocol, runtime_checkable

# --- ADD NEW COMMAND: imports ---
# Import your async handler (and any BLE helpers) from your module, e.g.:
#   from commands.my_handlers import cmd_mything, capture_mything_res_from_ble, try_feed_mything_session
# Parameter-related commands often live under ``commands.param/``.
# If the device streams a custom *_res(...) protocol, import capture/try_feed helpers here and
# register their use in ``main.py`` inside ``ble_dispatch_line`` (same pattern as help / param_list).

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

# --- ADD NEW COMMAND: public re-exports (optional) ---
# If ``main.py`` or other code must import BLE helpers from this package, add names here and
# in the ``__all__`` list below.

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

CLI_COMMAND_HANDLERS: dict[str, CLICommandHandler] = {}
INCOMING_MESSAGE_HANDLERS: dict[str, IncomingMessageHandler] = {}


def register_cli_command(name: str, handler: CLICommandHandler) -> None:
    """Register or replace a CLI (async) handler. Name is stored lowercased."""
    CLI_COMMAND_HANDLERS[name.lower()] = handler


def register_incoming_command(name: str, handler: IncomingMessageHandler) -> None:
    """Register or replace a sync handler for incoming BLE lines (same command shape)."""
    INCOMING_MESSAGE_HANDLERS[name.lower()] = handler


def _terminal():
    import main as main_module

    return main_module.Terminal


def is_command_invocation(message: str) -> bool:
    return parse_command_invocation(message) is not None


# --- ADD NEW COMMAND: inline CLI handlers (optional) ---
# For small commands you can define ``async def cmd_*`` here; larger ones belong in their own module.

async def cmd_echo(inv: CommandInvocation, nus: NusPort) -> None:
    _ = nus
    _terminal().log(f"🔊 Echo: {inv.raw_arguments}", "CYAN")


async def cmd_ping(inv: CommandInvocation, nus: NusPort) -> None:
    _ = inv
    _ = nus
    _terminal().log("🏓 pong", "GREEN")


def cmd_default(inv: CommandInvocation) -> None:
    _ = inv


# --- ADD NEW COMMAND: incoming BLE handlers (optional) ---
# Sync handlers for lines the device sends as command_name(...). Register each with
# ``register_incoming_command`` in the block below.

def _incoming_echo(inv: CommandInvocation) -> None:
    _terminal().log(f"🔊 Echo: {inv.raw_arguments}", "CYAN")


def _incoming_ping(inv: CommandInvocation) -> None:
    _ = inv
    _terminal().log("🏓 pong", "GREEN")


# --- ADD NEW COMMAND: register CLI handlers ---
# One line per command: ``register_cli_command("name", cmd_name)`` — must match the first token users type.

register_cli_command("help", cmd_help)
register_cli_command("param_list", cmd_param_list)
register_cli_command("echo", cmd_echo)
register_cli_command("ping", cmd_ping)

# --- ADD NEW COMMAND: register incoming BLE handlers ---
# For device → host lines you want to handle in Python (same ``command_name(...)`` syntax).

register_incoming_command("echo", _incoming_echo)
register_incoming_command("ping", _incoming_ping)


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


# --- ADD NEW COMMAND: load external plugin modules (optional) ---
# After your handler file calls ``register_cli_command`` at import time, import it here so it runs:
#   import my_robot_commands  # noqa: F401
