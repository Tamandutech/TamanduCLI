"""Extra :class:`~prompt_toolkit.key_binding.KeyBindings` for the BLE CLI prompt."""

from __future__ import annotations

from prompt_toolkit.application.current import get_app
from prompt_toolkit.enums import DEFAULT_BUFFER
from prompt_toolkit.filters import (
    Condition,
    emacs_insert_mode,
    has_focus,
    vi_insert_mode,
)
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.key_binding.key_processor import KeyPressEvent

__all__ = ["build_cli_input_key_bindings"]


@Condition
def _not_quoted_insert() -> bool:
    return not get_app().quoted_insert


def build_cli_input_key_bindings() -> KeyBindings:
    kb = KeyBindings()
    insert_mode = emacs_insert_mode | vi_insert_mode
    default_buf_insert = has_focus(DEFAULT_BUFFER) & insert_mode & _not_quoted_insert

    @kb.add("(", filter=default_buf_insert)
    def _auto_close_paren(event: KeyPressEvent) -> None:
        buf = event.current_buffer
        buf.insert_text("()")
        buf.cursor_position -= 1

    @kb.add(")", filter=default_buf_insert)
    def _skip_or_insert_close_paren(event: KeyPressEvent) -> None:
        buf = event.current_buffer
        if buf.document.current_char == ")":
            buf.cursor_position += 1
        else:
            buf.insert_text(")")

    @kb.add('"', filter=default_buf_insert)
    def _pair_or_skip_double_quote(event: KeyPressEvent) -> None:
        buf = event.current_buffer
        if buf.document.current_char == '"':
            buf.cursor_position += 1
        else:
            buf.insert_text('""')
            buf.cursor_position -= 1

    return kb
