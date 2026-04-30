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
2. Type commands using the **wire protocol** (`name(s,r,...);` — see [Wire message protocol](#wire-message-protocol)) or use **shorthand** for registered CLI commands (`help`, `help()`, etc.). Unrecognized lines can still be sent as raw text after confirmation.
3. Messages from the device will appear automatically
4. Type `quit`, `exit`, or `close` to disconnect

## Adding a new command

Registration and dispatch live in **`src/api/command_handlers.py`**. Text protocol helpers (parse/format, `;`-separated messages) are in **`src/api/protocol_utils.py`**. Handlers under **`src/commands/**`** **consume** that API only.

**CLI command plugins** — **your** `*_handlers.py` modules under **`src/commands/`** — use **`@cli_command`** and are **auto-loaded** on import (see `_load_command_handler_modules` in `api/command_handlers`). The repo ships examples there; you add or replace them like any other plugin tree. **`main.py`** already calls **`dispatch_ble_notification`** from the `api` package for BLE; for buffer/session flows, register **`@register_ble_capture`** / **`@register_ble_try_feed`** in your plugin module — **without** editing `src/api/command_handlers.py` for each new feature.

### Checklist: `src/commands/` plugins vs manual import vs `main.py`

#### New plugin module (`src/commands/**/<name>_handlers.py`)

| Step | What to do                                                                                                                                                                                                                                                                                           |
| ---- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| 1    | Add a module whose filename ends with **`_handlers.py`** (e.g. `src/commands/motion_handlers.py` or `src/commands/param_list_edit_handlers.py`). It is imported automatically when `api.command_handlers` loads.                                                                                     |
| 2    | Decorate your async handler with **`@cli_command`** (name inferred from `cmd_foo` → `foo`) or **`@cli_command("explicit_name")`**. You can still call **`register_cli_command`** manually if you prefer.                                                                                             |
| 3    | For list/stream BLE buffering: decorate with **`@register_ble_capture`** (``def f(message: str) -> None``) and/or **`@register_ble_try_feed`** (``def f(message: str) -> bool``). Order follows loaded ``*_handlers.py`` modules; you do **not** edit **`src/api/command_handlers.py`** per feature. |
| 4    | **`main.py`** only calls **`dispatch_ble_notification(message, router)`** from **`api`**—it runs all capture hooks, then try-feed hooks until one returns ``True``, then **`@incoming_command`** handlers.                                                                                           |

#### Module outside `src/commands/` (manual import)

| Step | What to do                                                                                                                                |
| ---- | ----------------------------------------------------------------------------------------------------------------------------------------- |
| 1    | Use **`@cli_command`** / **`@incoming_command`** (or **`register_*`**) in your module.                                                    |
| 2    | At the **bottom** of **`src/api/command_handlers.py`**: **`import my_robot_commands  # noqa: F401`** so registration runs at import time. |

#### `src/main.py` (BLE list/stream responses)

| Step | Where                           | What to do                                                                                                              |
| ---- | ------------------------------- | ----------------------------------------------------------------------------------------------------------------------- |
| 1    | Not needed                      | New capture/feed hooks register in ``commands`` modules via **`@register_ble_capture`** / **`@register_ble_try_feed`**. |
| 2    | **`dispatch_ble_notification`** | **`main.py`** already forwards each BLE line to **`api.command_handlers.dispatch_ble_notification`**.                   |

### Wire message protocol

Traffic with the device uses **text commands** (not JSON). A **message** may contain **several commands** separated by **`;`** (separators outside parentheses and quoted strings do not split inside them).

Each command looks like:

```text
name(mode, req_or_resp, ...arguments)
```

| Position after `name(`        | Meaning       | Values                                                                           |
| ----------------------------- | ------------- | -------------------------------------------------------------------------------- |
| 1st parameter (`mode`)        | Command shape | **`s`** = *single*, **`h`** = list **header**, **`b`** = list **body** (one row) |
| 2nd parameter (`req_or_resp`) | Message role  | **`r`** = request, **`s`** = response                                            |

Examples:

```text
help(s,r);
param_list(h,s,5,1,1,0);
param_list(b,s,1,"param_get","ref","read a parameter");
map_get(b,s,0,1,2,3,4,5);
```

- **Single** (`s`): after `r` or `s` come the command arguments, if any.
- **List** (`h` / `b`): **`h`** after `r`/`s` uses **four integers** `T,C,B,j` (total rows, rows in this message, total messages, message index); see `WireListHeader` in `api/protocol_utils.py` and `docs/wire_protocol_firmware_implementation.md`. **`b`** carries the **row index** then the arguments for that row.
- Firmware often caps message size (e.g. **256 bytes** on NUS); long lists are split across multiple messages.

In the **CLI**, **registered** commands accept **shorthand** (`help`, `help()`) which expands to `help(s,r);` before sending. Otherwise use full wire text or confirm raw send.

### What your CLI handler receives

**`@cli_command`** handlers are **async** and receive:

- **`inv: WireCommand`** — `name`, `kind` (`"single"` \| `"list_header"` \| `"list_body"`), `is_response`, `index` (for list bodies), `arguments` (tuple of strings as on the wire).
- **`ctx: CliHandlerContext`** — `nus`, `incoming` (parsed command buffer), `prompt_line` / `log`, and **`await ctx.send_wire(text)`** (adds a trailing `;` when appropriate).

With `src` on `PYTHONPATH` (as in `uv run src/main.py`):

```python
from api.command_handlers import CliHandlerContext, WireCommand, cli_command
```

### When you need `register_ble_capture`, `register_ble_try_feed`, or a session class

You **do not** need these for every script — only when the BLE flow requires them.

| Piece                                  | Role                                                                                                                                   | When you **need** it                                                                                                                                                                                                 |
| -------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **`@register_ble_capture`**            | `def f(message: str) -> None` runs **before** parsing on **every** notification; typically **buffers** lines.                          | Responses can arrive **before** the user starts the collecting command, and you want to **replay** them when the session opens (e.g. `help`, `param_list`).                                                          |
| **`@register_ble_try_feed`**           | `def f(message: str) -> bool`; returning **`True`** **stops** further handling for that notification (no generic `@incoming_command`). | An **active session** consumes a multi-line stream until complete or timeout.                                                                                                                                        |
| **A `*CollectionSession`-style class** | Holds state + `asyncio.Event` + file output.                                                                                           | Only for **multi-notification** “collect until done / partial” flows. A simple request/response command can use **`ctx.incoming.wait_for(...)`** inside the handler **without** capture/try_feed or a session class. |

For “send → wait for one reply → show”, **`@cli_command`** plus **`ctx`** / **`incoming`** is usually enough.

### Realtime monitor with decorators

The `open_realtime` command opens a **read-only** TUI monitor window. While this window is open, normal prompt input is paused.

To add new realtime variables, you do not need per-variable custom functions in the `api/` domain: just register getter functions with **`@register_realtime_variable`**.

- Each registered function runs automatically at its own `refresh_seconds` interval.
- The latest successful value is kept in memory while the realtime window is open.
- Registered functions can be synchronous or asynchronous.
- A function can accept `ctx: CliHandlerContext` (for wire send + BLE wait) or no arguments.

Example reading battery voltage with `battery_get()`:

```python
from __future__ import annotations

import re

from api.command_handlers import CliHandlerContext, WireCommand
from api.protocol_utils import format_message
from api.realtime import register_realtime_variable

_VOLTAGE_RE = re.compile(r"(-?\d+(?:\.\d+)?)")


@register_realtime_variable("battery", refresh_seconds=1.0, order=0)
async def get_realtime_battery_from_device(ctx: CliHandlerContext) -> str:
    wire = format_message([WireCommand.single_request("battery_get", ())])
    await ctx.send_wire(wire)
    resp = await ctx.incoming.wait_for(
        "battery_get",
        timeout=2.0,
        predicate=lambda c: c.kind == "single",
    )
    payload = " ".join(resp.arguments).strip() or "unknown"
    m = _VOLTAGE_RE.search(payload)
    return f"{m.group(1)} V" if m else payload
```

After registering your realtime functions (in a loaded `*_handlers.py` module), run:

```text
open_realtime
```

### Step-by-step: CLI command

**Plugin under `src/commands/`:**

1. Add **`src/commands/my_motion_handlers.py`** (must end with **`_handlers.py`**).
2. Register with a decorator (name defaults from `cmd_set_speed` → `set_speed`):

   ```python
   from __future__ import annotations

   from api.command_handlers import CliHandlerContext, WireCommand, cli_command
   from api.protocol_utils import format_message


   @cli_command  # or @cli_command("set_speed")
   async def cmd_set_speed(inv: WireCommand, ctx: CliHandlerContext) -> None:
       arg = inv.arguments[0] if inv.arguments else "42"
       await ctx.send_wire(format_message([WireCommand.single_request("set_speed", (arg,))]))
   ```

3. **Run** `uv run src/main.py` and type `set_speed(42)` or the wire form `set_speed(s,r,42);`.

**Module outside the `src/commands/` tree:** put the handler in e.g. `src/my_robot_commands.py`, then add **`import my_robot_commands  # noqa: F401`** at the bottom of **`src/api/command_handlers.py`**.

### External plugin: commands received from the device (BLE)

These are messages in the **same wire protocol** (parsed to **`WireCommand`**), **sent by the robot** and received on the PC over BLE — unlike what the user **types** in the terminal (`@cli_command`).

Register a **synchronous** handler with **`@incoming_command`** (or **`register_incoming_command`**). The registry name comes from **`_incoming_foo` → `foo`** or **`@incoming_command("name")`**. The handler receives a **`WireCommand`**.

```python
from api.command_handlers import WireCommand, incoming_command


@incoming_command  # or @incoming_command("status")
def _incoming_status(cmd: WireCommand) -> None:
    print("Device command:", cmd.name, cmd.arguments)
```

For modules outside `src/commands/**`, import the module at the bottom of **`src/api/command_handlers.py`** as for CLI commands.

### Reusable parsing helpers

Use **`src/api/protocol_utils.py`** for wire parsing — e.g. `parse_message`, `format_wire_command`, `split_top_level_commas`, `unquote_field`. Import from **`api.protocol_utils`** or from **`api.command_handlers`** (`__all__`).

### CLI command plugins (`src/commands/`)

Plugin handlers use **`@cli_command`** (or `register_cli_command`). Put them in `*_handlers.py` under `src/commands/` (e.g. `help_handlers.py`, `param_list_edit_handlers.py`); minimal **`echo` / `ping`** samples that ship with the **core** API live in **`src/api/command_handlers.py`**.

#### Scripts

- Registry smoke check (no BLE):

```bash
$ uv run scripts/list_registered_commands.py
```

- Nordic UART client smoke test:

```bash
$ uv run src/test_nus.py
```

Minimal **template** for a forward-only BLE command: see `cmd_echo` / `cmd_ping` in **`src/api/command_handlers.py`**, or a small `*_handlers.py` that only calls **`await ctx.send_wire(...)`**.

### Run Jupyter Notebook

https://docs.astral.sh/uv/guides/integration/jupyter/

```bash
$ uv run --with jupyter jupyter lab
```
