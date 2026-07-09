"""Dependency-free SVG line chart for the equity / P&L curve.

Contract: pure string generation, no JavaScript and no external resources, so
the chart embeds directly in a self-contained HTML report. Values are exact
Decimals; only the pixel geometry uses floats (rendering, never accounting).
"""

from collections.abc import Sequence
from decimal import Decimal

from tradewind.report.model import EquityPoint

_WIDTH = 720
_HEIGHT = 240
_PAD = 40


def _fmt(value: float) -> str:
    return f"{value:.2f}"


def equity_svg(points: Sequence[EquityPoint], is_pnl: bool) -> str:
    """Render an equity/P&L polyline as an ``<svg>`` string.

    Degrades gracefully: zero points renders an empty frame with a note; one
    point renders a single marker.
    """
    title = "Cumulative P&L (marks at fill prices)" if is_pnl else "Equity (marks at fill prices)"
    if not points:
        return (
            f'<svg viewBox="0 0 {_WIDTH} {_HEIGHT}" role="img" class="chart">'
            f'<text x="{_WIDTH // 2}" y="{_HEIGHT // 2}" text-anchor="middle" '
            f'class="chart-empty">no fills to plot</text></svg>'
        )

    values = [p.value for p in points]
    vmin, vmax = min(values), max(values)
    span = vmax - vmin
    if span == 0:
        span = Decimal(1)  # avoid divide-by-zero; flat line sits mid-frame

    n = len(points)
    plot_w = _WIDTH - 2 * _PAD
    plot_h = _HEIGHT - 2 * _PAD

    def x_at(i: int) -> float:
        return _PAD + (plot_w * (i / (n - 1)) if n > 1 else plot_w / 2)

    def y_at(value: Decimal) -> float:
        frac = float((value - vmin) / span)
        return _PAD + plot_h * (1 - frac)

    coords = [(x_at(i), y_at(p.value)) for i, p in enumerate(points)]
    polyline = " ".join(f"{_fmt(x)},{_fmt(y)}" for x, y in coords)
    dots = "".join(
        f'<circle cx="{_fmt(x)}" cy="{_fmt(y)}" r="2.5" class="chart-dot" />' for x, y in coords
    )
    zero_line = ""
    if vmin < 0 < vmax:
        yz = y_at(Decimal(0))
        zero_line = (
            f'<line x1="{_PAD}" y1="{_fmt(yz)}" x2="{_WIDTH - _PAD}" y2="{_fmt(yz)}" '
            f'class="chart-zero" />'
        )

    return (
        f'<svg viewBox="0 0 {_WIDTH} {_HEIGHT}" role="img" aria-label="{title}" class="chart">'
        f'<text x="{_PAD}" y="20" class="chart-title">{title}</text>'
        f'<text x="{_PAD}" y="{_PAD - 6}" class="chart-axis">{_fmt(float(vmax))}</text>'
        f'<text x="{_PAD}" y="{_HEIGHT - _PAD + 14}" class="chart-axis">{_fmt(float(vmin))}</text>'
        f"{zero_line}"
        f'<polyline points="{polyline}" fill="none" class="chart-line" />'
        f"{dots}"
        f"</svg>"
    )
