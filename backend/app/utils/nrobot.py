"""The Robot Framework remote-library protocol, spoken over XML-RPC -- deliberately
ignorant of who is on the other end (what the keywords *mean* is
:mod:`app.services.gaf`'s business). A failed keyword is not an XML-RPC fault: it comes
back as an ordinary reply with ``status: "FAIL"``, so :meth:`RemoteLibrary.run` checks
``status`` explicitly rather than relying on :class:`xmlrpc.client.Fault`. And every
argument crosses as a string, since the protocol has no types -- hence
:func:`as_argument` rather than a bare ``str()`` per call site."""

from __future__ import annotations

import http.client
import xmlrpc.client
from dataclasses import dataclass
from typing import Any

__all__ = [
    "PASS",
    "KeywordFailure",
    "KeywordReply",
    "RemoteError",
    "RemoteLibrary",
    "as_argument",
]

PASS = "PASS"
"""The only ``status`` a reply can carry and still have done the thing."""


class RemoteError(Exception):
    """The remote server could not be reached, or answered unintelligibly.
    Transport-level only -- a keyword that ran and failed is a
    :class:`KeywordFailure`, a different problem with a different fix."""


class KeywordFailure(Exception):
    """A keyword ran on the remote server and reported ``status: FAIL``."""

    def __init__(
        self, keyword: str, error: str, *, traceback: str = "", output: str = ""
    ) -> None:
        self.keyword = keyword
        self.error = error or "the remote server gave no reason"
        self.traceback = traceback
        self.output = output
        super().__init__(f"{keyword}: {self.error}")


@dataclass(frozen=True, slots=True)
class KeywordReply:
    """One ``run_keyword`` reply, unpacked. ``value`` is whatever the keyword returned
    -- a string, or a list of them -- and only means anything when :attr:`passed`; on a
    failure it is typically empty and :attr:`error` carries the reason."""

    keyword: str
    status: str
    value: Any
    output: str
    error: str
    traceback: str

    @property
    def passed(self) -> bool:
        """Whether the keyword did what it was asked."""
        return self.status == PASS

    def raise_for_status(self) -> Any:
        """Return :attr:`value`, or raise :class:`KeywordFailure`."""
        if not self.passed:
            raise KeywordFailure(
                self.keyword, self.error, traceback=self.traceback, output=self.output
            )
        return self.value

    @property
    def text(self) -> str:
        """:attr:`value` as a single string; a returned list joins on commas."""
        if isinstance(self.value, (list, tuple)):
            return ", ".join(str(item) for item in self.value)
        return "" if self.value is None else str(self.value)

    @property
    def truthy(self) -> bool:
        """Whether a keyword that answers with a boolean answered yes. The .NET side
        spells it ``"True"``; Robot's own conventions admit a few more spellings, none
        case-sensitive on the wire."""
        return self.passed and self.text.strip().casefold() in {"true", "yes", "1"}


def as_argument(value: Any) -> str:
    """Spell one argument the way the remote side parses it. ``None`` becomes the empty
    string rather than ``"None"``, since several keywords read empty as "not given" but
    would try to use a literal ``None`` as a value."""
    if value is None:
        return ""
    if isinstance(value, bool):
        return "True" if value else "False"
    return str(value)


class _TimeoutTransport(xmlrpc.client.Transport):
    """``ServerProxy`` takes no timeout, so the socket gets one here -- without it, a
    keyword that hangs (a game mid-dialog, a server wedged behind another client's
    session) hangs the calling thread forever."""

    def __init__(self, timeout: float) -> None:
        super().__init__()
        self._timeout = timeout

    def make_connection(self, host: Any) -> http.client.HTTPConnection:
        connection = super().make_connection(host)
        connection.timeout = self._timeout
        return connection


class RemoteLibrary:
    """One keyword library on a Robot Framework remote server, addressed by
    ``base_url`` plus the library's dotted name (a separate endpoint per library).
    Instances are cheap and hold no connection between calls -- every method opens its
    own -- and every method here **blocks**; async callers run them on a worker
    thread."""

    def __init__(self, base_url: str, library: str, *, timeout: float = 30.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.library = library
        self.timeout = timeout

    def __repr__(self) -> str:
        return f"RemoteLibrary({self.url!r})"

    @property
    def url(self) -> str:
        """The endpoint this library answers on."""
        return f"{self.base_url}/{self.library}"

    def _proxy(self) -> xmlrpc.client.ServerProxy:
        return xmlrpc.client.ServerProxy(
            self.url, transport=_TimeoutTransport(self.timeout), allow_none=True
        )

    def _call(self, method: str, *args: Any) -> Any:
        """Invoke one remote method, translating every transport failure."""
        try:
            return getattr(self._proxy(), method)(*args)
        except xmlrpc.client.ProtocolError as exc:
            raise RemoteError(
                f"{self.url} answered HTTP {exc.errcode} {exc.errmsg}; is "
                f"{self.library!r} a library this server hosts?"
            ) from exc
        except xmlrpc.client.ResponseError as exc:
            raise RemoteError(f"{self.url} sent a reply we cannot read: {exc}") from exc
        except xmlrpc.client.Fault as exc:
            raise RemoteError(f"{self.library}.{method} faulted: {exc}") from exc
        except TimeoutError as exc:
            raise RemoteError(
                f"{self.library}.{method} did not answer within {self.timeout}s"
            ) from exc
        except (OSError, http.client.HTTPException) as exc:
            # ConnectionRefusedError is the everyday one: NRobot is not running.
            raise RemoteError(
                f"Could not reach the Robot Framework remote server at "
                f"{self.url}: {exc}"
            ) from exc

    def keyword_names(self) -> list[str]:
        """Every keyword this library exposes, as the server spells them."""
        names = self._call("get_keyword_names")
        if not isinstance(names, list):
            raise RemoteError(f"{self.url} did not answer with a list of keywords")
        return [str(name) for name in names]

    def keyword_arguments(self, keyword: str) -> list[str]:
        """The argument names of one keyword, in order."""
        arguments = self._call("get_keyword_arguments", keyword)
        return [str(one) for one in arguments] if isinstance(arguments, list) else []

    def try_run(self, keyword: str, *args: Any) -> KeywordReply:
        """Run a keyword and return its reply, pass or fail. Use this where ``FAIL`` is
        a legitimate answer -- "is this button pressable" can say no -- and
        :meth:`run` where it's a failure."""
        raw = self._call("run_keyword", keyword, [as_argument(one) for one in args])
        if not isinstance(raw, dict):
            raise RemoteError(
                f"{keyword} on {self.url} answered {type(raw).__name__}, not a reply"
            )
        return KeywordReply(
            keyword=keyword,
            status=str(raw.get("status", "")),
            value=raw.get("return"),
            output=str(raw.get("output", "")),
            error=str(raw.get("error", "")),
            traceback=str(raw.get("traceback", "")),
        )

    def run(self, keyword: str, *args: Any) -> Any:
        """Run a keyword, raising :class:`KeywordFailure` unless it passed."""
        return self.try_run(keyword, *args).raise_for_status()

    def probe(self) -> str | None:
        """``None`` if the server answers for this library, else why not. Cheap and
        side-effect free (lists keyword names, discards them), so safe on a status poll.
        The reason is kept because two faults look alike but differ in fix: nothing
        listening at all, versus something listening that doesn't host this library --
        which is what ``NRobot.Server.exe`` started from the wrong directory produces
        (it resolves keyword assemblies relative to its start dir, hence the .bat's
        ``cd %~dp0``), answering HTTP 404 rather than refusing the connection."""
        try:
            self.keyword_names()
        except RemoteError as exc:
            return str(exc)
        return None

    def reachable(self) -> bool:
        """Whether the server answers for this library at all."""
        return self.probe() is None
