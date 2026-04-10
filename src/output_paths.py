"""Paths for generated CLI/BLE text artifacts (under repo root ``output/`` / ``input/``)."""

from __future__ import annotations

from pathlib import Path

# Repository root (parent of ``src/``).
_APP_ROOT = Path(__file__).resolve().parent.parent
OUTPUT_DIR = _APP_ROOT / "output"
INPUT_DIR = _APP_ROOT / "input"


def ensure_output_dir() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)


def ensure_input_dir() -> None:
    INPUT_DIR.mkdir(parents=True, exist_ok=True)
