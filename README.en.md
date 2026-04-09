# TamanduCLI

Command-line interface (CLI) and supporting tools for developing, testing, debugging, and visualizing data from **robots** and other embedded systems you connect over Bluetooth.

> You can pair this CLI with any companion app you use to operate the same hardware (web dashboard, desktop UI, etc.); wire that up in your own project docs.

## Getting Started

### Prerequisites

1. [Install astral-sh/uv](https://github.com/astral-sh/uv?tab=readme-ov-file#installation):

```bash
# On macOS and Linux.
$ curl -LsSf https://astral.sh/uv/install.sh | sh

# On Windows.
$ powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"

# With pip.
$ pip install uv
```

2. [Install ruff](https://github.com/astral-sh/ruff):

```bash
$ uv tool install ruff
```

### Run Python Scripts

#### Robot BLE console (Nordic UART Service)
The main app is a text console for talking to a robot (or any device) that exposes the standard **Nordic UART Service** over Bluetooth Low Energy:

```bash
$ uv run src/main.py
```

**Features:**
- 🔍 Automatic BLE device scanning
- 📱 Device selection interface
- 📤 Send commands and raw text to the connected robot
- 📨 Receive and display messages from the robot
- 🔌 Automatic connection management
- 🎨 Colorized terminal output

**Usage:**
1. Run the script and select a device from the list
2. Type commands in the form `command_name(arg1, arg2, ...)` (see [Adding a new command](#adding-a-new-command) below). Lines that are not registered CLI commands can still be sent as raw text after confirmation.
3. Messages from the device will appear automatically
4. Type `quit`, `exit`, or `close` to disconnect

## Adding a new command

Parsing and dispatch live in `src/commands/command_handlers.py` and `src/protocol_utils.py`. You register handlers that receive a **digested** invocation and optional BLE access.

**Most commands only touch `command_handlers.py`.** You must edit **`src/main.py`** only if the device streams a **custom response protocol** (like `help_res` / `param_list_res`) that must be buffered and fed into an active collection session—see the checklist below.

### Checklist: `command_handlers.py` and `main.py`

The source files contain `ADD NEW COMMAND` comments at the same spots; use this list so nothing is missed.

#### `src/commands/command_handlers.py`

| Step | Where (search for the comment)                        | What to do                                                                                                                                                                                                      |
| ---- | ----------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| 1    | **`ADD NEW COMMAND: imports`**                        | `import` your `cmd_*` handler from your module (e.g. `commands.my_handlers` or `commands.param.*`). If you parse device list responses, also import your `capture_*_from_ble` and `try_feed_*_session` helpers. |
| 2    | **`ADD NEW COMMAND: public re-exports`**              | If `main.py` must import BLE helpers from this package, add them to the import block and to **`__all__`**.                                                                                                      |
| 3    | **`ADD NEW COMMAND: inline CLI handlers`**            | Optional: define small `async def cmd_*` here instead of another file.                                                                                                                                          |
| 4    | **`ADD NEW COMMAND: incoming BLE handlers`**          | Optional: define sync `def _incoming_*` for device → host `command_name(...)` lines.                                                                                                                            |
| 5    | **`ADD NEW COMMAND: register CLI handlers`**          | `register_cli_command("name", cmd_name)` — name must match what the user types (first token).                                                                                                                   |
| 6    | **`ADD NEW COMMAND: register incoming BLE handlers`** | `register_incoming_command("name", handler)` for each incoming handler.                                                                                                                                         |
| 7    | **`ADD NEW COMMAND: load external plugin modules`**   | At the bottom: `import my_robot_commands  # noqa: F401` so a separate file can call `register_*` at import time.                                                                                                |

#### `src/main.py` (only for BLE list/stream responses)

| Step | Where                                                    | What to do                                                                                                                                                                      |
| ---- | -------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| 1    | **Imports** from `commands.command_handlers`             | Add your `capture_*_from_ble` and `try_feed_*_session` symbols (they must be exported from `command_handlers.py` first).                                                        |
| 2    | **`ble_dispatch_line`** (inside `main()`, after connect) | Call each `capture_*_from_ble(message)` first, then each `if try_feed_*_session(message): return`, then `handle_incoming_message(message)`—same order as `help` / `param_list`. |

### Command format

Every line the app treats as a command must look like a function call:

```text
command_name(parameter1, parameter2, ...)
```

Arguments are split on **top-level commas** (commas inside quoted strings are ignored). Each segment is **unquoted** if it was wrapped in double quotes. Examples:

| You type              | `inv.name`  | `inv.params`         |
| --------------------- | ----------- | -------------------- |
| `ping()`              | `ping`      | `()` (empty)         |
| `echo(hello, world)`  | `echo`      | `("hello", "world")` |
| `set_speed("10", 20)` | `set_speed` | `("10", "20")`       |

The full stripped line is always available as `inv.line` if you need to forward it verbatim to the device (for example after `help()`).

### What your handler receives

CLI handlers are **async** and get:

- **`inv: CommandInvocation`** — `name`, `raw_arguments` (text inside the parentheses), `params` (tuple of string arguments), and `line` (full line).
- **`nus: NusPort`** — anything with `await nus.send_message(text) -> bool`. You do not import `main` or the BLE client class for typing.

Import types from `commands.command_handlers`:

```python
from commands.command_handlers import CommandInvocation, NusPort, register_cli_command
```

### Step-by-step: CLI command

1. **Create a new module** on `sys.path` (often `src/` or `src/commands/`), for example `src/my_robot_commands.py`.

2. **Implement an async handler** whose first token name matches what you will type (lowercasing is applied when matching):

   ```python
   from __future__ import annotations

   from commands.command_handlers import CommandInvocation, NusPort, register_cli_command


   async def set_speed(inv: CommandInvocation, nus: NusPort) -> None:
       if len(inv.params) != 1:
           return
       speed = inv.params[0]
       ok = await nus.send_message(f"set_speed({speed})")
       if not ok:
           return


   register_cli_command("set_speed", set_speed)
   ```

3. **Load the module** after the registry in `src/commands/command_handlers.py` is defined. At the **bottom** of that file (checklist §7, comment `ADD NEW COMMAND: load external plugin modules`), add:

   ```python
   import my_robot_commands  # noqa: F401
   ```

   Use the same import style as your filename (e.g. `import my_robot_commands` for `my_robot_commands.py`). Placing this import last avoids circular import issues.

4. **Run** `uv run src/main.py` and type `set_speed(42)` at the prompt.

Registered CLI commands run **locally** in the app; use `nus.send_message(...)` when the firmware should receive a string.

### Incoming BLE commands

If the device sends the same `command_name(...)` shape and you want Python-side handling (logging, side effects), register a **synchronous** handler (checklist §4 and §6 in `command_handlers.py`):

```python
from commands.command_handlers import CommandInvocation, register_incoming_command


def on_status(inv: CommandInvocation) -> None:
    print("Device status:", inv.params)


register_incoming_command("status", on_status)
```

Load your module from the bottom of `commands/command_handlers.py` the same way as for CLI commands.

### Reusable parsing helpers

For other wire formats (not the main CLI invocation), use `src/protocol_utils.py`, for example `split_top_level_commas` and `unquote_field`. You can also import these via `commands.command_handlers` (see `__all__` there).

### Built-in commands

Built-in CLI commands use the same `register_cli_command` API as extensions (`help`, `param_list`, `echo`, `ping` in `src/commands/command_handlers.py`). Help over BLE and `help_res(...)` collection is in `src/commands/help_handlers.py`; `param_list` lives in `src/commands/param/param_list_handlers.py`.

#### Test script
Smoke-test the BLE Nordic UART client:

```bash
$ uv run src/test_nus.py
```

### Run Jupyter Notebook

https://docs.astral.sh/uv/guides/integration/jupyter/

```bash
$ uv run --with jupyter jupyter lab
```
