"""
map_edit: send map_get(), save CSV map to output/map.txt, copy to input/ for editing,
diff, confirm, then map_add(...) per changed row and map_SaveRuntime().
"""

from __future__ import annotations

import asyncio
import difflib
import json
import shutil
from collections import deque
from pathlib import Path
from typing import TYPE_CHECKING, Optional

from commands.command_handlers import cli_command
from output_paths import INPUT_DIR, OUTPUT_DIR, ensure_input_dir, ensure_output_dir

if TYPE_CHECKING:
    from commands.command_handlers import NusPort
    from protocol_utils import CommandInvocation

MAP_GET_WAIT_SECONDS = 5.0
MAP_OUTPUT_PATH = OUTPUT_DIR / "map.txt"
MAP_INPUT_PATH = INPUT_DIR / "map.txt"

_map_get_data_recent: deque[str] = deque(maxlen=8)
_active_map_get_session: Optional["MapGetSession"] = None


def _terminal():
    import main as main_module

    return main_module.Terminal


def _try_json_data_field(s: str) -> Optional[str]:
    """If ``s`` is JSON with a string ``data`` field, return that string."""
    t = s.strip()
    if not t:
        return None
    if not t.startswith("{"):
        i = t.find("{")
        if i < 0:
            return None
        t = t[i:]
    try:
        obj = json.loads(t)
    except json.JSONDecodeError:
        return None
    if not isinstance(obj, dict):
        return None
    v = obj.get("data")
    if isinstance(v, str):
        return v
    return None


def capture_map_get_res_from_ble(message: str) -> None:
    for line in message.replace("\r\n", "\n").split("\n"):
        data = _try_json_data_field(line)
        if data is not None:
            _map_get_data_recent.append(data)


class MapGetSession:
    def __init__(self) -> None:
        self._done = asyncio.Event()
        self._data: Optional[str] = None

    def feed(self, data: str) -> None:
        if self._done.is_set():
            return
        self._data = data
        self._done.set()

    async def wait_until_done(self, timeout: float) -> bool:
        try:
            await asyncio.wait_for(self._done.wait(), timeout=timeout)
            return True
        except asyncio.TimeoutError:
            return False

    @property
    def data(self) -> Optional[str]:
        return self._data


def try_feed_map_get_session(message: str) -> bool:
    if _active_map_get_session is None:
        return False
    fed = False
    for line in message.replace("\r\n", "\n").split("\n"):
        data = _try_json_data_field(line)
        if data is not None:
            _active_map_get_session.feed(data)
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


def _rows_to_apply(
    original: dict[int, tuple[int, int, int, int, int]],
    edited: dict[int, tuple[int, int, int, int, int]],
) -> list[tuple[int, int, int, int, int]]:
    out: list[tuple[int, int, int, int, int]] = []
    for idx in sorted(edited):
        if original.get(idx) != edited[idx]:
            out.append(edited[idx])
    return out


def _map_add_line(index: int, time_ms: int, enc_media: int, track_status: int, offset: int) -> str:
    return f"map_add({index},{time_ms},{enc_media},{track_status},{offset})"


async def _prompt(message: str, color: str = "CYAN") -> str:
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, lambda: _terminal().input(message, color))


@cli_command
async def cmd_map_edit(inv: "CommandInvocation", nus: "NusPort") -> None:
    _ = inv
    global _active_map_get_session
    t = _terminal()
    _map_get_data_recent.clear()
    session = MapGetSession()
    _active_map_get_session = session
    try:
        if not await nus.send_message("map_get()"):
            return
        completed = await session.wait_until_done(MAP_GET_WAIT_SECONDS)
        if not completed or session.data is None:
            t.log(
                f"⏱ map_get timed out after {MAP_GET_WAIT_SECONDS:g}s "
                '(no JSON with "data" field received).',
                "YELLOW",
            )
            return
        raw = session.data
        body = raw if raw.endswith("\n") else raw + "\n"
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
            "Were these changes intended? Type y to send map_add for each changed row, then map_SaveRuntime(): ",
            "YELLOW",
        )
        if ok.strip().lower() not in ("y", "yes"):
            t.log("Aborted — no map_add commands sent.", "YELLOW")
            return

        original_rows, _ = _parse_map_rows(MAP_OUTPUT_PATH)
        edited_rows, edit_errors = _parse_map_rows(MAP_INPUT_PATH)
        if edit_errors:
            t.log("⚠ Edited file has lines that are not valid index,time,encMedia,trackStatus,offset:", "RED")
            for e in edit_errors:
                t.log(e, "RED")
            t.log("Fix the file and run map_edit() again.", "YELLOW")
            return

        to_apply = _rows_to_apply(original_rows, edited_rows)
        if not to_apply:
            t.log("No map rows changed (whitespace-only edits). Nothing sent.", "YELLOW")
            return

        for row in to_apply:
            idx, tm, em, ts, off = row
            line = _map_add_line(idx, tm, em, ts, off)
            t.log(f"📤 {line}", "CYAN")
            if not await nus.send_message(line):
                t.log("⚠ send_message failed; stopping.", "RED")
                return

        t.log(f"✅ Sent {len(to_apply)} map_add command(s).", "GREEN")
        await asyncio.sleep(1.0)
        if not await nus.send_message("map_SaveRuntime()"):
            t.log("⚠ map_SaveRuntime() send failed.", "RED")
            return
        t.log("📤 Sent map_SaveRuntime().", "GREEN")
    finally:
        if _active_map_get_session is session:
            _active_map_get_session = None
        _map_get_data_recent.clear()
