import asyncio
import os
import platform
import queue
import threading
import time
from typing import Callable, Optional

from bleak import BleakClient, BleakScanner
from bleak.backends.characteristic import BleakGATTCharacteristic
from bleak.backends.device import BLEDevice
from bleak.backends.scanner import AdvertisementData

# ADD NEW COMMAND (BLE list/stream): import capture_*_from_ble and try_feed_*_session from
# commands.command_handlers (after exporting them there), then call them inside ble_dispatch_line below.
from commands.command_handlers import (
    capture_help_res_from_ble,
    capture_map_get_res_from_ble,
    capture_param_list_res_from_ble,
    dispatch_cli_command,
    handle_incoming_message,
    is_command_invocation,
    try_feed_help_session,
    try_feed_map_get_session,
    try_feed_param_list_session,
)

if platform.system() == "Windows":
    os.system("color")  # Enable ANSI color codes on Windows


# Section: Terminal Print Functions
TERM_COLOR = {
    "BLACK": "\033[30m",
    "RED": "\033[31m",
    "GREEN": "\033[32m",
    "YELLOW": "\033[33m",  # orange on some systems
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
    "RESET": "\033[0m",  # called to return to standard terminal text color
}


APP_TAG = f"{TERM_COLOR['BRIGHT_MAGENTA']}[TamanduCLI]{TERM_COLOR['RESET']}"


class Terminal:
    @staticmethod
    def log(message: str, color: str = "WHITE"):
        print(f"{APP_TAG} {TERM_COLOR[color]}{message}{TERM_COLOR['RESET']}")

    @staticmethod
    def input(message: str, color: str = "WHITE"):
        return input(f"{APP_TAG} {TERM_COLOR[color]}{message}{TERM_COLOR['RESET']}")


# Section: Nordic UART Service (NUS) Implementation
class NordicUARTService:
    """
    Nordic UART Service (BLE serial) client.
    Use this for bidirectional text with robots and other embedded devices that expose NUS.
    """
    
    # Nordic UART Service UUIDs
    NUS_SERVICE_UUID = "6E400001-B5A3-F393-E0A9-E50E24DCCA9E"
    NUS_RX_CHAR_UUID = "6E400002-B5A3-F393-E0A9-E50E24DCCA9E"  # Write characteristic
    NUS_TX_CHAR_UUID = "6E400003-B5A3-F393-E0A9-E50E24DCCA9E"  # Notify characteristic
    
    def __init__(self):
        self.client: Optional[BleakClient] = None
        self.is_connected = False
        self.message_queue = queue.Queue()
        self.rx_characteristic = None
        self.tx_characteristic = None
        self.message_handler: Optional[Callable[[str], None]] = None
        
    def set_message_handler(self, handler: Callable[[str], None]):
        """Set the callback function for received messages."""
        self.message_handler = handler
    
    def _handle_rx_data(self, characteristic: BleakGATTCharacteristic, data: bytearray):
        """Handle incoming data from the TX characteristic (notifications)."""
        try:
            message = data.decode("utf-8").replace("\x00", "")
            Terminal.log(f"📨 Received: {message}", "GREEN")
            if self.message_handler:
                self.message_handler(message)
        except UnicodeDecodeError:
            Terminal.log(f"📨 Received (raw): {data.hex()}", "GREEN")
            if self.message_handler:
                self.message_handler(data.hex())
    
    def _handle_disconnect(self, client: BleakClient):
        """Handle device disconnection."""
        Terminal.log("🔌 Device disconnected", "RED")
        self.is_connected = False
        # Cancel all running tasks
        for task in asyncio.all_tasks():
            if task != asyncio.current_task():
                task.cancel()
    
    async def connect(self, device: BLEDevice) -> bool:
        """Connect to a BLE device and set up NUS."""
        try:
            Terminal.log(f"🔗 Connecting to {device.name} ({device.address})...", "CYAN")
            
            self.client = BleakClient(device.address, disconnected_callback=self._handle_disconnect)
            await self.client.connect()
            
            if not self.client.is_connected:
                Terminal.log("❌ Failed to connect to device", "RED")
                return False
            
            # Get the Nordic UART Service
            nus_service = self.client.services.get_service(self.NUS_SERVICE_UUID)
            if not nus_service:
                Terminal.log("❌ Nordic UART Service not found on device", "RED")
                await self.client.disconnect()
                return False
            
            # Get characteristics
            self.rx_characteristic = nus_service.get_characteristic(self.NUS_RX_CHAR_UUID)
            self.tx_characteristic = nus_service.get_characteristic(self.NUS_TX_CHAR_UUID)
            
            if not self.rx_characteristic or not self.tx_characteristic:
                Terminal.log("❌ Required characteristics not found", "RED")
                await self.client.disconnect()
                return False
            
            # Start notifications on TX characteristic
            await self.client.start_notify(self.tx_characteristic, self._handle_rx_data)
            
            self.is_connected = True
            Terminal.log("✅ Connected to Nordic UART Service", "GREEN")
            return True
            
        except Exception as e:
            Terminal.log(f"❌ Connection error: {str(e)}", "RED")
            return False
    
    async def send_message(self, message: str) -> bool:
        """Send a message to the connected device."""
        if not self.is_connected or not self.rx_characteristic:
            Terminal.log("❌ Not connected to device", "RED")
            return False
        
        try:
            data = message.encode('utf-8')
            await self.client.write_gatt_char(self.rx_characteristic, data, response=True)
            Terminal.log(f"📤 Sent: {message}", "BLUE")
            return True
        except Exception as e:
            Terminal.log(f"❌ Send error: {str(e)}", "RED")
            return False
    
    async def disconnect(self):
        """Disconnect from the device."""
        if self.client and self.is_connected:
            await self.client.disconnect()
            self.is_connected = False
            Terminal.log("🔌 Disconnected from device", "YELLOW")
    
    async def __aenter__(self):
        return self
    
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.disconnect()

# Legacy UUIDs for backward compatibility
UART_SERVICE_UUID = NordicUARTService.NUS_SERVICE_UUID
UART_RX_CHAR_UUID = NordicUARTService.NUS_RX_CHAR_UUID
UART_TX_CHAR_UUID = NordicUARTService.NUS_TX_CHAR_UUID
STREAM_UUID = "3a8328fc-3768-46d2-b371-b34864ce8025"

# BLE scan filter: only peripherals whose advertised name starts with this prefix (set to "" for any named device).
DEVICE_NAME_PREFIX = "TT"


async def main():
    """Interactive BLE serial console for robots (Nordic UART Service)."""
    Terminal.log(
        f"🔍 Scanning for BLE devices (name prefix '{DEVICE_NAME_PREFIX}')...",
        "CYAN",
    )

    # Scan for devices
    devices = []
    while not devices:
        discovered_devices = await BleakScanner.discover(5.0)
        devices = [
            d
            for d in discovered_devices
            if d.name and d.name.startswith(DEVICE_NAME_PREFIX)
        ]
        if not devices:
            Terminal.log(
                f"❌ No devices with name prefix '{DEVICE_NAME_PREFIX}' found. Retrying in 3 seconds...",
                "YELLOW",
            )
            await asyncio.sleep(3)

    # Display available devices
    Terminal.log(f"📱 Found {len(devices)} device(s):", "GREEN")
    enumerated_devices: list[BLEDevice] = []
    for index, device in enumerate(devices):
        Terminal.log(f"  {index}. {device.name} ({device.address})", "CYAN")
        enumerated_devices.append(device)

    # Device selection
    selected_device = None
    while selected_device is None:
        try:
            option_input = Terminal.input("🔢 Enter device number: ", "CYAN")
            device_index = int(option_input)
            if 0 <= device_index < len(devices):
                selected_device = enumerated_devices[device_index]
            else:
                Terminal.log("❌ Invalid device number!", "RED")
        except ValueError:
            Terminal.log("❌ Please enter a valid number!", "RED")

    # Connect using Nordic UART Service
    async with NordicUARTService() as nus:
        if not await nus.connect(selected_device):
            Terminal.log("❌ Failed to connect to device", "RED")
            return

        def ble_dispatch_line(message: str) -> None:
            # ADD NEW COMMAND (BLE list/stream): buffer and session-dispatch device lines here
            # (mirror help / param_list: capture_* first, then try_feed_* before handle_incoming_message).
            capture_help_res_from_ble(message)
            capture_map_get_res_from_ble(message)
            capture_param_list_res_from_ble(message)
            if try_feed_help_session(message):
                return
            if try_feed_map_get_session(message):
                return
            if try_feed_param_list_session(message):
                return
            handle_incoming_message(message)

        nus.set_message_handler(ble_dispatch_line)

        Terminal.log("🚀 Robot BLE console ready!", "GREEN")
        Terminal.log("💡 Commands:", "YELLOW")
        Terminal.log(
            "  • Use command_name(param1, param2, ...) — e.g. help(), param_list(), param_get(ref), param_set(ref,val), ping()",
            "WHITE",
        )
        Terminal.log(
            "  • help() / param_list() save list responses under output/ (help_response.txt, param_list.txt); "
            "see commands/help_handlers.py and commands/param_list_handlers.py",
            "WHITE",
        )
        Terminal.log(
            "  • Unknown commands or non-matching lines prompt before sending as raw text",
            "WHITE",
        )
        Terminal.log("  • Type 'quit' or 'exit' to close the app", "WHITE")
        Terminal.log("─" * 50, "DARK_GRAY")

        # Main CLI loop
        try:
            while nus.is_connected:
                try:
                    # Block until Enter; do not use a short wait_for timeout — that cancels the
                    # executor future and discards typed input before send_message runs.
                    loop = asyncio.get_event_loop()
                    message = await loop.run_in_executor(
                        None, lambda: Terminal.input("📤 Send: ", "CYAN")
                    )

                    if message.strip().lower() in ["quit", "exit", "close"]:
                        Terminal.log("👋 Closing robot BLE console...", "YELLOW")
                        break

                    if message.strip():
                        if await dispatch_cli_command(message, nus):
                            continue
                        else:
                            if not is_command_invocation(message):
                                Terminal.log(
                                    "⚠ Expected format: command_name(param1, param2, ...)",
                                    "YELLOW",
                                )
                                Terminal.log(
                                    "⚠ Sending raw text: " + message,
                                    "YELLOW",
                                )
                            # confirm = await loop.run_in_executor(
                            #     None,
                            #     lambda: Terminal.input(
                            #         "Unknown or unregistered command. Send this line as raw text to the device? [y/N]: ",
                            #         "YELLOW",
                            #     ),
                            # )
                            # if confirm.strip().lower() in ("y", "yes"):
                            await nus.send_message(message)

                except KeyboardInterrupt:
                    Terminal.log("\n👋 Interrupted by user", "YELLOW")
                    break
                    
        except Exception as e:
            Terminal.log(f"❌ Error in main loop: {str(e)}", "RED")
        
        Terminal.log("🔌 Disconnecting...", "YELLOW")


if __name__ == "__main__":
    try:
        Terminal.log("🚀 Starting TamanduCLI (robot BLE console)...", "BRIGHT_GREEN")
        asyncio.run(main())
    except asyncio.CancelledError:
        # Task is cancelled on disconnect, so we ignore this error
        Terminal.log("👋 Application terminated", "YELLOW")
    except KeyboardInterrupt:
        Terminal.log("\n👋 Application interrupted by user", "YELLOW")
    except Exception as e:
        Terminal.log(f"❌ Unexpected error: {str(e)}", "RED")
    finally:
        Terminal.log("🔚 TamanduCLI closed", "DARK_GRAY")
