"""The Chrome DevTools Protocol, as much of it as driving a page needs.

A foreign format like the others here, and deliberately ignorant of who
consumes it: it knows how to find a page in a browser that was started with a
debugging port, read what is on it, and click something in it. It does not know
what an attendant menu is.

**Why a page is driven through this rather than clicked on screen.** A window
that renders a web page exposes no controls to Win32 -- one HWND, and
:func:`app.utils.win32.descendants` finds only the renderer's own plumbing --
so the alternative is a measured point, which has to be re-measured whenever the
page's layout changes and cannot say what it hit. Through the protocol the same
button is addressed by the text on it, and three problems stop existing:

* **Nothing is measured.** The element reports its own rectangle.
* **Nothing needs focus.** Input dispatched here goes into the renderer, not
  onto the desktop, so the window does not have to be raised, does not have to
  be uncovered, and nothing moves the cursor. A window's z-order and the
  desktop's DPI scaling both stop mattering.
* **Loading stops being a guess.** "The button exists and is visible" is a
  question with an answer, which is worth more than any grace period: see
  :func:`elements`.

The connection is a websocket per page, so a caller that clicks several things
should hold one :class:`Session` open across them.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any

import httpx
from websockets.asyncio.client import connect as ws_connect

__all__ = [
    "CdpError",
    "Element",
    "HitElement",
    "PageTarget",
    "Session",
    "elements",
    "find_page",
    "hit_test",
    "labels",
    "open_page",
]


class CdpError(RuntimeError):
    """The browser could not be reached, had no such page, or refused the call.

    One class on purpose: to a caller, "no debugging port", "no page open" and
    "the script threw" are all *the page cannot be driven right now*, and the
    message is what distinguishes them."""


@dataclass(frozen=True)
class PageTarget:
    """One debuggable page in a running browser."""

    target_id: str
    title: str
    url: str
    websocket_url: str


@dataclass(frozen=True)
class HitElement:
    """One element under a point, as the page reports it.

    Deliberately thinner than :class:`Element`: no geometry, because the
    question it answers is "what is here", not "where is it". Its ``text`` is
    truncated -- a wrapper's text is the whole panel -- which is fine for the
    only use, comparing it against one label.
    """

    tag: str
    element_id: str
    text: str


@dataclass(frozen=True)
class Element:
    """One element of a page, as the page itself describes it.

    Coordinates are CSS pixels relative to the viewport -- which is what
    :meth:`Session.click` wants, and is not a screen coordinate: nothing here
    is affected by where the window sits or how the desktop is scaled."""

    text: str
    tag: str
    element_id: str
    x: float
    y: float
    width: float
    height: float
    visible: bool

    in_viewport: bool = True
    """Whether the element's own box is inside the part of the page on screen.

    Separate from :attr:`visible`, and the distinction matters: CSS visibility
    says the element is *drawn*, which stays true for a list row scrolled below
    the fold, while its coordinates are then outside the viewport and a click
    there lands on whatever *is* at that point. A caller must not click one of
    these."""

    @property
    def center(self) -> tuple[float, float]:
        return (self.x + self.width / 2, self.y + self.height / 2)

    @property
    def area(self) -> float:
        return self.width * self.height

    def contains(self, other: Element) -> bool:
        """Whether this element's box encloses another's.

        Used to tell a button from the panel wrapped around it: both carry the
        same text, and only the inner one is the thing to click."""
        return (
            self.x <= other.x
            and self.y <= other.y
            and self.x + self.width >= other.x + other.width
            and self.y + self.height >= other.y + other.height
            and self.area > other.area
        )


# Anything that could be a control, with the text a human reads off it and the
# id the page gave it.
#
# **`[id]` is in this list first, not as an afterthought.** A framework is free
# to build a button out of a plain styled `div` -- this page builds two of them
# out of `div.MuiBox-root` -- and such an element matches none of the
# interactive selectors below it, so a query written only against those finds
# no button and reports an empty page. An id, when a page sets one, is also a
# better answer than text: it is exact, and it does not change when the label
# is restyled or translated.
#
# `checkVisibility` is what makes a hidden copy -- the other tab's panel, a
# closed drawer -- answerable rather than something to guess at; it is in every
# Chrome from 105 and falls back to a rectangle test where it is not.
_LIST_ELEMENTS = r"""
(() => {
  const nodes = document.querySelectorAll(
    '[id], [role], a, button, summary, li,' +
    '[class*="MuiTab-"], [class*="MuiListItem"], [class*="MuiButton"]'
  );
  const seen = [];
  for (const el of nodes) {
    const text = (el.innerText || el.textContent || '')
      .trim().replace(/\s+/g, ' ');
    const id = el.id || '';
    // Nothing to address it by is nothing to find it with.
    if (!id && (!text || text.length > 80)) continue;
    const r = el.getBoundingClientRect();
    const visible = (typeof el.checkVisibility === 'function')
      ? el.checkVisibility({checkOpacity: true, checkVisibilityCSS: true})
      : (r.width > 0 && r.height > 0);
    // Drawn is not the same as on screen: a row below the fold is both
    // visible and at coordinates that belong to something else.
    const inViewport = r.bottom > 0 && r.right > 0
      && r.top < window.innerHeight && r.left < window.innerWidth;
    seen.push({
      text: text.length > 80 ? '' : text,
      tag: el.tagName.toLowerCase(),
      id: id,
      x: r.x, y: r.y, width: r.width, height: r.height,
      visible: Boolean(visible) && r.width > 0 && r.height > 0,
      inViewport: inViewport,
    });
  }
  return JSON.stringify(seen);
})()
"""


async def find_page(
    base_url: str, *, url_contains: str = "", timeout: float = 5.0
) -> PageTarget:
    """Find the page to drive in a browser listening on ``base_url``.

    ``url_contains`` picks it out of whatever else that browser has open --
    including the DevTools front end, which is itself a page and is skipped
    here, since a browser being inspected by a human should not change which
    page a caller drives."""
    listing = f"{base_url.rstrip('/')}/json/list"
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.get(listing)
            response.raise_for_status()
            targets: Any = response.json()
    except httpx.HTTPError as exc:
        raise CdpError(
            f"No debuggable browser answered at {listing}: {exc}. The page is "
            "driven through its browser's debugging port, so that port has to "
            "be open (the browser is started with --remote-debugging-port)."
        ) from exc
    except ValueError as exc:
        raise CdpError(f"{listing} did not answer with a target list: {exc}") from exc

    pages = [
        PageTarget(
            target_id=str(one.get("id", "")),
            title=str(one.get("title", "")),
            url=str(one.get("url", "")),
            websocket_url=str(one.get("webSocketDebuggerUrl", "")),
        )
        for one in targets
        if isinstance(one, dict)
        and one.get("type") == "page"
        and not str(one.get("url", "")).startswith("devtools://")
        and one.get("webSocketDebuggerUrl")
    ]
    wanted = [page for page in pages if url_contains in page.url]
    if not wanted:
        open_now = ", ".join(f"{page.title!r} ({page.url})" for page in pages) or "none"
        raise CdpError(
            f"That browser has no page whose URL contains {url_contains!r}. "
            f"Pages open: {open_now}."
        )
    return wanted[0]


class Session:
    """An open connection to one page. Not thread-safe, and not concurrent:
    calls are matched to replies by id, one at a time."""

    def __init__(self, socket: Any, page: PageTarget) -> None:
        self._socket = socket
        self._next_id = 0
        self.page = page

    async def _call(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        self._next_id += 1
        call_id = self._next_id
        await self._socket.send(
            json.dumps({"id": call_id, "method": method, "params": params})
        )
        while True:
            # Events arrive on the same socket and are not replies; the one
            # carrying this id is.
            message = json.loads(await self._socket.recv())
            if message.get("id") != call_id:
                continue
            if "error" in message:
                raise CdpError(f"{method} was refused: {message['error']}")
            result: dict[str, Any] = message.get("result", {})
            return result

    async def evaluate(self, expression: str) -> Any:
        """Run one expression in the page and return its value."""
        result = await self._call(
            "Runtime.evaluate",
            {"expression": expression, "returnByValue": True, "awaitPromise": True},
        )
        if "exceptionDetails" in result:
            detail = result["exceptionDetails"]
            text = detail.get("exception", {}).get("description") or detail.get("text")
            raise CdpError(f"The page's script failed: {text}")
        return result.get("result", {}).get("value")

    async def click(self, x: float, y: float) -> None:
        """Click a point of the page, in CSS pixels from the viewport's corner.

        Dispatched as a press and a release rather than a DOM ``click()`` call,
        because a UI framework is free to act on either half -- and because a
        synthesised event that skipped the press would not be the event the
        page was written against."""
        for event in ("mousePressed", "mouseReleased"):
            await self._call(
                "Input.dispatchMouseEvent",
                {
                    "type": event,
                    "x": x,
                    "y": y,
                    "button": "left",
                    "clickCount": 1,
                    "buttons": 1 if event == "mousePressed" else 0,
                },
            )


@asynccontextmanager
async def open_page(
    base_url: str, *, url_contains: str = "", timeout: float = 5.0
) -> AsyncIterator[Session]:
    """Find a page and hold a session open on it."""
    page = await find_page(base_url, url_contains=url_contains, timeout=timeout)
    try:
        socket = await ws_connect(
            page.websocket_url, max_size=None, open_timeout=timeout
        )
    except (OSError, TimeoutError) as exc:  # pragma: no cover - needs a browser
        raise CdpError(f"Could not open a session on {page.url}: {exc}") from exc
    try:
        yield Session(socket, page)
    finally:
        await socket.close()


async def elements(session: Session) -> list[Element]:
    """Every plausibly clickable element on the page, with its own geometry.

    This is also the readiness check worth having: a page that has not finished
    rendering has no button to find, so a caller polls *this* instead of
    sleeping and hoping."""
    raw = await session.evaluate(_LIST_ELEMENTS)
    if not isinstance(raw, str):
        raise CdpError(f"The page described its elements as {type(raw).__name__}")
    try:
        decoded: Any = json.loads(raw)
    except ValueError as exc:
        raise CdpError(f"The page's element list was not JSON: {exc}") from exc
    if not isinstance(decoded, list):
        raise CdpError("The page's element list was not a list")

    found: list[Element] = []
    for item in decoded:
        if not isinstance(item, dict):
            continue
        found.append(
            Element(
                text=str(item.get("text", "")),
                tag=str(item.get("tag", "")),
                element_id=str(item.get("id", "")),
                x=float(item.get("x", 0.0)),
                y=float(item.get("y", 0.0)),
                width=float(item.get("width", 0.0)),
                height=float(item.get("height", 0.0)),
                visible=bool(item.get("visible", False)),
                in_viewport=bool(item.get("inViewport", True)),
            )
        )
    return found


_HIT_TEST = """
(() => {
  const at = document.elementFromPoint(%(x)s, %(y)s);
  const chain = [];
  for (let node = at; node && chain.length < 6; node = node.parentElement) {
    chain.push({
      tag: node.tagName.toLowerCase(),
      id: node.id || "",
      text: (node.textContent || "").replace(/\\s+/g, " ").trim().slice(0, 160),
    });
  }
  return JSON.stringify(chain);
})()
"""


async def hit_test(session: Session, x: float, y: float) -> list[HitElement]:
    """What is at a point of the page *now*, innermost first.

    The counterpart of a rectangle, and not the same question. An element's
    box says where it was when the page was read; this says what a click at
    that point would actually reach, which is the only thing that decides
    where a dispatched click lands. They part company whenever the page moves
    between the two -- a drawer sliding in, a list re-rendering, an overlay
    opening -- and a click aimed at a stale box then lands on whatever took
    that space.

    The chain rather than the single element, because the thing under a point
    is usually a child of the thing being aimed at: a MUI button's centre is
    covered by its own ripple ``<span>``, and a caller asking "did I hit the
    button" has to be able to see the button in the answer.
    """
    raw = await session.evaluate(_HIT_TEST % {"x": x, "y": y})
    if not isinstance(raw, str):
        raise CdpError(f"The page described the point as {type(raw).__name__}")
    try:
        decoded: Any = json.loads(raw)
    except ValueError as exc:
        raise CdpError(f"The page's hit test was not JSON: {exc}") from exc
    if not isinstance(decoded, list):
        raise CdpError("The page's hit test was not a list")

    return [
        HitElement(
            tag=str(item.get("tag", "")),
            element_id=str(item.get("id", "")),
            text=str(item.get("text", "")),
        )
        for item in decoded
        if isinstance(item, dict)
    ]


def labels(found: Sequence[Element]) -> list[str]:
    """The distinct visible texts on a page, for the error message that has to
    say what *was* there instead."""
    seen: list[str] = []
    for element in found:
        if element.visible and element.text and element.text not in seen:
            seen.append(element.text)
    return seen
