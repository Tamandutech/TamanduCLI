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
2. Digite comandos no **protocolo wire** (`nome(s,r,...);` — ver [Protocolo de mensagem wire](#protocolo-de-mensagem-wire)) ou use o **atalho** para comandos registrados na CLI (`help`, `help()`, etc.). Linhas não reconhecidas podem ser enviadas como texto bruto após confirmação.
3. Mensagens do dispositivo aparecerão automaticamente
4. Digite `quit`, `exit` ou `close` para desconectar

## Adicionar um novo comando

O registro e o despacho ficam em **`src/api/command_handlers.py`**. O protocolo de texto (parse/format, mensagens com `;`) está em **`src/api/protocol_utils.py`**. Os handlers em **`src/commands/**`** só **consomem** essa API.

**Plugins de comando na CLI** — módulos **`_handlers.py`** em **`src/commands/`** (os seus ou os de exemplo do repositório) — usam **`@cli_command`** e são **carregados automaticamente** na importação (ver `_load_command_handler_modules` em `api/command_handlers`). O **`main.py`** já usa **`dispatch_ble_notification`** do pacote `api` para BLE; para fluxos com buffer/sessão, registre hooks com **`@register_ble_capture`** / **`@register_ble_try_feed`** no módulo do plugin — **sem** editar `src/api/command_handlers.py` por recurso novo.

### Lista: plugins em `src/commands/` vs import manual vs `main.py`

#### Novo módulo plugin (`src/commands/**/<nome>_handlers.py`)

| Etapa | O que fazer |
| ----- | ----------- |
| 1     | Crie um módulo cujo nome termine em **`_handlers.py`** (ex.: `src/commands/motion_handlers.py` ou `src/commands/param_list_edit_handlers.py`). Ele é importado automaticamente ao carregar `api.command_handlers`. |
| 2     | Decore o handler assíncrono com **`@cli_command`** (nome inferido de `cmd_foo` → `foo`) ou **`@cli_command("nome_explicito")`**. Ainda pode usar **`register_cli_command`** manualmente. |
| 3     | Para buffer/sessão BLE em lista: decore com **`@register_ble_capture`** (``def f(message: str) -> None``) e/ou **`@register_ble_try_feed`** (``def f(message: str) -> bool``). A ordem é a do carregamento dos ``*_handlers.py``; não é preciso editar **`src/api/command_handlers.py`**. |
| 4     | O **`main.py`** chama só **`dispatch_ble_notification(message, router)`** do pacote **`api`** — ele executa todos os captures, depois try-feeds até um retornar ``True``, depois os **`@incoming_command`**. |

#### Módulo fora de `src/commands/` (carregamento manual)

| Etapa | O que fazer |
| ----- | ----------- |
| 1     | Use **`@cli_command`** / **`@incoming_command`** (ou **`register_*`**) no seu módulo. |
| 2     | No **final** de **`src/api/command_handlers.py`**: **`import my_robot_commands  # noqa: F401`** para executar o registro na importação. |

#### `src/main.py` (respostas BLE em lista/stream)

| Etapa | Onde | O que fazer |
| ----- | ---- | ----------- |
| 1     | Não é necessário | Novos captures/feeds são registrados nos módulos ``commands`` com **`@register_ble_capture`** / **`@register_ble_try_feed`**. |
| 2     | **`dispatch_ble_notification`** | O **`main.py`** já encaminha cada linha BLE para **`api.command_handlers.dispatch_ble_notification`**. |

### Protocolo de mensagem wire

O tráfego com o dispositivo segue **comandos em texto** no estilo abaixo (não JSON). Uma **mensagem** pode conter **vários comandos** separados por **`;`** (o separador não conta dentro de parênteses nem dentro de strings entre aspas).

Cada comando tem a forma:

```text
nome(modo, req_ou_resp, ...argumentos)
```

| Posição após `nome(` | Significado | Valores |
| -------------------- | ------------ | ------- |
| 1º parâmetro (`modo`) | Tipo de comando | **`s`** = *single* (comando único), **`h`** = *header* de lista, **`b`** = *body* (linha da lista) |
| 2º parâmetro (`req_ou_resp`) | Papel da mensagem | **`r`** = requisição, **`s`** = resposta |

Exemplos:

```text
help(s,r);
param_list(h,s,5);
param_list(b,s,1,"param_get","ref","read a parameter");
map_get(b,s,0,1,2,3,4,5);
```

- **Single** (`s`): após `r` ou `s` vêm os argumentos do comando, se houver.
- **Lista** (`h` / `b`): no **`h`**, após `r`/`s` costuma vir o tamanho da lista; no **`b`**, após `r`/`s` vem o **índice** da linha na lista e em seguida os argumentos daquela linha.
- O firmware costuma limitar o tamanho de cada mensagem (ex.: **256 bytes** no NUS); listas longas são fatiadas em várias mensagens.

Na **CLI**, comandos **já registrados** aceitam **atalho**: digitar só `help` ou `help()` é expandido para `help(s,r);` antes do envio. Para o restante, use a mensagem **wire** completa ou confirme envio como texto bruto.

### O que o handler da CLI recebe

Handlers **`@cli_command`** são **assíncronos** e recebem:

- **`inv: WireCommand`** — `name`, `kind` (`"single"` \| `"list_header"` \| `"list_body"`), `is_response`, `index` (índice em corpos de lista), `arguments` (tupla de strings como na mensagem **wire**).
- **`ctx: CliHandlerContext`** — `nus` (envio BLE), `incoming` (buffer de comandos já parseados), `prompt_line` / `log` (Prompt Toolkit / terminal), e **`await ctx.send_wire(text)`** (garante `;` no fim da mensagem quando fizer sentido).

Importe de `api.command_handlers` (com `src` no `PYTHONPATH`, como em `uv run src/main.py`):

```python
from api.command_handlers import CliHandlerContext, WireCommand, cli_command
```

### Quando usar `register_ble_capture`, `register_ble_try_feed` e uma classe de sessão

**Não** é obrigatório declarar isso em todo script — só quando o fluxo BLE exige esse modelo.

| Recurso | Para que serve | Quando **precisa** |
| ------- | ---------------- | ------------------- |
| **`@register_ble_capture`** | Função `def f(message: str) -> None` que roda **antes** do parse em **toda** notificação; costuma **guardar** linhas em um buffer. | Quando respostas podem chegar **antes** o usuário disparar o comando de coleta, e você quer **reproduzir** essas linhas ao abrir a sessão (ex.: `help`, `param_list`). |
| **`@register_ble_try_feed`** | Função `def f(message: str) -> bool`; se retornar **`True`**, o despacho **para** ali (não passa para `@incoming_command` genérico naquela notificação). | Quando há **sessão ativa** consumindo um fluxo de várias linhas até completar ou dar timeout. |
| **Classe tipo `*CollectionSession`** | Estado + `asyncio.Event` + escrita em ficheiro. | Só para fluxos **multi-notificação** com regra de “completo / parcial”. Comandos que enviam uma coisa e esperam **uma** resposta podem usar só `ctx.incoming.wait_for(...)` dentro do handler, **sem** capture/try_feed nem classe de sessão. |

Para um comando simples (“pedir → esperar resposta → mostrar”), em geral basta **`@cli_command`** e a API de **`ctx`** / **`incoming`**.

### Passo a passo: comando na CLI

**Plugin em `src/commands/`:**

1. Adicione **`src/commands/my_motion_handlers.py`** (o nome deve terminar em **`_handlers.py`**).
2. Registre com decorador (nome padrão: `cmd_set_speed` → `set_speed`):

   ```python
   from __future__ import annotations

   from api.command_handlers import CliHandlerContext, WireCommand, cli_command
   from api.protocol_utils import format_message


   @cli_command  # ou @cli_command("set_speed")
   async def cmd_set_speed(inv: WireCommand, ctx: CliHandlerContext) -> None:
       arg = inv.arguments[0] if inv.arguments else "42"
       await ctx.send_wire(format_message([WireCommand.single_request("set_speed", (arg,))]))
   ```

3. **Execute** `uv run src/main.py` e digite `set_speed(42)` ou o equivalente **wire** `set_speed(s,r,42);`.

**Módulo fora da árvore `src/commands/`:** coloque o handler em, por exemplo, `src/my_robot_commands.py` e adicione **`import my_robot_commands  # noqa: F401`** no final de **`src/api/command_handlers.py`**.

### Plugin externo: comandos recebidos do dispositivo (BLE)

São mensagens no **mesmo protocolo wire** (parseadas para **`WireCommand`**), **enviadas pelo robô** e recebidas no PC via BLE — em oposição ao que o utilizador **digita** no terminal (`@cli_command`).

Para reagir no Python (logs, efeitos colaterais), registe um handler **síncrono** com **`@incoming_command`** (ou **`register_incoming_command`**). O nome do registo vem de **`_incoming_foo` → `foo`** ou de **`@incoming_command("nome")`**. O handler recebe um **`WireCommand`** já parseado.

```python
from api.command_handlers import WireCommand, incoming_command


@incoming_command  # ou @incoming_command("status")
def _incoming_status(cmd: WireCommand) -> None:
    print("Comando do dispositivo:", cmd.name, cmd.arguments)
```

Para módulos fora de `src/commands/**`, importe o módulo no final de **`src/api/command_handlers.py`** como nos comandos da CLI.

### Helpers de parsing reutilizáveis

Para **wire** e argumentos (vírgulas de nível superior, aspas, etc.), use **`src/api/protocol_utils.py`** — por exemplo `parse_message`, `format_wire_command`, `split_top_level_commas`, `unquote_field`. Pode importá-los a partir de **`api.command_handlers`** (ver `__all__`) ou diretamente de **`api.protocol_utils`**.

### Plugins de comando (`src/commands/`)

Os handlers de plugin usam **`@cli_command`** (ou `register_cli_command`). Coloque-os em `*_handlers.py` sob `src/commands/` (por exemplo `help_handlers.py`, `param_list_edit_handlers.py`); exemplos mínimos **`echo` / `ping`** que vêm com o **núcleo** da API ficam em **`src/api/command_handlers.py`**.

#### Scripts

- Verificação rápida do registro (sem BLE):

```bash
$ uv run scripts/list_registered_commands.py
```

- Teste rápido do cliente BLE Nordic UART:

```bash
$ uv run src/test_nus.py
```

**Modelo mínimo** para comando só de envio ao BLE: veja `cmd_echo` / `cmd_ping` em **`src/api/command_handlers.py`** ou um handler simples em `*_handlers.py` que chame apenas **`await ctx.send_wire(...)`**.

### Executar Jupyter Notebook

https://docs.astral.sh/uv/guides/integration/jupyter/

```bash
$ uv run --with jupyter jupyter lab
```
