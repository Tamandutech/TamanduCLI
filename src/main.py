import asyncio
import os
import platform
from typing import Optional

from bleak.backends.device import BLEDevice
from prompt_toolkit import print_formatted_text
from prompt_toolkit.completion import WordCompleter
from prompt_toolkit.formatted_text import ANSI
from prompt_toolkit.patch_stdout import patch_stdout
from prompt_toolkit.shortcuts import PromptSession, confirm, prompt

from api.ble_nus import NordicUARTService, scan_devices
from api.command_handlers import (CLI_COMMAND_HANDLERS, CliHandlerContext,
                                  dispatch_ble_notification,
                                  dispatch_cli_command, is_command_invocation,
                                  is_registered_command)
from api.incoming import IncomingRouter

if platform.system() == "Windows":
    os.system("color")

TERM_COLOR = {
    "BLACK": "\033[30m",
    "RED": "\033[31m",
    "GREEN": "\033[32m",
    "YELLOW": "\033[33m",
    "BLUE": "\033[34m",
    "MAGENTA": "\033[35m",
    "CYAN": "\033[36m",
    "LIGHT_GRAY": "\033[37m",
    "DARK_GRAY": "\033[90m",
    "BRIGHT_RED": "\033[91m",
    "BRIGHT_GREEN": "\033[92m",
    "BRIGHT_YELLOW": "\033[93m",
    "BRIGHT_BLUE": "\033[94m",
    "BRIGHT_MAGENTA": "\033[95m",
    "BRIGHT_CYAN": "\033[96m",
    "WHITE": "\033[97m",
    "RESET": "\033[0m",
}

APP_TAG = f"{TERM_COLOR['BRIGHT_MAGENTA']}[TamanduCLI]{TERM_COLOR['RESET']}"


class Terminal:
    @staticmethod
    def log(message: str, color: str = "WHITE") -> None:
        line = f"{APP_TAG} {TERM_COLOR[color]}{message}{TERM_COLOR['RESET']}"
        print_formatted_text(ANSI(line))


DEVICE_NAME_PREFIX = "TT"


async def main() -> None:
    loop = asyncio.get_running_loop()
    router = IncomingRouter(loop)

    Terminal.log(
        f"🔍 Scanning for BLE devices (name prefix {DEVICE_NAME_PREFIX!r})...",
        "CYAN",
    )
    devices: list[BLEDevice] = []
    while not devices:
        devices = await scan_devices(timeout=3.0, name_prefix=DEVICE_NAME_PREFIX)
        if not devices:
            Terminal.log(
                f"❌ No devices with name prefix {DEVICE_NAME_PREFIX!r} found. Retrying in 1 second...",
                "YELLOW",
            )
            await asyncio.sleep(1)

    Terminal.log(f"📱 Found {len(devices)} device(s):", "GREEN")
    enumerated_devices: list[BLEDevice] = []
    for index, device in enumerate(devices):
        Terminal.log(f"  {index}. {device.name} ({device.address})", "CYAN")
        enumerated_devices.append(device)

    completer = WordCompleter(sorted(CLI_COMMAND_HANDLERS.keys()), ignore_case=True)
    session = PromptSession(completer=completer)

    async def prompt_line(message: str) -> str:
        return await session.prompt_async(message)

    def log(message: str, color: str = "WHITE") -> None:
        Terminal.log(message, color)

    selected_device: Optional[BLEDevice] = None
    while selected_device is None:
        raw = await session.prompt_async("🔢 Enter device number: ")
        try:
            device_index = int(raw.strip())
            if 0 <= device_index < len(enumerated_devices):
                selected_device = enumerated_devices[device_index]
            else:
                Terminal.log("❌ Invalid device number!", "RED")
        except ValueError:
            Terminal.log("❌ Please enter a valid number!", "RED")

    nus = NordicUARTService(
        log=lambda m, c: Terminal.log(m, c),
        on_disconnect_msg=lambda m, c: Terminal.log(m, c),
    )

    def ble_dispatch_line(message: str) -> None:
        loop.call_soon_threadsafe(lambda m=message: dispatch_ble_notification(m, router))

    nus.set_message_handler(ble_dispatch_line)

    if not await nus.connect(selected_device):
        Terminal.log("❌ Failed to connect to device", "RED")
        return

    ctx = CliHandlerContext(
        nus=nus,
        incoming=router,
        prompt_line=prompt_line,
        log=log,
    )

    Terminal.log("🚀 Robot BLE console ready!", "GREEN")
    Terminal.log("💡 Wire protocol: name(s,r); name(h,r,size); name(b,r,idx,args…);", "YELLOW")
    Terminal.log("  • Shorthand: registered command names expand to name(s,r);", "WHITE")
    Terminal.log("  • quit / exit / close — disconnect", "WHITE")
    Terminal.log("─" * 50, "DARK_GRAY")

    try:
        with patch_stdout():
            while nus.is_connected:
                try:
                    message = (await session.prompt_async("📤 Send: ")).strip()
                except (EOFError, KeyboardInterrupt):
                    Terminal.log("\n👋 Interrupted by user", "YELLOW")
                    break

                if message.lower() in ("quit", "exit", "close"):
                    Terminal.log("👋 Closing robot BLE console...", "YELLOW")
                    break

                if not message:
                    continue

                if await dispatch_cli_command(message, ctx):
                    continue

                if not is_command_invocation(message):
                    Terminal.log(
                        "⚠ Expected a registered command or wire format name(s,r,…).",
                        "YELLOW",
                    )

                if is_registered_command(message):
                    Terminal.log(
                        "⚠ Input matches a registered command but was not dispatched; check wire syntax.",
                        "RED",
                    )
                    continue

                ok = await loop.run_in_executor(
                    None,
                    lambda m=message: confirm(
                        f"Unknown or unhandled input. Send this line as raw text to the device?\n{m}"
                    ),
                )
                if ok:
                    await nus.send_message(message if message.endswith(";") else message + ";")

            if not nus.is_connected:
                Terminal.log("❌ Device disconnected — exiting.", "RED")

    except Exception as e:
        Terminal.log(f"❌ Error in main loop: {e!s}", "RED")
    finally:
        Terminal.log("🔌 Disconnecting...", "YELLOW")
        await nus.disconnect()


if __name__ == "__main__":
    try:
        Terminal.log("🚀 Starting TamanduCLI (robot BLE console)...", "BRIGHT_GREEN")
        asyncio.run(main())
    except asyncio.CancelledError:
        Terminal.log("👋 Application terminated", "YELLOW")
    except KeyboardInterrupt:
        Terminal.log("\n👋 Application interrupted by user", "YELLOW")
    except Exception as e:
        Terminal.log(f"❌ Unexpected error: {e!s}", "RED")
    finally:
        Terminal.log("🔚 TamanduCLI closed", "DARK_GRAY")
