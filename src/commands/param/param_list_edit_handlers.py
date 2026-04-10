"""
Copy output/param_list.txt to input/ for local editing, diff, confirm, then param_set each changed row.
"""

from __future__ import annotations

import asyncio
import difflib
import shutil
from pathlib import Path
from typing import TYPE_CHECKING

from commands.command_handlers import cli_command
from commands.param.param_list_handlers import PARAM_LIST_RESPONSE_PATH, parse_param_list_res
from output_paths import INPUT_DIR, OUTPUT_DIR, ensure_input_dir

if TYPE_CHECKING:
    from commands.command_handlers import NusPort
    from protocol_utils import CommandInvocation

PARAM_LIST_EDIT_PATH = INPUT_DIR / "param_list.txt"


def _terminal():
    import main as main_module

    return main_module.Terminal


def _wire_quote(s: str) -> str:
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _parse_param_list_rows(path: Path) -> tuple[dict[int, tuple[str, str]], list[str]]:
    rows: dict[int, tuple[str, str]] = {}
    errors: list[str] = []
    text = path.read_text(encoding="utf-8")
    for lineno, line in enumerate(text.splitlines(), start=1):
        s = line.strip()
        if not s:
            continue
        parsed = parse_param_list_res(s)
        if parsed is None:
            errors.append(f"  line {lineno}: {s[:120]!r}")
            continue
        idx, name, value = parsed
        rows[idx] = (name, value)
    return rows, errors


def _param_set_line(name: str, value: str) -> str:
    return f"param_set({_wire_quote(name)},{_wire_quote(value)})"


def _rows_to_apply(
    original: dict[int, tuple[str, str]], edited: dict[int, tuple[str, str]]
) -> list[tuple[int, str, str]]:
    """Non-header rows from edited whose (name, value) differs from the same index in original."""
    out: list[tuple[int, str, str]] = []
    for idx in sorted(edited):
        if idx == 0:
            continue
        name, value = edited[idx]
        if name.lower() == "header":
            continue
        if original.get(idx) != (name, value):
            out.append((idx, name, value))
    return out


async def _prompt(message: str, color: str = "CYAN") -> str:
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, lambda: _terminal().input(message, color))


@cli_command
async def cmd_param_list_edit(inv: "CommandInvocation", nus: "NusPort") -> None:
    _ = inv
    t = _terminal()
    if not PARAM_LIST_RESPONSE_PATH.is_file():
        missing = PARAM_LIST_RESPONSE_PATH.relative_to(OUTPUT_DIR.parent)
        t.log(f"⚠ No {missing} — run param_list() first.", "YELLOW")
        return

    ensure_input_dir()
    shutil.copy2(PARAM_LIST_RESPONSE_PATH, PARAM_LIST_EDIT_PATH)
    rel = PARAM_LIST_EDIT_PATH.relative_to(OUTPUT_DIR.parent)
    t.log(f"📄 Copied parameter list to {rel} — edit it in your editor, then save.", "GREEN")

    done = await _prompt("Finished editing? Type y to continue: ", "CYAN")
    if done.strip().lower() not in ("y", "yes"):
        t.log("Aborted (no diff or apply).", "YELLOW")
        return

    original_text = PARAM_LIST_RESPONSE_PATH.read_text(encoding="utf-8")
    edited_text = PARAM_LIST_EDIT_PATH.read_text(encoding="utf-8")
    orig_lines = original_text.splitlines(keepends=True)
    edit_lines = edited_text.splitlines(keepends=True)
    diff_lines = list(
        difflib.unified_diff(
            orig_lines,
            edit_lines,
            fromfile="output/param_list.txt",
            tofile="input/param_list.txt",
        )
    )
    t.log("--- Diff (output/param_list.txt → input/param_list.txt) ---", "YELLOW")
    if not diff_lines:
        t.log("(no differences)", "WHITE")
        t.log("Nothing to apply.", "YELLOW")
        return
    for line in diff_lines:
        t.log(line.rstrip("\n"), "WHITE")

    ok = await _prompt("Were these changes intended? Type y to apply param_set for each changed row: ", "YELLOW")
    if ok.strip().lower() not in ("y", "yes"):
        t.log("Aborted — no param_set commands sent.", "YELLOW")
        return

    original_rows, _ = _parse_param_list_rows(PARAM_LIST_RESPONSE_PATH)
    edited_rows, edit_errors = _parse_param_list_rows(PARAM_LIST_EDIT_PATH)
    if edit_errors:
        t.log("⚠ Edited file has lines that are not valid param_list_res(...):", "RED")
        for e in edit_errors:
            t.log(e, "RED")
        t.log("Fix the file and run param_list_edit() again.", "YELLOW")
        return

    to_apply = _rows_to_apply(original_rows, edited_rows)
    if not to_apply:
        t.log("No parameter rows changed (header-only or whitespace-only edits). Nothing sent.", "YELLOW")
        return

    for idx, name, value in to_apply:
        line = _param_set_line(name, value)
        t.log(f"📤 {line}", "CYAN")
        if not await nus.send_message(line):
            t.log("⚠ send_message failed; stopping.", "RED")
            return

    t.log(f"✅ Sent {len(to_apply)} param_set command(s).", "GREEN")
