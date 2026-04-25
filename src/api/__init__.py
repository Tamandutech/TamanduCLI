"""TamanduCLI internal API (BLE, wire protocol, registry, I/O helpers)."""

from api.ble_nus import NordicUARTService, scan_devices
from api.command_handlers import (
    BLE_CAPTURE_HOOKS,
    BLE_TRY_FEED_HOOKS,
    CLI_COMMAND_HANDLERS,
    INCOMING_MESSAGE_HANDLERS,
    CliHandlerContext,
    NusPort,
    WireCommand,
    cli_command,
    dispatch_ble_notification,
    dispatch_cli_command,
    incoming_command,
    incoming_handler,
    register_ble_capture,
    register_ble_try_feed,
    register_cli_command,
    register_incoming_command,
)
from api.incoming import IncomingRouter
from api.protocol_utils import (
    batch_wire_messages,
    format_message,
    format_wire_command,
    normalize_cli_input,
    parse_message,
    parse_wire_command_segment,
)

__all__ = [
    "BLE_CAPTURE_HOOKS",
    "BLE_TRY_FEED_HOOKS",
    "CLI_COMMAND_HANDLERS",
    "INCOMING_MESSAGE_HANDLERS",
    "CliHandlerContext",
    "IncomingRouter",
    "NordicUARTService",
    "NusPort",
    "WireCommand",
    "batch_wire_messages",
    "cli_command",
    "dispatch_ble_notification",
    "register_ble_capture",
    "register_ble_try_feed",
    "dispatch_cli_command",
    "format_message",
    "format_wire_command",
    "incoming_command",
    "incoming_handler",
    "normalize_cli_input",
    "parse_message",
    "parse_wire_command_segment",
    "register_cli_command",
    "register_incoming_command",
    "scan_devices",
]
