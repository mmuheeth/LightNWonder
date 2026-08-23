"""Patterns for the log ``OledPanelSvc`` writes -- sibling of
:mod:`app.utils.panel_xml`, which knows its layout format instead. Reading this
log is what makes a press provable: a press that didn't land can be reported
as a failure instead of a cheerful lie.
"""

from __future__ import annotations

import re

# SDL reports a pointer crossing its window edge. A bare mouse-move probe looks
# for this: it proves posted input reaches the panel without pressing anything.
MOUSE_CROSSING = re.compile(r"Mouse\s+(?:entered|left)\s+window", re.IGNORECASE)

# Bit 0x100 marks a press; the release repeats the value with it cleared.
_PRESSED_BIT = 0x100


def press_pattern(button_id: int) -> re.Pattern[str]:
    """Match ``GUIBPDevice``'s ``evt=1,ard=XXXXXXXX`` record of *this* switch
    going down (low byte is ``button_id``, bit 0x100 marks press vs release).
    Anchoring the whole 8-digit value pins one switch and one direction, so a
    person pressing other keys mid-confirm can't match. The adjacent
    ``OledSubsystem - Button Pressed ID=`` line uses a different legacy
    numbering and is deliberately not matched.
    """
    return re.compile(
        rf"\bevt=1\s*,\s*ard={_PRESSED_BIT | button_id:08x}\b", re.IGNORECASE
    )
