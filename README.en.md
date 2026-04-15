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

**Built-in commands** under `src/commands/**` use **`@cli_command`** and are **auto-imported** from any file named `*_handlers.py` (see `command_handlers._load_command_handler_modules`). You only need to edit **`src/commands/command_handlers.py`** to re-export BLE helpers for **`src/main.py`**, and you edit **`src/main.py`** only if the device streams a **custom response protocol** (like `help_res` / `param_list_res`) that must be buffered and fed into an active session.

### Checklist: built-in vs `main.py`

#### New built-in handler (`src/commands/**/<name>_handlers.py`)

| Step | What to do                                                                                                                                                                                                      |
| ---- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| 1    | Add a module whose filename ends with **`_handlers.py`** (e.g. `src/commands/motion_handlers.py` or `src/commands/param_list_handlers.py`). It is imported automatically when `commands.command_handlers` loads. |
| 2    | Decorate your async handler with **`@cli_command`** (name inferred from `cmd_foo` → `foo`) or **`@cli_command("explicit_name")`**. You can still call **`register_cli_command`** manually if you prefer.        |
| 3    | If `main.py` must call your **`capture_*_from_ble`** / **`try_feed_*_session`**, add imports and **`__all__`** entries in **`command_handlers.py`** (same block as the existing help/param helpers).            |
| 4    | Wire **`ble_dispatch_line`** in **`main.py`**: `capture_*` first, then `try_feed_*` with early `return`, then `handle_incoming_message`—same order as `help` / `param_list`.                                    |

#### Module outside `src/commands/` (manual import)

| Step | What to do                                                                                                                        |
| ---- | --------------------------------------------------------------------------------------------------------------------------------- |
| 1    | Use **`@cli_command`** / **`@incoming_command`** (or **`register_*`**) in your module.                                            |
| 2    | At the **bottom** of **`command_handlers.py`**: **`import my_robot_commands  # noqa: F401`** so registration runs at import time. |

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

Import types and decorators from `commands.command_handlers`:

```python
from commands.command_handlers import CommandInvocation, NusPort, cli_command
```

### Step-by-step: CLI command

**Built-in (`src/commands/` tree):**

1. Add **`src/commands/my_motion_handlers.py`** (must end with **`_handlers.py`**).
2. Register with a decorator (name defaults from `cmd_set_speed` → `set_speed`):

   ```python
   from __future__ import annotations

   from commands.command_handlers import CommandInvocation, NusPort, cli_command


   @cli_command  # or @cli_command("set_speed")
   async def cmd_set_speed(inv: CommandInvocation, nus: NusPort) -> None:
       if len(inv.params) != 1:
           return
       speed = inv.params[0]
       await nus.send_message(f"set_speed({speed})")
   ```

3. **Run** `uv run src/main.py` and type `set_speed(42)`.

**Module outside the `src/commands/` tree:** put the same handler in e.g. `src/my_robot_commands.py`, then add **`import my_robot_commands  # noqa: F401`** at the bottom of **`command_handlers.py`** so the module loads once.

Registered CLI commands run **locally** in the app; use `nus.send_message(...)` when the firmware should receive a string.

### External plugin: commands received from the device (BLE)

**External plugin** (in this project) means lines in the `command_name(...)` form **sent by the external device (robot) and received by the computer running the terminal** over BLE — as opposed to commands **typed in that terminal**, covered in the previous section with **`@cli_command`**.

If the firmware emits that shape and you want Python-side handling (logging, side effects), register a **synchronous** handler with **`@incoming_command`** (or **`register_incoming_command`**). For **`_incoming_status`**, the registered name is **`status`** (the **`_incoming_`** prefix is stripped).

```python
from commands.command_handlers import CommandInvocation, incoming_command


@incoming_command  # or @incoming_command("status")
def _incoming_status(inv: CommandInvocation) -> None:
    print("Device status:", inv.params)
```

For modules outside `src/commands/**`, load the module from the bottom of `command_handlers.py` as for CLI commands.

### Reusable parsing helpers

For other wire formats (not the main CLI invocation), use `src/protocol_utils.py`, for example `split_top_level_commas` and `unquote_field`. You can also import these via `commands.command_handlers` (see `__all__` there).

### Built-in commands

Built-in CLI commands use **`@cli_command`** (or `register_cli_command`). Handlers live in `*_handlers.py` files under `src/commands/` (for example `help_handlers.py`, `param_list_handlers.py`); small shared commands such as `echo` / `ping` are defined inline in `command_handlers.py`.

#### Scripts

- Registry smoke check (no BLE):

```bash
$ uv run scripts/list_registered_commands.py
```

- Nordic UART client smoke test:

```bash
$ uv run src/test_nus.py
```

Minimal **template** for a forward-only BLE command: copy `src/commands/param_set_handlers.py` (send `inv.line`, no response parsing).

### Run Jupyter Notebook

https://docs.astral.sh/uv/guides/integration/jupyter/

```bash
$ uv run --with jupyter jupyter lab
```
