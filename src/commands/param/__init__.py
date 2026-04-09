"""
Parameter-related CLI/BLE command handlers.

ADD NEW PARAM COMMAND: add ``*_handlers.py`` next to ``param_list_handlers.py`` / ``param_get_handlers.py`` /
``param_set_handlers.py``, implement ``@cli_command`` on ``cmd_*`` and any ``*_res`` parsing/session helpers.
Those modules are auto-imported; only add BLE re-exports and ``main.py`` wiring when the device streams a
matching ``*_res`` protocol.
"""
