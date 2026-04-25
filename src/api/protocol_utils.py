"""
Wire protocol: semicolon-separated commands.

Each command is ``name(mode,req_or_resp,...)`` where ``mode`` is ``s`` (single), ``h`` (list header),
or ``b`` (list body); ``req_or_resp`` is ``r`` (request) or ``s`` (response).

In-memory representation: :class:`WireCommand`.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal, Optional, Sequence

_KIND_FROM_MODE = {"s": "single", "h": "list_header", "b": "list_body"}
_MODE_FROM_KIND: dict[str, str] = {v: k for k, v in _KIND_FROM_MODE.items()}
_NAME_RE = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_]*$")

WireKind = Literal["single", "list_header", "list_body"]


@dataclass(frozen=True)
class WireCommand:
    """Parsed or built command for the device wire protocol."""

    name: str
    kind: WireKind
    is_response: bool
    index: int
    arguments: tuple[str, ...]

    @staticmethod
    def single_request(name: str, arguments: Sequence[str] = ()) -> WireCommand:
        return WireCommand(
            name=name,
            kind="single",
            is_response=False,
            index=0,
            arguments=tuple(arguments),
        )

    @staticmethod
    def single_response(name: str, arguments: Sequence[str] = ()) -> WireCommand:
        return WireCommand(
            name=name,
            kind="single",
            is_response=True,
            index=0,
            arguments=tuple(arguments),
        )


def split_top_level_commas(s: str) -> list[str]:
    """
    Split on commas that are not inside double-quoted strings.
    Parentheses depth is tracked so nested () inside unquoted regions is respected.
    """
    parts: list[str] = []
    i = 0
    n = len(s)
    start = 0
    in_quotes = False
    depth = 0
    while i < n:
        c = s[i]
        if in_quotes:
            if c == "\\" and i + 1 < n:
                i += 2
                continue
            if c == '"':
                in_quotes = False
            i += 1
            continue
        if c == '"':
            in_quotes = True
            i += 1
            continue
        if c == "(":
            depth += 1
        elif c == ")":
            depth -= 1
        elif c == "," and depth == 0:
            parts.append(s[start:i].strip())
            start = i + 1
        i += 1
    parts.append(s[start:].strip())
    return parts


def unquote_field(token: str) -> str:
    """Strip one pair of surrounding double quotes and unescape \\\" and \\\\."""
    t = token.strip()
    if len(t) >= 2 and t[0] == '"' and t[-1] == '"':
        inner = t[1:-1]
        return inner.replace('\\"', '"').replace("\\\\", "\\")
    return t


def _wire_needs_quotes(tok: str) -> bool:
    if not tok:
        return True
    if re.fullmatch(r"[a-zA-Z_][a-zA-Z0-9_]*", tok):
        return False
    if re.fullmatch(r"-?\d+", tok):
        return False
    return True


def format_wire_token(tok: str) -> str:
    if not _wire_needs_quotes(tok):
        return tok
    esc = tok.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{esc}"'


def format_wire_command(cmd: WireCommand) -> str:
    sm = _MODE_FROM_KIND[cmd.kind]
    rs = "s" if cmd.is_response else "r"
    if cmd.kind == "list_body":
        bits = [sm, rs, str(cmd.index)] + [format_wire_token(a) for a in cmd.arguments]
    else:
        bits = [sm, rs] + [format_wire_token(a) for a in cmd.arguments]
    inner = ",".join(bits)
    return f"{cmd.name}({inner})"


def format_message(commands: Sequence[WireCommand]) -> str:
    return ";".join(format_wire_command(c) for c in commands) + ";"


def split_wire_message_commands(s: str) -> list[str]:
    """Split a BLE/CLI message on top-level ``;`` (outside parentheses and quotes)."""
    s = s.strip()
    if not s:
        return []
    out: list[str] = []
    buf: list[str] = []
    depth = 0
    in_q = False
    esc = False
    for ch in s:
        if in_q:
            buf.append(ch)
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_q = False
            continue
        if ch == '"':
            in_q = True
            buf.append(ch)
            continue
        if ch == "(":
            depth += 1
            buf.append(ch)
        elif ch == ")":
            depth -= 1
            buf.append(ch)
        elif ch == ";" and depth == 0:
            piece = "".join(buf).strip()
            if piece:
                out.append(piece)
            buf = []
        else:
            buf.append(ch)
    piece = "".join(buf).strip()
    if piece:
        out.append(piece)
    return out


def _valid_command_name(name: str) -> bool:
    return bool(_NAME_RE.fullmatch(name))


def _extract_name_and_paren_body(segment: str) -> Optional[tuple[str, str]]:
    seg = segment.strip().rstrip(";").strip()
    lp = seg.find("(")
    if lp < 0:
        return None
    name = seg[:lp].strip()
    if not _valid_command_name(name):
        return None
    depth = 0
    in_q = False
    esc = False
    for j in range(lp, len(seg)):
        ch = seg[j]
        if in_q:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_q = False
            continue
        if ch == '"':
            in_q = True
        elif ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
            if depth == 0:
                inner = seg[lp + 1 : j]
                return name, inner
    return None


def parse_wire_command_segment(segment: str) -> Optional[WireCommand]:
    pair = _extract_name_and_paren_body(segment)
    if pair is None:
        return None
    name, inner = pair
    parts = split_top_level_commas(inner)
    if len(parts) < 2:
        return None
    mode_t = parts[0].strip().lower()
    rs_t = parts[1].strip().lower()
    if len(mode_t) != 1 or mode_t not in _KIND_FROM_MODE:
        return None
    if len(rs_t) != 1 or rs_t not in ("r", "s"):
        return None
    kind = _KIND_FROM_MODE[mode_t]
    is_response = rs_t == "s"
    rest = parts[2:]

    if kind == "list_body":
        if not rest:
            return None
        try:
            idx = int(rest[0].strip())
        except ValueError:
            return None
        args = tuple(rest[1:])
        nm = name.strip()
        return WireCommand(nm, kind, is_response, idx, args)
    if kind == "list_header":
        return WireCommand(name.strip(), kind, is_response, 0, tuple(rest))
    return WireCommand(name.strip(), kind, is_response, 0, tuple(rest))


def parse_message(message: str) -> list[WireCommand]:
    """Parse a full message (possibly multiple commands) into :class:`WireCommand` instances."""
    cmds: list[WireCommand] = []
    for seg in split_wire_message_commands(message):
        c = parse_wire_command_segment(seg)
        if c is not None:
            cmds.append(c)
    return cmds


def message_byte_length(message: str) -> int:
    return len(message.encode("utf-8"))


def batch_wire_messages(
    commands: Sequence[WireCommand],
    max_bytes: int = 256,
    inter_message_suffix: str = ";",
) -> list[str]:
    """
    Pack commands into UTF-8 messages not exceeding ``max_bytes`` each.
    Each chunk ends with ``;`` as required on the wire.
    """
    if not commands:
        return []
    chunks: list[str] = []
    current: list[WireCommand] = []
    current_len = 0

    def flush() -> None:
        nonlocal current, current_len
        if current:
            chunks.append(format_message(current))
            current = []
            current_len = 0

    for cmd in commands:
        piece = format_wire_command(cmd) + inter_message_suffix
        blen = len(piece.encode("utf-8"))
        if blen > max_bytes:
            flush()
            chunks.append(piece)
            continue
        trial = format_message([*current, cmd]) if current else piece
        if len(trial.encode("utf-8")) <= max_bytes:
            current.append(cmd)
            current_len = len(trial.encode("utf-8"))
        else:
            flush()
            current = [cmd]
            current_len = len(piece.encode("utf-8"))
    flush()
    return chunks


def _looks_like_wire_protocol(line: str) -> bool:
    s = line.strip()
    if ";" in s:
        return True
    pair = _extract_name_and_paren_body(s)
    if pair is None:
        return False
    inner = pair[1]
    parts = split_top_level_commas(inner)
    if len(parts) < 2:
        return False
    m0 = parts[0].strip().lower()
    m1 = parts[1].strip().lower()
    return len(m0) == 1 and m0 in _KIND_FROM_MODE and len(m1) == 1 and m1 in ("r", "s")


def normalize_cli_input(line: str, registered_names: set[str]) -> str:
    """
    If ``line`` is already wire-shaped, return it unchanged (trimmed).
    If it is ``name`` or ``name(...)`` for a registered command, expand to a single
    ``name(s,r,...);`` wire message.
    """
    s = line.strip()
    if not s:
        return s
    low_reg = {n.lower() for n in registered_names}
    if _looks_like_wire_protocol(s):
        return s
    m = re.match(r"^([a-zA-Z_][a-zA-Z0-9_]*)\s*$", s)
    if m and m.group(1).lower() in low_reg:
        return format_message([WireCommand.single_request(m.group(1).lower(), ())])
    m = re.match(r"^([a-zA-Z_][a-zA-Z0-9_]*)\s*\(\s*(.*?)\s*\)\s*$", s, re.DOTALL)
    if m and m.group(1).lower() in low_reg:
        name = m.group(1).lower()
        inner = m.group(2).strip()
        if not inner:
            return format_message([WireCommand.single_request(name, ())])
        args = tuple(split_top_level_commas(inner))
        return format_message([WireCommand.single_request(name, args)])
    return s


def parse_first_command_name(line: str, registered_names: set[str]) -> Optional[str]:
    """Best-effort command name for routing (first wire command or shorthand)."""
    s = normalize_cli_input(line, registered_names)
    cmds = parse_message(s)
    if cmds:
        return cmds[0].name
    pair = _extract_name_and_paren_body(line.strip())
    if pair:
        return pair[0].lower()
    m = re.match(r"^([a-zA-Z_][a-zA-Z0-9_]*)", line.strip())
    return m.group(1).lower() if m else None


def is_command_invocation(line: str, registered_names: set[str]) -> bool:
    s = line.strip()
    if not s:
        return False
    if _looks_like_wire_protocol(s):
        return True
    m = re.match(r"^([a-zA-Z_][a-zA-Z0-9_]*)\s*$", s)
    if m and m.group(1).lower() in {n.lower() for n in registered_names}:
        return True
    m = re.match(r"^([a-zA-Z_][a-zA-Z0-9_]*)\s*\(", s)
    return bool(m and m.group(1).lower() in {n.lower() for n in registered_names})


# --- Backwards-friendly aliases used by older call sites ---
def parse_command_message(message: str) -> list[WireCommand]:
    """Alias for :func:`parse_message`."""
    return parse_message(message)


def parse_command_line(line: str) -> Optional[WireCommand]:
    """Return the first wire command on the line, if any."""
    cmds = parse_message(line)
    return cmds[0] if cmds else parse_wire_command_segment(line.strip())


def parse_command_invocation(line: str) -> Optional[WireCommand]:
    """True if the line contains at least one parsable wire command."""
    return parse_command_line(line)


def digest_invocation_parameters(cmd: WireCommand, expected_names: Sequence[str]) -> dict[str, str]:
    """Map the first N string arguments by position to ``expected_names`` (unquoted values)."""
    out: dict[str, str] = {}
    for i, key in enumerate(expected_names):
        if i >= len(cmd.arguments):
            break
        out[key] = unquote_field(cmd.arguments[i])
    return out
