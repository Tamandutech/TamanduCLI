#!/usr/bin/env python3
"""
Smoke test for the BLE Nordic UART Service client used by TamanduCLI.
"""

import asyncio
import os
import sys

# Add the src directory to the path so we can import main
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from bleak.backends.device import BLEDevice

from main import BleakScanner, NordicUARTService, Terminal


async def test_nus_connection():
    """Exercise BLE connect + NUS read/write against a peripheral."""
    Terminal.log("🧪 Testing robot BLE (NUS) connection...", "BRIGHT_CYAN")
    
    # Scan for devices
    Terminal.log("🔍 Scanning for devices...", "CYAN")
    devices = await BleakScanner.discover(5.0)
    devices = [d for d in devices if d.name]
    
    if not devices:
        Terminal.log("❌ No devices found for testing", "RED")
        return False
    
    Terminal.log(f"📱 Found {len(devices)} device(s) for testing", "GREEN")
    
    # Use the first device for testing
    test_device = devices[0]
    Terminal.log(f"🎯 Testing with device: {test_device.name}", "CYAN")
    
    # Test connection and communication
    async with NordicUARTService() as nus:
        # Test connection
        if not await nus.connect(test_device):
            Terminal.log("❌ Connection test failed", "RED")
            return False
        
        Terminal.log("✅ Connection test passed", "GREEN")
        
        # Test message sending
        test_message = "Hello from TamanduCLI NUS test!"
        if await nus.send_message(test_message):
            Terminal.log("✅ Message sending test passed", "GREEN")
        else:
            Terminal.log("❌ Message sending test failed", "RED")
            return False
        
        # Wait a bit for any responses
        Terminal.log("⏳ Waiting for responses...", "YELLOW")
        await asyncio.sleep(2)
        
        Terminal.log("✅ BLE NUS test completed successfully", "GREEN")
        return True


async def main():
    """Main test function."""
    try:
        success = await test_nus_connection()
        if success:
            Terminal.log("🎉 All tests passed!", "BRIGHT_GREEN")
        else:
            Terminal.log("❌ Some tests failed", "RED")
            sys.exit(1)
    except Exception as e:
        Terminal.log(f"❌ Test error: {str(e)}", "RED")
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
