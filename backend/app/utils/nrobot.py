"""The Robot Framework remote-library protocol, spoken over XML-RPC.

Deliberately ignorant of who is on the other end: this is the wire format
``NRobot.Server.exe`` implements, and it would speak to any Robot Framework
remote server just as well. What the keywords *mean* is
:mod:`app.services.gaf`'s business, not this module's.

Two things about the protocol are easy to get wrong, and are handled here:

* **A failed keyword is not an XML-RPC fault.** It comes back as an ordinary
  reply whose ``status`` is ``"FAIL"``, so a caller that only catches
  :class:`xmlrpc.client.Fault` reads every failure as a success.
  :meth:`RemoteLibrary.run` checks ``status`` explicitly and raises;
  :meth:`RemoteLibrary.try_run` hands back the whole reply, for callers that
  want to treat a failure as an answer.
* **Every argument crosses as a string.** Robot's remote protocol has no
  types, so ``3`` and ``True`` have to be spelled the way the .NET side parses
  them -- hence :func:`as_argument` rather than a bare ``str()`` per call site.
"""

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

    Transport-level only: a keyword that ran and failed is a
    :class:`KeywordFailure`, which is a different problem with a different fix.
    """


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
    """One ``run_keyword`` reply, unpacked.

    ``value`` is whatever the keyword returned -- a string, or a list of them.
    It only means anything when :attr:`passed`; on a failure it is typically
    empty and :attr:`error` carries the reason.
    """

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
        """Whether a keyword that answers with a boolean answered yes.

        The .NET side spells it ``"True"``; Robot's own conventions admit a
        few more spellings, and none of them are case-sensitive on the wire.
        """
        return self.passed and self.text.strip().casefold() in {"true", "yes", "1"}


def as_argument(value: Any) -> str:
    """Spell one argument the way the remote side parses it.

    ``None`` becomes the empty string rather than ``"None"``: several keywords
    take an optional path or JSON blob and read empty as "not given", whereas
    a literal ``None`` is a value they would try to use.
    """
    if value is None:
        return ""
    if isinstance(value, bool):
        return "True" if value else "False"
    return str(value)


class _TimeoutTransport(xmlrpc.client.Transport):
    """``ServerProxy`` takes no timeout, so the socket gets one here.

    Without it a keyword that hangs -- a game mid-dialog, a server wedged
    behind another client's session -- hangs the calling thread forever.
    """

    def __init__(self, timeout: float) -> None:
        super().__init__()
        self._timeout = timeout

    def make_connection(self, host: Any) -> http.client.HTTPConnection:
        connection = super().make_connection(host)
        connection.timeout = self._timeout
        return connection


class RemoteLibrary:
    """One keyword library on a Robot Framework remote server.

    The server exposes a separate endpoint per library, so an instance is
    addressed by ``base_url`` plus the library's dotted name. Instances are
    cheap and hold no connection between calls; every method opens its own.

    Every method here **blocks**. Async callers run them on a worker thread.
    """

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
        """Run a keyword and return its reply, pass or fail.

        Use this where a ``FAIL`` is an answer -- "is this button pressable"
        legitimately says no. Where it is a failure, use :meth:`run`.
        """
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
        """``None`` if the server answers for this library, else why not.

        Cheap and side-effect free -- it lists keyword names and throws the
        list away -- so it is safe to call on a status poll.

        The reason comes back rather than being thrown away because two
        different faults look identical from outside, and they have
        different fixes: nothing is listening at all, or *something* is
        listening that does not host this library. The second is what
        launching ``NRobot.Server.exe`` from the wrong working directory
        produces -- it resolves its keyword assemblies relative to the
        directory it was started in, which is why the .bat does ``cd %~dp0``
        first -- and it answers HTTP 404 rather than refusing the connection.
        """
        try:
            self.keyword_names()
        except RemoteError as exc:
            return str(exc)
        return None

    def reachable(self) -> bool:
        """Whether the server answers for this library at all."""
        return self.probe() is None
