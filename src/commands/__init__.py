"""
User/plugin command modules (``*_handlers.py``).

They register handlers via :func:`api.command_handlers.cli_command`,
:func:`api.command_handlers.incoming_command`, and optional BLE hooks
:func:`api.command_handlers.register_ble_capture` /
:func:`api.command_handlers.register_ble_try_feed`. They use the ``api`` package for BLE,
wire parsing, paths, and shared BLE JSON helpers.
"""
