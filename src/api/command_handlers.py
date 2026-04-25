"""
Command registry, BLE ingestion, and CLI dispatch.

Handlers live under ``commands/`` and register via :func:`cli_command` / :func:`incoming_command`.
"""

from __future__ import annotations

import importlib
from dataclasses import dataclass
from pathlib import Path
from typing import Awaitable, Callable, Protocol, TypeVar, cast, runtime_checkable

from api.incoming import IncomingRouter
from api.protocol_utils import (
    WireCommand,
    normalize_cli_input,
    parse_first_command_name,
    parse_message,
    parse_wire_command_segment,
)
from api.protocol_utils import digest_invocation_parameters as digest_invocation_parameters
from api.protocol_utils import is_command_invocation as is_command_invocation_line

__all__ = [
    "BLE_CAPTURE_HOOKS",
    "BLE_TRY_FEED_HOOKS",
    "CLI_COMMAND_HANDLERS",
    "INCOMING_MESSAGE_HANDLERS",
    "CliHandlerContext",
    "NusPort",
    "WireCommand",
    "cli_command",
    "digest_invocation_parameters",
    "dispatch_ble_notification",
    "dispatch_cli_command",
    "ingest_incoming_ble_text",
    "incoming_command",
    "incoming_handler",
    "is_command_invocation",
    "is_registered_command",
    "normalize_cli_input",
    "parse_command_line",
    "parse_command_message",
    "parse_message",
    "register_ble_capture",
    "register_ble_try_feed",
    "register_cli_command",
    "register_incoming_command",
    "split_top_level_commas",
    "unquote_field",
]

from api.protocol_utils import parse_command_line as parse_command_line
from api.protocol_utils import parse_command_message as parse_command_message
from api.protocol_utils import split_top_level_commas as split_top_level_commas
from api.protocol_utils import unquote_field as unquote_field


@runtime_checkable
class NusPort(Protocol):
    async def send_message(self, message: str) -> bool: ...


PromptLineFn = Callable[[str], Awaitable[str]]
LogFn = Callable[[str, str], None]


@dataclass
class CliHandlerContext:
    """Passed to every ``@cli_command`` handler (transport + UI + incoming buffer)."""

    nus: NusPort
    incoming: IncomingRouter
    prompt_line: PromptLineFn
    log: LogFn

    async def send_wire(self, message: str) -> bool:
        text = message.strip()
        if not text.endswith(";"):
            text = text + ";"
        return await self.nus.send_message(text)


IncomingMessageHandler = Callable[[WireCommand], None]
CLICommandHandler = Callable[[WireCommand, CliHandlerContext], Awaitable[None]]

CLI_COMMAND_HANDLERS: dict[str, CLICommandHandler] = {}
INCOMING_MESSAGE_HANDLERS: dict[str, IncomingMessageHandler] = {}

BleCaptureFn = Callable[[str], None]
BleTryFeedFn = Callable[[str], bool]
BLE_CAPTURE_HOOKS: list[BleCaptureFn] = []
BLE_TRY_FEED_HOOKS: list[BleTryFeedFn] = []

FCap = TypeVar("FCap", bound=BleCaptureFn)
FFeed = TypeVar("FFeed", bound=BleTryFeedFn)


def register_ble_capture(
    name_or_fn: BleCaptureFn | None = None,
) -> BleCaptureFn | Callable[[FCap], FCap]:
    """
    Register a BLE line buffer (runs first on every notification, before parsing).

    Use as ``@register_ble_capture`` or ``@register_ble_capture()`` on a ``def f(message: str) -> None``.
    """

    if callable(name_or_fn):
        fn = cast(BleCaptureFn, name_or_fn)
        BLE_CAPTURE_HOOKS.append(fn)
        return fn

    def decorator(fn: FCap) -> FCap:
        BLE_CAPTURE_HOOKS.append(fn)
        return fn

    return decorator


def register_ble_try_feed(
    name_or_fn: BleTryFeedFn | None = None,
) -> BleTryFeedFn | Callable[[FFeed], FFeed]:
    """
    Register a session feeder (runs after captures; if it returns True, ingestion stops there).

    Use as ``@register_ble_try_feed`` on a ``def f(message: str) -> bool``.
    """

    if callable(name_or_fn):
        fn = cast(BleTryFeedFn, name_or_fn)
        BLE_TRY_FEED_HOOKS.append(fn)
        return fn

    def decorator(fn: FFeed) -> FFeed:
        BLE_TRY_FEED_HOOKS.append(fn)
        return fn

    return decorator


def register_cli_command(name: str, handler: CLICommandHandler) -> None:
    CLI_COMMAND_HANDLERS[name.lower()] = handler


def register_incoming_command(name: str, handler: IncomingMessageHandler) -> None:
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


incoming_handler = incoming_command


def _load_command_handler_modules() -> None:
    import commands as commands_pkg

    root = Path(commands_pkg.__file__).resolve().parent
    paths = sorted(root.rglob("*_handlers.py"))
    for path in paths:
        rel = path.relative_to(root).with_suffix("")
        mod_name = "commands." + ".".join(rel.parts)
        importlib.import_module(mod_name)


_load_command_handler_modules()


def _registered_names() -> set[str]:
    return set(CLI_COMMAND_HANDLERS.keys())


def is_command_invocation(message: str) -> bool:
    return is_command_invocation_line(message, _registered_names())


def is_registered_command(message: str) -> bool:
    first = parse_first_command_name(message, _registered_names())
    return first is not None and first in CLI_COMMAND_HANDLERS


async def dispatch_cli_command(message: str, ctx: CliHandlerContext) -> bool:
    names = _registered_names()
    normalized = normalize_cli_input(message, names)
    cmds = parse_message(normalized)
    if not cmds:
        one = parse_wire_command_segment(normalized.strip())
        cmds = [one] if one is not None else []
    if not cmds:
        return False
    if any(c.name.lower() not in CLI_COMMAND_HANDLERS for c in cmds):
        return False
    for cmd in cmds:
        await CLI_COMMAND_HANDLERS[cmd.name.lower()](cmd, ctx)
    return True


def cmd_default(inv: WireCommand) -> None:
    _ = inv


def dispatch_ble_notification(message: str, router: IncomingRouter) -> None:
    """
    Run registered BLE capture hooks, record parsed commands for :class:`IncomingRouter`, then
    registered try-feed hooks (first ``True`` stops further handling), then ``@incoming_command``.
    """
    for hook in BLE_CAPTURE_HOOKS:
        hook(message)
    cmds = parse_message(message)
    if not cmds and message.strip():
        seg = parse_wire_command_segment(message.strip())
        if seg is not None:
            cmds = [seg]
    router.record_many(cmds)
    for hook in BLE_TRY_FEED_HOOKS:
        if hook(message):
            return
    for cmd in cmds:
        INCOMING_MESSAGE_HANDLERS.get(cmd.name.lower(), cmd_default)(cmd)


def ingest_incoming_ble_text(message: str, router: IncomingRouter | None) -> None:
    if router is not None:
        dispatch_ble_notification(message, router)


def handle_incoming_message(message: str, router: IncomingRouter | None = None) -> None:
    if router is not None:
        dispatch_ble_notification(message, router)


# --- Sample echo/ping handlers (after registry load; not part of src/commands/ plugins) ---


@cli_command
async def cmd_echo(inv: WireCommand, ctx: CliHandlerContext) -> None:
    ctx.log(f"🔊 Echo: {inv.arguments}", "CYAN")


@cli_command
async def cmd_ping(inv: WireCommand, ctx: CliHandlerContext) -> None:
    _ = inv
    ctx.log("🏓 pong", "GREEN")


@incoming_command
def _incoming_echo(inv: WireCommand) -> None:
    _log_builtin(f"🔊 Echo: {inv.arguments}", "CYAN")


@incoming_command
def _incoming_ping(inv: WireCommand) -> None:
    _ = inv
    _log_builtin("🏓 pong", "GREEN")


def _log_builtin(message: str, color: str = "WHITE") -> None:
    try:
        import main as main_module

        main_module.Terminal.log(message, color)
    except Exception:
        print(message)


__all__.extend(
    [
        "handle_incoming_message",
        "ingest_incoming_ble_text",
    ]
)
