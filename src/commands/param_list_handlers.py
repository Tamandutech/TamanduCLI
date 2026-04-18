"""
BLE param_list flow: collect JSON ``{"data": "..."}`` lines.

The device may send one notification per logical line: a header ``Parameters: N``, then one
``{"data": "i - Name: value"}`` per parameter (or a single multiline ``data`` blob — both work).
"""

from __future__ import annotations

import asyncio
import json
import re
from collections import deque
from typing import TYPE_CHECKING, Optional

from output_paths import OUTPUT_DIR, ensure_output_dir

if TYPE_CHECKING:
    from commands.command_handlers import NusPort
    from protocol_utils import CommandInvocation

PARAM_LIST_WAIT_SECONDS = 3.0
PARAM_LIST_RESPONSE_PATH = OUTPUT_DIR / "param_list.txt"

_param_list_ble_recent: deque[str] = deque(maxlen=128)

_active_param_list_session: Optional["ParamListCollectionSession"] = None


def _terminal():
    import main as main_module

    return main_module.Terminal


def _sanitize_ble_payload(s: str) -> str:
    """Strip NUL/BOM and other noise embedded devices often append to NUS text."""
    return s.replace("\x00", "").replace("\ufeff", "").strip()


def _json_object_slices(s: str) -> list[str]:
    """
    Split ``s`` into top-level ``{...}`` substrings.

    Handles multiple objects concatenated on one line (``}{``) and leading junk
    before the first ``{``.
    """
    t = _sanitize_ble_payload(s)
    if not t:
        return []
    chunks: list[str] = []
    n = len(t)
    i = 0
    while i < n:
        j = t.find("{", i)
        if j < 0:
            break
        depth = 0
        in_string = False
        escape = False
        for k in range(j, n):
            ch = t[k]
            if in_string:
                if escape:
                    escape = False
                    continue
                if ch == "\\":
                    escape = True
                    continue
                if ch == '"':
                    in_string = False
                continue
            if ch == '"':
                in_string = True
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    chunks.append(t[j : k + 1])
                    i = k + 1
                    break
        else:
            break
    return chunks


def _iter_json_data_string_fields(message: str) -> list[str]:
    """Every string ``data`` field from JSON objects in ``message`` (one line or ``}{``)."""
    out: list[str] = []
    for raw_line in message.replace("\r\n", "\n").split("\n"):
        line = raw_line.strip()
        if not line:
            continue
        for chunk in _json_object_slices(line):
            try:
                obj = json.loads(chunk)
            except json.JSONDecodeError:
                continue
            if isinstance(obj, dict):
                v = obj.get("data")
                if isinstance(v, str) and v.strip():
                    out.append(v)
    return out


def _is_param_list_data_fragment(data: str) -> bool:
    """
    True when ``data`` is a param_list fragment: one line ``Parameters: N`` / ``i - Name: value``,
    or a multiline blob whose body includes a ``Parameters:`` header line.
    """
    s = data.strip()
    if not s:
        return False
    if "\n" in s:
        return bool(re.search(r"(?im)^\s*Parameters:\s*\d+\s*$", s))
    if re.match(r"^Parameters:\s*\d+\s*$", s, re.IGNORECASE):
        return True
    m = re.match(r"^\s*(\d+)\s*-\s*(.+)$", s)
    if not m:
        return False
    return ":" in m.group(2)


def iter_param_list_payloads_from_ble(message: str) -> list[str]:
    """Extract ``data`` strings from BLE JSON that belong to a param_list response."""
    return [s for s in _iter_json_data_string_fields(message) if _is_param_list_data_fragment(s)]


def parse_param_list_document(content: str) -> tuple[Optional[int], dict[int, tuple[str, str]], list[str]]:
    """
    Parse ``output/param_list.txt`` / device ``data`` body.

    - Header: ``Parameters: N`` (case-insensitive).
    - Rows: ``<index> - <name>: <value>`` (value may be empty; name should not contain a line break).
    """
    errors: list[str] = []
    expected: Optional[int] = None
    rows: dict[int, tuple[str, str]] = {}
    stripped = content.strip()
    if not stripped:
        return None, {}, ["  (empty document)"]

    for lineno, line in enumerate(content.splitlines(), start=1):
        raw = line.strip()
        if not raw:
            continue
        m_head = re.match(r"^Parameters:\s*(\d+)\s*$", raw, re.IGNORECASE)
        if m_head:
            expected = int(m_head.group(1))
            continue
        m = re.match(r"^\s*(\d+)\s*-\s*(.+)$", raw)
        if not m:
            errors.append(f"  line {lineno}: {raw[:120]!r}")
            continue
        try:
            idx = int(m.group(1))
        except ValueError:
            errors.append(f"  line {lineno}: {raw[:120]!r}")
            continue
        rest = m.group(2)
        if ":" not in rest:
            errors.append(f"  line {lineno}: missing ':' after parameter name: {raw[:120]!r}")
            continue
        c = rest.find(":")
        name = rest[:c].strip()
        value = rest[c + 1 :].lstrip()
        rows[idx] = (name, value)

    return expected, rows, errors


def _parse_param_list_single_line(raw: str) -> tuple[Optional[int], Optional[tuple[int, str, str]], Optional[str]]:
    """
    Parse one non-empty line (no embedded newlines).

    Returns ``(header_n, None, None)``, ``(None, (idx, name, value), None)``, or ``(None, None, err)``.
    """
    s = raw.strip()
    if not s:
        return None, None, "empty line"
    m_head = re.match(r"^Parameters:\s*(\d+)\s*$", s, re.IGNORECASE)
    if m_head:
        return int(m_head.group(1)), None, None
    m = re.match(r"^\s*(\d+)\s*-\s*(.+)$", s)
    if not m:
        return None, None, f"not a param_list line: {s[:100]!r}"
    try:
        idx = int(m.group(1))
    except ValueError:
        return None, None, f"bad index: {s[:100]!r}"
    rest = m.group(2)
    if ":" not in rest:
        return None, None, f"missing ':' after parameter name: {s[:100]!r}"
    c = rest.find(":")
    name = rest[:c].strip()
    value = rest[c + 1 :].lstrip()
    return None, (idx, name, value), None


def format_param_list_document(expected: Optional[int], rows: dict[int, tuple[str, str]]) -> str:
    """Serialize the canonical multiline format (matches typical device ``data`` layout)."""
    if not rows and expected is None:
        return ""
    if expected is None:
        n = max(rows.keys(), default=-1) + 1
        if n <= 0:
            return ""
    else:
        n = expected
    lines = [f"Parameters: {n}"]
    for i in sorted(rows.keys()):
        nm, val = rows[i]
        lines.append(f" {i} - {nm}: {val}")
    return "\n".join(lines) + "\n"


def capture_param_list_res_from_ble(message: str) -> None:
    """Buffer recent notifications that carry a param list ``data`` payload (for replay after ``param_list``)."""
    if iter_param_list_payloads_from_ble(message):
        _param_list_ble_recent.append(message)


class ParamListCollectionSession:
    def __init__(self) -> None:
        self._expected: Optional[int] = None
        self._rows: dict[int, tuple[str, str]] = {}
        self._done = asyncio.Event()

    def feed_ble_message(self, message: str) -> bool:
        fed = False
        for data_str in iter_param_list_payloads_from_ble(message):
            fed = True
            chunk = data_str.strip()
            if "\n" in chunk:
                exp, rows, errs = parse_param_list_document(chunk)
                if exp is not None:
                    self._expected = exp
                self._rows.update(rows)
                for e in errs:
                    _terminal().log(f"⚠ param_list payload line ignored: {e}", "YELLOW")
            else:
                exp, row, err = _parse_param_list_single_line(chunk)
                if err:
                    _terminal().log(f"⚠ param_list fragment ignored: {err}", "YELLOW")
                if exp is not None:
                    self._expected = exp
                if row is not None:
                    idx, name, value = row
                    self._rows[idx] = (name, value)
            if self._is_complete():
                self._done.set()
        return fed

    def _is_complete(self) -> bool:
        if self._expected is None:
            return False
        n = self._expected
        return all(i in self._rows for i in range(n))

    async def wait_until_done(self, timeout: float) -> bool:
        try:
            await asyncio.wait_for(self._done.wait(), timeout=timeout)
            return True
        except asyncio.TimeoutError:
            return False

    def write_file_and_log(self, completed: bool) -> None:
        t = _terminal()
        body = format_param_list_document(self._expected, self._rows)
        if body.strip():
            ensure_output_dir()
            PARAM_LIST_RESPONSE_PATH.write_text(body, encoding="utf-8")
            t.log(f"💾 Parameter list saved to {PARAM_LIST_RESPONSE_PATH}", "GREEN")
        else:
            t.log("⚠ No parameter list collected; file not written.", "YELLOW")

        status = "complete" if completed else "partial (timeout)"
        t.log(f"📋 Device parameters ({status}):", "YELLOW")
        if self._expected is not None:
            t.log(f"  Expecting {self._expected} entr(y/ies)", "CYAN")
        for idx in sorted(self._rows.keys()):
            name, value = self._rows[idx]
            t.log(f"  [{idx}] {name} = {value}", "WHITE")


def try_feed_param_list_session(message: str) -> bool:
    if _active_param_list_session is None:
        return False
    return _active_param_list_session.feed_ble_message(message)


async def collect_param_list_to_file(nus: "NusPort", send_line: str) -> bool:
    """
    Send ``send_line`` (e.g. ``param_list``), collect JSON ``data`` payloads, write ``PARAM_LIST_RESPONSE_PATH``.

    Returns whether the full list arrived before the wait timeout.
    """
    global _active_param_list_session
    t = _terminal()
    session = ParamListCollectionSession()
    _active_param_list_session = session
    try:
        for msg in list(_param_list_ble_recent):
            session.feed_ble_message(msg)
        if not await nus.send_message(send_line):
            return False
        completed = await session.wait_until_done(PARAM_LIST_WAIT_SECONDS)
        if not completed:
            t.log(
                f"⏱ param_list collection timed out after {PARAM_LIST_WAIT_SECONDS:g}s; "
                "showing partial results.",
                "YELLOW",
            )
        session.write_file_and_log(completed)
        return completed
    finally:
        if _active_param_list_session is session:
            _active_param_list_session = None
        _param_list_ble_recent.clear()


async def cmd_param_list(inv: "CommandInvocation", nus: "NusPort") -> None:
    """Send the user line over BLE, collect param list JSON, write output/param_list.txt."""
    _ = await collect_param_list_to_file(nus, inv.line)


def _register_param_list_cli_command() -> None:
    """Register after exports exist; avoids circular import with ``command_handlers``."""
    from commands.command_handlers import cli_command

    cli_command(cmd_param_list)


_register_param_list_cli_command()
