# TamanduCLI

Interface de linha de comando (CLI) e outras ferramentas para desenvolver, testar, depurar e visualizar dados de robôs seguidores de linha.

> Para um controlador com interface gráfica (GUI), use o [Dashboard](https://tt-linefollower.web.app).
>
> Repositório no GitHub: [Tamandutech/LineFollower_CCenter_Code](https://github.com/Tamandutech/LineFollower_CCenter_Code).

## Primeiros passos

### Pré-requisitos

1. [Instale astral-sh/uv](https://github.com/astral-sh/uv?tab=readme-ov-file#installation):

```bash
# No macOS e Linux.
$ curl -LsSf https://astral.sh/uv/install.sh | sh

# No Windows.
$ powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"

# Com pip.
$ pip install uv
```

2. [Instale o ruff](https://github.com/astral-sh/ruff):

```bash
$ uv tool install ruff
```

### Executar scripts Python

#### CLI do Nordic UART Service
O aplicativo principal oferece uma interface de linha de comando para se comunicar com dispositivos IoT usando o Nordic UART Service:

```bash
$ uv run src/main.py
```

**Recursos:**
- 🔍 Varredura automática de dispositivos BLE
- 📱 Interface de seleção de dispositivo
- 📤 Envio de mensagens para dispositivos IoT conectados
- 📨 Recebimento e exibição de mensagens dos dispositivos
- 🔌 Gerenciamento automático de conexão
- 🎨 Saída colorida no terminal

**Uso:**
1. Execute o script e escolha um dispositivo na lista
2. Digite comandos no formato `nome_do_comando(arg1, arg2, ...)` (veja [Adicionar um novo comando](#adicionar-um-novo-comando) abaixo). Linhas que não forem comandos registrados na CLI ainda podem ser enviadas como texto bruto após confirmação.
3. Mensagens do dispositivo aparecerão automaticamente
4. Digite `quit`, `exit` ou `close` para desconectar

## Adicionar um novo comando

Você pode estender a CLI sem editar `main.py`. A análise e o despacho ficam em `src/commands/command_handlers.py` e `src/protocol_utils.py`; sua parte é registrar um handler que recebe uma invocação **digesta** e acesso BLE opcional. Comandos orientados a parâmetros ficam em `src/commands/param/`.

### Formato do comando

Toda linha que o app trata como comando deve parecer uma chamada de função:

```text
nome_do_comando(parametro1, parametro2, ...)
```

Os argumentos são separados por **vírgulas de nível superior** (vírgulas dentro de strings entre aspas são ignoradas). Cada trecho é **desaspado** se estiver entre aspas duplas. Exemplos:

| Você digita           | `inv.name`  | `inv.params`         |
| --------------------- | ----------- | -------------------- |
| `ping()`              | `ping`      | `()` (vazio)         |
| `echo(hello, world)`  | `echo`      | `("hello", "world")` |
| `set_speed("10", 20)` | `set_speed` | `("10", "20")`       |

A linha completa já sem espaços extras está sempre em `inv.line` se você precisar repassá-la literalmente ao dispositivo (por exemplo após `help()`).

### O que o handler recebe

Os handlers da CLI são **assíncronos** e recebem:

- **`inv: CommandInvocation`** — `name`, `raw_arguments` (texto dentro dos parênteses), `params` (tupla de argumentos em string) e `line` (linha inteira).
- **`nus: NusPort`** — qualquer coisa com `await nus.send_message(text) -> bool`. Você não importa `main` nem a classe do cliente BLE para tipagem.

Importe os tipos de `commands.command_handlers`:

```python
from commands.command_handlers import CommandInvocation, NusPort, register_cli_command
```

### Passo a passo: comando na CLI

1. **Crie um novo módulo** em `src/`, por exemplo `src/my_robot_commands.py`.

2. **Implemente um handler assíncrono** cujo primeiro nome de token coincida com o que você vai digitar (a comparação usa minúsculas):

   ```python
   from __future__ import annotations

   from commands.command_handlers import CommandInvocation, NusPort, register_cli_command


   async def set_speed(inv: CommandInvocation, nus: NusPort) -> None:
       if len(inv.params) != 1:
           return
       speed = inv.params[0]
       ok = await nus.send_message(f"set_speed({speed})")
       if not ok:
           return


   register_cli_command("set_speed", set_speed)
   ```

3. **Carregue o módulo** depois que o registro em `src/commands/command_handlers.py` estiver definido. No **final** desse arquivo, adicione:

   ```python
   import my_robot_commands  # noqa: F401
   ```

   Use o mesmo estilo de import do nome do arquivo (por exemplo `import my_robot_commands` para `my_robot_commands.py`). Colocar esse import por último evita problemas de importação circular.

4. **Execute** `uv run src/main.py` e digite `set_speed(42)` no prompt.

Comandos registrados na CLI rodam **localmente** no app; use `nus.send_message(...)` quando o firmware deve receber uma string.

### Comandos BLE recebidos

Se o dispositivo envia o mesmo formato `nome_do_comando(...)` e você quer tratamento no lado Python (logs, efeitos colaterais), registre um handler **síncrono**:

```python
from commands.command_handlers import CommandInvocation, register_incoming_command


def on_status(inv: CommandInvocation) -> None:
    print("Status do dispositivo:", inv.params)


register_incoming_command("status", on_status)
```

Carregue seu módulo a partir do final de `commands/command_handlers.py` da mesma forma que para comandos da CLI.

### Helpers de parsing reutilizáveis

Para outros formatos no fio (não a invocação principal da CLI), use `src/protocol_utils.py`, por exemplo `split_top_level_commas` e `unquote_field`. Você também pode importar isso via `commands.command_handlers` (veja `__all__` lá).

### Comandos embutidos

Os handlers existentes são registrados em `src/commands/command_handlers.py` (`help`, `param_list`, `echo`, `ping`). Ajuda via BLE e a coleta `help_res(...)` estão em `src/commands/help_handlers.py`; `param_list` fica em `src/commands/param/param_list_handlers.py`.

#### Script de teste
Teste a implementação do Nordic UART Service:

```bash
$ uv run src/test_nus.py
```

### Executar Jupyter Notebook

https://docs.astral.sh/uv/guides/integration/jupyter/

```bash
$ uv run --with jupyter jupyter lab
```
