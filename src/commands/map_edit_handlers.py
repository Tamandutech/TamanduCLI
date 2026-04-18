"""
map_edit: send map_get, save CSV map to output/map.txt, copy to input/ for editing,
diff, confirm, then map_clear (1s pause), then ``map_add <row>`` for each line of input/map.txt,
and map_SaveRuntime (no parentheses on wire).
"""

from __future__ import annotations

import asyncio
import difflib
import json
import re
import shutil
from collections import deque
from pathlib import Path
from typing import TYPE_CHECKING, Optional

from output_paths import INPUT_DIR, OUTPUT_DIR, ensure_input_dir, ensure_output_dir

if TYPE_CHECKING:
    from commands.command_handlers import NusPort
    from protocol_utils import CommandInvocation

# map_get: device may send many {"data":"…"} lines. After ``map_get`` is sent, a 3s idle timer
# starts and resets on each valid row; collection ends only after 3s with no new responses.
MAP_GET_IDLE_SECONDS = 3.0
MAP_OUTPUT_PATH = OUTPUT_DIR / "map.txt"
MAP_INPUT_PATH = INPUT_DIR / "map.txt"

_map_get_data_recent: deque[str] = deque(maxlen=8)
_active_map_get_session: Optional["MapGetSession"] = None


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


def _payload_from_parsed_dict(obj: dict) -> Optional[str]:
    """``data`` may be a CSV string or a list of numbers from some firmware builds."""
    v = obj.get("data")
    if isinstance(v, str):
        return v.strip() if v.strip() else None
    if isinstance(v, list) and v:
        parts: list[str] = []
        for x in v:
            if isinstance(x, bool):
                return None
            if isinstance(x, int):
                parts.append(str(x))
            elif isinstance(x, float):
                parts.append(str(int(x)) if x.is_integer() else str(x))
            else:
                parts.append(str(x))
        return ",".join(parts)
    return None


def _try_parse_map_get_object_json(chunk: str) -> Optional[str]:
    try:
        obj = json.loads(chunk)
    except json.JSONDecodeError:
        return None
    if not isinstance(obj, dict):
        return None
    return _payload_from_parsed_dict(obj)


def _try_parse_map_get_object_regex(t: str) -> Optional[str]:
    """Last resort: ``{"data": "a,b,c,d,e"}`` with strict double quotes around ``data`` value."""
    m = re.search(r'\{\s*"data"\s*:\s*"((?:[^"\\]|\\.)*)"\s*\}', t)
    if not m:
        return None
    inner = m.group(1).strip()
    return inner or None


def iter_map_get_data_rows(message: str) -> list[str]:
    """
    Extract every map row from a BLE notification string (one or more lines, one or more
    JSON objects per line). Returns CSV strings like ``0,0,0,2,10``.
    """
    out: list[str] = []
    for raw_line in message.replace("\r\n", "\n").split("\n"):
        line = _sanitize_ble_payload(raw_line)
        if not line:
            continue
        parsed_any = False
        for chunk in _json_object_slices(line):
            row = _try_parse_map_get_object_json(chunk)
            if row is not None:
                out.append(row)
                parsed_any = True
        if parsed_any:
            continue
        # Whole line is one JSON blob but slice walker missed (e.g. no braces) — try direct.
        t = _sanitize_ble_payload(line)
        if t.startswith("{"):
            row = _try_parse_map_get_object_json(t)
            if row is not None:
                out.append(row)
                continue
        row = _try_parse_map_get_object_regex(t)
        if row is not None:
            out.append(row)
    return out


def capture_map_get_res_from_ble(message: str) -> None:
    for row in iter_map_get_data_rows(message):
        _map_get_data_recent.append(row)


class MapGetSession:
    """
    Collects one CSV row per BLE JSON line. Bleak may invoke the RX callback on a worker
    thread, so we must not call asyncio.Event.set() from there — schedule work on the loop.

    A ``MAP_GET_IDLE_SECONDS`` timer starts when :meth:`start_idle_watch` runs (after ``map_get``
    is sent) and resets on every valid row; the session completes only after that much
    continuous idle (no new rows).
    """

    def __init__(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop
        self._done = asyncio.Event()
        self._parts: list[str] = []
        self._idle_handle: Optional[asyncio.TimerHandle] = None
        self._dead = False
        self._finished = False

    def start_idle_watch(self) -> None:
        """Start (or restart) the idle countdown — call once right after ``map_get`` is sent."""
        self._schedule_idle()

    def feed(self, data: str) -> None:
        """Called from BLE path (possibly non-asyncio thread)."""
        self._loop.call_soon_threadsafe(self._on_row, data)

    def _schedule_idle(self) -> None:
        if self._dead or self._finished:
            return
        if self._idle_handle is not None:
            self._idle_handle.cancel()
            self._idle_handle = None
        self._idle_handle = self._loop.call_later(
            MAP_GET_IDLE_SECONDS,
            self._complete_after_idle,
        )

    def _on_row(self, data: str) -> None:
        if self._dead or self._finished:
            return
        row = data.strip()
        if not row:
            return
        self._parts.append(row)
        self._schedule_idle()

    def _complete_after_idle(self) -> None:
        self._idle_handle = None
        if self._dead or self._finished:
            return
        self._finished = True
        self._done.set()

    def close(self) -> None:
        self._dead = True
        if self._idle_handle is not None:
            self._idle_handle.cancel()
            self._idle_handle = None

    async def wait_until_done(self) -> None:
        await self._done.wait()

    @property
    def row_count(self) -> int:
        return len(self._parts)

    @property
    def data(self) -> Optional[str]:
        if not self._parts:
            return None
        return "\n".join(self._parts) + "\n"


def try_feed_map_get_session(message: str) -> bool:
    if _active_map_get_session is None:
        return False
    fed = False
    for row in iter_map_get_data_rows(message):
        _active_map_get_session.feed(row)
        fed = True
    return fed


def _parse_map_rows(path: Path) -> tuple[dict[int, tuple[int, int, int, int, int]], list[str]]:
    rows: dict[int, tuple[int, int, int, int, int]] = {}
    errors: list[str] = []
    text = path.read_text(encoding="utf-8")
    for lineno, line in enumerate(text.splitlines(), start=1):
        s = line.strip()
        if not s:
            continue
        parsed = _parse_map_row(s)
        if parsed is None:
            errors.append(f"  line {lineno}: {s[:120]!r}")
            continue
        idx = parsed[0]
        rows[idx] = parsed
    return rows, errors


def _parse_map_row(s: str) -> Optional[tuple[int, int, int, int, int]]:
    parts = [p.strip() for p in s.split(",")]
    if len(parts) != 5:
        return None
    try:
        a, b, c, d, e = (int(p) for p in parts)
    except ValueError:
        return None
    return a, b, c, d, e


async def _prompt(message: str, color: str = "CYAN") -> str:
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, lambda: _terminal().input(message, color))


async def cmd_map_edit(inv: "CommandInvocation", nus: "NusPort") -> None:
    _ = inv
    global _active_map_get_session
    t = _terminal()
    _map_get_data_recent.clear()
    session = MapGetSession(asyncio.get_running_loop())
    _active_map_get_session = session
    try:
        if not await nus.send_message("map_get"):
            return
        session.start_idle_watch()
        await session.wait_until_done()
        raw = session.data
        if raw is None:
            t.log(
                f'⏱ map_get: no JSON with "data" within {MAP_GET_IDLE_SECONDS:g}s idle '
                "after the request.",
                "YELLOW",
            )
            return
        body = raw
        ensure_output_dir()
        ensure_input_dir()
        MAP_OUTPUT_PATH.write_text(body, encoding="utf-8")
        shutil.copy2(MAP_OUTPUT_PATH, MAP_INPUT_PATH)
        rel_out = MAP_OUTPUT_PATH.relative_to(OUTPUT_DIR.parent)
        rel_in = MAP_INPUT_PATH.relative_to(OUTPUT_DIR.parent)
        t.log(f"💾 Saved {rel_out} and copied to {rel_in} — edit the file in input/, then save.", "GREEN")

        done = await _prompt("Finished editing? Type y to continue: ", "CYAN")
        if done.strip().lower() not in ("y", "yes"):
            t.log("Aborted (no diff or apply).", "YELLOW")
            return

        original_text = MAP_OUTPUT_PATH.read_text(encoding="utf-8")
        edited_text = MAP_INPUT_PATH.read_text(encoding="utf-8")
        orig_lines = original_text.splitlines(keepends=True)
        edit_lines = edited_text.splitlines(keepends=True)
        diff_lines = list(
            difflib.unified_diff(
                orig_lines,
                edit_lines,
                fromfile="output/map.txt",
                tofile="input/map.txt",
            )
        )
        t.log("--- Diff (output/map.txt → input/map.txt) ---", "YELLOW")
        if not diff_lines:
            t.log("(no differences)", "WHITE")
            t.log("Nothing to apply.", "YELLOW")
            return
        for line in diff_lines:
            t.log(line.rstrip("\n"), "WHITE")

        ok = await _prompt(
            "Were these changes intended? Type y to send map_clear, map_add for each line of input/map.txt, then map_SaveRuntime: ",
            "YELLOW",
        )
        if ok.strip().lower() not in ("y", "yes"):
            t.log("Aborted — nothing sent.", "YELLOW")
            return

        _, edit_errors = _parse_map_rows(MAP_INPUT_PATH)
        if edit_errors:
            t.log("⚠ Edited file has lines that are not valid index,time,encMedia,trackStatus,offset:", "RED")
            for e in edit_errors:
                t.log(e, "RED")
            t.log("Fix the file and run map_edit again.", "YELLOW")
            return

        t.log("📤 map_clear", "CYAN")
        if not await nus.send_message("map_clear"):
            t.log("⚠ map_clear send failed; stopping.", "RED")
            return
        await asyncio.sleep(1.0)

        map_input_body = MAP_INPUT_PATH.read_text(encoding="utf-8")
        sent_lines = 0
        for raw_line in map_input_body.splitlines():
            line = raw_line.strip()
            if not line:
                continue
            msg = f"map_add {line}"
            t.log(f"📤 {msg}", "CYAN")
            if not await nus.send_message(msg):
                t.log("⚠ send_message failed; stopping.", "RED")
                return
            sent_lines += 1

        t.log(f"✅ Sent map_clear and {sent_lines} map_add line(s).", "GREEN")
        await asyncio.sleep(1.0)
        if not await nus.send_message("map_SaveRuntime"):
            t.log("⚠ map_SaveRuntime send failed.", "RED")
            return
        t.log("📤 Sent map_SaveRuntime.", "GREEN")
    finally:
        session.close()
        if _active_map_get_session is session:
            _active_map_get_session = None
        _map_get_data_recent.clear()


def _register_map_edit_cli_command() -> None:
    """Register after exports exist; avoids circular import with ``command_handlers``."""
    from commands.command_handlers import cli_command

    cli_command(cmd_map_edit)


_register_map_edit_cli_command()
