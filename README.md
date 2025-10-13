# LineFollowerDevKit

Command Line Interface (CLI) and other tools for developing, testing, debugging, and data visualization of line follower robots.

> For a Graphic User Interface (GUI) Controller, use the [Dashboard](https://tt-linefollower.web.app).
>
> GitHub Repository: [Tamandutech/LineFollower_CCenter_Code](https://github.com/Tamandutech/LineFollower_CCenter_Code).

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

#### Nordic UART Service CLI
The main application provides a CLI interface for communicating with IoT devices using the Nordic UART Service:

```bash
$ uv run src/main.py
```

**Features:**
- 🔍 Automatic BLE device scanning
- 📱 Device selection interface
- 📤 Send messages to connected IoT devices
- 📨 Receive and display messages from devices
- 🔌 Automatic connection management
- 🎨 Colorized terminal output

**Usage:**
1. Run the script and select a device from the list
2. Type messages to send to the connected device
3. Messages from the device will appear automatically
4. Type `quit`, `exit`, or `close` to disconnect

#### Test Script
Test the Nordic UART Service implementation:

```bash
$ uv run src/test_nus.py
```

### Run Jupyter Notebook

https://docs.astral.sh/uv/guides/integration/jupyter/

```bash
$ uv run --with jupyter jupyter lab
```
