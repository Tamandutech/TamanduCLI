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
    Terminal.log("🧪 Testando conexão BLE (NUS) do robô...", "BRIGHT_CYAN")

    Terminal.log("🔍 Buscando dispositivos...", "CYAN")
    devices = await scan_devices(timeout=5.0, name_prefix="")

    if not devices:
        Terminal.log("❌ Nenhum dispositivo encontrado para o teste", "RED")
        return False

    Terminal.log(f"📱 Encontrado(s) {len(devices)} dispositivo(s) para o teste", "GREEN")
    test_device = devices[0]
    Terminal.log(f"🎯 Testando com o dispositivo: {test_device.name}", "CYAN")

    nus = NordicUARTService(log=lambda m, c: Terminal.log(m, c))
    try:
        if not await nus.connect(test_device):
            Terminal.log("❌ Teste de conexão falhou", "RED")
            return False

        Terminal.log("✅ Teste de conexão passou", "GREEN")

        test_message = "ping(s,r);"
        if await nus.send_message(test_message):
            Terminal.log("✅ Teste de envio de mensagem passou", "GREEN")
        else:
            Terminal.log("❌ Teste de envio de mensagem falhou", "RED")
            return False

        Terminal.log("⏳ Aguardando respostas...", "YELLOW")
        await asyncio.sleep(2)

        Terminal.log("✅ Teste BLE NUS concluído com sucesso", "GREEN")
        return True
    finally:
        await nus.disconnect()


async def main() -> None:
    try:
        success = await test_nus_connection()
        if success:
            Terminal.log("🎉 Todos os testes passaram!", "BRIGHT_GREEN")
        else:
            Terminal.log("❌ Alguns testes falharam", "RED")
            sys.exit(1)
    except Exception as e:
        Terminal.log(f"❌ Erro no teste: {e!s}", "RED")
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
