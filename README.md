# TamanduCLI

Interface de linha de comando (CLI) e ferramentas de apoio para desenvolver, testar, depurar e visualizar dados de **robôs** e outros sistemas embarcados conectados via Bluetooth.

> Você pode usar esta CLI junto com qualquer aplicativo complementar (dashboard web, UI desktop, etc.); documente o fluxo no seu próprio projeto.

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

#### Console BLE para robôs (Nordic UART Service)
O aplicativo principal é um console de texto para falar com um robô (ou outro dispositivo) que expõe o **Nordic UART Service** padrão em Bluetooth Low Energy:

```bash
$ uv run src/main.py
```

**Recursos:**
- 🔍 Varredura automática de dispositivos BLE
- 📱 Interface de seleção de dispositivo
- 📤 Envio de comandos e texto bruto para o robô conectado
- 📨 Recebimento e exibição de mensagens do robô
- 🔌 Gerenciamento automático de conexão
- 🎨 Saída colorida no terminal

**Uso:**
1. Execute o script e escolha um dispositivo na lista
2. Digite comandos no formato `nome_do_comando(arg1, arg2, ...)` (veja [Adicionar um novo comando](#adicionar-um-novo-comando) abaixo). Linhas que não forem comandos registrados na CLI ainda podem ser enviadas como texto bruto após confirmação.
3. Mensagens do dispositivo aparecerão automaticamente
4. Digite `quit`, `exit` ou `close` para desconectar

## Adicionar um novo comando

A análise e o despacho ficam em `src/commands/command_handlers.py` e `src/protocol_utils.py`. Você registra handlers que recebem uma invocação **digesta** e acesso BLE opcional.

**Comandos embutidos** em `src/commands/**` usam **`@cli_command`** e são **importados automaticamente** a partir de qualquer arquivo chamado `*_handlers.py` (veja `_load_command_handler_modules` em `command_handlers`). Você só precisa editar **`command_handlers.py`** para reexportar helpers BLE usados pelo **`main.py`**, e alterar **`src/main.py`** se o dispositivo enviar um **protocolo de resposta** (como `help_res` / `param_list_res`) que exija buffer e sessão de coleta.

### Lista: embutido vs `main.py`

#### Novo handler embutido (`src/commands/**/<nome>_handlers.py`)

| Etapa | O que fazer |
| ----- | ----------- |
| 1     | Crie um módulo cujo nome termine em **`_handlers.py`** (ex.: `src/commands/motion_handlers.py` ou `src/commands/param/foo_handlers.py`). Ele é importado automaticamente ao carregar `commands.command_handlers`. |
| 2     | Decore o handler assíncrono com **`@cli_command`** (nome inferido de `cmd_foo` → `foo`) ou **`@cli_command("nome_explicito")`**. Ainda pode usar **`register_cli_command`** manualmente. |
| 3     | Se o `main.py` precisar de **`capture_*_from_ble`** / **`try_feed_*_session`**, adicione imports e entradas em **`__all__`** em **`command_handlers.py`** (mesmo bloco dos helpers de help/param). |
| 4     | Conecte **`ble_dispatch_line`** no **`main.py`**: primeiro `capture_*`, depois `try_feed_*` com `return` antecipado, por fim `handle_incoming_message`—mesma ordem de `help` / `param_list`. |

#### Módulo fora de `src/commands/` (carregamento manual)

| Etapa | O que fazer |
| ----- | ----------- |
| 1     | Use **`@cli_command`** / **`@incoming_command`** (ou **`register_*`**) no seu módulo. |
| 2     | No **final** de **`command_handlers.py`**: **`import my_robot_commands  # noqa: F401`** para executar o registro na importação. |

#### `src/main.py` (somente para respostas BLE em lista/stream)

| Etapa | Onde                                                        | O que fazer                                                                                                                                                                         |
| ----- | ----------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| 1     | **Imports** de `commands.command_handlers`                  | Incluir `capture_*_from_ble` e `try_feed_*_session` (é preciso exportá-los antes em `command_handlers.py`).                                                                         |
| 2     | **`ble_dispatch_line`** (dentro de `main()`, após conectar) | Chamar cada `capture_*_from_ble(message)`, depois cada `if try_feed_*_session(message): return`, e por fim `handle_incoming_message(message)`—mesma ordem de `help` / `param_list`. |

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

Importe tipos e decoradores de `commands.command_handlers`:

```python
from commands.command_handlers import CommandInvocation, NusPort, cli_command
```

### Passo a passo: comando na CLI

**Embutido (árvore `src/commands/`):**

1. Adicione **`src/commands/my_motion_handlers.py`** (o nome deve terminar em **`_handlers.py`**).
2. Registre com decorador (nome padrão: `cmd_set_speed` → `set_speed`):

   ```python
   from __future__ import annotations

   from commands.command_handlers import CommandInvocation, NusPort, cli_command


   @cli_command  # ou @cli_command("set_speed")
   async def cmd_set_speed(inv: CommandInvocation, nus: NusPort) -> None:
       if len(inv.params) != 1:
           return
       speed = inv.params[0]
       await nus.send_message(f"set_speed({speed})")
   ```

3. **Execute** `uv run src/main.py` e digite `set_speed(42)`.

**Módulo fora da árvore `src/commands/`:** coloque o mesmo handler em, por exemplo, `src/my_robot_commands.py` e adicione **`import my_robot_commands  # noqa: F401`** no final de **`command_handlers.py`**.

Comandos registrados na CLI rodam **localmente** no app; use `nus.send_message(...)` quando o firmware deve receber uma string.

### Plugin externo: comandos recebidos do dispositivo (BLE)

**Plugin externo** (neste projeto) são linhas no formato `nome_do_comando(...)`, **enviadas pelo dispositivo externo (robô) e recebidas pelo computador que roda o terminal** via BLE — em oposição aos comandos **digitados nesse terminal**, cobertos na seção anterior com **`@cli_command`**.

Se o firmware envia esse formato e você quer tratamento no lado Python (logs, efeitos colaterais), registre um handler **síncrono** com **`@incoming_command`** (ou **`register_incoming_command`**). Para **`_incoming_status`**, o nome registrado é **`status`** (o prefixo **`_incoming_`** é removido).

```python
from commands.command_handlers import CommandInvocation, incoming_command


@incoming_command  # ou @incoming_command("status")
def _incoming_status(inv: CommandInvocation) -> None:
    print("Status do dispositivo:", inv.params)
```

Para módulos fora de `src/commands/**`, carregue o módulo no final de `command_handlers.py` como nos comandos da CLI.

### Helpers de parsing reutilizáveis

Para outros formatos no fio (não a invocação principal da CLI), use `src/protocol_utils.py`, por exemplo `split_top_level_commas` e `unquote_field`. Você também pode importar isso via `commands.command_handlers` (veja `__all__` lá).

### Comandos embutidos

Os comandos embutidos usam **`@cli_command`** (ou `register_cli_command`). Handlers ficam em arquivos `*_handlers.py` sob `src/commands/` (por exemplo `help_handlers.py`, `param/param_list_handlers.py`); comandos curtos como `echo` / `ping` ficam inline em `command_handlers.py`.

#### Scripts

- Verificação rápida do registro (sem BLE):

```bash
$ uv run scripts/list_registered_commands.py
```

- Teste rápido do cliente BLE Nordic UART:

```bash
$ uv run src/test_nus.py
```

**Modelo mínimo** para comando só de envio ao BLE: copie `src/commands/param/param_set_handlers.py` (reenvia `inv.line`, sem parsing de resposta).

### Executar Jupyter Notebook

https://docs.astral.sh/uv/guides/integration/jupyter/

```bash
$ uv run --with jupyter jupyter lab
```
