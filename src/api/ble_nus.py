"""Nordic UART Service (BLE) client."""

from __future__ import annotations

import asyncio
from typing import Callable, Optional

from bleak import BleakClient, BleakScanner
from bleak.backends.characteristic import BleakGATTCharacteristic
from bleak.backends.device import BLEDevice

LogFn = Callable[[str, str], None]
BleLineFn = Callable[[str], None]


class NordicUARTService:
    NUS_SERVICE_UUID = "6E400001-B5A3-F393-E0A9-E50E24DCCA9E"
    NUS_RX_CHAR_UUID = "6E400002-B5A3-F393-E0A9-E50E24DCCA9E"
    NUS_TX_CHAR_UUID = "6E400003-B5A3-F393-E0A9-E50E24DCCA9E"

    def __init__(
        self,
        *,
        log: Optional[LogFn] = None,
        on_disconnect_msg: Optional[LogFn] = None,
    ) -> None:
        self.client: Optional[BleakClient] = None
        self.is_connected = False
        self.rx_characteristic = None
        self.tx_characteristic = None
        self.message_handler: Optional[BleLineFn] = None
        self._log = log or (lambda m, _c: print(m))
        self._on_disconnect_msg = on_disconnect_msg

    def set_message_handler(self, handler: Optional[BleLineFn]) -> None:
        self.message_handler = handler

    def _handle_rx_data(self, characteristic: BleakGATTCharacteristic, data: bytearray) -> None:
        try:
            message = data.decode("utf-8").replace("\x00", "")
            self._log(f"📨 Recebido: {message}", "GREEN")
            if self.message_handler:
                self.message_handler(message)
        except UnicodeDecodeError:
            hx = data.hex()
            self._log(f"📨 Recebido (bruto): {hx}", "GREEN")
            if self.message_handler:
                self.message_handler(hx)

    def _handle_disconnect(self, client: BleakClient) -> None:
        if self._on_disconnect_msg:
            self._on_disconnect_msg("🔌 Dispositivo desconectado", "RED")
        self.is_connected = False
        for task in asyncio.all_tasks():
            if task != asyncio.current_task():
                task.cancel()

    async def connect(self, device: BLEDevice) -> bool:
        try:
            self._log(f"🔗 Conectando a {device.name} ({device.address})...", "CYAN")
            self.client = BleakClient(device.address, disconnected_callback=self._handle_disconnect)
            await self.client.connect()
            if not self.client.is_connected:
                self._log("❌ Falha ao conectar ao dispositivo", "RED")
                return False
            nus_service = self.client.services.get_service(self.NUS_SERVICE_UUID)
            if not nus_service:
                self._log("❌ Serviço Nordic UART não encontrado no dispositivo", "RED")
                await self.client.disconnect()
                return False
            self.rx_characteristic = nus_service.get_characteristic(self.NUS_RX_CHAR_UUID)
            self.tx_characteristic = nus_service.get_characteristic(self.NUS_TX_CHAR_UUID)
            if not self.rx_characteristic or not self.tx_characteristic:
                self._log("❌ Características necessárias não encontradas", "RED")
                await self.client.disconnect()
                return False
            await self.client.start_notify(self.tx_characteristic, self._handle_rx_data)
            self.is_connected = True
            self._log("✅ Conectado ao Nordic UART Service", "GREEN")
            return True
        except Exception as e:
            self._log(f"❌ Erro de conexão: {e!s}", "RED")
            return False

    async def send_message(self, message: str) -> bool:
        if not self.is_connected or not self.rx_characteristic:
            self._log("❌ Não conectado ao dispositivo", "RED")
            return False
        try:
            data = message.encode("utf-8")
            await self.client.write_gatt_char(self.rx_characteristic, data, response=True)
            self._log(f"📤 Enviado: {message}", "BLUE")
            return True
        except Exception as e:
            self._log(f"❌ Erro ao enviar: {e!s}", "RED")
            return False

    async def disconnect(self) -> None:
        if self.client and self.is_connected:
            await self.client.disconnect()
            self.is_connected = False
            self._log("🔌 Desconectado do dispositivo", "YELLOW")

    async def __aenter__(self) -> NordicUARTService:
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        await self.disconnect()


async def scan_devices(*, timeout: float = 3.0, name_prefix: str = "TT") -> list[BLEDevice]:
    discovered = await BleakScanner.discover(timeout)
    if not name_prefix:
        return [d for d in discovered if d.name]
    return [d for d in discovered if d.name and d.name.startswith(name_prefix)]
