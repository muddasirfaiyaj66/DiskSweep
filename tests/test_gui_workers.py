"""The GUI task runner marshals results back without needing a display."""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from typing import Any

from disksweep.gui.workers import TaskRunner


class FakeWidget:
    """Stands in for a Tk widget: records ``after`` callbacks for manual pumping."""

    def __init__(self) -> None:
        self.pending: list[Callable[[], None]] = []
        self.thread = threading.get_ident()

    def after(self, ms: int, func: Callable[[], None]) -> str:
        self.pending.append(func)
        return "id"

    def pump(self, until: Callable[[], bool], timeout: float = 5.0) -> None:
        deadline = time.monotonic() + timeout
        while not until():
            assert time.monotonic() < deadline, "timed out"
            funcs, self.pending = self.pending, []
            for f in funcs:
                f()
            time.sleep(0.01)


def test_results_progress_and_errors_arrive_on_ui_thread() -> None:
    w = FakeWidget()
    runner = TaskRunner(w, poll_ms=1)
    seen: dict[str, Any] = {"progress": [], "threads": set()}

    def work(report: Callable[[Any], None], cancel: threading.Event) -> int:
        for i in range(3):
            report(i)
        return 42

    def on_progress(p: int) -> None:
        seen["progress"].append(p)
        seen["threads"].add(threading.get_ident())

    def on_done(r: int) -> None:
        seen["done"] = r
        seen["threads"].add(threading.get_ident())

    runner.submit(work, on_done=on_done, on_progress=on_progress)
    w.pump(lambda: "done" in seen)
    assert seen["done"] == 42
    assert seen["progress"] == [0, 1, 2]
    assert seen["threads"] == {w.thread}  # callbacks ran on the "UI" thread

    def failing(report: Callable[[Any], None], cancel: threading.Event) -> None:
        raise RuntimeError("boom")

    runner.submit(failing, on_done=lambda r: None, on_error=lambda e: seen.update(err=e))
    w.pump(lambda: "err" in seen)
    assert str(seen["err"]) == "boom"


def test_cancel_event_reaches_work() -> None:
    w = FakeWidget()
    runner = TaskRunner(w, poll_ms=1)
    started = threading.Event()
    result: dict[str, bool] = {}

    def work(report: Callable[[Any], None], cancel: threading.Event) -> bool:
        started.set()
        return cancel.wait(5)

    cancel = runner.submit(work, on_done=lambda r: result.update(cancelled=r))
    started.wait(5)
    cancel.set()
    w.pump(lambda: "cancelled" in result)
    assert result["cancelled"] is True
