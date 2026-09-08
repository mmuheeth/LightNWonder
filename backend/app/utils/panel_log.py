"""Patterns for the log ``OledPanelSvc`` writes -- sibling of
:mod:`app.utils.panel_xml`, which knows its layout format instead."""

from __future__ import annotations

import re

# SDL reports a pointer crossing its window edge. A bare mouse-move probe looks
# for this: it proves posted input reaches the panel without pressing anything.
MOUSE_CROSSING = re.compile(r"Mouse\s+(?:entered|left)\s+window", re.IGNORECASE)

# Bit 0x100 marks a press; the release repeats the value with it cleared.
_PRESSED_BIT = 0x100


def press_pattern(button_id: int) -> re.Pattern[str]:
    """Match ``GUIBPDevice``'s ``evt=1,ard=XXXXXXXX`` record of *this* switch going down
    (low byte is ``button_id``, bit 0x100 marks press vs release)."""
    return re.compile(
        rf"\bevt=1\s*,\s*ard={_PRESSED_BIT | button_id:08x}\b", re.IGNORECASE
    )
