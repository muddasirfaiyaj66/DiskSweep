"""Run blocking work off the Tk main thread.

Tkinter is not thread-safe: widgets may only be touched from the main thread.
:class:`TaskRunner` runs a function in a daemon thread and delivers progress,
results and errors back through a queue that the main loop polls with
``after()``, so the UI never freezes.
"""

from __future__ import annotations

import logging
import queue
import threading
from collections.abc import Callable
from typing import Any, Protocol

log = logging.getLogger(__name__)

Report = Callable[[Any], None]
Work = Callable[[Report, threading.Event], Any]


class _AfterCapable(Protocol):
    def after(self, ms: int, func: Callable[[], None]) -> str: ...


class TaskRunner:
    """Executes work in background threads and marshals callbacks to the UI thread.

    Args:
        widget: any Tk widget (provides ``after``).
        poll_ms: how often the queue is drained.
    """

    def __init__(self, widget: _AfterCapable, poll_ms: int = 80) -> None:
        self._widget = widget
        self._poll_ms = poll_ms
        self._queue: queue.Queue[tuple[Callable[..., None], tuple[Any, ...]]] = queue.Queue()
        self._widget.after(self._poll_ms, self._poll)

    def submit(
        self,
        work: Work,
        *,
        on_done: Callable[[Any], None],
        on_error: Callable[[BaseException], None] | None = None,
        on_progress: Callable[[Any], None] | None = None,
    ) -> threading.Event:
        """Start ``work(report, cancel)`` in a thread. Returns its cancel event."""
        cancel = threading.Event()

        def report(payload: Any) -> None:
            if on_progress is not None:
                self._queue.put((on_progress, (payload,)))

        def run() -> None:
            try:
                result = work(report, cancel)
            except BaseException as exc:
                log.exception("background task failed")
                if on_error is not None:
                    self._queue.put((on_error, (exc,)))
                return
            self._queue.put((on_done, (result,)))

        threading.Thread(target=run, daemon=True, name="disksweep-worker").start()
        return cancel

    def _poll(self) -> None:
        try:
            while True:
                callback, args = self._queue.get_nowait()
                try:
                    callback(*args)
                except Exception:
                    log.exception("UI callback failed")
        except queue.Empty:
            pass
        self._widget.after(self._poll_ms, self._poll)
