"""
param_list: wire list collect and ``cmd_param_list``.

param_edit: fetch list to ``output/``, edit under ``input/``, diff, ``param_set`` per row, verify.
"""

from __future__ import annotations

import asyncio
import difflib
import shutil
from collections import deque
from typing import Optional

from api.command_handlers import (
    CliHandlerContext,
    cli_command,
    register_ble_capture,
    register_ble_try_feed,
)
from api.output_paths import INPUT_DIR, OUTPUT_DIR, ensure_input_dir, ensure_output_dir
from api.protocol_utils import WireCommand, format_message, format_wire_command, parse_message, unquote_field

PARAM_LIST_WAIT_SECONDS = 3.0
PARAM_LIST_RESPONSE_PATH = OUTPUT_DIR / "param_list.txt"
PARAM_LIST_INPUT_PATH = INPUT_DIR / "param_list.txt"
DEFAULT_PARAM_LIST_WIRE = format_message([WireCommand.single_request("param_list", ())])

_param_list_ble_recent: deque[str] = deque(maxlen=128)
_active_param_list_session: Optional["ParamListCollectionSession"] = None


def _message_has_param_list_list_response(text: str) -> bool:
    for c in parse_message(text):
        if c.name.lower() != "param_list" or not c.is_response:
            continue
        if c.kind in ("list_header", "list_body"):
            return True
    return False


@register_ble_capture
def capture_param_list_res_from_ble(message: str) -> None:
    for line in message.replace("\r\n", "\n").split("\n"):
        s = line.strip()
        if s and _message_has_param_list_list_response(s):
            _param_list_ble_recent.append(s)


def parse_param_list_document(content: str) -> tuple[Optional[int], dict[int, tuple[str, str]], list[str]]:
    """
    Parse ``param_list.txt``: each non-empty line should contain wire ``param_list`` **response**
    commands (``h`` header and/or ``b`` body), e.g. ``param_list(h,s,3);`` or ``param_list(b,s,1,"n","v");``.
    """
    errors: list[str] = []
    expected: Optional[int] = None
    rows: dict[int, tuple[str, str]] = {}
    if not content.strip():
        return None, {}, ["  (empty document)"]

    for lineno, line in enumerate(content.splitlines(), start=1):
        raw = line.strip()
        if not raw:
            continue
        wire = raw if raw.endswith(";") else raw + ";"
        got_param_list = False
        for cmd in parse_message(wire):
            if cmd.name.lower() != "param_list" or not cmd.is_response:
                continue
            if cmd.kind == "list_header":
                if not cmd.arguments:
                    errors.append(f"  line {lineno}: param_list header missing count")
                    continue
                try:
                    expected = int(unquote_field(cmd.arguments[0]))
                except ValueError:
                    errors.append(f"  line {lineno}: param_list header count is not an integer")
                    continue
                got_param_list = True
            elif cmd.kind == "list_body":
                if not cmd.arguments:
                    errors.append(f"  line {lineno}: param_list body missing arguments")
                    continue
                idx = cmd.index
                name = unquote_field(cmd.arguments[0])
                value = ", ".join(unquote_field(a) for a in cmd.arguments[1:])
                rows[idx] = (name, value)
                got_param_list = True
        if not got_param_list:
            errors.append(f"  line {lineno}: expected param_list(h,s,…) or param_list(b,s,…); got {raw[:120]!r}")

    if expected is None and rows:
        positive = {k for k in rows if k > 0}
        if positive:
            mx = max(positive)
            if positive == set(range(1, mx + 1)):
                expected = mx

    return expected, rows, errors


class ParamListCollectionSession:
    def __init__(self, log) -> None:
        self._log = log
        self._expected: Optional[int] = None
        self._rows: dict[int, tuple[str, str]] = {}
        self._wire_lines: list[str] = []
        self._done = asyncio.Event()

    def feed_wire(self, cmd: WireCommand) -> None:
        if cmd.name.lower() != "param_list" or not cmd.is_response:
            return
        if cmd.kind == "list_header":
            if not cmd.arguments:
                return
            try:
                n = int(unquote_field(cmd.arguments[0]))
            except ValueError:
                return
            self._rows[0] = ("header", str(n))
            self._expected = n
            self._wire_lines.append(format_wire_command(cmd))
        elif cmd.kind == "list_body":
            idx = cmd.index
            if not cmd.arguments:
                return
            name = unquote_field(cmd.arguments[0])
            value = ", ".join(unquote_field(a) for a in cmd.arguments[1:])
            self._rows[idx] = (name, value)
            self._wire_lines.append(format_wire_command(cmd))
        if self._is_complete():
            self._done.set()

    def _is_complete(self) -> bool:
        if self._expected is None or 0 not in self._rows:
            return False
        name0, _ = self._rows[0]
        if name0.lower() != "header":
            return False
        n = self._expected
        return all(i in self._rows for i in range(1, n + 1))

    async def wait_until_done(self, timeout: float) -> bool:
        try:
            await asyncio.wait_for(self._done.wait(), timeout=timeout)
            return True
        except asyncio.TimeoutError:
            return False

    def write_file_and_log(self, completed: bool) -> None:
        body = "\n".join(self._wire_lines) + ("\n" if self._wire_lines else "")
        if body.strip():
            ensure_output_dir()
            PARAM_LIST_RESPONSE_PATH.write_text(body, encoding="utf-8")
            self._log(f"💾 Parameter list saved to {PARAM_LIST_RESPONSE_PATH}", "GREEN")
        else:
            self._log("⚠ No parameter list collected; file not written.", "YELLOW")

        status = "complete" if completed else "partial (timeout)"
        self._log(f"📋 Device parameters ({status}):", "YELLOW")
        if self._expected is not None:
            self._log(f"  Expecting {self._expected} entr(y/ies)", "CYAN")
        for idx in sorted(self._rows.keys()):
            if idx == 0:
                continue
            name, value = self._rows[idx]
            self._log(f"  [{idx}] {name} = {value}", "WHITE")


@register_ble_try_feed
def try_feed_param_list_session(message: str) -> bool:
    if _active_param_list_session is None:
        return False
    fed = False
    seen: set[str] = set()
    for part in [message] + message.replace("\r\n", "\n").split("\n"):
        key = part.strip()
        if not key or key in seen:
            continue
        seen.add(key)
        for cmd in parse_message(key):
            if (
                cmd.name.lower() == "param_list"
                and cmd.is_response
                and cmd.kind in ("list_header", "list_body")
            ):
                _active_param_list_session.feed_wire(cmd)
                fed = True
    return fed


async def collect_param_list_to_file(ctx: CliHandlerContext, send_wire: str | None = None) -> bool:
    global _active_param_list_session
    wire = send_wire or DEFAULT_PARAM_LIST_WIRE
    session = ParamListCollectionSession(ctx.log)
    _active_param_list_session = session
    try:
        for msg in list(_param_list_ble_recent):
            for part in [msg] + msg.replace("\r\n", "\n").split("\n"):
                for cmd in parse_message(part.strip()):
                    session.feed_wire(cmd)
        if not await ctx.send_wire(wire):
            return False
        completed = await session.wait_until_done(PARAM_LIST_WAIT_SECONDS)
        if not completed:
            ctx.log(
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


def _param_set_wire_message(name: str, value: str) -> str:
    return format_message([WireCommand.single_request("param_set", (name, value))])


@cli_command
async def cmd_param_list(inv: WireCommand, ctx: CliHandlerContext) -> None:
    _ = inv
    await collect_param_list_to_file(ctx)


@cli_command
async def cmd_param_edit(inv: WireCommand, ctx: CliHandlerContext) -> None:
    _ = inv

    await collect_param_list_to_file(ctx)

    if not PARAM_LIST_RESPONSE_PATH.is_file() or not PARAM_LIST_RESPONSE_PATH.read_text(encoding="utf-8").strip():
        ctx.log("⚠ output/param_list.txt missing or empty after param_list.", "YELLOW")
        return

    ensure_output_dir()
    ensure_input_dir()
    shutil.copy2(PARAM_LIST_RESPONSE_PATH, PARAM_LIST_INPUT_PATH)
    rel_out = PARAM_LIST_RESPONSE_PATH.relative_to(OUTPUT_DIR.parent)
    rel_in = PARAM_LIST_INPUT_PATH.relative_to(OUTPUT_DIR.parent)
    ctx.log(f"💾 Wrote {rel_out} and copied to {rel_in} — edit the file under input/, then save.", "GREEN")

    done = (await ctx.prompt_line("Finished editing? Type y to continue: ")).strip().lower()
    if done not in ("y", "yes"):
        ctx.log("Aborted (no diff or apply).", "YELLOW")
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
    ctx.log("--- Diff (output/param_list.txt → input/param_list.txt) ---", "YELLOW")
    if not diff_lines:
        ctx.log("(no differences)", "WHITE")
        ctx.log("Nothing to apply.", "YELLOW")
        return
    for line in diff_lines:
        ctx.log(line.rstrip("\n"), "WHITE")

    ok = (
        await ctx.prompt_line(
            "Apply these changes? Type y to send param_set for each param_list(b,s,…) row from input/param_list.txt: "
        )
    ).strip().lower()
    if ok not in ("y", "yes"):
        ctx.log("Aborted — no param_set commands sent.", "YELLOW")
        return

    expected, edited_rows, edit_errors = parse_param_list_document(PARAM_LIST_INPUT_PATH.read_text(encoding="utf-8"))
    if edit_errors:
        ctx.log("⚠ Edited file has invalid lines (each line: param_list(h,s,N); or param_list(b,s,i,...);):", "RED")
        for e in edit_errors:
            ctx.log(e, "RED")
        ctx.log("Fix the file and run param_edit again.", "YELLOW")
        return
    if expected is None:
        ctx.log(
            "⚠ Edited file must include a param_list list header (param_list(h,s,<count>);) "
            "or complete param_list(b,s,…) body lines for indices 1..N.",
            "RED",
        )
        return
    missing = [i for i in range(expected) if i not in edited_rows]
    if missing:
        ctx.log(f"⚠ Missing parameter index(es) for Parameters: {expected}: {missing!r}", "RED")
        return
    extra = [i for i in edited_rows if i < 0 or i >= expected]
    if extra:
        ctx.log(f"⚠ Index out of range 0..{expected - 1}: {extra!r}", "RED")
        return

    to_apply = [(i, edited_rows[i][0], edited_rows[i][1]) for i in range(expected)]
    if not to_apply:
        ctx.log("No parameter rows to send.", "YELLOW")
        return

    for idx, name, value in to_apply:
        line = _param_set_wire_message(name, value)
        ctx.log(f"📤 {line}", "CYAN")
        if not await ctx.send_wire(line):
            ctx.log("⚠ send failed; stopping.", "RED")
            return

    ctx.log(f"✅ Sent {len(to_apply)} param_set command(s). Requesting param_list to verify…", "GREEN")

    await collect_param_list_to_file(ctx)
    if PARAM_LIST_RESPONSE_PATH.is_file():
        ctx.log("✅ Refreshed output/param_list.txt from device.", "GREEN")
    else:
        ctx.log("⚠ param_list after apply did not produce output/param_list.txt.", "YELLOW")
