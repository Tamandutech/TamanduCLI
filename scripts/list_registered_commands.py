#!/usr/bin/env python3
"""List CLI and incoming command names registered in commands.command_handlers (smoke check)."""

from __future__ import annotations

import sys
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC))

import commands.command_handlers as ch  # noqa: E402


def main() -> None:
    print("CLI commands:", ", ".join(sorted(ch.CLI_COMMAND_HANDLERS)))
    print("Incoming BLE:", ", ".join(sorted(ch.INCOMING_MESSAGE_HANDLERS)))


if __name__ == "__main__":
    main()
