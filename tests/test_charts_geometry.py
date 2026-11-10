"""Chart geometry helpers (pure functions, no display needed)."""

from __future__ import annotations

from disksweep.gui.charts import rounded_end_points


def test_rounded_end_points_shape() -> None:
    pts = rounded_end_points(10, 0, 110, 20, 4)
    xs, ys = pts[0::2], pts[1::2]
    assert min(xs) == 10 and abs(max(xs) - 110) < 1e-9  # square start, tip reaches x1
    assert min(ys) == 0 and max(ys) == 20
    assert (xs[0], ys[0]) == (10, 0) and (xs[-1], ys[-1]) == (10, 20)  # square baseline end
    tip = [y for x, y in zip(xs, ys, strict=True) if abs(x - 110) < 1e-9]
    assert tip and min(tip) >= 4 and max(tip) <= 16  # corners are rounded by r
