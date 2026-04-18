"""
param_edit: send ``param_list``, wait for JSON ``data`` parameter list, save to output/ and input/,
diff after manual edit, confirm, then ``param_set <name> <value>`` per data row from input,
then ``param_list`` again to verify.
"""

from __future__ import annotations

import asyncio
import difflib
import re
import shutil
from pathlib import Path
from typing import TYPE_CHECKING, Optional

from commands.command_handlers import cli_command
from commands.param_list_handlers import (
    PARAM_LIST_RESPONSE_PATH,
    collect_param_list_to_file,
    parse_param_list_document,
)
from output_paths import INPUT_DIR, OUTPUT_DIR, ensure_input_dir, ensure_output_dir

if TYPE_CHECKING:
    from commands.command_handlers import NusPort
    from protocol_utils import CommandInvocation

PARAM_LIST_INPUT_PATH = INPUT_DIR / "param_list.txt"


def _terminal():
    import main as main_module

    return main_module.Terminal


def _quote_payload(s: str) -> str:
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _param_set_wire(name: str, value: str) -> str:
    """``param_set <className>.<parameterName> <value>``; quote value when needed."""
    if re.search(r'[\s"\\]', value) or value == "":
        return f"param_set {name} {_quote_payload(value)}"
    return f"param_set {name} {value}"


def _parse_param_list_file(path: Path) -> tuple[Optional[int], dict[int, tuple[str, str]], list[str]]:
    return parse_param_list_document(path.read_text(encoding="utf-8"))


def _iter_data_rows(expected: int, rows: dict[int, tuple[str, str]]) -> list[tuple[int, str, str]]:
    """Rows ``0 .. expected-1`` in order (for ``param_set``); caller must ensure all keys exist."""
    return [(i, rows[i][0], rows[i][1]) for i in range(expected)]


async def _prompt(message: str, color: str = "CYAN") -> str:
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, lambda: _terminal().input(message, color))


@cli_command
async def cmd_param_edit(inv: "CommandInvocation", nus: "NusPort") -> None:
    _ = inv
    t = _terminal()

    await collect_param_list_to_file(nus, "param_list")

    if not PARAM_LIST_RESPONSE_PATH.is_file() or not PARAM_LIST_RESPONSE_PATH.read_text(encoding="utf-8").strip():
        t.log("⚠ output/param_list.txt missing or empty after param_list.", "YELLOW")
        return

    ensure_output_dir()
    ensure_input_dir()
    shutil.copy2(PARAM_LIST_RESPONSE_PATH, PARAM_LIST_INPUT_PATH)
    rel_out = PARAM_LIST_RESPONSE_PATH.relative_to(OUTPUT_DIR.parent)
    rel_in = PARAM_LIST_INPUT_PATH.relative_to(OUTPUT_DIR.parent)
    t.log(f"💾 Wrote {rel_out} and copied to {rel_in} — edit the file under input/, then save.", "GREEN")

    done = await _prompt("Finished editing? Type y to continue: ", "CYAN")
    if done.strip().lower() not in ("y", "yes"):
        t.log("Aborted (no diff or apply).", "YELLOW")
        return

    original_text = PARAM_LIST_RESPONSE_PATH.read_text(encoding="utf-8")
    edited_text = PARAM_LIST_INPUT_PATH.read_text(encoding="utf-8")
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

    ok = await _prompt(
        "Apply these changes? Type y to send param_set for each parameter line from input/param_list.txt: ",
        "YELLOW",
    )
    if ok.strip().lower() not in ("y", "yes"):
        t.log("Aborted — no param_set commands sent.", "YELLOW")
        return

    expected, edited_rows, edit_errors = _parse_param_list_file(PARAM_LIST_INPUT_PATH)
    if edit_errors:
        t.log("⚠ Edited file has invalid lines (expected 'Parameters: N' and 'i - name: value'):", "RED")
        for e in edit_errors:
            t.log(e, "RED")
        t.log("Fix the file and run param_edit again.", "YELLOW")
        return
    if expected is None:
        t.log("⚠ Edited file must include a line like: Parameters: 2", "RED")
        return
    missing = [i for i in range(expected) if i not in edited_rows]
    if missing:
        t.log(f"⚠ Missing parameter index(es) for Parameters: {expected}: {missing!r}", "RED")
        return
    extra = [i for i in edited_rows if i < 0 or i >= expected]
    if extra:
        t.log(f"⚠ Index out of range 0..{expected - 1}: {extra!r}", "RED")
        return

    to_apply = _iter_data_rows(expected, edited_rows)
    if not to_apply:
        t.log("No parameter rows to send.", "YELLOW")
        return

    for idx, name, value in to_apply:
        line = _param_set_wire(name, value)
        t.log(f"📤 {line}", "CYAN")
        if not await nus.send_message(line):
            t.log("⚠ send_message failed; stopping.", "RED")
            return

    t.log(f"✅ Sent {len(to_apply)} param_set command(s). Requesting param_list to verify…", "GREEN")

    await collect_param_list_to_file(nus, "param_list")
    if PARAM_LIST_RESPONSE_PATH.is_file():
        t.log("✅ Refreshed output/param_list.txt from device.", "GREEN")
    else:
        t.log("⚠ param_list after apply did not produce output/param_list.txt.", "YELLOW")
