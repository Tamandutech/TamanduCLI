"""
Reusable parsing helpers for wire/CLI text (comma splitting, quoting, command lines).

Import from here in custom command modules — no need to touch main.py parsing.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

_INVOCATION_NAME_RE = re.compile(r"^([a-zA-Z_][a-zA-Z0-9_]*)\s*\(")


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


def digest_invocation_parameters(inner: str) -> tuple[str, ...]:
    """Split the inside of command(...) on top-level commas and unquote each segment."""
    inner = inner.strip()
    if not inner:
        return ()
    return tuple(unquote_field(p) for p in split_top_level_commas(inner))


def parse_command_invocation(line: str) -> tuple[str, str] | None:
    """
    Parse command_name(param1, param2, ...).
    Returns (name_lower, inner_arguments_text) or None if the line does not match.
    """
    stripped = line.strip()
    if not stripped:
        return None
    m = _INVOCATION_NAME_RE.match(stripped)
    if not m:
        return None
    name = m.group(1).lower()
    open_idx = m.end() - 1
    depth = 0
    i = open_idx
    while i < len(stripped):
        ch = stripped[i]
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
            if depth == 0:
                if stripped[i + 1 :].strip():
                    return None
                return name, stripped[open_idx + 1 : i]
        i += 1
    return None


@dataclass(frozen=True)
class CommandInvocation:
    """
    Digested command line for handlers.

    - name: first token, lowercased (e.g. "help").
    - raw_arguments: verbatim text inside the outer parentheses.
    - params: top-level comma arguments after unquoting (all strings).
    - line: full stripped line (safe to send to the device as-is).
    """

    name: str
    raw_arguments: str
    params: tuple[str, ...]
    line: str


def parse_command_line(line: str) -> CommandInvocation | None:
    """Parse a full line into a CommandInvocation, or None if the format is invalid."""
    stripped = line.strip()
    if not stripped:
        return None
    base = parse_command_invocation(stripped)
    if base is None:
        return None
    name, inner = base
    params = digest_invocation_parameters(inner)
    return CommandInvocation(
        name=name,
        raw_arguments=inner,
        params=params,
        line=stripped,
    )
