#!/usr/bin/env python3
"""
Smoke test for the BLE Nordic UART Service client used by TamanduCLI.
"""

import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from api.ble_nus import NordicUARTService, scan_devices
from main import Terminal


async def test_nus_connection() -> bool:
    Terminal.log("🧪 Testing robot BLE (NUS) connection...", "BRIGHT_CYAN")

    Terminal.log("🔍 Scanning for devices...", "CYAN")
    devices = await scan_devices(timeout=5.0, name_prefix="")

    if not devices:
        Terminal.log("❌ No devices found for testing", "RED")
        return False

    Terminal.log(f"📱 Found {len(devices)} device(s) for testing", "GREEN")
    test_device = devices[0]
    Terminal.log(f"🎯 Testing with device: {test_device.name}", "CYAN")

    nus = NordicUARTService(log=lambda m, c: Terminal.log(m, c))
    try:
        if not await nus.connect(test_device):
            Terminal.log("❌ Connection test failed", "RED")
            return False

        Terminal.log("✅ Connection test passed", "GREEN")

        test_message = "ping(s,r);"
        if await nus.send_message(test_message):
            Terminal.log("✅ Message sending test passed", "GREEN")
        else:
            Terminal.log("❌ Message sending test failed", "RED")
            return False

        Terminal.log("⏳ Waiting for responses...", "YELLOW")
        await asyncio.sleep(2)

        Terminal.log("✅ BLE NUS test completed successfully", "GREEN")
        return True
    finally:
        await nus.disconnect()


async def main() -> None:
    try:
        success = await test_nus_connection()
        if success:
            Terminal.log("🎉 All tests passed!", "BRIGHT_GREEN")
        else:
            Terminal.log("❌ Some tests failed", "RED")
            sys.exit(1)
    except Exception as e:
        Terminal.log(f"❌ Test error: {e!s}", "RED")
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
