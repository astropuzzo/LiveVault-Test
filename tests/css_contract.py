"""Helpers for asserting against the single LiveVault stylesheet.

The stylesheet is formatted for humans, so contracts compare selectors and
declarations with whitespace removed instead of pinning one serialisation.
"""
from __future__ import annotations

import re
from pathlib import Path

STYLESHEET = Path(__file__).resolve().parents[1] / "app" / "static" / "style.css"


def compact(text: str) -> str:
    text = re.sub(r"\s+", "", text).replace(";}", "}")
    return re.sub(r"(?<![\w.])0\.(\d)", r".\1", text)


def stylesheet() -> str:
    return compact(STYLESHEET.read_text(encoding="utf-8"))


def has_css(css: str, needle: str) -> bool:
    return compact(needle) in css
