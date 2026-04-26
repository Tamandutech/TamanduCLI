# Wire protocol — firmware implementation reference

This document describes the **text wire protocol** as implemented in **TamanduCLI** (Python sources under `src/api/`). It is intended for engineers implementing the **IoT device / robot** side that speaks over **BLE Nordic UART Service (NUS)** with the same framing and semantics.

Encoding is **UTF-8** unless stated otherwise. Line endings may be `\n` or `\r\n`; the host splits on newlines and trims whitespace when ingesting BLE notifications.

---

## 1. Transport and message boundaries

### 1.1 BLE (host behavior)

- Each string passed to `send_wire` is sent as **one GATT write** to the NUS RX characteristic, UTF-8 encoded (`src/api/ble_nus.py`).
- The host **does not** fragment a single logical string across multiple writes for size; **your firmware** should accept the full UTF-8 payload of each write (or document MTU limits and negotiate accordingly).
- Default **packing budget** used when batching list sends is **256 UTF-8 bytes per string** (`DEFAULT_WIRE_MESSAGE_MAX_BYTES` in `src/api/protocol_utils.py`). Align firmware buffers with this expectation if possible.

### 1.2 Trailing semicolon

`CliHandlerContext.send_wire` ensures the outgoing string ends with **`;`** if it does not already (`src/api/command_handlers.py`).

### 1.3 Multiple commands in one string

A **message** is a string that may contain **several commands** separated by **`;`**.

Splitting is **only** at top-level semicolons:

- Semicolons **inside** `(...)` (respecting nesting depth) do **not** split.
- Semicolons **inside** double-quoted strings do **not** split (with `\"` and `\\` escapes handled).

Implementation: `split_wire_message_commands` in `src/api/protocol_utils.py`.

---

## 2. Command grammar

### 2.1 Shape

Every command has the form:

```text
name(mode,req_or_resp,…arguments…)
```

| Field | Allowed values | Meaning |
|--------|------------------|---------|
| `name` | `^[a-zA-Z_][a-zA-Z0-9_]*$` (case preserved on wire; host compares names case-insensitively in several paths) | Command identifier |
| `mode` | **`s`**, **`h`**, **`b`** | `s` = **single**, `h` = **list header**, `b` = **list body** |
| `req_or_resp` | **`r`**, **`s`** | `r` = **request** (host→device for outgoing), `s` = **response** (device→host for incoming) |

The host maps `(mode, req_or_resp)` into an internal `WireCommand` with:

- `kind`: `"single"` \| `"list_header"` \| `"list_body"`
- `is_response`: `True` if second letter is `s`, else `False`
- `index`: used only for **`list_body`** (integer row index, see below)
- `arguments`: tuple of **string tokens** exactly as split from the wire (still quoted where applicable)

### 2.2 Argument list and quoting

After `name(`, the payload is split on **top-level commas** with the same quoting/paren-depth rules as semicolon splitting (`split_top_level_commas`).

**Quoting rules (host output, `format_wire_token`):**

- Unquoted: empty string, identifiers `[a-zA-Z_][a-zA-Z0-9_]*`, or integers `-?[0-9]+`.
- Otherwise the token is emitted as a **double-quoted** string with escapes: `\` → `\\`, `"` → `\"`.

**Unquoting (host input, `unquote_field`):**

- If a token is wrapped in `"…"`, strip quotes and apply `\\` and `\"` unescaping.
- Otherwise return the trimmed token as-is.

---

## 3. Single commands (`s`)

Wire pattern:

```text
name(s,r,arg1,arg2,…);   ; request
name(s,s,arg1,arg2,…);   ; response
```

- There is **no** row index; all parameters are `arguments` on `WireCommand`.
- Examples used by the CLI: `map_clear(s,r);`, `map_SaveRuntime(s,r);`, `help(s,r);` (with optional extra args depending on handler).

---

## 4. List header (`h`)

Canonical wire shape (**always four comma-separated integers** after `h` and `r`/`s`):

```text
name(h,r,T,C,B,j);   ; request (batched list send)
name(h,s,T,C,B,j);   ; response (list download)
```

The host represents this as **`WireListHeader`** (`src/api/protocol_utils.py`): frozen fields **`total_row_count` (`T`)**, **`rows_in_this_message` (`C`)**, **`total_messages` (`B`)**, **`message_index` (`j`)**. **`WireListHeader.to_wire_command`** always emits all four arguments—there is **no** one-argument `h` form on transmit.

| Field | Wire | Meaning |
|-------|------|---------|
| `T` | 1st arg | Total **`list_body`** rows in the **whole** operation (valid indices **`1`…`T`**). |
| `C` | 2nd arg | Number of **`name(b,…)`** commands **in this same UTF-8 message** after this header. |
| `B` | 3rd arg | Number of **BLE messages** in this transfer; **`j` ∈ {0,…,`B`-1}**. |
| `j` | 4th arg | **This** message’s index among **`B`** (0-based). |

**Single-message list (entire list in one BLE write):** use **`(T, 1, 1, 0)`** — the CLI helper is **`WireListHeader.single_message(T)`** in code.

**Firmware:** implement **only** the four-integer header on the wire for new work.

**Host parse compatibility:** **`WireListHeader.from_wire_command`** still accepts a **single** integer argument (historical `name(h,s,N);`) and normalizes it to **`(N, 1, 1, 0)`** so old logs or transitional firmware keep parsing; new firmware should not emit the one-argument form.

**Requests:** the CLI uses **`h,r`** with the four-integer shape for batched **outbound** list sends (`pack_list_body_requests_into_batched_wire_messages`).

**Responses:** **`ListWireCollectionSession`** uses **`from_wire_command`** to read **`T`**. **Completion** (§7) is still “all **`list_body`** rows **`1`…`T`** received”; **`C`**, **`B`**, **`j`** are for framing, ordering, and progress on the wire.

---

## 5. List body (`b`)

Wire pattern:

```text
name(b,r,idx,arg1,arg2,…);   ; request
name(b,s,idx,arg1,arg2,…);   ; response
```

**Critical formatting rule (host parser):**

- The first token after `b` and `r`/`s` **must be an integer**: this becomes **`WireCommand.index`** (`idx`).
- All following tokens are **`arguments`** (tuple of strings).

So the wire always has **`idx` explicitly**, even if redundant with data inside `arg…`.

### 5.1 CLI usage examples (requests)

| Flow | Body shape |
|------|------------|
| **map_add** (from `map_edit_handlers`) | CSV row `index,time,encMedia,trackStatus,offset` → `map_add(b,r,<index>,time,encMedia,trackStatus,offset)` — first CSV column is **`idx`**, remaining four fields are arguments. |
| **param_set** (from `param_list_edit_handlers`) | `param_set(b,r,<paramIndex>,name,value)` — two string arguments after index. |

---

## 6. Batched outbound list sends (host → device)

Used for long sequences of **`list_body` requests** with the **same** `name`, e.g. many `map_add` or `param_set` rows.

### 6.1 Preconditions

- Every command in the batch must be **`list_body`**, **`r`** (request), same **`name`**, and `WireCommand.index` / `arguments` set as in §5.

### 6.2 One BLE message string

Format:

```text
name(h,r,T,C,B,j);name(b,r,idx1,…);name(b,r,idx2,…);…;
```

- Exactly **one** `list_header` as in §4, followed by **`C`** list bodies for that `name`.
- For the **k**-th packed message (`k` zero-based): **`j = k`**, **`B =`** total number of such strings, **`T =`** total number of bodies across **all** strings, **`C =`** number of bodies in **this** string.

### 6.3 Packing algorithm (host)

1. Let `T` = total number of `list_body` requests to send.
2. Greedily append bodies to the current chunk while the UTF-8 length of  
   `header + ";" + body1 + ";" + … + ";"` **+ final `;`** would stay **≤ `max_bytes`** (default **256**).
3. Length estimation uses a **pessimistic** header: `WireListHeader(T, len(chunk), T, max(0, T-1))` so real headers never exceed the budget (`_batched_wire_message_byte_length_pessimistic`).
4. If a **single** body does not fit even alone, the host still emits it as its own message (may exceed `max_bytes`).

Implementation: `pack_list_body_requests_into_batched_wire_messages` in `src/api/protocol_utils.py`.

### 6.4 Acknowledgment pacing (host expects device response)

When enabled (`max_messages_before_ack` ≥ 1 in `send_homogeneous_list_body_requests_batched`, `src/api/list_wire.py`):

1. The host sends up to **`N`** full BLE strings (each possibly containing `header + bodies` as above) **without** waiting.
2. It then waits (polling parsed incoming commands) for **exactly** this acknowledgment:

```text
name(s,s,lo,hi,ok_token);
```

- **`name`**: same command name as the list bodies (case-insensitive match on host).
- **`kind`**: **single** (`s` as first mode letter).
- **Second letter `s`**: **response**.
- **Arguments (at least three):**
  - `lo`, `hi`: inclusive integers — **indices of the batched BLE messages** just sent (`0`…`B-1` from §6.2), not row indices.
  - Third argument: compared case-insensitively after `unquote_field` to **`ok`** (quoted or unquoted, e.g. `"ok"`).

3. Default burst size **`5`** messages, default wait **`30`** seconds (`DEFAULT_LIST_BATCH_MESSAGES_BEFORE_ACK`, `DEFAULT_LIST_BATCH_ACK_TIMEOUT_SECONDS`).

If pacing is disabled (`max_messages_before_ack` is `None` or `< 1`), the host sends all packed strings back-to-back with **no** `name(s,s,lo,hi,…)` wait.

---

## 7. Batched / multi-notification list responses (device → host)

The host’s **`ListWireCollectionSession`** (`src/api/list_wire.py`) collects **`name(h,s,T,C,B,j)`** (or normalized single-arg parse) and **`name(b,s,…)`** for a fixed `name` until complete or timeout.

### 7.1 Completion rule

1. A valid **`list_header` response** sets expected total **`T`** (first field of the header, after `WireListHeader.from_wire_command`).
2. The session treats logical row **`0`** as the header record internally.
3. Collection is **complete** when rows **`1`, `2`, …, `T`** are all present: for each `list_body` response, row key is **`WireCommand.index`**, payload is derived from arguments (see §7.2).

**Timeout:** e.g. `param_list` / `help` use **3 seconds** (`wait_until_done`); partial data may still be written / logged.

### 7.2 Row payload for `help` / `param_list` style lists

For each **`list_body` response**:

- `idx` = `WireCommand.index`
- `name_col` = `unquote_field(arguments[0])`
- `value_col` = `", ".join(unquote_field(a) for a in arguments[1:])`  
  (comma+space between each remaining argument’s unquoted value)

Firmware emitting help/param-style tables should match this so saved files and diffs line up with the CLI.

### 7.3 File contents

- **`param_list`**: `record_raw_wire_lines=True` → file is the concatenation of **`format_wire_command`** for each fed command (preserves exact device quoting).
- **`help`**: `record_raw_wire_lines=False` → file is **re-serialized** from the internal row map using `list_header` / `list_body` **responses** (`h,s` / `b,s`) for a canonical layout.

---

## 8. Ingestion order on the host (BLE → parser)

For each notification string (`dispatch_ble_notification` in `src/api/command_handlers.py`):

1. BLE capture hooks (buffering).
2. `parse_message` → each `WireCommand` is appended to **`IncomingRouter`** (for `wait_for` / predicates).
3. BLE try-feed hooks; if one returns **`True`**, **no** `@incoming_command` handler runs for that notification.
4. Otherwise, per-command `@incoming_command` handlers run.

So list collection sessions fed via **`try_feed`** may consume the line before generic incoming handlers.

---

## 9. Reference tables

### 9.1 Mode / role matrix (abbreviated)

| Wire prefix | kind | is_response |
|-------------|------|----------------|
| `s,r` | single | False |
| `s,s` | single | True |
| `h,r` | list_header | False |
| `h,s` | list_header | True |
| `b,r` | list_body | False |
| `b,s` | list_body | True |

### 9.2 `WireListHeader` ↔ wire (`h`)

| Field | Wire arg | Notes |
|--------|----------|--------|
| `total_row_count` | `T` | Always present (first of four on the wire). |
| `rows_in_this_message` | `C` | Bodies in this message after this `h`. |
| `total_messages` | `B` | Total BLE messages in transfer. |
| `message_index` | `j` | This message’s index in `0..B-1`. |

### 9.3 Python reference map

| Topic | Primary source |
|--------|----------------|
| Split / parse / format | `src/api/protocol_utils.py` |
| `WireListHeader` | `src/api/protocol_utils.py` |
| List collection + ACK send pacing | `src/api/list_wire.py` |
| `send_wire` / handler context | `src/api/command_handlers.py` |
| BLE write | `src/api/ble_nus.py` |

---

## 10. Versioning

This document reflects the repository layout and logic at the time of writing. If behavior changes, prefer the cited source files as the authoritative specification.
