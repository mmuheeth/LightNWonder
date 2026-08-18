"""Patterns for the log ``OledPanelSvc`` writes.

The sibling of :mod:`app.utils.panel_xml`: that module knows the panel service's
layout format, this one knows its log format. Both exist so the one place that
understands a foreign file is not buried inside a service.

Reading this log is what makes a press provable rather than assumed -- every
press the panel accepts appears here within milliseconds, so a press that did not
land can be reported as a failure instead of a cheerful lie.
"""

from __future__ import annotations

import re

# SDL reports a pointer crossing its window edge. A bare mouse-move probe looks
# for this: it proves posted input reaches the panel without pressing anything.
MOUSE_CROSSING = re.compile(r"Mouse\s+(?:entered|left)\s+window", re.IGNORECASE)

# Bit 0x100 marks a press; the release repeats the value with it cleared.
_PRESSED_BIT = 0x100


def press_pattern(button_id: int) -> re.Pattern[str]:
    """Match the panel's device-level record of *this* switch going down.

    ``GUIBPDevice`` logs every switch transition as ``evt=1,ard=XXXXXXXX``. The
    low byte is the layout's ``button_id`` and bit 0x100 marks the press; the
    release repeats the value with that bit cleared::

        evt=1,ard=00000105   Hold1 (button_id 5) pressed
        evt=1,ard=00000005   Hold1 released

    Anchoring on the whole eight-digit value pins a match to one switch *and* one
    direction. That precision is not academic: a person at the machine can be
    pressing keys while a press of ours is being confirmed, and matching a release
    -- or a neighbouring key -- would confirm the wrong event.

    The adjacent ``OledSubsystem - Button Pressed ID=`` line is deliberately not
    matched. It reports a different, legacy numbering: pressing switch 5 logs
    ``ID=3``. Comparing that against a layout ``button_id`` would miss real
    presses and confirm unrelated ones.
    """
    return re.compile(
        rf"\bevt=1\s*,\s*ard={_PRESSED_BIT | button_id:08x}\b", re.IGNORECASE
    )
