"""Plotly figures from a single-ticker timeline payload.

THE ONE RULE, unchanged: no finance lives here.

The timeline payload is one company's rows, newest-first, with a ``metrics``
table describing each column — label, direction, format. These builders read
that table; they never decide orientation, rank, or the meaning of a value.

Two views:

  bars(measures)   grouped bars, value on Y, quarter on X — one measure over the
                   quarters, or several side by side (percentiles, which share a
                   0–100 axis; raw measures only when they share a format family).
  compare(measure) the pre and post reading of one measure, per quarter, side by
                   side — what was on file going into the event vs what was
                   reported at it.

The quarter a row's VALUES belong to is not the quarter it was served in: a pre
snapshot carries the PREVIOUSLY reported figures, so it is labelled one quarter
back. The service says which (see :mod:`earningspy_glass.facts`).
"""

import datetime
import math
import re

import plotly.graph_objects as go

from earningspy_glass import facts
from earningspy_glass import regime as _regime
from earningspy_glass.graph_client import (
    ChartError,
    PrintedList,
    _empty,
    column_rows,
    _layout,
    _rgba,
    _theme,
    format_value,
)

# Row columns that are identity / plumbing, not measures — kept out of the hover
# body (they are in the header or are noise). Everything else the frame carries
# is shown, so the hover is "all the frame output" the way peers and calendar are.
_HOVER_SKIP = frozenset({
    "TICKER", "COMPANY", "SECTOR", "INDUSTRY", "EARNINGS_DATE", "DATADATE",
    "DAYS_LEFT", "IS_AMC", "IS_BMO", "IS_S&P500", "PRICE_AS_OF",
})

# The tier's resolved reads, shown as their own labelled blocks at the foot of
# the hover — like the calendar's. Each block is a heading and the column
# prefixes that feed it, so a new quality/gold column appears without a client
# edit. Populated on the quarters they cover, absent on the rest (the
# ticker route serves the full history and nulls these where unscored).
_RAW_BLOCKS = (
    ("Quality read (silver)", ("QUALITY_", "QS_")),
    ("Forward read (gold)", ("GOLD_",)),
)

# Safety backstop on the packed measures block: at two per line, this is ~40
# tooltip rows before the tier blocks — a generous cap that a normal frame never
# reaches (the chart also grows to fit), there only so a pathological frame keeps
# a working tooltip instead of one too tall for Plotly to render.
_HOVER_MAX_CELLS = 80


# --- reading the payload ----------------------------------------------------

def _metrics(payload):
    return payload.get("metrics") or {}


def _rows(payload):
    return payload.get("data") or []


def _resolve(payload, metric):
    """Canonical column key, matched case-insensitively, or the input unchanged."""
    if metric is None:
        return None
    served = _metrics(payload)
    if metric in served:
        return metric
    lowered = metric.lower()
    for key in served:
        if key.lower() == lowered:
            return key
    return metric


def _meta(payload, metric):
    return _metrics(payload).get(_resolve(payload, metric))


def _higher_is_better(payload, metric):
    return (_meta(payload, metric) or {}).get("higher_is_better")


_ROW_COLUMN_FORMATS = {
    "MARKET_CAP": "currency",
    "P/E": "ratio", "FWD_P/E": "ratio",
    "PRICE": "currency_per_share",
}


def _fmt(payload, metric):
    meta = _meta(payload, metric)
    if meta:
        return meta.get("format")
    return _ROW_COLUMN_FORMATS.get(_resolve(payload, metric))


def _label(payload, metric):
    return (_meta(payload, metric) or {}).get("label", metric)


# Display overrides where the short name should differ from the mechanical strip
# (shared intent with the calendar's _SHORT_OVERRIDES). Display only.
_SHORT_OVERRIDES = {"EP": "E/P"}

# What happened after a report, in the words a reader uses: the abnormal return is
# "AR", the buy-and-hold abnormal return "BHAR", each with its window in days.
# Display only — the columns keep their names (CAR_30_CAL, 1+30_RET).
#
# The 30- and 60-day windows a reader means are CALENDAR days (*_CAL columns), so
# they take the plain names ("AR 60d", "S&P 500 60d"). The business-day 30 / 60
# windows say so ("AR 60d (business days)"); 3 days is the business-day window
# and stays plain. The calendar patterns come first: they are the specific ones.
_OUTCOME_NAMES = (
    (re.compile(r"^CAR_(\d+)_CAL$"), "AR", "AR (Abnormal return)", False),
    (re.compile(r"^BHAR_(\d+)_CAL$"), "BHAR", "BHAR (Buy-and-hold abnormal return)", False),
    (re.compile(r"^1\+(\d+)_CAL_RET$"), "Return", "Return", False),
    (re.compile(r"^SPX_(\d+)_CAL_RET$"), "S&P 500", "S&P 500 return", False),
    (re.compile(r"^CAR_(\d+)$"), "AR", "AR (Abnormal return)", True),
    (re.compile(r"^BHAR_(\d+)$"), "BHAR", "BHAR (Buy-and-hold abnormal return)", True),
    (re.compile(r"^1\+(\d+)_RET$"), "Return", "Return", True),
    (re.compile(r"^EXP\.RET_(\d+)$"), "Expected", "Expected return", False),
)


def _outcome(key):
    """(short name, family title) for an outcome column (CAR_60_CAL -> "AR 60d"), or None."""
    for pattern, name, family, business in _OUTCOME_NAMES:
        match = pattern.match(key)
        if match:
            days = match.group(1)
            suffix = " (business days)" if business and days != "3" else ""
            return f"{name} {days}d{suffix}", family
    return None


def _short(payload, metric):
    """A compact name for a bar legend / axis: EPS, ROIC, E/P, ROIC pctile.

    The metrics label is the long form; a legend wants the measure a reader
    recognises. Derived from the key: strip the ``_QUANT`` suffix (marking a
    percentile with a trailing " pctile"), the ``PRE_`` carry prefix, and tidy
    separators.
    """
    key = _resolve(payload, metric)
    if _outcome(key):
        return _outcome(key)[0]
    meta = _metrics(payload).get(key) or {}
    if meta.get("format") == "percent" and meta.get("label"):
        # A tier score (EarningSpy Rank, conviction): its key is plumbing, its
        # label is the name a reader knows.
        return meta["label"]
    pctile = key.endswith("_QUANT")
    if key.startswith("PRE_") and not pctile and meta.get("label"):
        # A raw read taken before the report (the pre-event abnormality
        # probability): stripping PRE_ would drop the "before" that gives it
        # its meaning, so the label names it.
        return meta["label"]
    base = key[:-len("_QUANT")] if pctile else key
    if base.startswith("PRE_"):
        base = base[len("PRE_"):]
    pretty = _SHORT_OVERRIDES.get(base, base.replace("_", " "))
    return f"{pretty} pctile" if pctile else pretty


def require_served(payload, *metrics):
    """Fail loudly for any column this payload does not carry."""
    served = _metrics(payload)
    for metric in metrics:
        if metric is None:
            continue
        if _resolve(payload, metric) not in served:
            raise ChartError(
                f"{metric!r} is not on this timeline — run describe() to see the "
                f"columns it carries. (The other frame may have it.)"
            )


# --- the quarter shift ------------------------------------------------------

def _event_quarter(date):
    """The calendar quarter a report falls in."""
    return f"{date.year}/Q{(date.month - 1) // 3 + 1}"


def _row_date(row):
    raw = row.get("EARNINGS_DATE")
    if not raw:
        return None
    try:
        return datetime.date.fromisoformat(str(raw)[:10])
    except ValueError:
        return None


def _q_index(label):
    """A sortable integer for a 'YYYY/QN' label, for building a continuous axis."""
    year, q = label.split("/Q")
    return int(year) * 4 + (int(q) - 1)


def _continuous_quarters(labels):
    """Every quarter from the earliest to the latest label, gaps included.

    A missing quarter (a company that skipped an earnings period) must show as an
    empty slot, not be silently closed up — closing it makes a flat stretch look
    like a trend. So the X axis spans the full range, not just the events present.
    """
    if not labels:
        return []
    idxs = [_q_index(x) for x in labels]
    lo, hi = min(idxs), max(idxs)
    out = []
    for i in range(lo, hi + 1):
        year, q = divmod(i, 4)
        out.append(f"{year}/Q{q + 1}")
    return out


# --- format families (for the mixed-format refusal) -------------------------
#
# Bars sharing a Y axis must share a unit. Currency-per-share, a fraction and a
# billions figure on one axis make the small numbers vanish beside the large and
# imply a comparison that is not there. So a multi-measure raw chart must be one
# family; percentiles (0–100) are the exception that always shares.

_FORMAT_FAMILY = {
    "percent_fraction": "percent",
    "percent": "percent",
    "percent_signed": "percent",
    "ratio": "ratio",
    "ratio_2dp": "ratio",
    "ratio_x": "ratio",
    "currency": "currency",
    "currency_compact": "currency",
    "currency_2dp": "currency",
    "currency_per_share": "currency_per_share",
    "count": "count",
    "shares_count": "count",
    "percentile_points": "percentile",
    "percentile_points_signed": "percentile",
}


def _family(payload, metric):
    """The format family a measure belongs to; a _QUANT is always 'percentile'."""
    key = _resolve(payload, metric)
    if key.endswith("_QUANT") or key.endswith("_QUANT_CHANGE"):
        return "percentile"
    fmt = _fmt(payload, metric)
    return _FORMAT_FAMILY.get(fmt, fmt or "unknown")


def _assert_one_family(payload, metrics):
    """Refuse a multi-measure chart that mixes format families.

    A chart that quietly misleads is worse than one that refuses — the same
    loud-failure stance Glass takes on a served-but-unsubscribed metric. One
    measure is always fine; several must share a family (percentiles always do).
    """
    if len(metrics) < 2:
        return
    families = {m: _family(payload, m) for m in metrics}
    distinct = set(families.values())
    if len(distinct) > 1:
        detail = ", ".join(f"{_short(payload, m)} ({families[m]})" for m in metrics)
        raise ChartError(
            f"these measures do not share a scale, so grouping them on one axis "
            f"would mislead: {detail}. Run describe() to see the measures grouped "
            f"by scale and pick from one group, or use the percentiles (every "
            f"*_QUANT is 0–100 and shares an axis) to compare across families."
        )


# --- series assembly --------------------------------------------------------

def _series(payload, metric):
    """(quarter -> value) for one measure across the timeline.

    Keyed by the shifted quarter label, so pre and post align on reporting
    period. A quarter with no value is simply absent from the dict (rendered as
    an empty slot on the continuous axis).
    """
    key = _resolve(payload, metric)
    out = {}
    for row in _rows(payload):
        quarter = facts.quarter(payload, row)
        value = row.get(key)
        if quarter and value is not None:
            out[quarter] = value
    return out


def _dates_by_quarter(payload):
    """quarter -> event date (for the show_dates axis annotation)."""
    out = {}
    for row in _rows(payload):
        date, quarter = _row_date(row), facts.quarter(payload, row)
        if date is not None and quarter:
            out[quarter] = date
    return out


def _axis_labels(quarters, payload, show_dates):
    """X tick text: the quarter, optionally with the event date beneath it."""
    if not show_dates:
        return quarters
    dates = _dates_by_quarter(payload)
    out = []
    for q in quarters:
        d = dates.get(q)
        out.append(f"{q}<br><span style='font-size:9px;opacity:.6'>"
                   f"{d.strftime('%b %d')}</span>" if d else q)
    return out


# --- bars -------------------------------------------------------------------

def _window(quarters, last):
    """The latest ``last`` quarters (all of them for 0 / None)."""
    return quarters[-int(last):] if last else quarters


def _span(payload, present, last):
    """The quarters a chart shows. Every quarter between the first and the last
    value; with ``last``, the latest quarters of the whole history, so a measure
    the recent reports lack shows its empty slots rather than shrinking the chart."""
    if not last:
        return _continuous_quarters(present)
    reported = [q for r in _rows(payload) if (q := facts.quarter(payload, r))]
    return _window(_continuous_quarters(sorted(set(reported) | set(present))), last)


def gaps(payload, measures, last=None):
    """The quarters inside the chart's window that a measure lacks, as readable lines:
    ``["No EPS for 2025/Q4."]``. What a skipped slot on :func:`bars` means."""
    if isinstance(measures, str):
        measures = [measures]
    measures = [_resolve(payload, m) for m in measures]
    series = {m: _series(payload, m) for m in measures}
    present = sorted({q for s in series.values() for q in s})
    quarters = _span(payload, present, last) if present else []
    notes = []
    for m in measures:
        missing = [q for q in quarters if series[m].get(q) is None]
        if missing:
            notes.append(f"No {_short(payload, m)} for {', '.join(missing)}.")
    return notes


def bars(payload, measures, show_dates=False, theme="light", title=None, regime=False,
         last=None, **_):
    """One or several measures for this company across its quarters.

    Value on Y, quarter on X, oldest left. One measure reads as "how has this
    moved"; several share the axis only when they share a unit — currency with
    currency, percent with percent — because a fraction beside a billions figure
    makes the small one vanish and implies a comparison that is not there. Mixing
    families raises a clear error naming them. The percentiles are the exception:
    every ``*_QUANT`` is 0–100, so ``["ROIC_QUANT", "EPS_QUANT", "FCF_QUANT"]``
    compare honestly on one axis.

    ``last`` keeps the latest quarters (``None``, the default: every one).
    A skipped quarter shows as an empty slot, never closed up — a gap is
    information, and compressing it would fake a trend; :func:`gaps` lists them. ``show_dates`` prints the
    event date under each quarter, for a company whose fiscal calendar does not
    match the calendar quarter.

    ``regime=True`` adds the market each report landed in, on a second axis: the
    VIX two weeks before the report as a dotted line, each point captioned with
    the market state and the shape of the VIX curve (e.g. "Elevated · Flat"), and
    the VIX on the report day in the hover. Use it to tell a move made in a calm
    market from one made under stress. Quarters without a reading stay blank and
    are counted in the subtitle.

    Examples
    --------
    >>> es.timeline("AAPL").bars("EPS")
    >>> es.timeline("AAPL").bars(["EPS", "FCF"])                 # both currency
    >>> es.timeline("AAPL", view="calendar_quant").bars(
    ...     ["ROIC_QUANT", "EPS_QUANT", "FCF_QUANT"])            # head to head
    >>> es.timeline("AAPL").bars("EPS", regime=True)              # + market at each report
    """
    t = _theme(theme)
    if isinstance(measures, str):
        measures = [measures]
    if not measures:
        return _empty("name at least one measure to plot", t, title)
    require_served(payload, *measures)
    measures = [_resolve(payload, m) for m in measures]
    _assert_one_family(payload, measures)
    if regime and not _regime.carried(_rows(payload)):
        raise ChartError("the market regime is not on this timeline — this view or API version "
                         "does not serve MARKET_STATE / VIX_TERM / VIX_PRE_2W. Run describe() to see "
                         "what it carries.")

    series = {m: _series(payload, m) for m in measures}
    present = sorted({q for s in series.values() for q in s})
    if not present:
        return _empty("no quarter carries a value for "
                      f"{', '.join(_short(payload, m) for m in measures)}",
                      t, title)
    quarters = _span(payload, present, last)
    present = [q for q in present if q in quarters]
    rows_by_q = _rows_by_quarter(payload)

    percentile = _family(payload, measures[0]) == "percentile"
    # Full height for a null slot's ghost (see below): the axis top on a
    # percentile chart (0-100), else the tallest real value.
    ghost_h = 100 if percentile else max(
        (v for s in series.values() for v in s.values()
         if isinstance(v, (int, float))), default=1)

    fig = go.Figure()
    palette = t["series"]
    for i, m in enumerate(measures):
        s = series[m]
        og = f"m{i}"   # one offset group per measure, so its real bar and its
                       # null-slot ghost share the same slot in the grouped chart.
        # The hover carries the WHOLE frame for that quarter — every measure the
        # row holds, not just this bar's — with the charted measures emphasised.
        fig.add_trace(go.Bar(
            name=_short(payload, m), offsetgroup=og,
            x=quarters,
            y=[s.get(q) for q in quarters],   # None -> empty slot
            marker=dict(color=palette[i % len(palette)],
                        line=dict(width=0.4, color=t["line"])),
            hovertext=[_row_hover(payload, q, rows_by_q.get(q), measures)
                       for q in quarters],
            hovertemplate="%{hovertext}",
        ))

        # A quarter whose value is null renders no bar, and a bar with no height
        # has nothing to hover — a not-ranked quarter would be a silent gap. So in
        # each null slot draw a near-invisible full-height ghost in the SAME
        # offset group, carrying the same whole-frame hover: the reader sees the
        # quarter exists, gets the "not ranked" note, and never meets a dead patch
        # of chart. It sits only where the real bar is absent.
        gaps = [q for q in quarters if s.get(q) is None]
        if gaps:
            fig.add_trace(go.Bar(
                offsetgroup=og, showlegend=False,
                x=gaps, y=[ghost_h] * len(gaps),
                marker=dict(color="rgba(0,0,0,0.001)", line=dict(width=0)),
                hovertext=[_row_hover(payload, q, rows_by_q.get(q), measures)
                           for q in gaps],
                hovertemplate="%{hovertext}",
            ))

    subtitle = _bars_subtitle(payload, measures, len(present))
    if regime:
        subtitle = " · ".join(b for b in (subtitle, _add_regime(fig, quarters, rows_by_q, t)) if b)
    _layout(fig, t,
            title or _bars_title(payload, measures),
            subtitle,
            height=_fit_height(payload, quarters, rows_by_q, measures))
    fig.update_xaxes(tickvals=quarters,
                     ticktext=_axis_labels(quarters, payload, show_dates),
                     tickangle=0)
    fig.update_yaxes(title=_short(payload, measures[0]) if len(measures) == 1
                     else None,
                     range=[0, 100] if percentile else None)
    fig.update_layout(
        barmode="group",
        showlegend=len(measures) > 1,
        legend=dict(orientation="h", y=1.04, x=0, font=dict(size=11,
                    color=t["muted"])),
    )
    return fig


def _add_regime(fig, quarters, rows_by_q, t):
    """The VIX-two-weeks-before line on a second axis, captioned with the market
    state and the VIX curve. Returns the subtitle note on coverage."""
    readings = {q: _regime.reading(rows_by_q.get(q)) for q in quarters}
    read = [q for q in quarters if readings[q] and readings[q].get("VIX_PRE_2W") is not None]
    if not read:
        return "no quarter has a market-regime reading yet"
    fig.add_trace(go.Scatter(
        name="VIX 2w pre-report", x=quarters,
        y=[readings[q]["VIX_PRE_2W"] if q in read else None for q in quarters],
        yaxis="y2", mode="lines+markers+text", connectgaps=False,
        line=dict(color=t["muted"], width=1.4, dash="dot"),
        marker=dict(size=9, color=[_regime.state_color(readings[q].get("MARKET_STATE"), t) if q in read
                                   else t["line"] for q in quarters],
                    line=dict(width=1, color=t["bg"])),
        text=[_regime.short_text(readings[q]) if q in read else "" for q in quarters],
        textposition="top center", textfont=dict(size=10, color=t["muted"]),
        hovertext=[_regime.hover_text(readings[q]) if q in read else "" for q in quarters],
        hovertemplate="%{hovertext}<extra></extra>",
    ))
    fig.update_layout(yaxis2=dict(title="VIX", overlaying="y", side="right", showgrid=False,
                                  rangemode="tozero", color=t["muted"]))
    if len(read) < len(quarters):
        return (f"market regime on {len(read)} of {len(quarters)} quarters "
                "(read 2 weeks before each report)")
    return "market regime read 2 weeks before each report"


def _rows_by_quarter(payload):
    """quarter -> the source row, so the hover can show the whole frame.

    Newest-first input; if two events map to one quarter (rare), the newer wins.
    """
    out = {}
    for row in _rows(payload):
        quarter = facts.quarter(payload, row)
        if quarter:
            out.setdefault(quarter, row)
    return out


# Raw column -> its percentile column, where the names do not mechanically match.
# The earnings yield is served raw as "E/P" but ranked as "EP_QUANT".
_QUANT_TWIN_OVERRIDES = {"E/P": "EP_QUANT"}
# The reverse, for folding an encoded percentile back to its raw measure.
_RAW_BASE_OVERRIDES = {"EP_QUANT": "E/P"}


def _raw_base(payload, metric):
    """The raw measure key behind a metric: 'EP_QUANT' -> 'E/P', 'EPS' -> 'EPS'.

    So a chart that encodes a percentile column folds to its raw measure's line
    in the hover, and the percentile rides along inline rather than as its own row.
    """
    if metric in _RAW_BASE_OVERRIDES:
        return _RAW_BASE_OVERRIDES[metric]
    if metric.endswith("_QUANT"):
        return metric[:-len("_QUANT")]
    return metric


def _quant_twin(payload, key):
    """The `<key>_QUANT` percentile for a raw measure, if the payload carries it."""
    override = _QUANT_TWIN_OVERRIDES.get(key)
    if override and _resolve(payload, override) in _metrics(payload):
        return override
    q = f"{key}_QUANT"
    return q if _resolve(payload, q) in _metrics(payload) else None


def _row_cell(payload, row, key, note_thin=True):
    """One measure as plain text for packing — raw value paired with its rank.

    ``E/P: 0.03 100p`` when the percentile twin has a value (calendar style: raw
    figure, then the percentile as an ``NNp`` token greyed by the packer). When
    the twin exists but its value is NULL while the raw is present, the reason is
    always a thin peer set (the only null branch left once the subject's own
    value is in hand — subject_missing would drop the raw too), so it reads
    ``E/P: 0.03 (PIT: thin)`` rather than dropping the rank silently.
    ``note_thin=False`` suppresses that per-cell note — used when the whole
    quarter is thin and the header already says so once. Just ``E/P: 0.03`` when
    there is no percentile column at all. No tags in.
    """
    shown = format_value(row.get(key), _fmt(payload, key))
    twin = _quant_twin(payload, key)
    if twin is not None:
        pct = row.get(twin)
        if isinstance(pct, (int, float)):
            return f"{_short(payload, key)}: {shown} {pct:.0f}p"
        # twin present but null, and we have the raw value -> thin_universe.
        if note_thin and row.get(key) is not None:
            return f"{_short(payload, key)}: {shown} (PIT: thin)"
    return f"{_short(payload, key)}: {shown}"


# The rank token trailing a paired cell ("100p", "-9p"), greyed so the raw figure
# reads as the fact and the percentile as its context — the calendar/peers move.
_RANK_TOKEN = re.compile(r"(&nbsp;)(-?\d+p)\b")


def _pack_rows(cells):
    """Pack 'Label: raw NNp' cells two-per-line, greying the trailing rank token."""
    inner = "<br>".join(column_rows(cells, 2))
    inner = _RANK_TOKEN.sub(r"\1<span style='opacity:.55'>\2</span>", inner)
    # Dim the "(PIT: thin)" aside so it reads as a note, not a value.
    inner = re.sub(r"(\(PIT:(?:&nbsp;|[^)])*\))",
                   r"<span style='opacity:.5;font-style:italic'>\1</span>", inner)
    return f"<span style='font-family:monospace;font-size:11px'>{inner}</span>"


def _row_hover(payload, quarter, row, encoded):
    """Everything the frame carries for one report — the moat, timeline-side.

    Identity and the event up top, the measure(s) this chart encodes emphasised
    (raw value paired with its industry percentile), then every other measure the
    row carries — raw paired with its percentile — packed two-per-line. Nothing
    is hidden: the whole frame is shown, the same depth the peers and calendar
    hovers give. Percentile columns and identity/plumbing are folded into their
    raw measure's line rather than listed twice.
    """
    if row is None:
        return f"<b>{quarter}</b><br>n.m.<extra></extra>"

    encoded = [_resolve(payload, m) for m in encoded if m]
    lines = [f"<b>{row.get('TICKER', '')} · {quarter}</b>"]

    where = " · ".join(x for x in (row.get("INDUSTRY"), row.get("SECTOR")) if x)
    if where:
        lines.append(f"<span style='font-size:11px;opacity:.6'>{where}</span>")
    d = _row_date(row)
    if d is not None:
        lines.append(f"<span style='font-size:11px;opacity:.75'>reported "
                     f"{d.strftime('%b %d, %Y')}</span>")

    # If the payload ranks percentiles but NONE is populated on this row, the whole
    # quarter's peer set was thin — say it once here instead of tagging every line
    # with "(PIT: thin)". `whole_thin` then suppresses the per-cell note.
    quant_keys = [k for k in _metrics(payload) if k.endswith("_QUANT")]
    whole_thin = bool(quant_keys) and all(row.get(k) is None for k in quant_keys)
    if whole_thin:
        lines.append("<span style='font-size:11px;opacity:.5;font-style:italic'>"
                     "not ranked — thin industry peer set at this date</span>")

    # The measures to list: every column with a value that is NOT identity/
    # plumbing, NOT itself a percentile (percentiles ride on their raw measure's
    # line via _row_cell), and NOT part of a tier block (those get their own
    # labelled block below). A raw measure the chart encodes leads, emphasised;
    # the rest follow, packed.
    block_prefixes = tuple(p for _, prefixes in _RAW_BLOCKS for p in prefixes)

    def _listable(k):
        return (k not in _HOVER_SKIP and not k.endswith("_QUANT")
                and not k.endswith("_QUANT_CHANGE")
                and not k.startswith(block_prefixes)
                and row.get(k) is not None)

    # Encoded first, emphasised (raw + percentile inline). If a chart encodes the
    # percentile column itself (bars on *_QUANT), fold to its raw base.
    for m in encoded:
        base = _raw_base(payload, m)
        if _resolve(payload, base) in _metrics(payload) and row.get(base) is not None:
            lines.append(f"<b>{_row_cell(payload, row, base, not whole_thin)}</b>")

    # Every measure the ROW carries — not just those in metrics{}. The frame
    # includes plain columns the metrics block does not describe (MARKET_CAP,
    # P/E …); dropping them would hide real data. metrics-described columns lead
    # (in catalogue order), then the extras, so the familiar block stays on top.
    encoded_bases = {_raw_base(payload, m) for m in encoded}
    described = [k for k in _metrics(payload)
                 if _listable(k) and k not in encoded_bases]
    extras = [k for k in row
              if _listable(k) and k not in encoded_bases
              and _resolve(payload, k) not in _metrics(payload)]
    rest = described + extras
    if rest:
        # A safety cap so an exceptionally wide frame cannot build a tooltip too
        # tall to render (Plotly drops one that overflows the plot, blanking the
        # bar). The chart height scales with the frame (see bars()), so in
        # practice everything fits and nothing is trimmed; this only bites a
        # pathologically large frame, and then it keeps the tooltip working with
        # a "+N more" note rather than showing nothing.
        trimmed = max(0, len(rest) - _HOVER_MAX_CELLS)
        shown = rest[:_HOVER_MAX_CELLS]
        cells = [_row_cell(payload, row, k, not whole_thin) for k in shown]
        lines.append("<span style='opacity:.45'>─────</span>")
        lines.append(_pack_rows(cells))
        if trimmed:
            lines.append(f"<span style='font-size:10.5px;opacity:.6'>+{trimmed} "
                         f"more measure(s)</span>")

    # The tier's resolved reads, each under its own heading, on the quarters that
    # carry them. Absent (an unscored quarter, or a bronze fetch) -> no block.
    for heading, prefixes in _RAW_BLOCKS:
        cells = [_block_cell(payload, row, k) for k in row
                 if k.startswith(prefixes) and row.get(k) is not None]
        if not cells:
            continue
        lines.append(f"<span style='font-size:10.5px;opacity:.55'>{heading}</span>")
        lines.append(_pack_rows(cells))

    return "<br>".join(lines) + "<extra></extra>"


def _block_cell(payload, row, key):
    """One tier-block line: the payload's label and format where it describes
    the column (conviction, the abnormal-move probability), else the tidied key
    and the plain value."""
    meta = _metrics(payload).get(key)
    if meta:
        return f"{meta.get('label', key)}: {format_value(row[key], meta.get('format'))}"
    return f"{key.replace('_', ' ').title()}: {format_value(row[key], None)}"


def _fit_height(payload, quarters, rows_by_q, measures):
    """A figure height tall enough that the richest bar's tooltip renders.

    Plotly suppresses a tooltip taller than the plot area, so a bar near the edge
    of a short chart shows nothing. The timeline hover is the whole frame — dozens
    of rows — so the chart grows with the tallest tooltip in it: ~15px per line
    above a floor, capped so it never becomes absurd. This is why the full hover
    can stay complete without blanking the last bars.
    """
    tallest = 0
    for q in quarters:
        h = _row_hover(payload, q, rows_by_q.get(q), measures)
        tallest = max(tallest, h.count("<br>") + 1)
    return max(460, min(900, 150 + tallest * 15))


def _bars_title(payload, measures):
    # Every bar one kind of outcome (AR 3d, AR 30d, AR 60d): the title names the kind.
    families = {(_outcome(_resolve(payload, m)) or (None, None))[1] for m in measures}
    if len(families) == 1 and None not in families:
        return f"{payload.get('ticker', '')} — {families.pop()}".strip(" —")
    names = ", ".join(_short(payload, m) for m in measures)
    return f"{payload.get('ticker', '')} — {names}".strip(" —")


def _bars_subtitle(payload, measures, n):
    frame = payload.get("frame")
    bits = [f"{n} quarters", f"{frame} frame"]
    if _family(payload, measures[0]) == "percentile":
        bits.append("industry percentile (0–100)")
    return " · ".join(b for b in bits if b)


# --- compare (pre vs post) --------------------------------------------------

def compare(payload_pre, payload_post, measure, show_dates=False, theme="light",
            title=None, last=None, **_):
    """One measure across the event, per report: what was on file vs what printed.

    Two bars per report, joined on the EVENT DATE: the pre bar is what was on file
    going into that report, the post bar is what it printed. That is exactly the
    move the API's ``<measure>_CHANGE`` measures — a single event's before and
    after — so the two bars and the change label above them always agree.

    Each pair is labelled by its reported quarter (the post frame's own quarter).
    A report that has pre-but-not-post (the most recent snapshot, not yet
    reported) shows a pre bar with an empty post slot rather than being dropped —
    the asymmetry is information. The change above each pair is the API's own,
    read not computed.

    (This joins on the event, not the reporting quarter — the two readings at one
    event are the before/after of that report, which is what "on file vs reported"
    means and what the change column measures.)

    Examples
    --------
    >>> tl = EarningSpy().timeline("AAPL")
    >>> tl.compare("EPS")            # on file vs reported, per event
    """
    t = _theme(theme)
    require_served(payload_pre, measure)
    require_served(payload_post, measure)
    key = _resolve(payload_pre, measure)

    # Join on the event date: the pre and post rows of the SAME report.
    pre_by_date = _by_event_date(payload_pre, key)
    post_by_date = _by_event_date(payload_post, key)
    change_by_date = _by_event_date(payload_post, f"{key}_CHANGE") \
        if _resolve(payload_post, f"{key}_CHANGE") in _metrics(payload_post) else {}

    dates = _window(sorted(set(pre_by_date) | set(post_by_date)), last)
    if not dates:
        return _empty(f"no report carries {_short(payload_pre, measure)} on "
                      f"both frames", t, title)

    # Label each report by its reported (post/event) quarter; oldest left.
    labels = [_event_quarter(d) for d in dates]
    pre_vals = [pre_by_date.get(d) for d in dates]
    post_vals = [post_by_date.get(d) for d in dates]

    cfmt = _fmt(payload_post, f"{key}_CHANGE") if change_by_date else None
    pre_rows = _rows_by_event(payload_pre)
    post_rows = _rows_by_event(payload_post)
    fig = go.Figure()
    # Each bar's hover carries that frame's whole row for the report — the pre
    # snapshot on the "on file" bar, the post report on the "reported" bar — so
    # the compare view is as deep as bars(), peers and calendar.
    fig.add_trace(go.Bar(
        name="on file", x=labels, y=pre_vals,
        marker=dict(color=t["series"][1], line=dict(width=0.4, color=t["line"])),
        hovertext=[_row_hover(payload_pre, f"{q} · on file",
                              pre_rows.get(d), [key])
                   for q, d in zip(labels, dates)],
        hovertemplate="%{hovertext}",
    ))
    fig.add_trace(go.Bar(
        name="reported", x=labels, y=post_vals,
        marker=dict(color=t["series"][0], line=dict(width=0.4, color=t["line"])),
        hovertext=[_row_hover(payload_post, f"{q} · reported",
                              post_rows.get(d), [key])
                   for q, d in zip(labels, dates)],
        hovertemplate="%{hovertext}",
    ))

    # The API's own across-event delta, above each report's bar pair — the move
    # from the pre bar to the post bar, read not computed, so it agrees with them.
    if change_by_date:
        for q, d, pv, qv in zip(labels, dates, pre_vals, post_vals):
            dv = change_by_date.get(d)
            if dv is None:
                continue
            fig.add_annotation(
                x=q, y=max(pv or 0, qv or 0),
                text=f"<b>{format_value(dv, cfmt)}</b>",
                showarrow=False, yshift=12,
                font=dict(size=10, color=_delta_color(dv, t)),
            )

    # Tall enough for the richest of the two frames' tooltips to render (same
    # reason as bars(): Plotly drops a tooltip taller than the plot).
    pre_h = _fit_height(payload_pre, labels, {q: pre_rows.get(d)
                        for q, d in zip(labels, dates)}, [key])
    post_h = _fit_height(payload_post, labels, {q: post_rows.get(d)
                         for q, d in zip(labels, dates)}, [key])
    _layout(fig, t,
            title or f"{payload_pre.get('ticker', '')} — {_short(payload_pre, key)}"
            f": on file vs reported".strip(),
            f"{len(dates)} reports · on file going in vs reported"
            + (" · label = reported change" if change_by_date else ""),
            height=max(pre_h, post_h))
    fig.update_xaxes(tickvals=labels,
                     ticktext=_event_axis_labels(labels, dates, show_dates),
                     tickangle=0)
    fig.update_yaxes(title=_short(payload_pre, key))
    fig.update_layout(
        barmode="group",
        legend=dict(orientation="h", y=1.04, x=0, font=dict(size=11,
                    color=t["muted"])),
    )
    return fig


def _by_event_date(payload, metric):
    """(event date -> value) for one column, so pre and post pair on the report.

    Unlike :func:`_series` (keyed by the shifted reporting quarter, for the raw
    timeline), compare joins on the event itself: the pre and post rows of one
    report are the before and after of that report.
    """
    key = _resolve(payload, metric)
    out = {}
    for row in _rows(payload):
        d = _row_date(row)
        if d is None:
            continue
        v = row.get(key)
        if v is not None:
            out[d] = v
    return out


def _rows_by_event(payload):
    """event date -> the source row, for the compare hover's full-frame body."""
    out = {}
    for row in _rows(payload):
        d = _row_date(row)
        if d is not None:
            out.setdefault(d, row)
    return out


def _event_axis_labels(labels, dates, show_dates):
    """X tick text for compare: the reported quarter, optionally with the date."""
    if not show_dates:
        return labels
    return [f"{q}<br><span style='font-size:9px;opacity:.6'>{d.strftime('%b %d')}"
            f"</span>" for q, d in zip(labels, dates)]


def _delta_color(value, theme):
    """Green up / amber down for a reported change, from the theme's ramp ends.

    Direction of the NUMBER only — a sign, not a judgement about whether the move
    is good (that would need the metric's orientation and is not asserted here).
    """
    if value is None or value == 0:
        return theme["muted"]
    return theme["diverging"][-1][1] if value > 0 else theme["diverging"][0][1]


# --- radar ------------------------------------------------------------------
#
# One company's recent quarters as overlaid shapes — how its profile is moving.
# Two modes, each honestly single-scaled so the spokes share an axis:
#   "percentile" — the *_QUANT columns, 0-100, oriented so the outer ring is best.
#   "change"     — the raw *_CHANGE family, all percent_fraction and centred on
#                  zero, so a spoke past the mid-ring improved and inside it
#                  declined. (Post frame only — pre has no across-event change.)

# Default 5-feature sets — a quality/value/growth/cash/risk-adjusted spread, and
# the stable change family (EPS_SURPRISE_CHANGE excluded: it spikes to +500% and
# would dominate the axis). Overridable via `metrics=`.
_RADAR_DEFAULTS = {
    "percentile": ["ROIC_QUANT", "EP_QUANT", "EPS_SURPRISE_QUANT",
                   "FCF_QUANT", "SORTINO_RATIO_QUANT"],
    "change": ["EPS_CHANGE", "ROIC_CHANGE", "ROE_CHANGE",
               "ROA_CHANGE", "FCF_CHANGE"],
    # the risk-adjusted ratios: one unit (a ratio), higher is better on every one
    "level": ["SHARPE_ANNZD", "SORTINO_RATIO", "CALMAR_RATIO",
              "INFORMATION_RATIO", "SECTOR_INFORMATION_RATIO"],
}


def level_scale(payload, spokes, rows):
    """The shared axis of a level radar: from the lowest value shown (or zero, if
    every value is positive) to the highest. One unit, so one scale for every spoke."""
    return nice_level_axis([r.get(m) for r in rows for m in spokes if isinstance(r.get(m), (int, float))])


def nice_level_axis(values):
    """(lo, hi) of a level radar: zero or the lowest value, up to five round steps
    (0.5, 1, 2, 2.5, 5 × 10ⁿ), so the rings read 1, 2, 3 rather than 0.71, 1.68."""
    if not values:
        return 0.0, 1.0
    lo, top = min(0.0, min(values)), max(values)
    raw = max((top - lo) / 5.0, 1e-9)
    mag = 10 ** math.floor(math.log10(raw))
    step = next(m * mag for m in (1, 2, 2.5, 5, 10) if m * mag >= raw)
    lo = math.floor(lo / step) * step
    while lo + 5 * step < top:
        step = next(m * mag for m in (1, 2, 2.5, 5, 10, 20, 25, 50) if m * mag > step)
        lo = math.floor(min(0.0, min(values)) / step) * step
    return lo, lo + 5 * step


def _check_level(payload, spokes):
    """A level radar draws raw values on one axis, outer = more: every spoke must
    share a unit and read better when higher."""
    _assert_one_family(payload, spokes)
    lower = [_short(payload, m) for m in spokes if _higher_is_better(payload, m) is False]
    if lower:
        raise ChartError(
            f"a level radar reads further out as better, but less is better for "
            f"{', '.join(lower)}. Use mode='percentile' (oriented) for those.")

# The change radar clamps each spoke to ±CHANGE_CLAMP around zero, then maps that
# to the 0-1 radius (0.5 = no change). Most changes sit well inside ±0.5; the
# clamp only tames a rare outlier so the common shape stays readable.
_CHANGE_CLAMP = 0.5


def radar(payload, mode="percentile", metrics=None, quarters=3, theme="light",
          title=None, **_):
    """One company's last few quarters as overlaid shapes — how its profile moves.

    Each quarter is a shape; comparing them shows whether the company is
    strengthening or slipping across the measures. Two honestly single-scaled
    modes:

    - ``"percentile"`` — the industry-percentile columns (0–100), oriented so the
      outer ring is always the good end. The default.
    - ``"change"`` — the across-event raw changes (all fractional, centred on
      zero): a spoke past the mid-ring improved that quarter, inside it declined.
      Post frame only — the pre frame has no across-event change.
    - ``"level"`` — the values themselves, on one axis: the spokes must share a unit
      and read better when higher (the default: Sharpe, Sortino, STARR and the two
      information ratios). The axis runs from the lowest value shown (or zero) to
      the highest, so a negative quarter sits inside the zero ring.

    ``quarters`` sets how many recent quarters to overlay (default 3; two or three
    read best). ``metrics`` overrides the five default spokes. The hover shows
    each spoke's real value.

    Examples
    --------
    >>> es.timeline("AAPL").radar()                         # last 3q, percentiles
    >>> es.timeline("AAPL", frame="post").radar(mode="change")
    """
    t = _theme(theme)
    if mode not in ("percentile", "change", "level"):
        raise ChartError(
            f"radar mode must be 'percentile', 'change' or 'level' — got {mode!r}.")
    if mode == "change" and payload.get("frame") != "post":
        raise ChartError(
            "the change radar needs a post timeline — the pre frame has no "
            "across-event change. Fetch with frame='post'.")

    spokes = [_resolve(payload, m) for m in (metrics or _RADAR_DEFAULTS[mode])]
    spokes = [m for m in spokes if m in _metrics(payload)]
    if len(spokes) < 3:
        return _empty("this radar needs at least three of its measures on the "
                      "timeline", t, title)

    # The most recent `quarters` rows — sort by date rather than trust the
    # payload's order (the service sends newest-first, but a chart owns its own
    # axis), then take the tail.
    dated = sorted((r for r in _rows(payload) if _row_date(r) is not None),
                   key=_row_date)
    rows = dated[-quarters:]              # oldest of the selected first, newest last
    if not rows:
        return _empty("no quarters to profile", t, title)
    labels = [_short(payload, m) for m in spokes]
    scale = None
    if mode == "level":
        _check_level(payload, spokes)
        scale = level_scale(payload, spokes, rows)

    fig = go.Figure()
    palette = t["series"]
    for i, row in enumerate(rows):
        colour = palette[i % len(palette)]
        q = facts.quarter(payload, row)
        radii, hovers = [], []
        for m, lab in zip(spokes, labels):
            v = row.get(m)
            radii.append(_radar_radius(payload, m, v, mode, scale))
            hovers.append(_radar_hover(payload, q, m, lab, v, mode))
        fig.add_trace(go.Scatterpolar(
            r=radii + radii[:1], theta=labels + labels[:1],
            fill="toself", name=q, mode="lines",
            line=dict(color=colour, width=2), fillcolor=_rgba(colour, 0.12),
            hovertext=hovers + hovers[:1], hovertemplate="%{hovertext}",
        ))

    if mode == "percentile":
        rng, ticks, subtitle = [0, 100], [25, 50, 75], \
            "oriented industry percentile — outer ring is best"
    elif mode == "level":
        lo, hi = scale
        rng, ticks = [0, 100], ([100 * (0 - lo) / (hi - lo)] if lo < 0 else [])
        subtitle = f"{_family(payload, spokes[0])} — one axis from {lo:.1f} to {hi:.1f}; outer ring is best"
    else:
        rng, ticks, subtitle = [0, 1], [0.5], \
            "across-event change — outside the mid-ring improved, inside declined"

    _layout(fig, t, title or f"{payload.get('ticker','')} — {mode} profile".strip(),
            subtitle, height=560)
    fig.update_layout(
        showlegend=True,
        # Sit the legend clear above the plot (a radar's spokes reach the top
        # edge, so a legend hard against it collides with them).
        legend=dict(orientation="h", y=1.16, x=0.5, xanchor="center",
                    yanchor="bottom", font=dict(size=11, color=t["muted"])),
        # Shrink the polar area so it does not run into the legend, and leave top
        # room for both.
        margin=dict(t=120, b=48, l=64, r=64),
        polar=dict(
            domain=dict(y=[0.0, 0.9]),
            bgcolor=t["panel"],
            # Hide the radial axis LINE (the spoke Plotly otherwise draws from the
            # centre straight up to the rim — the "weird straight line") while
            # keeping the faint reference rings.
            radialaxis=dict(range=rng, gridcolor=t["grid"], tickvals=ticks,
                            showticklabels=False, showline=False, ticks=""),
            angularaxis=dict(gridcolor=t["grid"], linecolor=t["line"],
                             tickfont=dict(size=11.5, color=t["ink"])),
        ),
    )
    return fig


def _radar_radius(payload, metric, value, mode, scale=None):
    """A spoke's plotted radius. Null -> 0 (centre; the hover flags it)."""
    if not isinstance(value, (int, float)):
        return 0.0
    if mode == "level":
        lo, hi = scale
        return 100.0 * (value - lo) / (hi - lo)
    if mode == "percentile":
        v = value
        if _higher_is_better(payload, metric) is False:
            v = 100.0 - v            # orient: outer ring is always the good end
        return v
    # change: clamp to ±_CHANGE_CLAMP, map to 0-1 with 0.5 = no change.
    c = max(-_CHANGE_CLAMP, min(_CHANGE_CLAMP, value))
    return 0.5 + (c / (2 * _CHANGE_CLAMP))


def _radar_hover(payload, quarter, metric, label, value, mode):
    if not isinstance(value, (int, float)):
        note = "n.m. (thin peer set)" if mode == "percentile" else "n.m."
        return f"<b>{quarter}</b> · {label}<br><i>{note}</i><extra></extra>"
    if mode == "percentile":
        return (f"<b>{quarter}</b> · {label}<br><b>{value:.0f}</b>"
                f"<span style='opacity:.5;font-size:10px'>p</span><extra></extra>")
    if mode == "level":
        return (f"<b>{quarter}</b> · {label}<br><b>{format_value(value, _fmt(payload, metric))}</b>"
                "<extra></extra>")
    return (f"<b>{quarter}</b> · {label}<br><b>"
            f"{format_value(value, 'percent_fraction')}</b> change"
            "<extra></extra>")


# --- self-description -------------------------------------------------------

# Reader-facing names for the format families, so describe() says "currency"
# rather than "currency_per_share" — the grouping bars() enforces, in plain words.
_FAMILY_LABEL = {
    "percent": "percent (%)",
    "ratio": "ratio",
    "currency": "currency ($, billions)",
    "currency_per_share": "currency per share ($/sh)",
    "count": "count",
    "percentile": "industry percentile (0–100)",
    "unknown": "other",
}


def describe(payload):
    """Print what this timeline offers, grouped by scale, and return the rows.

    Measures are grouped by their **format family** — the same grouping
    ``bars()`` enforces — so you can pick fields that share a scale and never trip
    the "do not share a scale" refusal. One measure alone always works; several
    must come from the same group (the percentiles are the exception — every
    ``*_QUANT`` shares a 0–100 axis, so they compare across families).

    Returns the measure rows ``(key, label, family)``.
    """
    ticker = payload.get("ticker")
    frame = payload.get("frame")
    view = payload.get("view")
    rows = _rows(payload)
    quarters = sorted({q for r in rows if (q := facts.quarter(payload, r))}, key=_q_index)

    print(f"{ticker} · {frame} frame · {view} · {len(quarters)} quarters")
    if quarters:
        print(f"  {quarters[0]} … {quarters[-1]}")

    # Group by format family, so measures that can share a chart sit together.
    measures = []
    by_family = {}
    for key in _metrics(payload):
        label = _label(payload, key)
        fam = _family(payload, key)
        measures.append((key, label, fam))
        by_family.setdefault(fam, []).append((key, label))

    total = len(measures)
    print(f"\n{total} measures for bars() / compare(), grouped by scale — "
          f"several on one chart must share a group:")
    # Percentiles first (the cross-family surface), then the rest alphabetically.
    ordered = (["percentile"] if "percentile" in by_family else []) + \
        sorted(f for f in by_family if f != "percentile")
    for fam in ordered:
        items = sorted(by_family[fam])
        note = "  — always share a 0–100 axis" if fam == "percentile" else ""
        print(f"\n  {_FAMILY_LABEL.get(fam, fam)}{note}:")
        for key, label in items:
            print(f"    {key:<26} {label}")

    print("\nOne measure alone always works; several must be from the SAME group "
          "above. Percentiles (*_QUANT) compare across families.")
    # Returned as a real list, but its notebook echo is one line — the menu was
    # just printed above, so a second tuple dump would be noise.
    return PrintedList(measures, "measures")
