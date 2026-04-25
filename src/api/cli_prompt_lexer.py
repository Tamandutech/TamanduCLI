"""
Syntax highlighting for the BLE CLI prompt (prompt_toolkit).

Highlights registered commands, exit keywords (quit/exit/close), strings, numbers, and punctuation.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Iterator

from prompt_toolkit.document import Document
from prompt_toolkit.lexers import Lexer
from prompt_toolkit.styles import Style

__all__ = ["CLI_PROMPT_STYLE", "WireCliLexer"]

CLI_PROMPT_STYLE = Style.from_dict(
    {
        "": "",
        "cli-command": "ansibrightcyan",
        "cli-exit": "bold ansibrightred",
        "cli-punct": "#ffffff",
        "cli-number": "ansibrightmagenta",
        "cli-string": "ansibrightyellow",
    }
)

_STYLE_CMD = "class:cli-command"
_STYLE_EXIT = "class:cli-exit"
_STYLE_PUNCT = "class:cli-punct"
_STYLE_NUM = "class:cli-number"
_STYLE_STR = "class:cli-string"
_EXIT_WORDS_LOWER = frozenset({"quit", "exit", "close"})
_NAME_RE = re.compile(r"[a-zA-Z_][a-zA-Z0-9_]*")


def _merge_runs(parts: Iterable[tuple[str, str]]) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    cur_style: str | None = None
    buf: list[str] = []
    for style, text in parts:
        if not text:
            continue
        if style == cur_style:
            buf.append(text)
        else:
            if buf:
                out.append((cur_style or "", "".join(buf)))
            cur_style = style
            buf = [text]
    if buf:
        out.append((cur_style or "", "".join(buf)))
    return out


class WireCliLexer(Lexer):
    """Lexer for wire / shorthand CLI lines (see :data:`api.command_handlers.CLI_COMMAND_HANDLERS`)."""

    def __init__(self, registered_names_lower: frozenset[str]) -> None:
        self._registered = registered_names_lower

    def lex_document(self, document: Document):
        def get_line(lineno: int) -> list[tuple[str, str]]:
            if lineno >= document.line_count:
                return []
            return _merge_runs(_lex_line(document.lines[lineno], self._registered))

        return get_line


def _lex_line(line: str, registered: frozenset[str]) -> Iterator[tuple[str, str]]:
    """Yield (style, text) fragments for one line."""
    i = 0
    depth = 0
    in_q: str | None = None
    q_esc = False
    at_seg_start = True

    while i < len(line):
        ch = line[i]

        if in_q is not None:
            start = i
            if q_esc:
                q_esc = False
                i += 1
                yield (_STYLE_STR, line[start:i])
                continue
            if ch == "\\" and in_q == '"':
                q_esc = True
                i += 1
                yield (_STYLE_STR, line[start:i])
                continue
            if ch == "\\" and in_q == "'":
                q_esc = True
                i += 1
                yield (_STYLE_STR, line[start:i])
                continue
            if ch == in_q:
                i += 1
                yield (_STYLE_STR, line[start:i])
                in_q = None
                continue
            i += 1
            yield (_STYLE_STR, line[start:i])
            continue

        if ch in "\"'":
            in_q = ch
            i += 1
            yield (_STYLE_STR, ch)
            continue

        if ch == ";" and depth == 0:
            i += 1
            yield (_STYLE_PUNCT, ch)
            at_seg_start = True
            continue

        if ch in "()":
            i += 1
            yield (_STYLE_PUNCT, ch)
            if ch == "(":
                depth += 1
            else:
                depth = max(0, depth - 1)
            continue

        if ch == ",":
            i += 1
            yield (_STYLE_PUNCT, ch)
            continue

        if depth >= 1 and (
            ch.isdigit() or (ch == "-" and i + 1 < len(line) and line[i + 1].isdigit())
        ):
            start = i
            if ch == "-":
                i += 1
            while i < len(line) and line[i].isdigit():
                i += 1
            yield (_STYLE_NUM, line[start:i])
            continue

        if depth == 0 and at_seg_start and (ch.isalpha() or ch == "_"):
            m = _NAME_RE.match(line, i)
            if m:
                name = m.group(0)
                i = m.end()
                low = name.lower()
                if low in _EXIT_WORDS_LOWER:
                    style = _STYLE_EXIT
                elif low in registered:
                    style = _STYLE_CMD
                else:
                    style = ""
                yield (style, name)
                at_seg_start = False
                continue

        if ch.isspace():
            if depth == 0:
                j = i
                while j < len(line) and line[j].isspace():
                    j += 1
                yield ("", line[i:j])
                i = j
                continue
            i += 1
            yield ("", ch)
            continue

        if depth == 0 and at_seg_start:
            at_seg_start = False

        i += 1
        yield ("", ch)
