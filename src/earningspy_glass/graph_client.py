"""Plotly figures from a peers-percentile payload.

THE ONE RULE: no finance lives here.

Every sentinel decision, orientation, median, percentile and grade arrives
already computed. This module positions marks and reads labels. If you find
yourself about to write `if metric == "pe": value = -value`, or a median, or a
check for whether earnings are positive - stop. The payload already answered it,
and a second answer computed here is how the same company ends up labelled
differently on a chart and in a PDF.

What that means concretely:

  orientation   read `higher_is_better` from metrics[key]. Never hardcode which
                metrics invert.
  exclusion     read `flags.plot_pe` and `nm[metric]`. Never test a value for
                being non-positive.
  distributions read `distributions[grouping][metric]`. Never compute a
                quantile.
  grades        read `grade_scale.bands`. Never pick thresholds.

And it describes what a value is FOR, never how it was made: no talk of models,
scores-behind-the-score, inputs, windows or weightings — in a docstring, a label,
a tooltip or an error. The reader is a finance professional, not an engineer.

Charts are registered in _REGISTRY, so adding one is a builder plus a row. Fields
are registered in _FIELDS (see "field subscription" below): a field must be
subscribed before it renders, and an unsubscribed or undelivered field is a hard
error, not a silent skip.
"""

import difflib
import inspect
import json
import logging
import math
import re
import warnings

import plotly.graph_objects as go

from earningspy_glass import regime as _regime
from earningspy_glass._derived import nearest_in_size, outlier_tickers
from earningspy_glass.errors import ChartError
from earningspy_glass.filters import Filter, _norm_state, state_label

log = logging.getLogger(__name__)

DEFAULT_GROUPING = "industry"


def grouped(block, grouping=DEFAULT_GROUPING):
    """One peer group's entry of a block keyed by group (percentiles, distributions,
    reference, percentile_delta): the one asked for, else - a payload ranks against
    one group, its industry or its sector - the only one it carries."""
    block = block or {}
    if grouping in block:
        return block[grouping] or {}
    return next(iter(block.values())) or {} if len(block) == 1 else {}


class PrintedList(list):
    """A list whose describe()/catalog() PRINTED above — so its echo stays quiet.

    These helpers print a readable, grouped menu and also return the rows so a
    program can use them. In a notebook the returned value is echoed, and a raw
    list of tuples would dump the whole menu a second time as noise. This keeps
    the value a real list (index, iterate, ``list(...)``) while its repr is a
    one-line summary. ``noun`` names what the rows are ("measures", "fields").
    """

    def __new__(cls, rows, noun="rows"):
        self = super().__new__(cls, rows)
        return self

    def __init__(self, rows, noun="rows"):
        super().__init__(rows)
        self._noun = noun

    def __repr__(self):
        return f"<{len(self)} {self._noun} — printed above>"


# --- field subscription -----------------------------------------------------
#
# A field is rendered only once it is subscribed here. The subscription is
# presentation, never finance: it says a field is expected and how to draw it —
# a diverging colour ramp instead of a sequential one is the only property so
# far, because everything else (label, format, scale, polarity) is read live
# from the payload's own metric table.
#
# This list is the client's contract with the payload. Extending it is one line.
# The rule it buys: a metric the payload serves but nobody subscribed, or a
# metric subscribed here that the payload fails to deliver, is a loud error
# rather than a chart that quietly renders wrong.

# Fields whose zero is a meaningful centre, so a diverging ramp reads correctly.
# Purely a colour choice — it says nothing about what any field means.
_DIVERGING = frozenset({
    "eps_change", "eps_qoq", "eps_yoy_ttm", "eps_surprise", "rev_surprise",
    "sortino", "starr", "roic", "valuation_spread",
    "sharpe", "information_ratio", "sector_information_ratio",
    "sales_growth", "perf_half", "eps_revision", "oper_margin", "net_income",
    # An ownership stake that rose and one that fell are opposite stories, so
    # zero is the centre the ramp should pivot on.
    "inst_own_change", "insider_own_change",
})

# Every measure this client knows how to present. A measure the payload serves
# must appear here before it can be charted; anything served-but-unsubscribed is
# a loud error at the builder, never a quiet skip. Extending it is one line.
#
# Grouped only for reading; the groups carry no behaviour.
_SUBSCRIBED = _DIVERGING | frozenset({
    # value / growth
    "pe", "fwd_pe", "ep", "eps", "eps_change", "eps_qoq", "eps_yoy_ttm",
    "eps_surprise", "rev_surprise", "valuation_spread",
    "sales_growth", "eps_revision", "book_yield", "fcf_yield",
    # profitability / quality
    "roic", "roe", "sortino", "starr", "sharpe", "quality",
    "information_ratio", "sector_information_ratio",
    # risk
    "vol", "beta", "rsi", "var", "cvar", "max_drawdown",
    # momentum / positioning
    "perf_half", "short_float", "short_ratio",
    # cash / balance sheet
    "fcf", "ebit", "ebitda", "gross_margin", "net_margin", "oper_margin",
    "sales", "net_income", "book_per_share",
    "debt_eq", "ltdebt_eq", "debt", "current_ratio", "quick_ratio", "cash_per_share",
    # ESpy Risk Score — event risk, higher = calmer (bronze, free)
    "espy_risk",
    # size / market
    "market_cap", "avg_volume", "prev_close",
    # ownership — who holds the float
    "inst_own", "insider_own", "float_shares", "float_pct",
    # the resolved read carried by companies that have already reported. On a
    # post payload the live quality/direction/abnormality columns are empty
    # (nothing is still open), and these frozen copies are the ones peers carry
    # and the API ranks — so charting a post peer set uses these.
    "quality_prob", "is_abnormal_prob", "conviction",
    "pre_event_quality_prob", "pre_event_is_abnormal_prob", "pre_event_conviction",
    # Silver V3's weekly quality percentile, live and frozen pre-event
    "silver_pctl", "pre_event_silver_pctl",
})


def _known(payload):
    """Metric keys the payload serves. The set the subscription is checked against."""
    return set(payload.get("metrics") or {})


def require_subscribed(payload, *metrics):
    """Fail loudly for any named metric this client will not present.

    Two ways a metric is unusable, both errors rather than a quiet empty chart:
    the payload does not serve it (a stale axis name, or a tier that omits it),
    or the payload serves it but this client has not subscribed it (a new field
    that needs a one-line registration before it can be drawn). Either way the
    message names the field and points at describe().
    """
    served = _known(payload)
    for metric in metrics:
        if metric is None:
            continue
        if metric not in served:
            raise ChartError(
                f"{metric!r} is not available on this payload — run describe() "
                f"to see what is. (A higher tier may include it.)"
            )
        if metric not in _SUBSCRIBED:
            raise ChartError(
                f"{metric!r} is served by the API but this version of "
                f"earningspy-glass does not present it yet — upgrade the package, "
                f"or run describe() for the fields it does show."
            )
        if not _has_values(payload, metric):
            twin = _resolved_twin(payload, metric)
            if twin:
                raise ChartError(
                    f"{metric!r} has no values on this payload — these peers have "
                    f"reported; plot {twin!r} (the ex-ante value) instead."
                )
            layers = ", ".join(payload.get("layers_included") or []) or "unknown"
            if "gold" in (payload.get("layers_included") or []):
                # every layer is in: the tier isn't what's missing, the companies don't report it
                raise ChartError(
                    f"{metric!r} has no values on this payload (layers: {layers}) — no company "
                    f"in this group reports it."
                )
            raise ChartError(
                f"{metric!r} has no values on this payload (layers: {layers}) — "
                f"a higher tier may include it."
            )


def _has_values(payload, metric, grouping=DEFAULT_GROUPING):
    """Whether any company carries a value or a standing for ``metric``.

    A measure listed with no values anywhere is what a field above the caller's
    tier looks like; drawing it would be an empty chart, so it is refused like
    a measure the payload does not carry. EarningSpy Studio applies the same rule.
    """
    for point in payload.get("points") or []:
        if (point.get("values") or {}).get(metric) is not None:
            return True
        if grouped(point.get("percentiles"), grouping).get(metric) is not None:
            return True
    return False


# --- choosing the peers a chart names ----------------------------------------
#
# The same rule as EarningSpy Studio's charts, so a preview here and a branded
# render there name the same companies.

PEER_COUNTS = (1, 2, 3, 5, 8)   # 1 or 2 make a straight "vs" comparison
DEFAULT_PEERS = 5


def _find(payload, ticker):
    ticker = (ticker or "").upper()
    return next((p for p in payload.get("points") or []
                 if (p.get("ticker") or "").upper() == ticker), None)


def _require_point(payload, ticker, role):
    point = _find(payload, ticker)
    if point is None:
        known = ", ".join(sorted((p.get("ticker") or "") for p in payload.get("points") or []))
        raise ChartError(f"{str(ticker).upper()!r} is not in this peer group, so it cannot be "
                         f"the {role}. Companies it carries: {known}.")
    return point


def select_peers(payload, focus, peers=DEFAULT_PEERS, tickers=None, among=None):
    """The peers a chart names next to the focus company: a list of points.

    ``tickers`` names them exactly (each must be in the payload, or this
    raises). Otherwise the ``peers`` companies closest to the focus in market
    cap, compared on a log scale; ``peers`` must be 1, 2, 3, 5 or 8. ``among``
    limits the automatic choice (e.g. to the companies a scatter can plot).
    """
    focus_point = _require_point(payload, focus, "focus company")
    focus_ticker = (focus_point.get("ticker") or "").upper()
    if tickers is not None:
        chosen, seen = [], {focus_ticker}
        for t in [tickers] if isinstance(tickers, str) else tickers:
            point = _require_point(payload, t, "peer")
            key = (point.get("ticker") or "").upper()
            if key not in seen:
                seen.add(key)
                chosen.append(point)
        return chosen
    if peers not in PEER_COUNTS:
        raise ChartError(f"peers must be one of {', '.join(map(str, PEER_COUNTS))}; got {peers!r}.")
    if _market_cap(focus_point) is None:
        # No size to be near: the biggest names stand in (the chart's own fallback).
        pool = [p for p in (among if among is not None else payload.get("points") or [])
                if (p.get("ticker") or "").upper() != focus_ticker and _market_cap(p)]
        return sorted(pool, key=_market_cap, reverse=True)[:peers]
    return nearest_peers(payload, focus_ticker, peers, among=among)


def _market_cap(point):
    v = (point.get("values") or {}).get("market_cap")
    return v if isinstance(v, (int, float)) and v > 0 else None


def nearest_peers(payload, focus, n=3, among=None):
    """The ``n`` points closest to ``focus`` in market cap (log scale), closest first.

    The rule the charts use to pick peers on their own, for any ``n``. Companies
    without a market cap are skipped; a focus without one is an error - there is
    no size to be near.
    """
    focus_point = _require_point(payload, focus, "focus company")
    focus_ticker = (focus_point.get("ticker") or "").upper()
    anchor_cap = _market_cap(focus_point)
    if anchor_cap is None:
        raise ChartError(f"{focus_ticker} has no market cap, so no peer is nearest to it "
                         f"— use largest() or name tickers= yourself.")
    if not isinstance(n, int) or n < 1:
        raise ChartError(f"n must be a positive whole number; got {n!r}.")
    pool = [p for p in (among if among is not None else payload.get("points") or [])
            if (p.get("ticker") or "").upper() != focus_ticker and _market_cap(p)]
    return nearest_in_size(anchor_cap, pool, n, _market_cap)


# --- themes -----------------------------------------------------------------
#
# Two palettes, one layout. Serif titles and a muted grid so the marks carry the
# page rather than the chrome.
#
# The colour RAMPS are custom (not Plotly's stock scales) so they read punchy for
# a financial surface rather than washing out:
#   sequential  deep teal -> green, for a magnitude (market cap, volume). Never
#               bottoms out to near-white, so the low end stays visible.
#   diverging   amber (negative) -> neutral -> teal (positive), for a signed
#               metric pivoting at zero. Colourblind-safer than red/green and
#               distinctly fintech rather than traffic-light. Symmetric endpoints
#               so equal magnitudes read equally strong either side of zero.
# Each is a [[position, colour], …] list, which Plotly accepts anywhere a named
# scale would go. Light and dark variants keep the endpoints legible on each ground.

_SEQ_LIGHT = [[0.0, "#e5f5f0"], [0.25, "#99d8c9"], [0.5, "#41ae9a"],
              [0.75, "#1a8a72"], [1.0, "#00443a"]]
_SEQ_DARK = [[0.0, "#12403a"], [0.25, "#1a7a68"], [0.5, "#2ca88f"],
             [0.75, "#5cc9b0"], [1.0, "#a7e8d8"]]
_DIV_LIGHT = [[0.0, "#b3541e"], [0.25, "#d98032"], [0.5, "#f4f1ea"],
              [0.75, "#2a9d8f"], [1.0, "#0f6e6e"]]
_DIV_DARK = [[0.0, "#e0954a"], [0.25, "#c9772f"], [0.5, "#2b2f38"],
             [0.75, "#3aa89a"], [1.0, "#6fd6c6"]]

_THEMES = {
    "light": {
        "bg": "#ffffff", "panel": "#fbfaf8", "ink": "#1a1a1a",
        "muted": "#6b6b6b", "grid": "#e8e6e1", "line": "#c9c5bd",
        "accent": "#1f5c8b", "anchor": "#c0392b",
        "sequential": _SEQ_LIGHT, "diverging": _DIV_LIGHT,
        # Categorical palette for multi-series charts (radar / bar_pctile / donut).
        # Anchor first so the focus company always reads in the anchor colour;
        # the rest are drawn from the same muted family as the rest of the theme
        # rather than ad-hoc hex, so a two-series chart matches a ten-series one.
        "series": ["#c0392b", "#1f5c8b", "#2e7d5b", "#8a6d3b", "#6d5b8a",
                   "#4a7a8c", "#a3562f"],
    },
    "dark": {
        "bg": "#12141a", "panel": "#171a21", "ink": "#eceff4",
        "muted": "#9aa3b2", "grid": "#252932", "line": "#39404e",
        "accent": "#6cb6e0", "anchor": "#e8734a",
        "sequential": _SEQ_DARK, "diverging": _DIV_DARK,
        "series": ["#e8734a", "#6cb6e0", "#5cc39a", "#d0b56a", "#b59ad6",
                   "#79c2d0", "#e0956a"],
    },
}

_TITLE_FONT = "Iowan Old Style, Palatino, Georgia, serif"
_BODY_FONT = "Inter, Helvetica Neue, Arial, sans-serif"


def _theme(name):
    if name not in _THEMES:
        raise ChartError(
            f"unknown theme {name!r} — choose one of {', '.join(_THEMES)}."
        )
    return _THEMES[name]


def _layout(fig, t, title, subtitle=None, height=620):
    fig.update_layout(
        template=None,
        paper_bgcolor=t["bg"], plot_bgcolor=t["panel"],
        font=dict(family=_BODY_FONT, color=t["ink"], size=13),
        title=dict(
            text=(f"<span style='font-family:{_TITLE_FONT};font-size:21px'>"
                  f"{title}</span>"
                  + (f"<br><span style='font-size:12.5px;color:{t['muted']}'>"
                     f"{subtitle}</span>" if subtitle else "")),
            # A fixed distance from the top (≈22px), not a share of the height:
            # on a tall chart (a long ranking) a share lands inside the plot.
            x=0.012, xanchor="left", y=1 - 22 / height, yanchor="top",
        ),
        margin=dict(l=64, r=34, t=96 if subtitle else 74, b=58),
        height=height,
        hoverlabel=dict(bgcolor=t["bg"], bordercolor=t["line"],
                        font=dict(family=_BODY_FONT, size=12, color=t["ink"]),
                        # Force left alignment. Plotly otherwise right-aligns the
                        # text for points on the right of the plot (and centres
                        # near the middle), which shifts every line independently
                        # and destroys the space-padded two-column layout. Left
                        # alignment keeps the columns lined up wherever the point
                        # sits.
                        align="left"),
        # Resolve hover to the nearest single marker, not an x/y-unified band.
        # (Tooltip HEIGHT is what made points near an edge show nothing — that is
        # capped in _hover via _HOVER_MAX_LINES, not here.)
        hovermode="closest",
        showlegend=False,
    )
    # Force left alignment ON EVERY TRACE, not just the layout. Plotly does not
    # reliably propagate layout.hoverlabel.align to individual traces, so a point
    # on the right of the plot still right-aligns its tooltip — which shifts each
    # line independently and breaks the space-padded two-column layout. Setting it
    # per trace here (after the builder has added them all) makes it stick.
    fig.update_traces(hoverlabel=dict(align="left"))

    axis = dict(gridcolor=t["grid"], zerolinecolor=t["line"],
                linecolor=t["line"], tickfont=dict(color=t["muted"], size=11),
                title_font=dict(color=t["muted"], size=12.5))
    fig.update_xaxes(**axis)
    fig.update_yaxes(**axis)
    return fig


def _rgba(hex_color, alpha):
    """A ``#rrggbb`` colour as an ``rgba(...)`` string — for a translucent fill."""
    h = hex_color.lstrip("#")
    if len(h) != 6:
        return hex_color
    try:
        r, g, b = (int(h[i:i + 2], 16) for i in (0, 2, 4))
    except ValueError:
        return hex_color
    return f"rgba({r},{g},{b},{alpha})"


# Formats for plain row columns the frame carries but the metrics{} block does
# not describe — the calendar identity/price columns (MARKET_CAP, P/E, …). They
# are real, plottable and shown on hover, so a hint keeps them from rendering as
# a bare 4537070000000.0. The API states a format where it has one; this fills
# only the gap for the undescribed plain columns, and is never applied over a
# metrics{} format.


# --- formatting -------------------------------------------------------------
#
# The payload names a format per metric; this maps those names to strings. It is
# presentation only - no value is transformed, just rendered.

def _compact(value):
    magnitude = abs(value)
    for cutoff, suffix in ((1e12, "T"), (1e9, "B"), (1e6, "M"), (1e3, "K")):
        if magnitude >= cutoff:
            return f"{value / cutoff:,.2f}{suffix}"
    return f"{value:,.0f}"


_FORMATTERS = {
    # Sign leads the $ (-$2.30B, not $-2.30B) — the natural way a loss reads.
    "currency_compact": lambda v: f"{'-' if v < 0 else ''}${_compact(abs(v))}",
    "currency_2dp": lambda v: f"{'-' if v < 0 else ''}${abs(v):,.2f}",
    "ratio_x": lambda v: f"{v:,.1f}x",
    "ratio_2dp": lambda v: f"{v:,.2f}",
    "percent": lambda v: f"{v * 100:,.1f}%",
    "percent_signed": lambda v: f"{v * 100:+,.1f}%",
    # Percentile POINTS, already 0-100 - so no ×100 (that is the "3210%" trap the
    # calendar's percentile columns would fall into with the "percent" format).
    # `_signed` is for a rank MOVEMENT, where the sign is the story.
    "percentile_points": lambda v: f"{v:,.1f}",
    "percentile_points_signed": lambda v: f"{v:+,.1f}",
    # A count of shares, so compact but unprefixed: 628.08K, not $628.08K.
    "shares_count": _compact,
    # --- raw-value formats the calendar's underlying figures arrive with -----
    # A fraction the API stores as 0.0631 and a reader wants as 6.3%.
    "percent_fraction": lambda v: f"{v * 100:,.1f}%",
    # A plain ratio (earnings yield, a multiple) — two decimals, no unit.
    "ratio": lambda v: f"{v:,.2f}",
    # A large money figure — abbreviated B/M/K so a market cap or free cash flow
    # reads as $1.36B, not $1,360,000,000. The sign leads the $ (-$2.30B).
    "currency": lambda v: f"{'-' if v < 0 else ''}${_compact(abs(v))}",
    # A per-share money figure — small, so shown in full to the cent (-$0.30).
    "currency_per_share": lambda v: f"{'-' if v < 0 else ''}${abs(v):,.2f}",
    # A plain count (share volume) — abbreviated, unprefixed: 1.58M, not $1.58M.
    "count": _compact,
}


def format_value(value, fmt):
    """Render one value. Null renders as n.m., never as zero or a blank."""
    if value is None or (isinstance(value, float) and
                         (math.isnan(value) or math.isinf(value))):
        return "n.m."
    formatter = _FORMATTERS.get(fmt)
    if formatter is None:
        return f"{value:,.2f}" if isinstance(value, (int, float)) else str(value)
    try:
        return formatter(value)
    except (TypeError, ValueError):
        return str(value)


# --- reading the payload ----------------------------------------------------

def _meta(payload, metric):
    """Metric semantics, or None when the payload does not serve it.

    A quiet lookup used by the readers below. Loudness lives at the builder
    entry points: each builder calls require_subscribed() on the metrics the
    caller actually named, so a bad axis fails there with a clear message rather
    than here. This stays quiet so an OPTIONAL encoding (a colour or size the
    payload happens not to carry) can be skipped without taking the figure down.
    """
    return (payload.get("metrics") or {}).get(metric)


def _label(payload, metric):
    """A measure's name as drawn: with PRE_EVENT_MARK when it is computed pre event."""
    meta = (payload.get("metrics") or {}).get(metric) or {}
    name = meta.get("label", metric)
    return name + _mark(_basis(payload, metric))


# --- measures computed before the event ----------------------------------------------
#
# A pre_event_* field is a read taken before a report, by definition; so is a
# live score a company carries as its pre-report read. Such a measure is
# marked where its name is drawn - "Quality Probability*" - and the figure carries
# one tiny line, PRE_EVENT_FOOTNOTE. Company names stay plain. The same rule as
# EarningSpy Studio.

PRE_EVENT_MARK = "*"
PRE_EVENT = "pre_event"
# The mark each note is drawn with. The service says which measures carry a note and
# in what words (metrics[].basis and the basis table); a note it adds later gets the
# last mark.
_MARKS = {PRE_EVENT: PRE_EVENT_MARK, "pre_event_90d": "**", "one_year_spread": "***"}
_OTHER_MARK = "\u2020"


def _mark(code):
    return _MARKS.get(code, _OTHER_MARK) if code else ""


def _basis(payload, metric):
    """The code of a measure's note, or None. A score carried as its pre-report read
    is one computed pre event, whatever its own basis."""
    if metric in pre_event_keys(payload):
        return PRE_EVENT
    return ((payload.get("metrics") or {}).get(metric) or {}).get("basis")


def _note(payload, code):
    """A note in the service's words."""
    return (payload.get("basis") or {}).get(code) or code.replace("_", " ")


def footnote(payload, code):
    """The key line for a note's mark: "** 90 days pre-event window"."""
    words = _note(payload, code)
    return f"{_mark(code)} {words[:1].upper()}{words[1:]}"


def _codes(payload, keys):
    """The notes these measures carry, in the marks' order."""
    found = {c for k in keys if (c := _basis(payload, k))}
    return [c for c in _MARKS if c in found] + sorted(found - set(_MARKS))


# A chart ABOUT one or two measures (scatter axes, bar, bar_signed, treemap
# colour, shift_scatter) names them plainly in its title and axes - no "*" - and
# the footnote names them instead: "Sortino, STARR: 90 days pre-event window".
# Charts with a row per measure keep the marks on the rows and the symbol key.
# The same rule as EarningSpy Studio.
_NAMED_META = "earningspy_named"


def _name(payload, metric):
    """A measure's name with no mark."""
    return ((payload.get("metrics") or {}).get(metric) or {}).get("label", metric)


def named_marks(payload, keys):
    """Footnote lines that name the chart's own marked measures, in mark order."""
    keys = [k for k in dict.fromkeys(keys) if k]
    return [", ".join(_name(payload, k) for k in keys if _basis(payload, k) == code) + ": " + _note(payload, code)
            for code in _codes(payload, keys)]


_MEASURES_META = "earningspy_measures"
_MEASURE_LABELS_META = "earningspy_measure_labels"


def _declare_measures(fig, keys):
    """Record every measure a chart drew, in order, for GlassFigure.measures()."""
    meta = fig.layout.meta if isinstance(fig.layout.meta, dict) else {}
    fig.update_layout(meta={**meta, _MEASURES_META: [k for k in dict.fromkeys(keys) if k]})
    return fig


def _declare_named(fig, keys):
    """Record the measures a chart is about, for _mark_pre_event to name."""
    meta = fig.layout.meta if isinstance(fig.layout.meta, dict) else {}
    fig.update_layout(meta={**meta, _NAMED_META: [k for k in keys if k]})
    return fig


def _unmark(payload, value, keys):
    """``value`` with the marked names of ``keys`` replaced by their plain names."""
    pairs = sorted({(_label(payload, k), _name(payload, k)) for k in keys if k
                    and _label(payload, k) != _name(payload, k)}, key=lambda p: -len(p[0]))
    if not pairs:
        return value

    def walk(v):
        if isinstance(v, str):
            for marked, plain in pairs:
                v = v.replace(marked, plain)
            return v
        if isinstance(v, dict):
            return {k: walk(x) for k, x in v.items()}
        if isinstance(v, (list, tuple)):
            return [walk(x) for x in v]
        return v
    return walk(value)


def pre_event_keys(payload):
    """The measures of this payload computed before the event."""
    keys = {k for k in (payload.get("metrics") or {}) if str(k).startswith("pre_event_")}
    for point in payload.get("points") or []:
        keys |= {k for k, read in (point.get("reads") or {}).items() if read == "pre_report"}
    return keys


def marks_used(payload, fig_json):
    """The key lines a figure needs, in order: one per mark whose measure name appears."""
    marked = set(payload.get("metrics") or {}) | pre_event_keys(payload)
    drawn = [k for k in marked if _basis(payload, k) and _label(payload, k) in fig_json]
    return [footnote(payload, code) for code in _codes(payload, drawn)]


def _mark_pre_event(fig, payload):
    """Add the tiny key lines under the figure for the marked measures on it.

    A chart about one or two measures (declared with _declare_named) draws them
    plain and names them in the footnote; any other marked name drawn keeps its
    symbol key line.
    """
    meta = fig.layout.meta if isinstance(fig.layout.meta, dict) else {}
    named = list(meta.get(_NAMED_META) or [])
    if named:
        rest = {k: v for k, v in meta.items() if k != _NAMED_META}
        whole = fig.to_dict()
        whole.setdefault("layout", {})["meta"] = rest or None
        fig = go.Figure(_unmark(payload, whole, named))
    # What is DRAWN, not what a hover would show: every hover lists many measures.
    drawn = fig.to_dict()
    for trace in drawn.get("data", []):
        for k in ("hovertemplate", "hovertext", "customdata"):
            trace.pop(k, None)
    lines = named_marks(payload, named) + marks_used(
        payload, json.dumps(drawn, ensure_ascii=False, default=str))
    if not lines:
        return fig
    bottom = fig.layout.margin.b if fig.layout.margin.b is not None else 58
    fig.add_annotation(text="<br>".join(lines), showarrow=False, xref="paper", yref="paper",
                       x=0, y=0, xanchor="left", yanchor="top", yshift=-(bottom - 4),
                       align="left", font=dict(size=9.5, color="#8a8a8a"))
    fig.update_layout(margin=dict(b=bottom + 14 * len(lines)))
    return fig


def _fmt(payload, metric):
    meta = (payload.get("metrics") or {}).get(metric) or {}
    return meta.get("format")


def _is_log(payload, metric):
    """log and log_signed both want a log axis; the payload decides which."""
    meta = (payload.get("metrics") or {}).get(metric) or {}
    return meta.get("scale") in ("log", "log_signed")


def oriented(percentile, payload, metric):
    """Natural percentile turned into an attractiveness percentile.

    The single place polarity is applied, and it is applied by READING
    `higher_is_better` rather than by knowing anything about the metric. A
    directionless metric is returned unchanged and excluded upstream.
    """
    if percentile is None:
        return None
    meta = (payload.get("metrics") or {}).get(metric) or {}
    direction = meta.get("higher_is_better")
    if direction is None:
        return percentile
    return percentile if direction else 100.0 - percentile


def is_oriented_metric(payload, metric):
    """Whether a metric belongs in an attractiveness view at all."""
    meta = (payload.get("metrics") or {}).get(metric) or {}
    return meta.get("higher_is_better") is not None


def _direction_hint(payload, metric):
    """A short 'which way is good' phrase from the payload's own orientation."""
    meta = (payload.get("metrics") or {}).get(metric) or {}
    d = meta.get("higher_is_better")
    return "higher is better" if d else ("lower is better" if d is False
                                         else "direction neutral")


def _axis_title(payload, metric, hints=True):
    """An axis label: the measure's name, with the direction hint small beneath
    (``hints=False`` leaves the name alone).

    So a reader sees at a glance which end is the good one — read from the
    payload's ``higher_is_better``, never decided here. Matches the calendar's
    axis convention.
    """
    if not hints:
        return _label(payload, metric)
    return (f"{_label(payload, metric)}<br>"
            f"<span style='font-size:9.5px'>{_direction_hint(payload, metric)}"
            f"</span>")


def grade_for(percentile, payload, metric):
    """A letter from an oriented percentile, using the payload's own bands.

    Null in means n.m. out. An F would assert something about a company that
    could not be measured.
    """
    if percentile is None or not is_oriented_metric(payload, metric):
        return "n.m."
    value = oriented(percentile, payload, metric)
    for band in (payload.get("grade_scale") or {}).get("bands") or []:
        if value >= band["min"]:
            return band["grade"]
    return "n.m."


def _bar_hint(payload, metric):
    """Under a measure's name on oriented bars: "lower is better" when its raw value
    reads the other way, else nothing."""
    d = ((payload.get("metrics") or {}).get(metric) or {}).get("higher_is_better")
    return "lower is better" if d is False else None


def _left_off_line(payload, points, metrics):
    """What a chart says about companies it could not place: "11 left off: negative
    equity". Said on the chart - a reader must know the group is short, and why."""
    counts = {}
    for p in points:
        why = next((r for m in metrics if (r := _unplottable_reason(p, m))), None)
        if why:
            counts[why] = counts.get(why, 0) + 1
    if not counts:
        return ""
    total = sum(counts.values())
    return f"{total} left off: " + ", ".join(
        f"{why} ({n})" if len(counts) > 1 else why
        for why, n in sorted(counts.items(), key=lambda kv: -kv[1]))


def left_off_by_axis(points, metrics):
    """Which axis dropped each company, and why: "fwd_pe: non-positive forward
    earnings (GPRO, VUZI); pe: no data (AXIL)". For logs, where tickers help."""
    groups = {}
    for p in points:
        for m in metrics:
            why = _unplottable_reason(p, m)
            if why:
                groups.setdefault((m, why), []).append(p.get("ticker") or "?")
                break
    return "; ".join(f"{m}: {why} ({', '.join(t)})" for (m, why), t in groups.items())


def rank_group(payload):
    """What this payload's percentiles rank against: "sector" or "industry". The two are
    different numbers, so every word about a rank says this one. The same rule as Studio."""
    column = ((payload.get("request") or {}).get("peer_group") or {}).get("column") or "industry"
    return "sector" if str(column).lower() == "sector" else "industry"


def neutral_label(percentile):
    """What a grade card writes for a ranked measure with no direction: its
    percentile in the peer group ("P82"), since a letter would claim a good end."""
    return f"P{percentile:.0f}"


def _points(payload, grouping=DEFAULT_GROUPING, include_anchor=True):
    return [p for p in payload.get("points") or []
            if include_anchor or p.get("role") != "anchor"]


def _pct(point, metric, grouping=DEFAULT_GROUPING):
    return grouped(point.get("percentiles"), grouping).get(metric)


def require_industry_ranks(payload, kind):
    """Refuse a ranking chart when the key could not read industry percentiles.

    Industry percentiles are a silver feature: for a bronze key the API removes
    them and says so in ``withheld``. A ranking chart drawn from such a payload
    would show every company as unranked, so it is refused, in the same words
    EarningSpy Studio uses.
    """
    tier = (payload.get("withheld") or {}).get("industry_percentiles")
    if tier:
        raise ChartError(
            f"{kind} ranks companies against their {rank_group(payload)}: {rank_group(payload)} percentiles need a "
            f"{tier} key, and this payload has raw values only. Fetch it with "
            f"tier='{tier}' or higher."
        )


# --- valuation state ----------------------------------------------------------
#
# `valuation="state"` on the ranked charts shows the valuation state the API
# delivers instead of a percentile. Every company is shown, whatever its state.

VALUATION_VIEWS = ("percentile", "state")
_STATE_METRIC = "valuation_spread"
STATE_ROW_LABEL = "Valuation State"


# Categorical tags: a short label on a measure's bar, read from a categorical
# field of the company. One entry per tag: the measure it rides on and its words.
# The same table as EarningSpy Studio.
TAGS = {
    "valuation_state": {
        "measure": _STATE_METRIC,
        # Short enough for a bar; a state with no words here reads as the service names it.
        "text": {"still_cheap": "Still cheap", "newly_cheap": "Newly cheap",
                 "exiting": "Exiting", "normal": "Fair"},
    },
}


def _check_tags(tags):
    if tags is None:
        return []
    tags = [tags] if isinstance(tags, str) else list(tags)
    unknown = [t for t in tags if t not in TAGS]
    if unknown:
        raise ChartError(f"unknown tag {unknown[0]!r} — choose from {', '.join(TAGS)}.")
    return tags


def _states(payload):
    """The valuation states the service declares, in its order: key -> its record."""
    return {s.get("key"): s for s in payload.get("valuation_states") or []}


def _tag_for(payload, point, tag, focus=False):
    """{"text", "strong"} for a company's tag, or None: no value, or a value that is
    not worth naming (the service says which) on a company that is not the focus."""
    if tag == "valuation_state":
        state = _state(point)
        known = _states(payload).get(state)
        if known and (focus or known.get("notable")):
            text = TAGS[tag]["text"].get(state) or str(known.get("label") or state).capitalize()
            return {"text": text, "strong": bool(known.get("strong"))}
    return None


def _state_view(valuation):
    """True for `valuation="state"`; anything but the two views is an error."""
    if valuation not in VALUATION_VIEWS:
        raise ChartError(
            f"valuation must be 'percentile' or 'state', not {valuation!r}."
        )
    return valuation == "state"


def _state(point):
    return _norm_state((point.get("valuation") or {}).get("VALUATION_STATE")) or None


def _state_rank(payload, point):
    """A company's place in the service's order of states (no state last)."""
    order = list(_states(payload))
    s = _state(point)
    return order.index(s) if s in order else len(order)


def _state_text(point):
    """The reader-facing state ("Still cheap"), or "n/a" when the payload has none."""
    s = _state(point)
    return state_label(s).capitalize() if s else "n/a"


def _state_hover(point):
    now = (point.get("values") or {}).get(_STATE_METRIC)
    prior = (point.get("valuation") or {}).get("VALUATION_SPREAD_PRIOR")
    parts = [f"valuation state: {_state_text(point)}"]
    if now is not None:
        parts.append(f"spread {now:+.1%}")
    if prior is not None:
        parts.append(f"prior quarter {prior:+.1%}")
    return "<br>".join(parts)


_NM_REASONS = {"nonpositive_earnings": "non-positive earnings", "missing": "no data",
               # Forward P/E reads next year's estimate; the source has none below zero.
               "nonpositive_forward_earnings": "non-positive forward earnings",
               "stale": "out of date",
               # FCF comes only from market cap / P/FCF, published only for a positive FCF.
               "no_positive_fcf": "no positive FCF, or no data",
               # The source writes debt/equity as 0 when equity is negative.
               "negative_equity": "negative equity"}


def _unplottable_reason(point, metric):
    """Why this point cannot sit on this metric's axis (Studio's wording), or None."""
    reason = (point.get("nm") or {}).get(metric)
    if reason:
        return _NM_REASONS.get(reason, str(reason).replace("_", " "))
    if metric == "pe" and not (point.get("flags") or {}).get("plot_pe", False):
        return _NM_REASONS["nonpositive_earnings"]
    if (point.get("values") or {}).get(metric) is None:
        return _NM_REASONS["missing"]
    return None


def focus_unplaceable(payload, ticker, metric, reason):
    """What a scatter says when its own focus company cannot be placed. Studio
    refuses with it (a static chart without its subject would mislead); the
    interactive chart draws the rest and says it, on the chart and as a warning."""
    hint = (' Try x="ep" (Earnings Yield): it plots through negative earnings.'
            if metric in ("pe", "fwd_pe") else "")
    return (f"{str(ticker).upper()} cannot be placed on this scatter: no usable "
            f"{_label(payload, metric)} ({reason}).{hint}")


def _plottable(point, metric):
    """Whether this point may appear on this metric's axis.

    `plot_pe` is the payload's own verdict on trailing P/E; forward P/E has its own
    `nm` reason when next year's estimate is not positive. Reading them is the whole
    mechanism - there is deliberately no test of the value here.
    """
    if metric == "pe" and not (point.get("flags") or {}).get("plot_pe", False):
        return False
    return (point.get("values") or {}).get(metric) is not None


# --- colour -----------------------------------------------------------------
#
# Diverging where zero is a meaningful centre, sequential otherwise. The choice
# is carried by the subscription list (_DIVERGING, near the top): presentation,
# not finance — which ramp reads correctly, not what any metric means.

def _colorscale(payload, metric, theme):
    diverging = metric in _DIVERGING
    return (theme["diverging"] if diverging else theme["sequential"]), diverging


def _color_range(values, diverging, payload=None, metric=None):
    """Symmetric around zero for a diverging ramp, so the midpoint is zero.

    Clipped to the payload's `color_clip_pct` when it names one: growth ratios
    have tails in the hundreds, and one of them would otherwise flatten the
    whole ramp. The DATA is never clipped, only the colour mapping.
    """
    clean = [v for v in values if v is not None]
    if not clean:
        return None, None
    lo, hi = min(clean), max(clean)

    clip = ((payload or {}).get("metrics") or {}).get(metric or "", {}) \
        .get("color_clip_pct")
    if clip and len(clean) > 4:
        ordered = sorted(clean)
        low_pct, high_pct = clip
        lo = ordered[int(len(ordered) * low_pct / 100.0)]
        hi = ordered[min(len(ordered) - 1,
                         int(len(ordered) * high_pct / 100.0))]

    if diverging:
        bound = max(abs(lo), abs(hi)) or 1.0
        return -bound, bound
    return lo, hi


# --- hover ------------------------------------------------------------------

# Blocks the payload carries beside `values`, in the order a reader wants them.
# Each is raw - already-resolved facts the API does not rank, so the tooltip
# shows them verbatim rather than deriving anything. The headings describe what
# the reader gets from each block, never how it was produced (the block KEY is
# the API's own; the HEADING is ours and stays utility-facing).
_RAW_BLOCKS = (
    ("outcomes", "Abnormality Scores"),
    ("model", "Forward read"),
    ("valuation", "Valuation"),
)

# Outcome keys that are returns (a fraction the reader wants as a %). Rendered
# with the percent format in the raw block; everything else in the block shows
# verbatim. The API stores these as fractions (CAR_3 = -0.0893 -> -8.9%).
_OUTCOME_PERCENT_KEYS = frozenset({
    "CAR_3", "CAR_30", "CAR_60",
    "EXP.RET_3", "EXP.RET_30", "EXP.RET_60",
    "1+3_RET", "1+30_RET", "1+60_RET",
    "CAR_30_CAL", "CAR_60_CAL", "1+30_CAL_RET", "1+60_CAL_RET",
    "SPX_30_CAL_RET", "SPX_60_CAL_RET",
})

# The order returns read best in on the tooltip: realized cumulative abnormal
# return, then expected, then the raw realized return beside the S&P 500 over the
# same dates — 3 days in business days, 30 / 60 in calendar days (the windows a
# reader means). The business-day 30 / 60 columns close the list, labelled as
# such. A key absent from the payload is simply skipped; a key present but null
# renders as n.m. (the event has not matured to that horizon yet).
_OUTCOME_RETURN_ORDER = (
    "CAR_3", "CAR_30_CAL", "CAR_60_CAL",
    "EXP.RET_3", "EXP.RET_30", "EXP.RET_60",
    "1+3_RET", "1+30_CAL_RET", "1+60_CAL_RET",
    "SPX_30_CAL_RET", "SPX_60_CAL_RET",
    "CAR_30", "CAR_60", "1+30_RET", "1+60_RET",
)

# --- hover views ------------------------------------------------------------
#
# "compact" (the default) shows what a reader scans first; "all" shows every
# measure and block the payload carries; "encoded" shows only what the chart
# draws. The compact sets are a reading choice, never a rule that hides data:
# hover="all" always has the rest.
HOVER_VIEWS = ("compact", "all", "encoded")

_COMPACT_MEASURES = frozenset({
    # size and valuation
    "market_cap", "pe", "fwd_pe", "ep",
    # the business
    "eps", "roic", "roe", "ebit", "ebitda", "fcf", "gross_margin", "oper_margin", "net_margin", "sales",
    "sales_growth", "eps_surprise", "eps_change",
    # balance sheet
    "debt", "debt_eq", "cash_per_share",
    # risk
    "beta", "vol", "var", "cvar", "sortino", "sharpe", "information_ratio",
    "sector_information_ratio", "espy_risk", "perf_half",
    # the stock
    "short_ratio", "avg_volume", "inst_own",
    # the scores, live and pre-event
    "conviction", "pre_event_conviction", "silver_pctl", "pre_event_silver_pctl",
    "is_abnormal_prob", "pre_event_is_abnormal_prob",
})

# Raw blocks the compact view keeps, and within the outcomes the returns it keeps.
_COMPACT_BLOCKS = frozenset({"outcomes", "valuation"})
_COMPACT_OUTCOMES = frozenset({"EXP.RET_3", "EXP.RET_30", "EXP.RET_60", "1+3_RET",
                               "1+60_CAL_RET", "SPX_60_CAL_RET"})

# What the report changed, each against the company's previous report: the order
# the changes block reads in, and the label each one shows under.
_CHANGE_LABELS = {
    "EPS_CHANGE": "EPS", "EPS_NEXT_Y_CHANGE": "EPS est. next Y", "EPS_Q/Q_CHANGE": "EPS Q/Q",
    "EPS_YOY_TTM_CHANGE": "EPS YoY", "EPS_SURPRISE_CHANGE": "EPS surprise",
    "INCOME_CHANGE": "Net income",
    "SALES_CHANGE": "Sales", "SALES_Q/Q_CHANGE": "Sales Q/Q", "SALES_YOY_TTM_CHANGE": "Sales YoY",
    "REVENUE_SURPRISE_CHANGE": "Rev surprise",
    "OPER_M_CHANGE": "Oper. margin", "FCF_CHANGE": "FCF", "ROIC_CHANGE": "ROIC",
    "EBIT_CHANGE": "EBIT", "EBITDA_CHANGE": "EBITDA",
    "FWD_P/E_CHANGE": "Fwd P/E", "P/E_CHANGE": "P/E", "MARKET_CAP_CHANGE": "Market cap",
    "DEBT/EQ_CHANGE": "Debt/Eq", "LTDEBT/EQ_CHANGE": "LT Debt/Eq",
    "INST_OWN_CHANGE": "Inst. own.", "INSIDER_OWN_CHANGE": "Insider own.", "BETA_CHANGE": "Beta",
}
_COMPACT_CHANGES = frozenset(_CHANGE_LABELS) - {
    "EPS_NEXT_Y_CHANGE", "EPS_SURPRISE_CHANGE", "REVENUE_SURPRISE_CHANGE",
    "INSIDER_OWN_CHANGE", "BETA_CHANGE",
}
CHANGES_HEADING = "What the report changed (vs the previous report)"


def hover_options(payload, hover="compact", changes=False, hover_all=None):
    """The tooltip a peers chart was asked for, checked against the payload.

    `hover_all` is the older switch: True reads as hover="all", False as
    hover="encoded". `changes` needs companies that have reported: the
    after-the-report view, or the reported peers on the run-up view.
    """
    if hover_all is not None:
        hover = "all" if hover_all else "encoded"
    if hover not in HOVER_VIEWS:
        raise ChartError(f"hover must be one of {', '.join(HOVER_VIEWS)} — got {hover!r}")
    if changes and not any(p.get("changes") for p in payload.get("points") or []):
        raise ChartError(
            "changes=True reads what each report changed, and no company in this "
            "response has reported yet — try peers(..., frame='post')."
        )
    return hover, bool(changes)


# The hard ceiling on tooltip HEIGHT, in lines. This is not about readability —
# it is about rendering: a tooltip taller than the plot area has nowhere to draw,
# and the browser suppresses it ENTIRELY, so a marker near an edge shows nothing
# on hover. Keeping the whole tooltip under this many lines guarantees every
# point answers. The header (identity + report status + encoded metrics) is
# always kept; the extra depth below it WIDENS (more columns) to fit, never drops a measure.
_HOVER_MAX_LINES = 28


def report_status(point):
    """(headline, when, reported): whether this company has reported, as the service
    says, and when. The first thing a reader needs."""
    reported = point.get("reported")
    session = point.get("session")
    date = point.get("earnings_date") or ""
    days = point.get("days_left")

    if reported:
        headline = "REPORTED"
        when = f"{date}"
        if session:
            when += f" {session}"
        if isinstance(days, (int, float)) and days < 0:
            when += f" · {abs(int(days))}d ago"
    else:
        headline = "NOT REPORTED" if reported is False else "REPORT NOT CONFIRMED"
        when = f"{date}"
        if session:
            when += f" {session}"
        if isinstance(days, (int, float)) and days >= 0:
            when += f" · in {int(days)}d"

    return headline, when, reported


# --- showing only some companies -----------------------------------------------------
#
# One rule for every chart kind: narrow the payload's companies once, and every
# chart draws just those. Ranks never move — percentiles, grades and medians were
# computed against the whole peer group and stay that way; the chart says so.

def _with_focus(payload, tickers, focus=None):
    """The tickers a chart was asked for, plus the company it is about.

    Every chart is about one company - the anchor, or the one highlighted - so a
    shortlist (``tickers=peers.nearest(3)``) is drawn against it, never without it.
    """
    focus = (focus or (payload.get("request") or {}).get("anchor_ticker") or "").upper()
    wanted = {str(t).upper() for t in ([tickers] if isinstance(tickers, str) else tickers)}
    return wanted | ({focus} if focus else set())


def only(payload, tickers):
    """A copy of ``payload`` showing only ``tickers`` (the anchor is always kept).

    Every ticker must be in the payload, or this raises. The companies' ranks,
    the group's distributions and reference lines are untouched: a chart of the
    subset still reads each company against its whole peer group. The subset is
    recorded in the payload (``payload["only"]``) and in every figure's recipe,
    so EarningSpy Studio renders the same companies.
    """
    import copy

    tickers = [tickers] if isinstance(tickers, str) else list(tickers or [])
    if not tickers:
        raise ChartError("only() needs at least one ticker.")
    anchor = ((payload.get("request") or {}).get("anchor_ticker") or "").upper()
    for t in tickers:
        _require_point(payload, t, "company to show")
    keep = {t.upper() for t in tickers} | ({anchor} if anchor else set())
    narrowed = copy.deepcopy(payload)
    total = len(payload.get("points") or [])
    narrowed["points"] = [p for p in narrowed.get("points") or []
                          if (p.get("ticker") or "").upper() in keep]
    delta = narrowed.get("delta")
    if isinstance(delta, dict) and delta.get("points") is not None:
        delta["points"] = [p for p in delta["points"] if (p.get("ticker") or "").upper() in keep]
    order = [anchor] if anchor else []
    order += [t.upper() for t in tickers if t.upper() != anchor]
    narrowed["only"] = {"tickers": list(dict.fromkeys(order)), "of": total}
    return narrowed


def companies_label(points):
    """The companies a chart shows, by name: "MU · Micron Technology Inc" for one;
    "MU · … vs. AVGO · …" for two; each ticker with its name for more.
    EarningSpy Studio uses the same wording."""
    items = []
    for p in points:
        ticker = (p.get("ticker") or "").upper()
        items.append(f"{ticker} · {p['company']}" if p.get("company") else ticker)
    if not items:
        return None
    return " vs. ".join(items) if len(items) == 2 else ", ".join(items)


def vs_label(payload):
    """The companies of a chosen subset (only()), in its order; None for the whole group."""
    subset = payload.get("only")
    if not subset:
        return None
    by_ticker = {(p.get("ticker") or "").upper(): p for p in payload.get("points") or []}
    return companies_label([by_ticker.get(t, {"ticker": t}) for t in subset["tickers"]])


def _ticker_pattern(payload):
    tickers = sorted({(p.get("ticker") or "").upper() for p in payload.get("points") or []} - {""},
                     key=len, reverse=True)
    if not tickers:
        return None
    alts = "|".join(re.escape(t) for t in tickers)
    return re.compile(rf"\b({alts})(?= · )|(?<= · )({alts})\b")


def bold_tickers(text, payload):
    """``text`` with the company tickers in bold: a payload ticker written as "TICKER · Company"
    or "Company · TICKER". Only those - so "EPS (TTM)" in a subtitle is never bold."""
    pattern = _ticker_pattern(payload) if text else None
    return pattern.sub(lambda m: f"<b>{m.group(0)}</b>", text) if pattern else text


def focus_label(payload, point=None):
    """The subtitle for a whole-group chart: "Amazon.com Inc (AMZN) against its
    industry peers". Never the group's size. The same wording as Studio."""
    request = payload.get("request") or {}
    anchor = (request.get("anchor_ticker") or "").upper()
    if point is None:
        point = next((p for p in payload.get("points") or []
                      if (p.get("ticker") or "").upper() == anchor), {})
    ticker = (point.get("ticker") or anchor).upper()
    column = (request.get("peer_group") or {}).get("column") or "industry"
    who = f"{point['company']} · {ticker}" if point.get("company") else ticker
    return f"{who} against its {column.lower()} peers" if who else None


def only_note(payload):
    """The log line a chart of a subset carries, or None."""
    subset = payload.get("only")
    if not subset:
        return None
    return f"ranks are against the whole group of {subset['of']} companies"


def _mark_only(fig, payload):
    """Log that ranks stay against the whole group; the chart does not say it."""
    note = only_note(payload)
    if note:
        log.info("%s", note)
    return fig


# --- pre-report reads ------------------------------------------------------------
#
# On the run-up view a peer that has already reported has no live score, so the API
# gives it its PRE-REPORT read (the one from before its own report) and flags it in
# point["reads"]. Every place a reader meets such a value says so: "*" on the
# measure's name (PRE_EVENT_MARK) and "(pre-report read)" in the hover; the longer
# explanation is logged, not drawn.

PRE = "Computed pre event"
_SCORE_METRICS = ("quality_prob", "silver_pctl", "is_abnormal_prob", "conviction")


def is_pre_report(point, metric):
    """Whether this company's value for ``metric`` is a pre-report read."""
    return (point.get("reads") or {}).get(metric) == "pre_report"


def _tagged(point, metrics):
    """The company's ticker as drawn. Always plain: a pre-report read is marked on
    the measure (PRE_EVENT_MARK), not on the company."""
    return point.get("ticker") or ""


def pre_report_note(payload, metrics):
    """One log line explaining the pre-report reads among ``metrics``, or None."""
    counted = {}
    for point in payload.get("points") or []:
        for m in metrics:
            if m and is_pre_report(point, m):
                counted[m] = counted.get(m, 0) + 1
    if not counted:
        return None
    n = max(counted.values())
    names = ", ".join(_label(payload, m) for m in counted)
    return (f"{PRE}: {n} reported peer{'s' if n != 1 else ''} show the "
            f"read from before their own report ({names}), not a live score")


def _plotted_metrics(payload, kind, params):
    named = [params.get(k) for k in ("x", "y", "size", "color", "metric")]
    listed = params.get("metrics")
    listed = [listed] if isinstance(listed, str) else list(listed or [])
    if kind in ("bar_pctile", "radar", "grade_card") and not listed:
        listed = list(_SCORE_METRICS)               # the default measure lists include them
    return [m for m in named + listed if isinstance(m, str)]


def _mark_pre_report(fig, payload, kind, params):
    """Log the pre-report explanation; the chart marks the companies themselves."""
    note = pre_report_note(payload, _plotted_metrics(payload, kind, params))
    if note:
        log.info("%s", note)
    return fig


def _metric_line(payload, point, metric, grouping, emphasis=False):
    """One metric row: value, why it is missing, whether it is capped, its rank."""
    label = _label(payload, metric)
    value = (point.get("values") or {}).get(metric)
    reason = (point.get("nm") or {}).get(metric)

    if value is None and reason:
        shown = f"n.m. <i>({_NM_REASONS.get(reason, reason.replace('_', ' '))})</i>"
    else:
        shown = format_value(value, _fmt(payload, metric))
        if (point.get("capped") or {}).get(metric):
            shown += " ≥cap"

    if is_pre_report(point, metric):
        shown += " <i>(pre-report read)</i>"

    pct = _pct(point, metric, grouping)
    rank = (f" <span style='opacity:.65'>p{pct:.0f}</span>"
            if pct is not None else "")
    if emphasis:
        return f"<b>{label}: {shown}</b>{rank}"
    return f"{label}: {shown}{rank}"


# --- two-column tooltip packing ---------------------------------------------
#
# Plotly's tooltip HTML ignores CSS widths, so columns are aligned the only way
# that survives: a monospace span with the left cell padded to a fixed number of
# non-breaking spaces. Cells must therefore be PLAIN TEXT (tags render as zero
# width and break the count), so these build tag-free strings.

_HOVER_COLUMN_GAP = 2    # spaces between the widest cell of a column and the next column

# Short names for the tooltip grid, where every character widens a column. Keyed
# by measure, so the API's label still names it everywhere else (axes, titles,
# the bold rows of the plotted measures).
_HOVER_SHORT = {
    "ep": "E/P", "fcf": "FCF", "fcf_yield": "FCF Yield", "book_yield": "Book Yield",
    "debt": "Debt", "debt_eq": "Debt/Eq", "ltdebt_eq": "LT Debt/Eq",
    "cash_per_share": "Cash/Sh", "book_per_share": "Book/Sh",
    "current_ratio": "Curr. Ratio", "quick_ratio": "Quick Ratio",
    "sales_growth": "Sales YoY", "oper_margin": "Oper. Margin", "gross_margin": "Gross Margin", "net_margin": "Net Margin",
    "net_income": "Net Income", "perf_half": "6M Perf.", "vol": "Ann. Vol.",
    "eps_revision": "EPS Rev. (NY)", "rev_surprise": "Rev. Surprise",
    "inst_own": "Inst. Own.", "insider_own": "Insider Own.", "float_pct": "Float %",
    "avg_volume": "Avg Vol.", "var": "VaR", "cvar": "CVaR",
    "information_ratio": "IR", "sector_information_ratio": "Sector IR",
    "espy_risk": "ESpy Risk", "valuation_spread": "Val. Spread",
    "quality_prob": "Quality Prob.", "silver_pctl": "ESpy Rank",
    "is_abnormal_prob": "Abn. Prob.", "conviction": "Conviction",
    "pre_event_quality_prob": "Quality Prob. (pre)", "pre_event_silver_pctl": "ESpy Rank (pre)",
    "pre_event_is_abnormal_prob": "Abn. Prob. (pre)", "pre_event_conviction": "Conviction (pre)",
}
_RAW_SHORT = {"VALUATION_SPREAD_PRIOR": "Spread prior Q", "VALUATION_STATE": "State"}


def _hover_label(payload, metric):
    """The grid's name for a measure: the short form when there is one, marks kept."""
    full, plain = _label(payload, metric), _name(payload, metric)
    short = _HOVER_SHORT.get(metric)
    return full if short is None else short + full[len(plain):]


def _metric_cell(payload, point, metric, grouping):
    """One metric as plain text: 'Label: value pNN'. No tags — for column packing.

    A pre-report read carries the measure's "*" here instead of the words, which
    would widen a whole column.
    """
    value = (point.get("values") or {}).get(metric)
    reason = (point.get("nm") or {}).get(metric)
    if value is None and reason:
        shown = f"n.m. ({_NM_REASONS.get(reason, reason.replace('_', ' '))})"
    else:
        shown = format_value(value, _fmt(payload, metric))
        if (point.get("capped") or {}).get(metric):
            shown += " ≥cap"
    pct = _pct(point, metric, grouping)
    rank = f" p{pct:.0f}" if pct is not None else ""
    return f"{_hover_label(payload, metric)}: {shown}{rank}"


def _raw_cells(block):
    """Plain-text 'Name: value' cells for a raw pass-through block.

    Non-null entries only, shown verbatim — the API resolved these, so the
    tooltip does not rank or transform them.
    """
    cells = []
    for name, value in block.items():
        if value is None:
            continue
        pretty = _RAW_SHORT.get(name) or name.replace("_", " ").title()
        shown = value.replace("_", " ") if isinstance(value, str) \
            else format_value(value, None)
        cells.append(f"{pretty}: {shown}")
    return cells


def _outcome_label(key):
    """A readable label for a return outcome key, acronyms kept uppercase.

    ``CAR_3`` → ``CAR 3d``; ``CAR_60_CAL`` → ``CAR 60d``; ``EXP.RET_30`` →
    ``Exp. return 30d``; ``1+60_CAL_RET`` → ``Realized 60d``; ``SPX_60_CAL_RET``
    → ``S&P 500 60d``. The 30 / 60-day windows a reader means are the calendar
    ones (*_CAL); the business-day 30 / 60 say so (``CAR 30d (business days)``).
    Titlecasing alone would mangle CAR into "Car", so the families are named
    explicitly.
    """
    match = re.match(r"^SPX_(\d+)_CAL_RET$", key)
    if match:
        return f"S&P 500 {match.group(1)}d"
    match = re.match(r"^CAR_(\d+)(_CAL)?$", key)
    if match:
        return f"CAR {match.group(1)}d{_business(match)}"
    if key.startswith("EXP.RET_"):
        return f"Exp. return {key.split('_', 1)[1]}d"
    match = re.match(r"^1\+(\d+)(_CAL)?_RET$", key)
    if match:
        return f"Realized {match.group(1)}d{_business(match)}"
    return key.replace("_", " ").title()


def _business(match):
    """" (business days)" for a business-day 30 / 60-day window; "" otherwise."""
    return "" if match.group(2) or match.group(1) == "3" else " (business days)"


def _outcome_cells(block):
    """Cells for the Abnormality Scores block: returns as %, in a fixed horizon
    order, then the realized flags.

    The returns (CAR / expected / realized at 3·30·60d) lead, each shown as a
    percentage — a fraction the API stores as -0.0893 reads as -8.9%. A return
    key the payload carries but has not filled yet (a 60-day CAR on a two-week-old
    event) is kept and shown as n.m., so a reader sees the horizon exists and is
    simply not mature — the calendar's "empty is a real answer" rule. Non-return
    outcome fields (REALIZED_DIRECTION, …) follow, verbatim; a wholly absent key
    is skipped.
    """
    cells = []
    for name in _OUTCOME_RETURN_ORDER:
        if name not in block:
            continue                      # the payload does not carry this horizon
        cells.append(f"{_outcome_label(name)}: "
                     f"{format_value(block[name], 'percent')}")
    # the rest of the block (realized flags, persistence), non-null, verbatim.
    rest = {k: v for k, v in block.items()
            if k not in _OUTCOME_PERCENT_KEYS and v is not None}
    cells.extend(_raw_cells(rest))
    return cells


def _change_cells(block, compact):
    """Cells for the changes block: each change as a signed percent, in a fixed order.

    A change the payload carries but could not compute (no previous report) shows
    as n.m.; a change column this client has no label for still shows, under its
    own name, on the full view.
    """
    cells = [f"{label}: {format_value(block[name], 'percent_signed')}"
             for name, label in _CHANGE_LABELS.items()
             if name in block and (not compact or name in _COMPACT_CHANGES)]
    if not compact:
        cells += [f"{name[:-len('_CHANGE')].replace('_', ' ')}: "
                  f"{format_value(value, 'percent_signed')}"
                  for name, value in block.items() if name not in _CHANGE_LABELS]
    return cells


def _profile_line(point):
    """Sector · country · the indexes the company belongs to, or None."""
    profile = point.get("profile") or {}
    parts = [profile.get("SECTOR"), profile.get("COUNTRY")]
    if profile.get("IS_S&P500"):
        parts.append("S&P 500")
    if profile.get("IS_RUSSELL"):
        parts.append("Russell")
    parts = [p for p in parts if p]
    return " · ".join(parts) or None


def column_rows(cells, columns=2):
    """Plain-text cells laid out ``columns`` per line, each column as wide as its widest
    cell plus a gap, spaces made non-breaking. One line of HTML per row."""
    grid = [cells[i:i + columns] for i in range(0, len(cells), columns)]
    widths = [max((len(r[c]) for r in grid if c < len(r)), default=0) + _HOVER_COLUMN_GAP
              for c in range(columns)]
    return ["".join([c.ljust(widths[i]) for i, c in enumerate(row[:-1])] + row[-1:])
            .replace(" ", "&nbsp;") for row in grid]


def _two_column(cells, columns=2):
    """Pack plain-text cells into ``columns`` monospace-aligned columns, one row per line.

    Each column is as wide as its widest cell plus a gap, so no cell runs into the
    next column. Cells are padded with non-breaking spaces (the browser keeps them)
    and the whole block is wrapped once in a monospace span so every character is
    the same width and the columns line up. Halves a long metric list's height so
    the tooltip renders even near a plot edge.
    """
    inner = "<br>".join(column_rows(cells, columns))
    # Grey the percentile tokens to match the single-column rows above. Done AFTER
    # padding so the span's characters never enter the width count: a rank reads
    # as "&nbsp;pNN" (nbsp from the space before it), so wrap that exact shape.
    inner = re.sub(r"(&nbsp;)(p\d+)",
                   r"\1<span style='opacity:.65'>\2</span>", inner)
    return f"<span style='font-family:monospace;font-size:11px'>{inner}</span>"


def _hover(payload, point, metrics=(), grouping=DEFAULT_GROUPING,
           view="compact", changes=False):
    """Everything known about one company, not just what the chart encodes.

    A scatter positions four measures; the payload carries twenty-odd more plus
    what happened after the report, the forward read and the valuation state.
    Hiding the rest means reading a chart and then leaving it to look something
    up, which is the thing an interactive tooltip exists to prevent.

    The metrics the chart encodes are listed first and bolded, so the axes stay
    findable in a long list. `view` picks the depth below them: "compact" (what a
    reader scans first), "all" (everything the payload carries) or "encoded"
    (those alone, for charts where a wall of text would drown the mark).
    `changes` adds what the report changed against the previous one.
    """
    compact = view != "all"
    encoded = [m for m in metrics if m]
    lines = [f"<b>{point.get('ticker')}</b>"]

    company = point.get("company")
    if company:
        lines.append(f"<span style='font-size:11px;opacity:.75'>{company}</span>")

    # The peer group this company is being compared against. Every percentile on
    # the row is read against it, so naming it stops the reader from guessing
    # which industry the ranks belong to.
    group = ((payload.get("request") or {}).get("peer_group") or {}).get("value")
    if group:
        lines.append(f"<span style='font-size:11px;opacity:.6'>{group}</span>")
    profile = _profile_line(point)
    if profile:
        lines.append(f"<span style='font-size:11px;opacity:.6'>{profile}</span>")

    # Reported or not, and when - before anything else. It is the fact that
    # decides how to read every other number on the row: a percentile against
    # peers who have printed means something different from one against peers
    # who have not.
    headline, when, reported = report_status(point)
    mark = "✓" if reported else "○"
    lines.append(f"<b>{mark} {headline}</b>")
    lines.append(f"<span style='font-size:11px;opacity:.75'>{when}</span>")

    tags = []
    if point.get("role") == "anchor":
        tags.append("anchor")
    if point.get("stale"):
        tags.append("prior quarter figures")
    state = (point.get("valuation") or {}).get("VALUATION_STATE")
    if state:
        tags.append(state.replace("_", " "))
    if tags:
        lines.append(f"<span style='font-size:11px;opacity:.75'><i>"
                     f"{' · '.join(tags)}</i></span>")

    # The market this company reported into: one line in the header, so it is never
    # trimmed away by the depth budget below. Absent until the reading is taken.
    market = _regime.line(point.get("market"), reported=reported) if view == "all" else None
    if market:
        lines.append(f"<span style='font-size:11px;opacity:.75'>{market}</span>")

    # What the chart encodes, first and emphasised.
    for metric in encoded:
        if metric in (payload.get("metrics") or {}):
            lines.append(_metric_line(payload, point, metric, grouping, True))

    if view == "encoded" and not changes:
        return "<br>".join(lines) + "<extra></extra>"

    # Everything below is EXTRA depth. Laid out in TWO columns so it is half as
    # tall — a tooltip taller than the plot is dropped by the browser and the
    # point then shows nothing on hover (the bug this guards against). The extra
    # widens (more columns) to keep the whole tooltip under _HOVER_MAX_LINES; the
    # header above always renders. `lines_left` is the line budget left for it.
    lines_left = _HOVER_MAX_LINES - len(lines)

    # Everything else the payload knows. Null-only metrics are dropped: a tooltip full of
    # "n.m." rows buries the ones with values. Nothing with a value is ever hidden: when two
    # columns would run past the line budget, the blocks widen (more columns) instead.
    rest = [m for m in (payload.get("metrics") or {})
            if m not in encoded
            and (point.get("values") or {}).get(m) is not None
            and (not compact or m in _COMPACT_MEASURES)] if view != "encoded" else []
    blocks = []
    if rest:
        blocks.append((f"{rank_group(payload)} percentile", [_metric_cell(payload, point, m, grouping) for m in rest]))
    if changes:
        cells = _change_cells(point.get("changes") or {}, compact)
        blocks.append((CHANGES_HEADING, cells or ["n.m. (no previous report to compare)"]))
    for key, heading in (_RAW_BLOCKS if view != "encoded" else ()):
        if compact and key not in _COMPACT_BLOCKS:
            continue
        raw = point.get(key) or {}
        if compact and key == "outcomes":
            raw = {k: v for k, v in raw.items() if k in _COMPACT_OUTCOMES}
        cells = _outcome_cells(raw) if key == "outcomes" else _raw_cells(raw)
        if cells:
            blocks.append((heading, cells))
    # The key for every mark this tooltip shows, one line each - counted before the
    # blocks are packed, so the whole tooltip, keys included, stays under the budget.
    shown = list(encoded) + rest
    keys = [footnote(payload, code) for code in _codes(payload, shown)]
    columns = 2
    while sum(
            1 + (len(cells) + columns - 1) // columns
            for _, cells in blocks) > max(lines_left - len(keys), 1):
        columns += 1
    for heading, cells in blocks:
        # Name each block, so a reader knows which group the pNN token on a row ranks against.
        lines.append(f"<span style='font-size:10.5px;opacity:.55'>{heading}</span>")
        lines.append(_two_column(cells, columns))

    # The key for every mark this tooltip shows ("*" computed pre event, …), at the bottom.
    if keys:
        lines.append("<span style='font-size:10px;opacity:.6'>" + "<br>".join(keys) + "</span>")

    return "<br>".join(lines) + "<extra></extra>"


def _marker_size(values, lo=9, hi=46):
    """Marker diameters with AREA proportional to value, measured from zero.

    The largest value gets ``hi``; every other one scales by the square root of
    its share of it, so a company 1.4x another's size draws 1.4x its area. ``lo``
    is only a floor, so the smallest companies stay visible. (Stretching sizes
    between the smallest and largest shown made a 1.4x difference look like 5x.)
    EarningSpy Studio sizes its bubbles by the same rule.
    """
    clean = [v for v in values if v is not None and v > 0]
    if not clean:
        return [lo] * len(values)
    largest = max(clean)
    return [lo if (v is None or v <= 0) else max(lo, hi * math.sqrt(v / largest))
            for v in values]


def available_metrics(payload, rankable_only=False):
    """What this payload serves, as (key, label, scale, direction) rows.

    With twenty-odd metrics and any pair plottable, the first question is always
    "what can I put on an axis" - and reading it out of the payload beats
    keeping a list in a notebook that goes stale.
    """
    rows = []
    for key, meta in (payload.get("metrics") or {}).items():
        direction = meta.get("higher_is_better")
        if rankable_only and direction is None:
            continue
        rows.append((
            key,
            meta.get("label", key),
            meta.get("scale", "linear"),
            "higher is better" if direction
            else ("lower is better" if direction is False else "neutral"),
        ))
    return sorted(rows)


# Minimum peers a metric needs before it can be ranked cross-sectionally. Mirrors
# the API's own floor; used only to explain a thin metric, never to compute one.
_RANKABLE_MIN = 2


def _thin_but_resolved(payload):
    """Metrics too sparse to rank live, whose resolved (pre-event) twin is not.

    A live forward read (`quality_prob`, …) is empty across a set of peers that
    have already reported — the reading for those companies now lives, unchanged,
    under its `pre_event_*` name. This finds those pairs by reading the payload's
    own distribution counts, so describe() can name the key a user should plot
    instead of the empty one. It aliases nothing: the two keys stay distinct and
    the user reaches for the resolved one explicitly.

    Returns a list of (thin_key, resolved_key) the payload actually serves.
    """
    dist = grouped(payload.get("distributions"))
    served = payload.get("metrics") or {}
    pairs = []
    for key in served:
        resolved = f"pre_event_{key}"
        if resolved not in served:
            continue
        thin_n = (dist.get(key) or {}).get("n")
        full_n = (dist.get(resolved) or {}).get("n")
        if (thin_n is not None and thin_n < _RANKABLE_MIN
                and full_n is not None and full_n >= _RANKABLE_MIN):
            pairs.append((key, resolved))
    return pairs


def _resolved_twin(payload, metric):
    """The populated `pre_event_<metric>` key when `metric` is the empty live one.

    Lets an empty chart name the exact key to plot instead — the resolved,
    ex-ante measure — without aliasing it. Returns None when there is no such
    better-populated twin, so the plain empty message stands.
    """
    for thin, resolved in _thin_but_resolved(payload):
        if thin == metric:
            return resolved
    return None


class Description:
    """What a payload offers with the key that fetched it: the company, its
    season and industry, who has reported, the measures you can plot, and the
    service's notes.

    Returned by :func:`describe` so a program can read it (``.metrics``,
    ``.key_tier``, ``.reported``) instead of scraping printed text; printing it —
    or letting a notebook echo it — shows the same human-readable summary. Only
    what can be plotted is listed; hover-only detail is left out.
    """

    def __init__(self, payload):
        request = payload.get("request") or {}
        season = payload.get("season") or {}
        self.anchor = request.get("anchor_ticker")
        self.frame = request.get("frame")
        self.peer_group = (request.get("peer_group") or {}).get("value")
        self.season = request.get("season") or season.get("label")
        self.cohort = request.get("cohort") or season.get("cohort")
        self.lookback_season = (season.get("lookback") or {}).get("label")
        self.tiers_included = list(payload.get("layers_included") or [])
        self.key_tier = self.tiers_included[-1] if self.tiers_included else "bronze"
        points = payload.get("points") or []
        peers = [p for p in points if p.get("role") != "anchor"]
        self.company_count = len(points)
        self.reported = sum(1 for p in peers if p.get("reported"))
        self.not_reported = len(peers) - self.reported
        self.carried = sum(1 for p in peers if p.get("carried"))
        # Only measures something can be drawn from: a measure the key cannot
        # read is either not in the table or carries no value for anyone.
        self.metrics = [row for row in available_metrics(payload)
                        if _has_values(payload, row[0])]
        self.has_delta = bool(payload.get("delta"))
        # Hover-only detail, kept for code; not printed (nothing plots from it).
        self.hover_blocks = {}
        if peers:
            for block, heading in _RAW_BLOCKS + (("changes", CHANGES_HEADING),
                                                 ("profile", "Profile")):
                keys = sorted(peers[0].get(block) or {})
                if keys:
                    self.hover_blocks[heading] = keys
        # (thin live key, populated resolved key) pairs — see _thin_but_resolved.
        self.use_resolved = _thin_but_resolved(payload)
        # Every note the response carries, in full.
        self.warnings = list(payload.get("warnings") or [])

    def __str__(self):
        head = f"{self.anchor} · {self.frame} view · {self.key_tier} key"
        where = " · ".join(filter(None, [
            f"season {self.season}" + (f" (cohort: {self.cohort})" if self.cohort else "")
            if self.season else None,
            self.peer_group,
        ]))
        peers = self.reported + self.not_reported
        tally = (f"{peers} peers: {self.reported} reported, "
                 f"{self.not_reported} not reported yet")
        lines = [head] + ([where] if where else []) + [tally]
        if self.carried:
            # Data from the previous season: say how much, and what it costs.
            lines.append(f"lookback: {self.carried} of the reported are carried from "
                         f"{self.lookback_season or 'last season'} (their last report; "
                         f"price-based measures are as of that date)")
        lines.append(f"\n{len(self.metrics)} measures you can plot, any pair "
                     f"(pass the KEY as x / y / metric / color):")
        # Grouped by which direction is the good one, so the menu reads like the
        # calendar's — the direction is a heading, not a cryptic word per row.
        groups = {"higher is better": [], "lower is better": [], "neutral": []}
        for key, label, scale, direction in self.metrics:
            groups.setdefault(direction, []).append((key, label, scale))
        headings = {
            "higher is better": "higher is better",
            "lower is better": "lower is better (risk / valuation)",
            "neutral": "direction neutral, context implications",
        }
        for direction in ("higher is better", "lower is better", "neutral"):
            rows = groups.get(direction) or []
            if not rows:
                continue
            lines.append(f"\n  {headings[direction]}:")
            for key, label, scale in rows:
                tail = f"   [{scale}]" if scale not in ("linear", None) else ""
                lines.append(f"    {key:<26} {label}{tail}")
        if self.has_delta:
            lines.append("\nbefore-and-after view available "
                         "(dumbbell, shift_scatter)")
        if self.use_resolved:
            lines.append("\nthese companies have already reported, so their live "
                         "reads are empty (not listed above) — plot the resolved "
                         "(pre-event) measure instead:")
            for thin, resolved in self.use_resolved:
                lines.append(f"  {thin}  →  {resolved}")
        if self.warnings:
            n = len(service_notes(self.warnings, echo=False))
            lines.append(f"\n{n} note{'s' if n != 1 else ''} from the service: peers.warnings()")
        return "\n".join(lines)

    def __repr__(self):
        # describe() has already printed the summary; a notebook echoing the
        # returned object would print it a second time. print(d) or str(d) gives
        # the full text again.
        return (f"<Description {self.anchor} · {len(self.metrics)} measures — printed above; "
                f"print() it to see it again>")


def service_notes(warnings_, echo=True):
    """The notes the service attached to a response, once each, in arrival order.

    Thin distributions, pre-report reads, stale figures: what a reader should know
    about the numbers, not errors. Printed when `echo` is set; always returned.
    """
    seen, notes = set(), []
    for w in warnings_ or []:
        key = (w.get("code"), w.get("detail"))
        if key not in seen:
            seen.add(key)
            notes.append(w)
    if echo:
        print("\n".join(["notes from the service:"]
                        + [f"  [{w.get('code')}] {w.get('detail')}" for w in notes])
              if notes else "no notes from the service")
    return PrintedList(notes, noun="notes") if echo else notes


def describe(payload):
    """Summarise what you can do with a payload, with the key that fetched it:
    the company, season and industry, who has reported, the measures you can
    plot and the service's notes.

    The payload is self-describing, but nobody wants to read raw JSON to find an
    axis name. This prints a readable summary and returns a :class:`Description`
    a program can inspect, so it works both at a notebook prompt and in code.

    Examples
    --------
    >>> d = describe(payload)      # prints the summary
    >>> [m[0] for m in d.metrics]  # the measure keys, for code
    """
    description = Description(payload)
    print(description)
    return description


# --- scatter ----------------------------------------------------------------

DEFAULT_COLOR = "eps_change"
DEFAULT_COLOR_FALLBACKS = ("eps_change", "eps_qoq", "eps", "valuation_spread")
DEFAULT_SIZE = "market_cap"


def _first_available(payload, candidates):
    """The first candidate the payload actually serves."""
    served = payload.get("metrics") or {}
    for name in candidates:
        if name in served:
            return name
    return None


def scatter(payload, x="pe", y="roic", size=DEFAULT_SIZE, color=None,
            highlight=None, grouping=DEFAULT_GROUPING, theme="light",
            title=None, hover="compact", changes=False, hover_all=None,
            peers=DEFAULT_PEERS, tickers=None, outliers=True, hints=True, **_):
    """Plot any two measures against each other to see who stands out.

    The workhorse view: put one measure on each axis and read which companies
    sit apart from the pack — the cheap-and-profitable corner, the expensive
    laggard, the outlier the peer group is pricing differently. Any pair of the
    measures this payload carries works, so "value vs quality", "risk vs return"
    and "growth vs size" are three calls, not three different charts. The anchor
    company is drawn larger and labelled so it is easy to find among its peers.

    Colour defaults to the change in earnings and size to company size, so the
    picture reads as "who is growing, and how big" without extra instruction.
    Both are overridable. `hover` sets the tooltip's depth: "compact" (default)
    shows what a reader scans first, "all" every measure the payload carries, and
    "encoded" only the plotted ones. On the after-the-report view, `changes=True`
    adds what each report changed against the previous one, at the same depth.

    Available on the bronze view. `x` and `y` name any measure the payload
    carries — run describe() to see them for the payload in hand.

    `peers` (1, 2, 3, 5 or 8) names that many companies beside their marks: the
    ones closest to the focus company in market cap. `outliers` (on by
    default) labels, a little smaller, the companies stretching the axes. `tickers` names exactly
    those instead, and zooms the axes to the focus company and those peers (the
    rest stay as context where they fall inside). `highlight` picks the focus
    company (default: the anchor).

    Examples
    --------
    >>> plot(payload, "scatter", x="pe", y="roic")   # value vs quality
    >>> plot(payload, "scatter", x="ep", y="roic", peers=3)
    """
    color = color or _first_available(payload, DEFAULT_COLOR_FALLBACKS)
    t = _theme(theme)
    require_subscribed(payload, x, y)
    view, changes = hover_options(payload, hover, changes, hover_all)
    if tickers and not payload.get("only"):
        # Hand-picked companies: draw exactly those (and the focus), axes fitted.
        chosen = [tickers] if isinstance(tickers, str) else list(tickers)
        payload = only(payload, chosen + ([highlight] if highlight else []))

    anchor_ticker = highlight or (payload.get("request") or {}).get("anchor_ticker")
    if not tickers and peers not in PEER_COUNTS:            # a bad argument before any data verdict
        raise ChartError(f"peers must be one of {', '.join(map(str, PEER_COUNTS))}; got {peers!r}.")
    usable = [p for p in _points(payload, grouping)
              if _plottable(p, x) and _plottable(p, y)]
    dropped = [p for p in _points(payload, grouping)
               if not (_plottable(p, x) and _plottable(p, y))]
    focus_point = next((p for p in dropped
                        if (p.get("ticker") or "").upper() == (anchor_ticker or "").upper()), None)
    focus_note = None
    if focus_point is not None and usable:        # nobody plottable: the empty state says so below
        # The chart is about this company and cannot place it: draw the rest, and say
        # so on the chart and as a warning, so it is never silently missing.
        metric = next(m for m in (x, y) if not _plottable(focus_point, m))
        reason = _unplottable_reason(focus_point, metric) or "no data"
        warnings.warn(focus_unplaceable(payload, anchor_ticker, metric, reason), stacklevel=3)
        focus_note = (f"{str(anchor_ticker).upper()} not placed: no usable "
                      f"{_name(payload, metric)} ({reason})")

    if not usable:
        twins = [f"{m} → {_resolved_twin(payload, m)}"
                 for m in (x, y) if _resolved_twin(payload, m)]
        hint = (" — these peers have reported; use the ex-ante value(s): "
                + "; ".join(twins)) if twins else ""
        return _empty(f"no company has both {x} and {y}{hint}", t, title)

    fig = go.Figure()
    scale, diverging = _colorscale(payload, color, t)
    color_values = [(p.get("values") or {}).get(color) for p in usable] \
        if color and _meta(payload, color) else []
    cmin, cmax = _color_range(color_values, diverging, payload, color) \
        if color_values else (None, None)

    sizes = _marker_size([(p.get("values") or {}).get(size) for p in usable]) \
        if size and _meta(payload, size) else [16] * len(usable)

    # One trace per KIND of marker, not one per point. Plotly renders per-point
    # colour, size and hover from arrays within a single trace — and a single
    # trace is one continuous hover surface, where dozens of one-point traces
    # leave dead zones between them (the "some points show no tooltip" bug).
    # Three groups keep the distinct looks: regular peers (colour-ramped),
    # stale peers (hollow, dotted — prior-quarter figures), and the anchor.
    groups = {"peer": [], "stale": [], "anchor": []}
    for point, marker_size in zip(usable, sizes):
        if point.get("ticker") == anchor_ticker:
            groups["anchor"].append((point, marker_size))
        elif point.get("stale"):
            groups["stale"].append((point, marker_size))
        else:
            groups["peer"].append((point, marker_size))

    def _xy(pt):
        return (pt.get("values") or {}).get(x), (pt.get("values") or {}).get(y)

    # Regular peers. A marker colour-ramps on the colour metric — but a peer that
    # lacks a colour value cannot take a colourscale number (Plotly rejects a
    # None in a colour array), so those go in a second flat-coloured trace. Two
    # continuous hover surfaces at most, never one-per-point.
    peer = groups["peer"]

    def _peer_trace(members, colored):
        if not members:
            return
        marker = dict(size=[s for _, s in members],
                      line=dict(width=0.7, color=t["line"]), opacity=0.86)
        if colored:
            marker.update(
                color=[(p.get("values") or {}).get(color) for p, _ in members],
                colorscale=scale, cmin=cmin, cmax=cmax)
        else:
            marker["color"] = t["accent"]
        fig.add_trace(go.Scatter(
            x=[_xy(p)[0] for p, _ in members],
            y=[_xy(p)[1] for p, _ in members],
            mode="markers", marker=marker,
            hovertext=[_hover(payload, p, [x, y, size, color], grouping,
                              view=view, changes=changes) for p, _ in members],
            hovertemplate="%{hovertext}", showlegend=False,
        ))

    if color_values:
        with_color = [(p, s) for p, s in peer
                      if (p.get("values") or {}).get(color) is not None]
        without = [(p, s) for p, s in peer
                   if (p.get("values") or {}).get(color) is None]
        _peer_trace(with_color, colored=True)
        _peer_trace(without, colored=False)
    else:
        _peer_trace(peer, colored=False)

    # Stale peers: hollow, dotted — still fully hoverable (they carry a fill of
    # near-zero-alpha so the cursor has something to land on, not pure transparent).
    stale = groups["stale"]
    if stale:
        fig.add_trace(go.Scatter(
            x=[_xy(p)[0] for p, _ in stale], y=[_xy(p)[1] for p, _ in stale],
            mode="markers",
            marker=dict(
                size=[s for _, s in stale],
                color="rgba(0,0,0,0.001)",
                line=dict(width=1.8, color=t["muted"], dash="dot"),
                opacity=0.86,
            ),
            hovertext=[_hover(payload, p, [x, y, size, color], grouping,
                              view=view, changes=changes) for p, _ in stale],
            hovertemplate="%{hovertext}", showlegend=False,
        ))

    # The anchor: identical to any peer marker — same colour-ramp value, same
    # size, same border — so it reads truthfully on the colour scale and in the
    # crowd. It is identified ONLY by its ticker label above it (in the anchor
    # colour), never by recolouring or enlarging the mark.
    anchor = groups["anchor"]
    if anchor:
        avals = [(p.get("values") or {}).get(color) for p, _ in anchor] \
            if color_values else []
        amarker = dict(size=[s for _, s in anchor],
                       line=dict(width=0.7, color=t["line"]), opacity=0.86)
        if avals and all(v is not None for v in avals):
            amarker.update(color=avals, colorscale=scale, cmin=cmin, cmax=cmax)
        else:
            amarker["color"] = t["accent"]
        fig.add_trace(go.Scatter(
            x=[_xy(p)[0] for p, _ in anchor], y=[_xy(p)[1] for p, _ in anchor],
            mode="markers+text",
            # Bold (<b>) and in the theme ink — black on light, near-white on dark
            # — so the anchor's ticker reads as the label, not another red mark.
            text=[f"<b>{_tagged(p, [x, y])}</b>" for p, _ in anchor],
            textposition="top center",
            textfont=dict(color=t["ink"], size=12, family=_BODY_FONT),
            marker=amarker,
            hovertext=[_hover(payload, p, [x, y, size, color], grouping,
                              view=view, changes=changes) for p, _ in anchor],
            hovertemplate="%{hovertext}", showlegend=False,
        ))

    # Named peers: the closest in size among those plotted, or exactly `tickers`.
    focus_ticker = anchor_ticker or (payload.get("request") or {}).get("anchor_ticker")
    named = []
    if focus_ticker and _find(payload, focus_ticker):
        named = select_peers(payload, focus_ticker, peers=peers, tickers=tickers, among=usable)
        named = [p for p in named if p in usable]
        if named:
            fig.add_trace(go.Scatter(
                x=[_xy(p)[0] for p in named], y=[_xy(p)[1] for p in named],
                mode="text", text=[_tagged(p, [x, y]) for p in named],
                textposition="top center",
                textfont=dict(color=t["muted"], size=11, family=_BODY_FONT),
                hoverinfo="skip", showlegend=False,
            ))

    # Outliers: the companies stretching the axes, labelled so a reader sees why.
    if outliers and not payload.get("only"):
        taken = {(focus_ticker or "").upper()} | {
            (p.get("ticker") or "").upper() for p in named}
        outs = [t for t in outlier_tickers([(p.get("ticker"), *_xy(p)) for p in usable],
                                           _is_log(payload, x), _is_log(payload, y))
                if (t or "").upper() not in taken]
        points_out = [p for p in usable if p.get("ticker") in outs]
        if points_out:
            fig.add_trace(go.Scatter(
                x=[_xy(p)[0] for p in points_out], y=[_xy(p)[1] for p in points_out],
                mode="text", text=[_tagged(p, [x, y]) for p in points_out],
                textposition="top center",
                textfont=dict(color=t["muted"], size=9.5, family=_BODY_FONT),
                hoverinfo="skip", showlegend=False,
            ))

    _quadrant(fig, payload, x, y, t, grouping)

    if color_values and cmin is not None:
        fig.add_trace(go.Scatter(
            x=[None], y=[None], mode="markers", showlegend=False,
            marker=dict(colorscale=scale, cmin=cmin, cmax=cmax,
                        color=[cmin], size=0.1, showscale=True,
                        colorbar=dict(
                            title=dict(text=_label(payload, color),
                                       side="right",
                                       font=dict(size=11.5, color=t["muted"])),
                            thickness=11, len=0.52, x=1.015,
                            tickfont=dict(size=10, color=t["muted"]),
                        )),
            hoverinfo="skip",
        ))

    # Hand-picked peers (`tickers`): the axes fit the focus company and those
    # peers, so a "MU vs AVGO" chart is about them, not the whole group's range.
    zoom = None
    if payload.get("only"):                     # chosen companies: fit everyone shown
        chosen = list(usable)
        xr = zoom_range([_xy(p)[0] for p in chosen], _is_log(payload, x))
        yr = zoom_range([_xy(p)[1] for p in chosen], _is_log(payload, y))
        if chosen and xr and yr:
            def inside(v, r, log):
                v = math.log10(v) if log and v and v > 0 else v
                return v is not None and r[0] <= v <= r[1]
            outside = sum(1 for p in usable
                          if not (inside(_xy(p)[0], xr, _is_log(payload, x))
                                  and inside(_xy(p)[1], yr, _is_log(payload, y))))
            zoom = (xr, yr, outside)

    # Who could not be placed is said on the chart, with the reason.
    left_off = _left_off_line(payload, [p for p in dropped
                                        if not (focus_note and p is focus_point)], [x, y])
    if dropped:
        log.info("scatter %s vs %s: %d left off — %s", y, x, len(dropped),
                 left_off_by_axis(dropped, [x, y]))
    subtitle = bold_tickers(vs_label(payload) or focus_label(payload), payload)   # never the group's size
    if focus_note:
        subtitle = f"{subtitle} · <b>{focus_note}</b>"
    if left_off:
        subtitle = f"{subtitle} · {left_off}"
    _layout(fig, t, title or f"{_label(payload, y)} vs {_label(payload, x)}",
            subtitle)
    fig.update_xaxes(title=_axis_title(payload, x, hints),
                     type="log" if _is_log(payload, x) else "linear")
    fig.update_yaxes(title=_axis_title(payload, y, hints),
                     type="log" if _is_log(payload, y) else "linear")
    if zoom:
        fig.update_xaxes(range=zoom[0])
        fig.update_yaxes(range=zoom[1])
    _declare_named(fig, [x, y, color, size])
    return fig


ZOOM_PAD = 0.15


def zoom_range(values, log=False):
    """The axis range that fits ``values`` with padding (log10 units on a log axis).

    Shared with EarningSpy Studio, so a zoomed preview and its render match.
    """
    vals = [v for v in values if isinstance(v, (int, float)) and (v > 0 or not log)]
    if not vals:
        return None
    if log:
        vals = [math.log10(v) for v in vals]
    lo, hi = min(vals), max(vals)
    span = hi - lo
    pad = span * ZOOM_PAD if span else (0.1 if log else (abs(hi) * 0.1 or 1.0))
    return [lo - pad, hi + pad]


def _quadrant(fig, payload, x, y, t, grouping):
    """Crosshairs at the payload's own medians, when they match these axes."""
    reference = grouped(payload.get("reference"), grouping).get("quadrant")
    if not reference:
        return
    if reference.get("x", {}).get("metric") == x:
        value = reference["x"].get("value")
        if value is not None:
            fig.add_vline(x=value, line=dict(color=t["line"], width=1,
                                             dash="dash"))
    if reference.get("y", {}).get("metric") == y:
        value = reference["y"].get("value")
        if value is not None:
            fig.add_hline(y=value, line=dict(color=t["line"], width=1,
                                             dash="dash"))


# --- treemap ----------------------------------------------------------------

def treemap(payload, size="market_cap", color=None, grouping=DEFAULT_GROUPING,
            theme="light", title=None, hover="compact", changes=False, **_):
    """See the whole peer group at a glance, sized by how big each company is.

    A market map: each company is a tile whose area is its size, shaded by a
    recent move (earnings change by default), so the group's weight and momentum
    read together — who dominates the industry and which way they are drifting.

    Available on the bronze view.
    """
    t = _theme(theme)
    require_subscribed(payload, size)
    if color is not None:
        require_subscribed(payload, color)
    view, changes = hover_options(payload, hover, changes)

    if color is None:
        for candidate in ("eps_change", "eps_qoq", "valuation_spread"):
            if (payload.get("metrics") or {}).get(candidate):
                color = candidate
                break

    # A geometric constraint, not a financial one: a tile cannot have negative
    # area. This is the only place a value is tested against zero, and it is
    # about the rendering surface rather than about the company.
    rows = [p for p in _points(payload, grouping)
            if (p.get("values") or {}).get(size) not in (None, 0)
            and (p.get("values") or {}).get(size) > 0]
    if not rows:
        return _empty(f"no company carries a positive {size}", t, title)

    scale, diverging = _colorscale(payload, color, t) if color else (t["sequential"], False)
    values = [(p.get("values") or {}).get(color) for p in rows] if color else []
    cmin, cmax = _color_range(values, diverging, payload, color) if values else (None, None)

    fig = go.Figure(go.Treemap(
        labels=[p.get("ticker") for p in rows],
        parents=[""] * len(rows),
        values=[(p.get("values") or {}).get(size) for p in rows],
        marker=dict(
            colors=values if values else None,
            colorscale=scale if values else None,
            cmin=cmin, cmax=cmax,
            line=dict(width=1.2, color=t["bg"]),
            colorbar=dict(
                title=dict(text=_label(payload, color), side="right",
                           font=dict(size=11.5, color=t["muted"])),
                thickness=11, len=0.5, tickfont=dict(size=10, color=t["muted"]),
            ) if values else None,
        ),
        text=[(p.get("company") or "")
              + ("<br>" + format_value((p.get("values") or {}).get(color), _fmt(payload, color))
                 if color else "")
              for p in rows],
        textinfo="label+text",
        textfont=dict(family=_BODY_FONT, size=12),
        hovertemplate=[_hover(payload, p, [size, color], grouping, view, changes)
                       for p in rows],
    ))
    _layout(fig, t, title or f"{_label(payload, size)} map",
            f"area is {_label(payload, size)}"
            + (f", colour is {_label(payload, color)}" if color else ""))
    # Fit, never overflow: text that would have to shrink below a readable size
    # is hidden (the tile keeps its hover), as Studio leaves such tiles unlabelled.
    fig.update_layout(uniformtext=dict(minsize=9, mode="hide"))
    _declare_named(fig, [size, color])
    return fig


# --- bars -------------------------------------------------------------------

def bar(payload, metric="roic", top=20, tickers=None, grouping=DEFAULT_GROUPING,
        theme="light", title=None, highlight=None, hover="compact", changes=False, group=None, **_):
    """Rank the peer group on one measure, top to bottom.

    The simplest read: pick a measure and see who leads and who trails, with the
    anchor company highlighted so you can place it in the pack at a glance.
    Horizontal bars keep the company names readable.

    Available on the bronze view. `metric` names any measure the payload
    carries — run describe() to see them. `highlight` picks the company drawn
    in the anchor colour (default: the anchor). `group="industry"` (or
    `"sector"`) orders the bars by each company's industry, then by the measure,
    with the industry named beside its block — the read of a sector-wide group.
    """
    group_col = {"sector": "SECTOR", "industry": "INDUSTRY"}.get(group)
    if group and not group_col:
        raise ChartError(f"group must be 'sector' or 'industry', not {group!r}.")
    t = _theme(theme)
    require_subscribed(payload, metric)
    view, changes = hover_options(payload, hover, changes)
    if highlight:
        _require_point(payload, highlight, "focus company")

    pool = _points(payload, grouping)
    if tickers:
        wanted = _with_focus(payload, tickers, highlight)
        pool = [p for p in pool if (p.get("ticker") or "").upper() in wanted]
    rows = [p for p in pool if _plottable(p, metric)]
    left_off = _left_off_line(payload, [p for p in pool if not _plottable(p, metric)], [metric])
    if not rows:
        twin = _resolved_twin(payload, metric)
        msg = (f"no company has a usable {metric} — these peers have reported; "
               f"plot {twin} (the ex-ante value) instead" if twin
               else f"no company has a usable {metric}")
        return _empty(msg, t, title)

    rows.sort(key=lambda p: (p.get("values") or {}).get(metric))
    if top:
        rows = rows[-int(top):]
    group_of = (lambda p: (p.get("profile") or {}).get(group_col) or "—") if group_col else None
    if group_of:                         # bottom-up: the first group alphabetically sits on top
        rows = sorted(rows, key=lambda p: str(group_of(p) if group_of(p) != "—" else "~"), reverse=True)

    anchor_ticker = (highlight or (payload.get("request") or {}).get("anchor_ticker") or "").upper()
    values = [(p.get("values") or {}).get(metric) for p in rows]

    fig = go.Figure(go.Bar(
        x=values,
        y=[_tagged(p, [metric]) for p in rows],
        orientation="h",
        marker=dict(
            color=[t["anchor"] if (p.get("ticker") or "").upper() == anchor_ticker
                   else t["accent"] for p in rows],
            line=dict(width=0),
            pattern=dict(shape=["/" if (p.get("capped") or {}).get(metric)
                                else "" for p in rows],
                         fgcolor=t["bg"], size=4),
        ),
        text=[format_value(v, _fmt(payload, metric)) for v in values],
        textposition="outside",
        textfont=dict(size=11, color=t["muted"]),
        hovertemplate=[_hover(payload, p, [metric], grouping, view, changes) for p in rows],
    ))
    if group_of:
        # Each group named at the right edge of its block, a hairline between blocks.
        labels = [_tagged(p, [metric]) for p in rows]
        start = 0
        for i, p in enumerate(rows):
            if i == len(rows) - 1 or group_of(rows[i + 1]) != group_of(p):
                fig.add_annotation(x=1, xref="paper", y=labels[(start + i) // 2], yref="y",
                                   text=f"<b>{group_of(p)}</b>", xanchor="right", showarrow=False,
                                   font=dict(size=11, color=t["muted"]))
                if start > 0:
                    fig.add_hline(y=start - 0.5, line=dict(color=t["line"], width=1))
                start = i + 1
    _layout(fig, t, title or _label(payload, metric),
            " · ".join(filter(None, [f"top {len(rows)} by {_label(payload, metric)}", left_off])),
            height=max(340, 26 * len(rows) + 150))
    fig.update_xaxes(title=_label(payload, metric),
                     type="log" if _is_log(payload, metric) else "linear")
    _declare_named(fig, [metric])
    return fig


# Three bars per company is as many as a row reads side by side.
SIGNED_MEASURES = 3


def bar_signed(payload, metrics=("sortino", "starr"), tickers=None,
               grouping=DEFAULT_GROUPING, theme="light", title=None, hover="compact",
               changes=False, **_):
    """Compare companies on one to three signed measures, above and below zero.

    Bars grow right for positive, left for negative, so who is in the black and
    who is in the red separates at a glance — useful for risk-adjusted measures
    where the sign is the whole story (e.g. Sortino, Sharpe and the sector
    information ratio side by side).

    Available on the bronze view.
    """
    t = _theme(theme)
    metrics = [metrics] if isinstance(metrics, str) else list(metrics)
    if not 1 <= len(metrics) <= SIGNED_MEASURES:
        raise ChartError(f"bar_signed compares one to {SIGNED_MEASURES} measures.")
    require_subscribed(payload, *metrics)
    view, changes = hover_options(payload, hover, changes)
    served = list(metrics)

    rows = _points(payload, grouping)
    if tickers:
        wanted = _with_focus(payload, tickers)
        rows = [p for p in rows if (p.get("ticker") or "").upper() in wanted]
    rows = [p for p in rows
            if any((p.get("values") or {}).get(m) is not None for m in served)]
    if not rows:
        return _empty("no company carries those measures", t, title)

    primary = served[0]
    rows.sort(key=lambda p: (p.get("values") or {}).get(primary) or 0)

    fig = go.Figure()
    palette = t["series"]
    for index, metric in enumerate(served):
        values = [(p.get("values") or {}).get(metric) for p in rows]
        fig.add_trace(go.Bar(
            x=values, y=[_tagged(p, served) for p in rows], orientation="h",
            name=_label(payload, metric),
            marker=dict(
                color=palette[index % len(palette)],
                line=dict(width=0),
                pattern=dict(shape=["/" if (p.get("capped") or {}).get(metric)
                                    else "" for p in rows],
                             fgcolor=t["bg"], size=4),
            ),
            hovertemplate=[_hover(payload, p, [metric], grouping, view, changes) for p in rows],
        ))

    fig.add_vline(x=0, line=dict(color=t["line"], width=1.2))
    _layout(fig, t, title or " / ".join(_label(payload, m) for m in served),
            "hatched bars mark a value shown at its display limit",
            height=max(360, 24 * len(rows) + 160))
    fig.update_layout(barmode="group")
    _top_legend(fig, t, show=len(served) > 1)
    _declare_named(fig, list(served))
    return fig


def bar_pctile(payload, tickers=None, metrics=None, grouping=DEFAULT_GROUPING,
               theme="light", title=None, peers=DEFAULT_PEERS, highlight=None,
               show_labels=False, hide_nm=None, hints=True, valuation="percentile", tags=None, **_):
    """Rank companies side by side on a common 0-100 "better" scale.

    Every measure is turned into a same-direction standing against the peer
    group — higher is better on every axis, whatever the underlying measure — so
    a fast read tells you where each company is strong and where it is weak
    without knowing which way each raw number should point. The clearest view for
    someone who wants the verdict, not the raw figures.

    The focus company (`highlight`, default the anchor) against its `peers`
    closest in market cap (1, 2, 3, 5 or 8; 1 or 2 make a "vs" chart), or
    against exactly `tickers`. The axis is gridded every 10 points; values are
    printed on the bars only with `show_labels=True` (a missing standing always
    says "n.m."). A measure with no good end (leverage, market cap) is drawn as
    its plain industry percentile.
    `hide_nm=True` leaves off a measure any company shown cannot be ranked on;
    by default a gap is a hollow marker and the row stays.
    `valuation="state"` shows Valuation Spread as each company's valuation
    state (still cheap, exiting cheap, newly cheap, fairly valued) instead of
    its percentile; the default is "percentile". `tags=["valuation_state"]`
    keeps the percentile bar and writes the state on it ("Still cheap",
    "Newly cheap", "Exiting", "Fair").

    Needs a silver key: industry percentiles are a silver feature.
    """
    require_industry_ranks(payload, "bar_pctile")
    t = _theme(theme)
    as_state = _state_view(valuation)
    tags = _check_tags(tags)
    for tag in tags:
        measure = TAGS[tag]["measure"]
        if measure not in (metrics or _gradeable(payload)):
            raise ChartError(f"tag {tag!r} labels the {_name(payload, measure)} bars — "
                             f"add {measure!r} to metrics.")
    if metrics:
        require_subscribed(payload, *metrics)
    # Every measure with data is drawn; one with no good end is its plain percentile.
    metrics = [m for m in (metrics or _gradeable(payload)) if _meta(payload, m) is not None]
    if not metrics:
        return _empty("no ranked measures available for this payload", t, title)

    focus = highlight or (payload.get("request") or {}).get("anchor_ticker")
    focus_point = _require_point(payload, focus, "focus company")
    rows = [focus_point, *select_peers(payload, focus, peers=peers, tickers=tickers)]

    def is_state(m):
        return as_state and m == _STATE_METRIC

    def has(p, m):
        return _state(p) is not None if is_state(m) else _pct(p, m, grouping) is not None

    # A measure no company shown is ranked on is an empty row: left off, as Studio does.
    unranked = [m for m in metrics if not any(has(p, m) for p in rows)]
    metrics = [m for m in metrics if m not in unranked]
    if unranked:
        log.info("bar_pctile: not ranked for any company shown: %s",
                 ", ".join(_label(payload, m) for m in unranked))
    if hide_nm:
        hidden = [m for m in metrics if not all(has(p, m) for p in rows)]
        metrics = [m for m in metrics if m not in hidden]
        if hidden:
            log.info("bar_pctile: left off, a company shown has no standing (n.m. or n/a): %s",
                     ", ".join(_label(payload, m) for m in hidden))
    if not metrics:
        return _empty("no measure ranks every company shown", t, title)

    group = ((payload.get("request") or {}).get("peer_group") or {}).get("value")
    group_line = f"<br><span style='font-size:11px;opacity:.6'>{group}</span>" \
        if group else ""

    fig = go.Figure()
    palette = t["series"]
    # Within a measure the bars run longest first, whatever their colour: each
    # bar is placed by its rank in that measure (n.m. last; ties keep legend
    # order). The colour stays with the company, and so does the legend.
    width = 0.8 / len(rows)
    rank = {}
    for m in metrics:
        if is_state(m):
            # States run strongest first: still cheap, exiting, newly cheap, fairly valued.
            order = sorted(range(len(rows)), key=lambda i: (_state_rank(payload, rows[i]), i))
        else:
            vals = [oriented(_pct(p, m, grouping), payload, m) for p in rows]
            order = sorted(range(len(rows)), key=lambda i: (vals[i] is None, -(vals[i] or 0), i))
        for r, i in enumerate(order):
            rank[(m, i)] = r
    if as_state and _STATE_METRIC in metrics:
        log.info("bar_pctile: Valuation Spread shown as the valuation state "
                 "(still cheap: cheap last quarter too).")
    # Bars are oriented (taller is better); a measure whose raw value reads
    # the other way says so under its name. The state row has no direction.
    labels = [STATE_ROW_LABEL if is_state(m) else
              _label(payload, m) + (f"<br><span style='font-size:10px;opacity:.6'>{hint}</span>"
                                    if hints and (hint := _bar_hint(payload, m)) else "")
              for m in metrics]
    for index, point in enumerate(rows):
        values = [None if is_state(m) else oriented(_pct(point, m, grouping), payload, m)
                  for m in metrics]
        # A null measure has no standing. Draw it at zero height so the bar does
        # not disappear, but the label and the hover say "not ranked" rather than
        # letting a zero read as "worst in group".
        hovers = [
            (f"<b>{point.get('ticker')}</b>{group_line}<br>"
             + (_state_hover(point) if is_state(m) else
                f"{lab}: " + (f"{v:.0f} percentile" if v is not None
                              else "<i>n.m. — not ranked here</i>"))
             + "<extra></extra>")
            for m, lab, v in zip(metrics, labels, values)
        ]
        color = palette[index % len(palette)]
        fig.add_trace(go.Bar(
            x=labels,
            width=[width] * len(metrics),
            offset=[-0.4 + rank[(m, index)] * width for m in metrics],
            # A real 0 gets a sliver so it shows as ranked-last, not missing (hover says 0).
            y=[(max(v, 0.8) if v is not None else 0) for v in values],
            name=_tagged(point, metrics),
            marker=dict(color=color, line=dict(width=0)),
            text=["" if is_state(m) else "n.m." if v is None else f"{v:.0f}" if show_labels else ""
                  for m, v in zip(metrics, values)],
            textposition="outside", textfont=dict(size=10, color=t["muted"]),
            hovertext=hovers, hovertemplate="%{hovertext}",
            legendgroup=point.get("ticker"),
        ))
        if as_state and _STATE_METRIC in metrics:
            # The state row: a short stub in the company's colour, its state written
            # upright above it. Not a height: a state is a category, not a score.
            m = metrics.index(_STATE_METRIC)
            fig.add_trace(go.Bar(
                x=[labels[m]], y=[3], width=[width],
                offset=[-0.4 + rank[(_STATE_METRIC, index)] * width],
                marker=dict(color=color, line=dict(width=0)),
                text=[_state_text(point)], textposition="outside", textangle=-90,
                textfont=dict(size=12, color=t["ink"]), cliponaxis=False, constraintext="none",
                hovertext=[hovers[m]], hovertemplate="%{hovertext}",
                showlegend=False, legendgroup=point.get("ticker"),
            ))

    # Tags: the state written upright on each company's bar of its measure (the bar
    # stays its percentile). Over the bar's end when it is tall, above it otherwise.
    tag_on = {}
    for tag in tags:
        measure = TAGS[tag]["measure"]
        if measure not in metrics:      # asked for, but not ranked on this chart
            log.info("bar_pctile: tag %s left off: %s is not drawn on this chart.",
                     tag, _label(payload, measure))
        elif is_state(measure):
            log.info("bar_pctile: tag %s left off: the row already shows the valuation state.", tag)
        else:
            tag_on[measure] = tag
    for measure, tag in tag_on.items():
        m = metrics.index(measure)
        for index, point in enumerate(rows):
            label = _tag_for(payload, point, tag, focus=point is focus_point)
            value = oriented(_pct(point, measure, grouping), payload, measure)
            if not label or value is None:
                continue
            fig.add_trace(go.Bar(
                x=[labels[m]], y=[max(value, 0.8)], width=[width],
                offset=[-0.4 + rank[(measure, index)] * width],
                marker=dict(color="rgba(0,0,0,0)", line=dict(width=0)),
                text=[label["text"]], textangle=-90, cliponaxis=False, constraintext="none",
                textposition="inside" if value > 70 else "outside", insidetextanchor="end",
                textfont=dict(size=11, color=t["ink"] if value > 70 else
                              (t["accent"] if label["strong"] else t["muted"])),
                hoverinfo="skip", showlegend=False, legendgroup=point.get("ticker"),
            ))
    _layout(fig, t, title or "Head to head",
            bold_tickers(companies_label(rows), payload) or "oriented percentile")
    fig.update_layout(barmode="group")
    _top_legend(fig, t)
    fig.update_yaxes(range=[0, 108], title="percentile vs. peers", dtick=10)
    return _declare_measures(fig, metrics)


# --- radar ------------------------------------------------------------------

# The most companies one radar draws: the focus and three peers. More shapes overlap
# into noise.
RADAR_MAX = 4


def radar(payload, tickers=None, metrics=None, grouping=DEFAULT_GROUPING,
          theme="light", title=None, **_):
    """A shape-at-a-glance profile of a company's strengths and weaknesses.

    Each spoke is one measure on a common "better is further out" scale, so a
    company's overall profile reads as a shape — well-rounded, or spiky where it
    leads and lags. Best for one to three companies at once; more than that and
    the shapes overlap into noise.

    Needs a silver key: industry percentiles are a silver feature.
    """
    require_industry_ranks(payload, "radar")
    t = _theme(theme)
    if metrics:
        require_subscribed(payload, *metrics)
    # Every measure with data is a spoke; one with no good end is its plain
    # percentile, and its name says so.
    metrics = [m for m in (metrics or _gradeable(payload)) if _meta(payload, m) is not None]
    if len(metrics) < 3:
        return _empty("this profile needs at least three ranked measures", t,
                      title)

    rows = _points(payload, grouping)
    if tickers:
        # The focus first, then the companies in the order they were named.
        anchor = ((payload.get("request") or {}).get("anchor_ticker") or "").upper()
        order = list(dict.fromkeys([anchor] + [str(t).upper() for t in
                                               ([tickers] if isinstance(tickers, str) else tickers)]))
        by_ticker = {(p.get("ticker") or "").upper(): p for p in rows}
        rows = [by_ticker[t] for t in order if t in by_ticker]
    elif payload.get("only"):               # a chosen subset: profile it together
        order = payload["only"]["tickers"]
        rows = sorted((p for p in rows if (p.get("ticker") or "").upper() in order),
                      key=lambda p: order.index((p.get("ticker") or "").upper()))
    else:
        anchor = (payload.get("request") or {}).get("anchor_ticker")
        rows = [p for p in rows if p.get("ticker") == anchor] or rows[:1]
    notes = []
    if len(rows) > RADAR_MAX:
        cut = [p.get("ticker") for p in rows[RADAR_MAX:]]
        notes.append(f"{RADAR_MAX} companies at most — {', '.join(cut)} not drawn")
        rows = rows[:RADAR_MAX]
    if not rows:
        return _empty("no companies selected", t, title)
    # A spoke a company has no standing on is drawn at the centre (the shape must
    # close); say who and why, so a collapsed shape is not read as worst-in-group.
    for point in rows:
        gaps = {}
        for m in metrics:
            if _pct(point, m, grouping) is None:
                why = _unplottable_reason(point, m) or "not ranked"
                gaps.setdefault(why, []).append(_name(payload, m))
        for why, names in gaps.items():
            every = len(names) == len(metrics)
            notes.append(f"{point.get('ticker')} not ranked on "
                         f"{'any of these measures' if every else ', '.join(names)} ({why})"
                         + (" — no shape" if every else " — at the centre"))
    if notes:
        log.info("radar: %s", "; ".join(notes))

    group = ((payload.get("request") or {}).get("peer_group") or {}).get("value")
    group_line = f"<br><span style='font-size:11px;opacity:.6'>{group}</span>" \
        if group else ""

    fig = go.Figure()
    palette = t["series"]
    labels = [_label(payload, m) for m in metrics]
    for index, point in enumerate(rows):
        raw = [oriented(_pct(point, m, grouping), payload, m) for m in metrics]
        # A null measure is drawn at zero (the shape has to close), but its hover
        # says "not ranked" so a spoke collapsed to the centre is not misread as
        # a genuine worst-in-group score.
        values = [v if v is not None else 0 for v in raw]
        hovers = [
            (f"<b>{point.get('ticker')}</b>{group_line}<br>{lab}: "
             + (f"{v:.0f} percentile" if v is not None
                else "<i>n.m. — not ranked here</i>")
             + "<extra></extra>")
            for lab, v in zip(labels, raw)
        ]
        fig.add_trace(go.Scatterpolar(
            r=values + values[:1], theta=labels + labels[:1],
            fill="toself", name=_tagged(point, metrics),
            line=dict(color=palette[index % len(palette)], width=2),
            fillcolor=palette[index % len(palette)], opacity=0.24,
            hovertext=hovers + hovers[:1], hovertemplate="%{hovertext}",
        ))

    subtitle = bold_tickers(companies_label(rows), payload) or "oriented percentile"
    if notes:
        subtitle = f"{subtitle}<br>" + "<br>".join(notes)
    _layout(fig, t, title or "Percentile profile", subtitle)
    _top_legend(fig, t)
    fig.update_layout(
        polar=dict(
            bgcolor=t["panel"],
            radialaxis=dict(range=[0, 100], dtick=10, gridcolor=t["grid"],
                            tickfont=dict(size=9.5, color=t["muted"])),
            angularaxis=dict(gridcolor=t["grid"],
                             tickfont=dict(size=11, color=t["ink"])),
        ),
    )
    return _declare_measures(fig, metrics)


# --- donut ------------------------------------------------------------------

def donut(payload, mode="market_cap", grouping=DEFAULT_GROUPING, theme="light",
          title=None, slice_text="label", **_):
    """How the peer group divides up — by size, or by who is making money.

    A part-to-whole view. In the default "market_cap" mode the ring shows how
    concentrated the group's size is in a few names; in "profitability" mode it
    splits the group by who is earning and who is only generating cash — a quick
    read of how healthy the peer set is overall.

    Available on the bronze view.
    """
    t = _theme(theme)
    if mode not in ("market_cap", "profitability"):
        # profitability reads flags; any other mode is a size-style measure and
        # must be one this client presents.
        require_subscribed(payload, mode)
    rows = _points(payload, grouping)
    if not rows:
        return _empty("no companies in this payload", t, title)

    if mode == "profitability":
        # Three definitions that legitimately disagree - a company can post an
        # accounting loss while generating cash. The payload supplies all three
        # and the split is worth showing rather than resolving.
        buckets = {"EPS and FCF positive": 0, "FCF positive only": 0,
                   "EPS positive only": 0, "neither": 0}
        for point in rows:
            flags = point.get("flags") or {}
            eps, fcf = flags.get("eps_positive"), flags.get("fcf_positive")
            key = ("EPS and FCF positive" if eps and fcf else
                   "FCF positive only" if fcf else
                   "EPS positive only" if eps else "neither")
            buckets[key] += 1
        labels = [k for k, v in buckets.items() if v]
        values = [buckets[k] for k in labels]
        subtitle = "companies by profitability definition"
        # One theme colour per surviving bucket, assigned after the empty ones
        # are dropped so the colours never slip out of step with the labels.
        colors = [t["series"][i % len(t["series"])] for i in range(len(labels))]
    else:
        usable = [p for p in rows
                  if (p.get("values") or {}).get(mode) not in (None, 0)]
        usable.sort(key=lambda p: (p.get("values") or {}).get(mode),
                    reverse=True)
        head, tail = usable[:9], usable[9:]
        labels = [p.get("ticker") for p in head]
        values = [(p.get("values") or {}).get(mode) for p in head]
        if tail:
            labels.append(f"other ({len(tail)})")
            values.append(sum((p.get("values") or {}).get(mode) for p in tail))
        subtitle = f"share of group {_label(payload, mode)}"
        colors = None

    fig = go.Figure(go.Pie(
        labels=labels, values=values, hole=0.58,
        marker=dict(colors=colors, line=dict(color=t["bg"], width=1.6)),
        textinfo={"none": "none", "share": "percent"}.get(slice_text, "label+percent"),
        textfont=dict(family=_BODY_FONT, size=11.5),
        hovertemplate="<b>%{label}</b><br>%{value:,.0f} (%{percent})"
                      "<extra></extra>",
        sort=True, direction="clockwise",
    ))
    _layout(fig, t, title or ("Profitability split" if mode == "profitability"
                              else f"{_label(payload, mode)} concentration"),
            subtitle, height=560)
    return fig


# --- dumbbell ---------------------------------------------------------------

def dumbbell(payload, metrics=None, grouping=DEFAULT_GROUPING, theme="light",
             title=None, **_):
    """See how a company's standing moved from before its report to after it.

    For one company, each measure is drawn as a short barbell: where it stood
    against its peers before the print, and where it stands now. Long connectors
    are the measures the report changed most — a fast read of what the earnings
    actually shifted.

    Needs the before-and-after view: request the payload with frame="delta" and a
    gold key. Asking for this chart on a payload without that view is an error,
    not an empty chart.
    """
    t = _theme(theme)
    delta = payload.get("delta")
    if not delta:
        raise ChartError(
            "this chart needs the before-and-after view — fetch the payload with "
            "frame='delta' (a gold view) before plotting it."
        )
    if metrics:
        require_subscribed(payload, *metrics)

    anchor = delta.get("anchor") or {}
    shifts = grouped(anchor.get("percentile_delta"), grouping)
    asked = list(metrics or ())
    metrics = [m for m in (metrics or list(shifts))
               if shifts.get(m) is not None and _meta(payload, m) is not None]
    # A measure asked for that has no before-or-after standing is named on the chart.
    skipped = [m for m in asked if m not in metrics]
    if not metrics:
        return _empty("no measure shifted enough to show for this company", t,
                      title)

    point = next((p for p in _points(payload, grouping)
                  if p.get("ticker") == anchor.get("ticker")), None)
    post_pct = {m: _pct(point, m, grouping) for m in metrics} if point else {}

    fig = go.Figure()
    for metric in metrics:
        after = post_pct.get(metric)
        if after is None:
            skipped.append(metric)
            continue
        before = after - shifts[metric]
        label = _label(payload, metric)
        # Colour reads the measure's own direction; one with no good end is neutral.
        way = ((payload.get("metrics") or {}).get(metric) or {}).get("higher_is_better")
        better = None if way is None else (shifts[metric] >= 0) == bool(way)
        fig.add_trace(go.Scatter(
            x=[before, after], y=[label, label], mode="lines",
            line=dict(color=t["line"], width=2.4), hoverinfo="skip",
        ))
        fig.add_trace(go.Scatter(
            x=[before], y=[label], mode="markers",
            marker=dict(size=13, color=t["bg"],
                        line=dict(color=t["muted"], width=2)),
            hovertemplate=f"{label} before: {before:.1f}<extra></extra>",
        ))
        fig.add_trace(go.Scatter(
            x=[after], y=[label], mode="markers",
            marker=dict(size=15,
                        color=t["muted"] if better is None else
                        (t["accent"] if better else t["anchor"])),
            hovertemplate=(f"{label} after: {after:.1f}"
                           f"<br>shift: {shifts[metric]:+.1f}<extra></extra>"),
        ))

    no_shift = (f" · no before/after standing: {', '.join(_label(payload, m) for m in skipped)}"
                if skipped else "")
    _layout(fig, t,
            title or f"{anchor.get('ticker')} before and after the print",
            f"natural percentile against the {rank_group(payload)}" + no_shift,
            height=max(340, 52 * len(metrics) + 150))
    fig.update_xaxes(title="percentile", range=[0, 100])
    return _declare_measures(fig, [m for m in metrics if m not in skipped])


# --- shift scatter ----------------------------------------------------------

def shift_scatter(payload, metric="valuation_spread", grouping=DEFAULT_GROUPING,
                  theme="light", title=None, **_):
    """See who actually gained ground on their peers after reporting.

    Two things that can disagree, which is the whole point: a company's own
    number can improve while it still slips against its industry, because the
    rest of the group improved more. One axis is the change in the raw measure,
    the other the change in standing against peers, so the quadrants read
    directly:

        right and up    improved, and gained on the group
        right and down  improved, but the group improved faster
        left and up     worsened, but less than the group did
        left and down   worsened, and lost ground

    Needs the before-and-after view: fetch with frame="delta" and a gold key.
    Asking for it without that view is an error, not an empty chart.
    """
    t = _theme(theme)
    require_subscribed(payload, metric)
    delta = payload.get("delta")
    if not delta:
        raise ChartError(
            "this chart needs the before-and-after view — fetch the payload with "
            "frame='delta' (a gold view) before plotting it."
        )

    rows = list(delta.get("points") or [])
    anchor_shift = delta.get("anchor")
    if anchor_shift:
        rows = rows + [anchor_shift]
    if not rows:
        return _empty("the delta block carries no companies", t, title)

    anchor_ticker = (anchor_shift or {}).get("ticker")
    label = _label(payload, metric)

    fig = go.Figure()
    plotted = 0
    for row in rows:
        value = (row.get("value_delta") or {}).get(metric)
        rank = grouped(row.get("percentile_delta"), grouping).get(metric)
        if value is None or rank is None:
            continue
        plotted += 1
        is_anchor = row.get("ticker") == anchor_ticker
        state = (row.get("state_delta") or {}).get("VALUATION_STATE")
        moved = f"<br>state: {state[0]} → {state[1]}" if state else ""

        fig.add_trace(go.Scatter(
            x=[value], y=[rank],
            mode="markers+text" if is_anchor else "markers",
            text=[row.get("ticker")] if is_anchor else None,
            textposition="top center",
            textfont=dict(color=t["anchor"], size=12, family=_BODY_FONT),
            marker=dict(
                size=17 if is_anchor else 11,
                color=t["anchor"] if is_anchor else t["accent"],
                # A state change is the loudest thing on this chart, so it gets
                # a ring rather than a different colour, which the axes use.
                line=dict(width=2.4 if state else 0.7,
                          color=t["ink"] if state else t["line"]),
                opacity=1.0 if is_anchor else 0.82,
            ),
            hovertemplate=(f"<b>{row.get('ticker')}</b><br>"
                           f"{label} moved {value:+.3f}<br>"
                           f"rank moved {rank:+.1f} points{moved}"
                           "<extra></extra>"),
            showlegend=False,
        ))

    if not plotted:
        return _empty(f"no company has a {label} shift", t, title)

    fig.add_vline(x=0, line=dict(color=t["line"], width=1.1, dash="dash"))
    fig.add_hline(y=0, line=dict(color=t["line"], width=1.1, dash="dash"))

    _layout(fig, t, title or f"How the print moved {label}",
            f"{plotted} companies · ringed markers changed state")
    fig.update_xaxes(title=f"{label} change")
    fig.update_yaxes(title=f"percentile change vs {rank_group(payload)}")
    _declare_named(fig, [metric])
    return fig


# --- grade card -------------------------------------------------------------

# The report card grades annualised volatility against the industry: calmer earns the better grade.
RISK_SCORE_METRIC = "vol"
RISK_SCORE_LABEL = "Volatility Grade"


def grade_card(payload, ticker=None, metrics=None, grouping=DEFAULT_GROUPING,
               theme="light", title=None, hide_nm=False, valuation="percentile", **_):
    """A one-company report card: a letter grade on each measure.

    Turns each measure into an A-to-F grade against the peer group, so a
    non-expert gets the verdict at a glance — where this company is best in class
    and where it is bottom of the pack — without reading a single raw number.
    Measures that cannot be judged show "n.m." (not meaningful), never a false F;
    `hide_nm=True` leaves them off instead. `valuation="state"` writes the
    company's valuation state (still cheap, exiting cheap, newly cheap, fairly
    valued) on the Valuation Spread row instead of a grade. Annualised volatility
    is the card's Volatility Grade: the calmer the stock against its industry,
    the better the grade.

    Needs a silver key: industry percentiles are a silver feature.
    """
    require_industry_ranks(payload, "grade_card")
    if "espy_risk" in (metrics or ()):
        raise ChartError("the report card already grades volatility, shown as Volatility Grade. "
            "Leave espy_risk off.")
    t = _theme(theme)
    as_state = _state_view(valuation)
    ticker = ticker or (payload.get("request") or {}).get("anchor_ticker")
    point = next((p for p in _points(payload, grouping)
                  if p.get("ticker") == ticker), None)
    if point is None:
        raise ChartError(
            f"{str(ticker).upper()!r} is not in this peer group — run describe() "
            f"to see which companies are."
        )
    if metrics:
        require_subscribed(payload, *metrics)

    metrics = [m for m in (metrics or _gradeable(payload))
               if _meta(payload, m) is not None]

    def is_state(m):
        return as_state and m == _STATE_METRIC

    if hide_nm:
        hidden = [m for m in metrics if (_state(point) is None if is_state(m)
                                         else _pct(point, m, grouping) is None)]
        metrics = [m for m in metrics if m not in hidden]
        if hidden:
            log.info("grade_card: left off, %s has no standing (n.m. or n/a): %s",
                     str(ticker).upper(), ", ".join(_label(payload, m) for m in hidden))
    if not metrics:
        return _empty("no gradeable measures available for this payload", t,
                      title)

    labels, grades, values, colors, hovers = [], [], [], [], []
    for metric in metrics:
        if is_state(metric):
            # A state is a category, not a grade: no bar, the state written in its place.
            labels.append(STATE_ROW_LABEL)
            grades.append(_state_text(point))
            values.append(0)
            colors.append(t["muted"])
            hovers.append(f"<b>{STATE_ROW_LABEL}</b><br>{_state_hover(point)}<extra></extra>")
            continue
        pct = _pct(point, metric, grouping)
        labels.append(RISK_SCORE_LABEL if metric == RISK_SCORE_METRIC else _label(payload, metric))
        if pct is not None and not is_oriented_metric(payload, metric):
            # Ranked but directionless (leverage, market cap): the industry
            # percentile, neutral, with no letter - never "n.m.", which reads as
            # a measure the company could not be measured on.
            grades.append(neutral_label(pct))
            values.append(pct)
            colors.append(t["muted"])
            hovers.append(f"<b>{labels[-1]}</b><br>{rank_group(payload)} percentile {pct:.0f}<extra></extra>")
            continue
        grade = grade_for(pct, payload, metric)
        grades.append(grade)
        values.append(oriented(pct, payload, metric) or 0)
        colors.append(t["muted"] if grade == "n.m." else
                      (t["accent"] if grade in ("A+", "A", "B") else t["anchor"]))
        lab, v = labels[-1], values[-1]
        hovers.append(f"<b>{lab}</b><br>grade {grade}<br>oriented percentile {v:.0f}<extra></extra>"
                      if grade != "n.m." else f"<b>{lab}</b><br>not meaningful<extra></extra>")
    if as_state and _STATE_METRIC in metrics:
        log.info("grade_card: Valuation Spread shown as the valuation state "
                 "(still cheap: cheap last quarter too).")

    fig = go.Figure(go.Bar(
        x=values, y=labels, orientation="h",
        marker=dict(color=colors, line=dict(width=0)),
        text=[f"  {g}" for g in grades],
        textposition="outside",
        textfont=dict(size=15, family=_TITLE_FONT, color=t["ink"]),
        hovertemplate=hovers,
    ))
    _layout(fig, t, title or f"{ticker} factor grades",
            f"graded on oriented percentile against the {rank_group(payload)}",
            height=max(340, 40 * len(labels) + 150))
    fig.update_xaxes(range=[0, 118], title="oriented percentile")
    # Measure names are long ("Earnings Yield (E/P)"): let the axis make room.
    fig.update_yaxes(automargin=True)
    return _declare_measures(fig, metrics)


# --- helpers ----------------------------------------------------------------

def _gradeable(payload):
    return [key for key, meta in (payload.get("metrics") or {}).items()
            if meta.get("gradeable")]


def _top_legend(fig, t, show=True):
    """A one-row legend just ABOVE the plot, with the top margin grown to hold it.

    Anchored by its bottom edge: a legend placed at y>1 with Plotly's default
    anchor hangs down into the plot and covers the first row of bars.
    """
    fig.update_layout(showlegend=show,
                      legend=dict(orientation="h", yanchor="bottom", y=1.01,
                                  xanchor="left", x=0,
                                  font=dict(size=11, color=t["muted"])))
    if show:
        top = fig.layout.margin.t if fig.layout.margin.t is not None else 74
        fig.update_layout(margin=dict(t=top + 26))
    return fig


def _empty(message, t, title=None):
    """A figure that says why it is empty.

    Returning None would make the notebook crash on .show(); a blank figure
    would leave the reader guessing.
    """
    fig = go.Figure()
    fig.add_annotation(text=message, showarrow=False, xref="paper",
                       yref="paper", x=0.5, y=0.5,
                       font=dict(size=13.5, color=t["muted"],
                                 family=_BODY_FONT))
    _layout(fig, t, title or "Nothing to plot", height=320)
    fig.update_xaxes(visible=False)
    fig.update_yaxes(visible=False)
    return fig


# --- dispatch ---------------------------------------------------------------

_REGISTRY = {
    "scatter": scatter,
    "treemap": treemap,
    "bar": bar,
    "bar_signed": bar_signed,
    "bar_pctile": bar_pctile,
    "radar": radar,
    "donut": donut,
    "dumbbell": dumbbell,
    "shift_scatter": shift_scatter,
    "grade_card": grade_card,
}

KINDS = tuple(_REGISTRY)


def check_options(builder, kind, kwargs):
    """Refuse a keyword the chart does not take. Ignored, a misspelled option would
    draw the default chart and look like the one asked for."""
    takes = [name for name, p in inspect.signature(builder).parameters.items()
             if p.kind is not p.VAR_KEYWORD and name != "payload"]
    for name in kwargs:
        if name not in takes:
            near = difflib.get_close_matches(name, takes, n=1)
            raise ChartError(f"the {kind} chart has no {name!r} option"
                             + (f" — did you mean {near[0]!r}?" if near else
                                f". It takes: {', '.join(takes)}."))


def plot(payload, kind, **kwargs):
    """Draw one chart from a payload.

    `kind` picks the chart; the keyword arguments pick what it shows. Each chart
    has its own help — ``help(plot)`` lists the kinds, and e.g.
    ``help(earningspy_glass.graph_client.scatter)`` explains one.

    Examples
    --------
    >>> plot(payload, "scatter", x="pe", y="roic").show()
    >>> plot(payload, "grade_card").show()
    """
    builder = _REGISTRY.get(kind)
    if builder is None:
        raise ChartError(
            f"there is no {kind!r} chart — choose one of: {', '.join(KINDS)}."
        )
    check_options(builder, kind, kwargs)
    # highlight= names one company; peers.largest(1) / peers.nearest(1) hand it over
    # as a one-item list, which reads the same.
    highlight = kwargs.get("highlight")
    if isinstance(highlight, (list, tuple)):
        if len(highlight) != 1:
            raise ChartError(f"highlight names one company; got {list(highlight)!r}.")
        kwargs["highlight"] = highlight[0]
    fig = builder(payload, **kwargs)
    # The measures drawn: the builder's own list, else the one or two a chart is
    # about (scatter axes, the bar's measure). Read back by fig.measures().
    meta = fig.layout.meta if isinstance(fig.layout.meta, dict) else {}
    if _MEASURES_META not in meta:
        _declare_measures(fig, meta.get(_NAMED_META) or [])
    meta = fig.layout.meta if isinstance(fig.layout.meta, dict) else {}
    fig.update_layout(meta={**meta, _MEASURE_LABELS_META: {
        k: _name(payload, k) for k in meta.get(_MEASURES_META) or []}})
    fig = _mark_pre_report(fig, payload, kind, kwargs)
    fig = _mark_pre_event(fig, payload)
    fig = _mark_only(fig, payload)
    return GlassFigure(stamp(fig, payload, kind, kwargs))


class GlassFigure(go.Figure):
    """A plotly Figure that also says which measures it drew.

    Everything a Figure does still works (``.show()``, ``write_image``, Studio
    renders it). ``fig.measures()`` prints the measures on the chart and returns
    their keys, ready to pass back as ``metrics=``.
    """

    def measures(self):
        """Print the measures this chart drew and return their keys, in order."""
        meta = self.layout.meta if isinstance(self.layout.meta, dict) else {}
        keys = list(meta.get(_MEASURES_META) or [])
        labels = meta.get(_MEASURE_LABELS_META) or {}
        for key in keys:
            print(f"  {key:28} {labels.get(key, '')}".rstrip())
        if not keys:
            print("  (this chart is not about named measures)")
        return keys


# --- the recipe ----------------------------------------------------------------
#
# Every figure carries how it was made — which company, view, tier and chart —
# in its layout metadata. It survives fig.write_json(), holds no data and no key,
# and is what EarningSpy Studio renders from: Studio fetches the data again with
# its own key, so a recipe on its own grants nothing.

RECIPE_KEY = "earningspy"
_TIER_LADDER = ("bronze", "silver", "gold")


def _jsonable(value):
    try:
        json.dumps(value)
        return True
    except (TypeError, ValueError):
        return False


def stamp(fig, payload, kind, params, **request):
    """Write the recipe for ``kind`` on ``fig`` and return ``fig``.

    ``request`` overrides what the payload says about how it was fetched
    (``ticker``, ``frame``, ``tier``, ``peer_group``) — the client object knows
    the tier it asked for; a bare payload only shows the tiers it carries.
    """
    req = payload.get("request") or {}
    layers = [t for t in _TIER_LADDER if t in (payload.get("layers_included") or [])]
    group = ((req.get("peer_group") or {}).get("column") or "industry").lower()
    recipe = {
        "graph": "peers",
        "ticker": req.get("anchor_ticker"),
        "frame": "delta" if payload.get("delta") else req.get("frame"),
        "tier": layers[-1] if layers else "bronze",
        "peer_group": group if group in ("industry", "sector") else "industry",
        "kind": kind,
        "params": {k: v for k, v in params.items() if _jsonable(v)},
    }
    if payload.get("only"):
        recipe["only"] = list(payload["only"]["tickers"])
    recipe.update({k: v for k, v in request.items() if v is not None})
    # The cohort the service RESOLVED (auto -> quarter or lookback), so Studio
    # refetches the same peers. A season is pinned only when it was ASKED for
    # (?season=): an automatic season is "the latest", and pinning it would turn
    # a next-season pre anchor into a season that has not started.
    if req.get("cohort"):
        recipe["cohort"] = req["cohort"]
    if req.get("season") and (payload.get("season") or {}).get("requested"):
        recipe["season"] = req["season"]
    meta = fig.layout.meta if isinstance(fig.layout.meta, dict) else {}
    fig.update_layout(meta={**meta, RECIPE_KEY: recipe})
    return fig


def _recipe_params(params):
    """A chart's arguments as recipe data: a Filter (as ``filter`` or
    ``highlight``) becomes its data form; anything else that is not plain JSON
    is left out. A filter built from a custom test cannot be written as data, so
    the recipe names it instead and EarningSpy Studio refuses it with that
    reason; glass still draws it."""
    out = {}
    for key, value in params.items():
        if isinstance(value, Filter):
            value = value.to_dict() if value.travels else {"custom": value.description}
        if _jsonable(value):
            out[key] = value
    return out


def _put_recipe(fig, recipe):
    meta = fig.layout.meta if isinstance(fig.layout.meta, dict) else {}
    fig.update_layout(meta={**meta, RECIPE_KEY: recipe})
    return fig


def stamp_calendar(fig, payload, kind, params, tier=None):
    """Write a calendar chart's recipe on ``fig`` and return ``fig``.

    The window is pinned as the dates it resolved to (``window.start`` /
    ``window.end``), so a render tomorrow shows the companies the preview did.
    """
    layers = [t for t in _TIER_LADDER if t in (payload.get("layers_included") or [])]
    recipe = {
        "graph": "calendar",
        "frame": payload.get("frame"),
        "tier": tier or payload.get("tier") or (layers[-1] if layers else "bronze"),
        "view": payload.get("view"),
        "window": dict(payload.get("window") or {"scope": payload.get("scope")}),
        "kind": kind,
        "params": _recipe_params(params),
    }
    return _put_recipe(fig, recipe)


def stamp_timeline(fig, payload, kind, params):
    """Write a timeline chart's recipe on ``fig`` and return ``fig``.

    ``compare`` reads both frames; the recipe names the frame the timeline was
    opened on, and the other is fetched alongside it, as here.
    """
    recipe = {
        "graph": "timeline",
        "ticker": payload.get("ticker"),
        "frame": payload.get("frame"),
        "view": payload.get("view"),
        "tier": payload.get("tier"),
        "kind": kind,
        # An option left at None (no title, default spokes) is not written: the
        # renderer applies its own default, as glass did.
        "params": _recipe_params({k: v for k, v in params.items() if v is not None}),
    }
    return _put_recipe(fig, recipe)


def recipe(fig):
    """The recipe a glass figure carries (a dict), or None.

    Takes a figure or its JSON dict (``fig.to_dict()`` / a ``write_json`` file
    loaded with ``json.load``).
    """
    layout = fig.get("layout") if isinstance(fig, dict) else fig.to_dict().get("layout")
    meta = (layout or {}).get("meta")
    return dict(meta[RECIPE_KEY]) if isinstance(meta, dict) and RECIPE_KEY in meta else None
