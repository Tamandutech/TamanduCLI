"""
Command registry and dispatch.

Extending (built-in tree):
  1. Add ``src/commands/**/your_handlers.py`` (name must end with ``_handlers.py``).
  2. Implement ``async def cmd_yourthing(...)`` and decorate with ``@cli_command`` (or
     ``@cli_command("explicit_name")``). Those modules are imported automatically—no new line in
     this file unless you need re-exports for ``main.py``.

Extending (external plugin):
  1. Implement handlers with ``@cli_command`` / ``@incoming_command`` or call ``register_*``.
  2. At the bottom of this file: ``import my_robot_commands  # noqa: F401``.

Incoming BLE lines use the same command_name(...) shape; register with ``incoming_command`` or
``register_incoming_command``.
"""

from __future__ import annotations

import importlib
from pathlib import Path
from typing import Awaitable, Callable, Protocol, TypeVar, cast, runtime_checkable

from protocol_utils import (
    CommandInvocation,
    digest_invocation_parameters,
    parse_command_invocation,
    parse_command_line,
    parse_command_message,
    split_top_level_commas,
    unquote_field,
)

__all__ = [
    "CLI_COMMAND_HANDLERS",
    "INCOMING_MESSAGE_HANDLERS",
    "CommandInvocation",
    "NusPort",
    "capture_help_res_from_ble",
    "capture_map_get_res_from_ble",
    "capture_param_list_res_from_ble",
    "cli_command",
    "digest_invocation_parameters",
    "dispatch_cli_command",
    "handle_incoming_message",
    "incoming_command",
    "is_command_invocation",
    "is_registered_command",
    "parse_command_invocation",
    "parse_command_line",
    "parse_command_message",
    "register_cli_command",
    "register_incoming_command",
    "split_top_level_commas",
    "try_feed_help_session",
    "try_feed_map_get_session",
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


def _infer_cli_command_name(fn: CLICommandHandler) -> str:
    n = fn.__name__.lower()
    if n.startswith("cmd_"):
        return n[4:]
    return n


def _infer_incoming_command_name(fn: IncomingMessageHandler) -> str:
    n = fn.__name__.lower()
    if n.startswith("_incoming_"):
        return n[len("_incoming_") :]
    return n


FCli = TypeVar("FCli", bound=CLICommandHandler)
FInc = TypeVar("FInc", bound=IncomingMessageHandler)


def cli_command(
    name_or_fn: str | CLICommandHandler | None = None,
) -> CLICommandHandler | Callable[[FCli], FCli]:
    """
    Register a CLI handler at import time.

    - ``@cli_command`` — name from function (``cmd_foo`` → ``foo``).
    - ``@cli_command()`` — same.
    - ``@cli_command("set_speed")`` — explicit registry name.
    """

    if callable(name_or_fn):
        fn = cast(CLICommandHandler, name_or_fn)
        register_cli_command(_infer_cli_command_name(fn), fn)
        return fn
    explicit = name_or_fn

    def decorator(fn: FCli) -> FCli:
        cmd_name = explicit.lower() if isinstance(explicit, str) else _infer_cli_command_name(fn)
        register_cli_command(cmd_name, fn)
        return fn

    return decorator


def incoming_command(
    name_or_fn: str | IncomingMessageHandler | None = None,
) -> IncomingMessageHandler | Callable[[FInc], FInc]:
    """
    Register a device → host handler at import time.

    - ``@incoming_command`` — name from ``_incoming_echo`` → ``echo``.
    - ``@incoming_command("status")`` — explicit name.
    """

    if callable(name_or_fn):
        fn = cast(IncomingMessageHandler, name_or_fn)
        register_incoming_command(_infer_incoming_command_name(fn), fn)
        return fn
    explicit = name_or_fn

    def decorator(fn: FInc) -> FInc:
        inc_name = (
            explicit.lower() if isinstance(explicit, str) else _infer_incoming_command_name(fn)
        )
        register_incoming_command(inc_name, fn)
        return fn

    return decorator


def _load_command_handler_modules() -> None:
    """Import every ``commands/**/<name>_handlers.py`` so decorators run (skip ``command_handlers``)."""
    import commands as commands_pkg

    root = Path(commands_pkg.__file__).resolve().parent
    paths = sorted(root.rglob("*_handlers.py"))
    for path in paths:
        if path.name == "command_handlers.py":
            continue
        rel = path.relative_to(root).with_suffix("")
        mod_name = "commands." + ".".join(rel.parts)
        importlib.import_module(mod_name)


_load_command_handler_modules()


# --- Re-exports for main.py (BLE capture / session feeding; after auto-load) ---

from commands.help_handlers import (  # noqa: E402
    capture_help_res_from_ble,
    try_feed_help_session,
)
from commands.map_edit_handlers import (  # noqa: E402
    capture_map_get_res_from_ble,
    try_feed_map_get_session,
)
from commands.param_list_handlers import (  # noqa: E402
    capture_param_list_res_from_ble,
    try_feed_param_list_session,
)


def _terminal():
    import main as main_module

    return main_module.Terminal


def is_command_invocation(message: str) -> bool:
    return parse_command_invocation(message) is not None


@cli_command
async def cmd_echo(inv: CommandInvocation, nus: NusPort) -> None:
    _ = nus
    _terminal().log(f"🔊 Echo: {inv.raw_arguments}", "CYAN")


@cli_command
async def cmd_ping(inv: CommandInvocation, nus: NusPort) -> None:
    _ = inv
    _ = nus
    _terminal().log("🏓 pong", "GREEN")


def cmd_default(inv: CommandInvocation) -> None:
    _ = inv


@incoming_command
def _incoming_echo(inv: CommandInvocation) -> None:
    _terminal().log(f"🔊 Echo: {inv.raw_arguments}", "CYAN")


@incoming_command
def _incoming_ping(inv: CommandInvocation) -> None:
    _ = inv
    _terminal().log("🏓 pong", "GREEN")


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


# --- External plugins: import after registry is ready ---
#   import my_robot_commands  # noqa: F401
