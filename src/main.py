import asyncio
import os
import platform

from bleak.backends.device import BLEDevice
from prompt_toolkit import print_formatted_text
from prompt_toolkit.completion import WordCompleter
from prompt_toolkit.formatted_text import ANSI
from prompt_toolkit.patch_stdout import patch_stdout
from prompt_toolkit.shortcuts import PromptSession, confirm
from prompt_toolkit.shortcuts.choice_input import ChoiceInput

from api.ble_nus import NordicUARTService, scan_devices
from api.cli_prompt_bindings import build_cli_input_key_bindings
from api.cli_prompt_lexer import CLI_PROMPT_STYLE, WireCliLexer
from api.command_handlers import (
    CLI_COMMAND_HANDLERS,
    CliHandlerContext,
    dispatch_ble_notification,
    dispatch_cli_command,
    is_command_invocation,
    is_registered_command,
)
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
        f"🔍 Buscando dispositivos BLE (prefixo do nome {DEVICE_NAME_PREFIX!r})...",
        "CYAN",
    )
    devices: list[BLEDevice] = []
    while not devices:
        devices = await scan_devices(timeout=3.0, name_prefix=DEVICE_NAME_PREFIX)
        if not devices:
            Terminal.log(
                f"❌ Nenhum dispositivo com prefixo do nome {DEVICE_NAME_PREFIX!r}. Tentando novamente em 1 segundo...",
                "YELLOW",
            )
            await asyncio.sleep(1)

    Terminal.log(f"📱 Encontrado(s) {len(devices)} dispositivo(s).", "GREEN")

    if len(devices) == 1:
        selected_device = devices[0]
        label = selected_device.name or "sem nome"
        Terminal.log(
            f"✓ Apenas um dispositivo encontrado — usando {label} ({selected_device.address}).",
            "GREEN",
        )
    else:
        try:
            selected_device = await ChoiceInput(
                message=f"Selecione um dispositivo (prefixo do nome {DEVICE_NAME_PREFIX!r}):",
                options=[(d, f"{d.name} ({d.address})") for d in devices],
                default=devices[0],
            ).prompt_async()
        except (KeyboardInterrupt, EOFError):
            selected_device = None
    if selected_device is None:
        Terminal.log("👋 Nenhum dispositivo selecionado — saindo.", "YELLOW")
        return

    completer = WordCompleter(sorted(CLI_COMMAND_HANDLERS.keys()), ignore_case=True)
    registered_lower = frozenset(k.lower() for k in CLI_COMMAND_HANDLERS)
    session = PromptSession(
        completer=completer,
        lexer=WireCliLexer(registered_lower),
        style=CLI_PROMPT_STYLE,
        include_default_pygments_style=False,
        key_bindings=build_cli_input_key_bindings(),
    )

    async def prompt_line(message: str) -> str:
        return await session.prompt_async(message)

    def log(message: str, color: str = "WHITE") -> None:
        Terminal.log(message, color)

    nus = NordicUARTService(
        log=lambda m, c: Terminal.log(m, c),
        on_disconnect_msg=lambda m, c: Terminal.log(m, c),
    )

    def ble_dispatch_line(message: str) -> None:
        loop.call_soon_threadsafe(
            lambda m=message: dispatch_ble_notification(m, router)
        )

    nus.set_message_handler(ble_dispatch_line)

    if not await nus.connect(selected_device):
        Terminal.log("❌ Falha ao conectar ao dispositivo", "RED")
        return

    ctx = CliHandlerContext(
        nus=nus,
        incoming=router,
        prompt_line=prompt_line,
        log=log,
    )

    Terminal.log("🚀 Console BLE do robô pronto!", "GREEN")
    Terminal.log(
        "💡 Protocolo wire: name(s,r); name(h,r,size); name(b,r,idx,args…);", "YELLOW"
    )
    Terminal.log("  • Atalho: nomes de comandos registrados viram name(s,r);", "WHITE")
    Terminal.log("  • quit / exit / close — desconectar", "WHITE")
    Terminal.log("─" * 50, "DARK_GRAY")

    try:
        with patch_stdout():
            while nus.is_connected:
                try:
                    message = (await session.prompt_async("📤 Enviar: ")).strip()
                except (EOFError, KeyboardInterrupt):
                    Terminal.log("\n👋 Interrompido pelo usuário", "YELLOW")
                    break

                if message.lower() in ("quit", "exit", "close"):
                    Terminal.log("👋 Encerrando o console BLE do robô...", "YELLOW")
                    break

                if not message:
                    continue

                if await dispatch_cli_command(message, ctx):
                    continue

                if not is_command_invocation(message):
                    Terminal.log(
                        "⚠ Esperado um comando registrado ou formato wire name(s,r,…).",
                        "YELLOW",
                    )

                if is_registered_command(message):
                    Terminal.log(
                        "⚠ A entrada coincide com um comando registrado, mas não foi despachada; verifique a sintaxe wire.",
                        "RED",
                    )
                    continue

                ok = await loop.run_in_executor(
                    None,
                    lambda m=message: confirm(
                        f"Entrada desconhecida ou não tratada. Enviar esta linha como texto bruto ao dispositivo?\n{m}"
                    ),
                )
                if ok:
                    await nus.send_message(message)

            if not nus.is_connected:
                Terminal.log("❌ Dispositivo desconectado — saindo.", "RED")

    except Exception as e:
        Terminal.log(f"❌ Erro no loop principal: {e!s}", "RED")
    finally:
        Terminal.log("🔌 Desconectando...", "YELLOW")
        await nus.disconnect()


if __name__ == "__main__":
    try:
        Terminal.log("🚀 Iniciando TamanduCLI (console BLE do robô)...", "BRIGHT_GREEN")
        asyncio.run(main())
    except asyncio.CancelledError:
        Terminal.log("👋 Aplicativo encerrado", "YELLOW")
    except KeyboardInterrupt:
        Terminal.log("\n👋 Aplicativo interrompido pelo usuário", "YELLOW")
    except Exception as e:
        Terminal.log(f"❌ Erro inesperado: {e!s}", "RED")
    finally:
        Terminal.log("🔚 TamanduCLI encerrado", "DARK_GRAY")
