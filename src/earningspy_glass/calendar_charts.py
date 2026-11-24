"""Plotly figures from a calendar-percentile payload.

THE ONE RULE, unchanged: no finance lives here.

The calendar payload is flat — one row per company, each carrying its industry
percentiles as plain columns — with a ``metrics`` table beside the rows that
says, per column, its label, which way is better, and how to render it. These
builders read that table exactly the way the peers builders read theirs; they
never decide orientation, never rank, never pick a threshold. They position
marks and read labels.

Three views, dedicated to the flat shape (they do not share the peers
point-reader, which expects the nested ``points[]``/``percentiles{}`` form):

  scatter        any two percentile columns across the whole window
  bar_pctile     one percentile column, companies ranked
  change_scatter a level against its across-event move (post frame only)

Two plot-level controls both views take:

  filter    a Filter object (see filters.py) — narrows the visible companies
  highlight an industry name, "sp500", a ticker list, or a Filter — emphasises
            a subset without hiding the rest
"""

import datetime
import re

import plotly.graph_objects as go

from earningspy_glass import facts
from earningspy_glass import regime as _regime
from earningspy_glass._derived import quantile
from earningspy_glass.filters import (
    Filter,
    earnings_week,
    resolve_highlight,
    state_label,
)
from earningspy_glass.graph_client import (
    ChartError,
    PrintedList,
    _BODY_FONT,
    column_rows,
    _color_range,
    _compact,
    _empty,
    _layout,
    _marker_size,
    _rgba,
    _theme,
    check_options,
    format_value,
)


# --- reading the flat payload -----------------------------------------------
#
# Every reader takes the metrics table from the payload and the plain column
# value from the row. Nothing is derived.

def _metrics(payload):
    return payload.get("metrics") or {}


def _rows(payload):
    return facts.listed(payload)


def _is_percentile_key(key):
    """A percentile column — a level, a change, or a carried pre-event rank.

    The metrics table now carries the raw underlying values too (``ROIC`` beside
    ``ROIC_QUANT``); this tells the two apart so the plottable axes and the
    ranked views stay percentile-only, while the hover can pair each rank with
    its raw figure.
    """
    return (key.endswith("_QUANT") or key.endswith("_QUANT_CHANGE")
            or key.startswith("PRE_"))


def _is_score_key(payload, key):
    """A 0–1 score the tier adds — the EarningSpy Rank, conviction, the
    abnormal-move probability.

    Read from the payload: a described measure that is not a percentile and
    arrives as a fraction (format ``percent``). Plotted on the same 0–100 axis
    as the percentiles (see :func:`_pos`) and shown on hover in its own format.
    """
    meta = _metrics(payload).get(key)
    return (meta is not None and not _is_percentile_key(key)
            and meta.get("format") == "percent")


def _is_axis_key(payload, key):
    """A column that can go on an axis: a percentile or a tier score."""
    return _is_percentile_key(key) or _is_score_key(payload, key)


# A few raw columns are not the mechanical strip of the _QUANT key: the earnings
# yield percentile EP_QUANT is ranked from the column literally named "E/P".
_RAW_TWIN_OVERRIDES = {
    "EP_QUANT": "E/P",
}


def _raw_twin(payload, quant_key):
    """The raw column a level percentile was ranked from, or None.

    ``ROIC_QUANT`` → ``ROIC``. Only levels have a single raw twin; a change has
    no one raw value, and a carried ``PRE_*`` rank reuses its level's raw. A few
    keys need an override where the raw column is not the stripped base
    (``EP_QUANT`` → ``E/P``). Returns the key only if the payload serves it.
    """
    if not quant_key.endswith("_QUANT"):
        return None
    served = _metrics(payload)
    override = _RAW_TWIN_OVERRIDES.get(quant_key)
    if override and override in served:
        return override
    base = quant_key[:-len("_QUANT")]
    return base if base in served else None


def _resolve(payload, metric):
    """The canonical metric key, matched case-insensitively, or the input.

    The payload's keys are the raw column names (``EP_QUANT``); an analyst types
    ``ep_quant``. Accepting either — and returning the canonical form the row and
    metrics table use — keeps the axis names friendly without a second naming
    scheme to maintain. An unknown name is returned unchanged so require_served()
    raises on it with the right message.
    """
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
    """One metric's presentation record, or None if the payload does not carry it."""
    return _metrics(payload).get(_resolve(payload, metric))


def _label(payload, metric):
    meta = _meta(payload, metric) or {}
    return meta.get("label", metric)


# Formats for plain row columns the calendar shows that are NOT in the metrics
# table (so they carry no format of their own) — chiefly the size measures used
# for a marker or a treemap tile. Without this a market cap renders as
# "1,360,000,000.00" instead of "$1.36B".
_ROW_COLUMN_FORMATS = {
    "MARKET_CAP": "currency",
}


def _fmt(payload, metric):
    meta = _meta(payload, metric)
    if meta:
        return meta.get("format")
    return _ROW_COLUMN_FORMATS.get(_resolve(payload, metric))


def _higher_is_better(payload, metric):
    meta = _meta(payload, metric) or {}
    return meta.get("higher_is_better")


def _is_oriented(payload, metric):
    """Whether a column belongs in an attractiveness view — has a good direction."""
    return _higher_is_better(payload, metric) is not None


def _oriented(payload, metric, value):
    """A percentile turned so higher always means better, by READING direction.

    The calendar values are already percentile points (0-100); a lower-is-better
    column is flipped to ``100 - value`` so a radar's outer ring is "best" on
    every spoke regardless of the underlying measure. Direction is read from the
    payload's metrics table, never decided here. A directionless column is
    returned unchanged (and excluded upstream).
    """
    if value is None:
        return None
    direction = _higher_is_better(payload, metric)
    if direction is None:
        return value
    return value if direction else 100.0 - value


def _color_clip(payload, metric):
    return (_meta(payload, metric) or {}).get("color_clip_pct")


def _is_diverging(payload, metric):
    """A signed movement column diverges around zero; a level does not.

    Read from the payload's own signal, not from a hardcoded list: the API marks
    a movement column with a `color_clip_pct` band (it clips the ramp around the
    zero-heavy centre) and formats it signed. Either is sufficient and both
    agree — a level carries neither.
    """
    meta = _meta(payload, metric) or {}
    return (meta.get("color_clip_pct") is not None
            or meta.get("format") == "percentile_points_signed")


def require_served(payload, *metrics):
    """Fail loudly for any named column this payload does not carry.

    The calendar's metrics table IS the subscription — it names exactly what the
    view served, already presentation-annotated. A column absent from it is
    either a stale axis name or one this frame does not have (the six pre-only
    metrics on a post payload, say), and either way the message says so and
    points at describe().
    """
    served = _metrics(payload)
    for metric in metrics:
        if metric is None:
            continue
        if _resolve(payload, metric) not in served:
            raise ChartError(
                f"{metric!r} is not on this calendar — run describe() to see the "
                f"columns it carries. (The other frame, or a wider scope, may "
                f"have it.)"
            )


def _value(row, metric):
    """The plain column value off a flat row (None if absent or null).

    ``metric`` must already be the canonical column name — builders resolve their
    axis arguments once at entry (via :func:`_resolve`), so by the time a value is
    read the name matches the row's own keys.
    """
    if metric is None:
        return None
    v = row.get(metric)
    return v if v is not None else None


def _pos(payload, row, metric):
    """Where a value sits on a 0–100 axis.

    A percentile is already 0–100; a tier score arrives as a fraction, so it is
    placed at ``value × 100`` to share the axis. Display only — hover and labels
    keep the value as delivered, in its own format.
    """
    v = _value(row, metric)
    if isinstance(v, (int, float)) and _is_score_key(payload, metric):
        return v * 100.0
    return v


def _week_ranked(payload, *metrics):
    """The metrics among these that the payload says are ranked within the week."""
    keys = [_resolve(payload, m) for m in metrics if m]
    return [k for k in keys
            if (_meta(payload, k) or {}).get("ranked_within") == "earnings_week"]


def _rank_note(payload, rows, *metrics):
    """How to read a week-ranked score on this chart, or None.

    The EarningSpy Rank and conviction rank a company against the others
    reporting the same earnings week (Monday to Sunday). The chart draws any
    window; when it spans several weeks the note says so, so each company is
    read against its own week. ``Filter.week(date)`` narrows to one.
    """
    if not _week_ranked(payload, *metrics):
        return None
    weeks = {w for w in (earnings_week(r.get("EARNINGS_DATE")) for r in rows)
             if w is not None}
    if len(weeks) > 1:
        return f"ranked within each earnings week ({len(weeks)} weeks shown)"
    return "ranked within its earnings week"


def _top_cut(payload, shown, metric):
    """Where the week's top 10% begins (see :func:`facts.week_cut`). None when the
    shown companies span several weeks: each week has its own."""
    weeks = {earnings_week(r.get("EARNINGS_DATE")) for r in shown} - {None}
    return facts.week_cut(payload, weeks.pop(), metric) if len(weeks) == 1 else None


def _visible_rows(payload, filter=None):
    """The rows a plot draws: all of them, or those a Filter keeps."""
    rows = _rows(payload)
    if filter is None:
        return rows
    if isinstance(filter, dict):
        filter = Filter.from_dict(filter)        # the data form a recipe carries
    if not isinstance(filter, Filter):
        raise TypeError(
            "filter must be a Filter object — build one with Filter.industry(...), "
            "Filter.sp500(), Filter.tickers([...]), combined with & | ~."
        )
    return filter.keep(rows)


def _colorscale(payload, metric, theme):
    diverging = _is_diverging(payload, metric)
    return (theme["diverging"] if diverging else theme["sequential"]), diverging


# --- hover ------------------------------------------------------------------
#
# The hover is the product. A calendar mark answers every follow-up question
# without a redraw: identity and event up top, the encoded measures emphasised,
# then every other percentile the company carries, then the resolved quality and
# forward reads a higher-tier key unlocks. It reads SHORT names (ROIC, not
# "Return on invested capital industry percentile") with the percentile point in
# grey, packed two-per-line so the tooltip stays short enough to render even near
# a plot edge — the same discipline as the peers hover.

_HOVER_MAX_LINES = 30

# Raw pass-through blocks a higher tier adds beside the percentile columns. Like
# the peers outcomes/model/valuation blocks, these are already-resolved facts the
# calendar does not rank — shown verbatim under a utility-facing heading, never
# with a word about how they were produced. Discovered by column prefix so a new
# quality/gold column appears without a client edit.
_RAW_BLOCKS = (
    ("QUALITY_", "Quality read (silver)"),
    ("GOLD_", "Forward read (gold)"),
)


# Display overrides for the short name, where the mechanical strip of the column
# key is not how a reader wants to see the measure. Display only — the payload
# key is unchanged. "EP" reads as "E/P", the earnings yield, so it is legible as
# the inverted P/E (and its "higher is better" direction makes immediate sense).
_SHORT_OVERRIDES = {
    "EP": "E/P",
}


def _short(payload, metric):
    """A compact axis-style name for a percentile column: ROIC, EPS surprise.

    The metrics table's label is the long form ("Return on invested capital
    industry percentile") — right for describe(), too heavy for a tooltip packed
    with twenty of them. The short name comes from the column KEY, which is the
    measure a quant already recognises: strip the PRE_ carry prefix and the
    _QUANT / _QUANT_CHANGE suffix, and tidy the separators. A trailing "Δ" marks
    a movement column so a level and its change stay distinct.
    """
    key = _resolve(payload, metric)
    if _is_score_key(payload, key):
        # A score's key is plumbing; its label ("EarningSpy Rank") is the name.
        return _label(payload, key)
    change = key.endswith("_CHANGE")
    base = key[:-len("_CHANGE")] if change else key
    if base.startswith("PRE_"):
        base = base[len("PRE_"):]
    if base.endswith("_QUANT"):
        base = base[:-len("_QUANT")]
    pretty = _SHORT_OVERRIDES.get(base, base.replace("_", " "))
    return f"{pretty} Δ" if change else pretty


def _cell(payload, row, metric):
    """One metric as plain text for column packing, peers-style.

    ``ROIC: -6.3% 32p`` — the raw value in the raw column's own format, then the
    percentile as a ``NNp`` rank token (greyed by :func:`_pack` after alignment).
    When the payload carries no raw twin (a change, a carried rank, or a bronze
    frame without raws) it falls back to the percentile alone (``ROIC: 32.1``).
    A null cell is skipped by the caller.
    """
    short = _short(payload, metric)
    pct = _value(row, metric)
    twin = _raw_twin(payload, metric)
    rank = f" {pct:.0f}p" if isinstance(pct, (int, float)) else ""
    if twin is not None:
        raw = format_value(_value(row, twin), _fmt(payload, twin))
        return f"{short}: {raw}{rank}"
    # No raw twin (a change / carried rank) — the percentile is the only figure,
    # shown as the same NNp rank token so the packer greys it uniformly.
    if rank:
        return f"{short}:{rank}"
    return f"{short}: {format_value(pct, _fmt(payload, metric))}"


# The rank token in a packed cell — an optional sign, digits, then 'p' ("32p",
# "-9p"), the percentile (or its move) that trails each metric. Greyed after
# alignment so the raw figure reads as the fact and the rank as its context —
# exactly the peers hover's move. Matched on the padded form ("&nbsp;<n>p").
_RANK_TOKEN = re.compile(r"(&nbsp;)(-?\d+p)\b")

# A small greyed 'p' for single-value contexts (the radar hover), where a value
# stands alone rather than trailing a raw figure.
_P = "<span style='opacity:.5;font-size:10px'>p</span>"


def _pack(cells, *, percentile=True):
    """Pack plain-text 'label: value' cells into two aligned monospace columns.

    The calendar cousin of the peers two-column packer: same column padding
    so the right column lines up and the tooltip stays short. When ``percentile``
    is set it greys the trailing ``NNp`` rank token, so a paired cell reads as
    "raw value · rank" — the raw figure in ink, the percentile in grey, the peers
    layout. Cells must be tag-free going in; styling is applied once, after
    padding, so no tag enters the width count. The raw quality / forward blocks
    pass ``percentile=False`` — plain figures, no rank token to grey.
    """
    inner = "<br>".join(column_rows(cells, 2))
    if percentile:
        inner = _RANK_TOKEN.sub(
            r"\1<span style='opacity:.55'>\2</span>", inner)
    return f"<span style='font-family:monospace;font-size:11px'>{inner}</span>"


def _raw_block(row, prefix, described=()):
    """The (pretty-name, shown-value) pairs of a raw block on this row.

    Reads every column with the prefix that carries a value, tidies the name
    (QUALITY_QUINT -> Quality Quint) and renders the value plainly. No ranking,
    no interpretation — a pass-through, exactly like the peers raw blocks.
    Columns the payload describes are skipped: they show in the scores block,
    labelled and formatted.
    """
    cells = []
    for key, value in row.items():
        if not key.startswith(prefix) or value is None or key in described:
            continue
        name = key.replace("_", " ").title()
        shown = value if isinstance(value, str) else format_value(value, None)
        cells.append(f"{name}: {shown}")
    return cells


def _valuation_cells(row):
    """The valuation-state cells for the hover: the state as a phrase, and where
    the spread sits now vs. before.

    Reads the fields the calendar now carries (``VALUATION_STATE``,
    ``VALUATION_SPREAD``, ``VALUATION_SPREAD_PRIOR``). The state is turned into a
    reader phrase (``newly cheap``); the spreads show as plain ratios. Absent
    fields are skipped, so a payload without them adds no block.
    """
    cells = []
    st = row.get("VALUATION_STATE")
    if st:
        cells.append(f"State: {state_label(st)}")
    now = row.get("VALUATION_SPREAD")
    if isinstance(now, (int, float)):
        cells.append(f"Spread: {format_value(now, 'ratio')}")
    prior = row.get("VALUATION_SPREAD_PRIOR")
    if isinstance(prior, (int, float)):
        cells.append(f"Spread prior: {format_value(prior, 'ratio')}")
    return cells


def _report_status(payload, row):
    """``(reported, headline)``: whether this company has reported, as the service says."""
    known = facts.reported(payload, row)
    if known is None:
        return None, "REPORT NOT CONFIRMED"
    return known, "REPORTED" if known else "NOT REPORTED"


def _hover(payload, row, encoded):
    """Everything known about one company on this calendar row.

    Identity and event first; then the measures this chart encodes, emphasised
    with their short name so the axes stay findable; then every other percentile
    the company carries, packed two-per-line; then the quality and forward reads
    a higher-tier key unlocks. Trimmed to `_HOVER_MAX_LINES` so a mark near an
    edge still renders its tooltip — the header is always kept.
    """
    encoded = [_resolve(payload, m) for m in encoded if m]
    lines = [f"<b>{row.get('TICKER')}</b>"]

    company = row.get("COMPANY")
    if company:
        lines.append(f"<span style='font-size:11px;opacity:.75'>{company}</span>")

    where = " · ".join(x for x in (row.get("INDUSTRY"), row.get("SECTOR")) if x)
    if where:
        lines.append(f"<span style='font-size:11px;opacity:.6'>{where}</span>")

    # Reported or not, in bold on one line with the date — the fact that decides
    # how to read every percentile below it: a rank against peers that have
    # printed means something different from one against peers that have not.
    _, headline = _report_status(payload, row)
    date = row.get("EARNINGS_DATE")
    line = f"<b>{headline}</b>"
    if date:
        line += (f"<span style='opacity:.75'> on {str(date)[:10]}</span>")
    days = row.get("DAYS_LEFT")
    if isinstance(days, (int, float)):
        rel = f"in {int(days)}d" if days >= 0 else f"{abs(int(days))}d ago"
        line += f"<span style='font-size:11px;opacity:.6'> · {rel}</span>"
    lines.append(line)

    # What the chart encodes, first and emphasised — the raw value in its own
    # format, then the percentile in grey (peers-style). No raw twin (a change, a
    # carried rank): the percentile stands alone with a small 'p'.
    for metric in encoded:
        if metric not in _metrics(payload):
            continue
        short = _short(payload, metric)
        pct = _value(row, metric)
        twin = _raw_twin(payload, metric)
        if twin is not None:
            raw = format_value(_value(row, twin), _fmt(payload, twin))
            rank = (f"<span style='opacity:.55'> {pct:.0f} p</span>"
                    if isinstance(pct, (int, float)) else "")
            lines.append(f"<b>{short}: {raw}</b>{rank}")
        elif _is_score_key(payload, metric):
            shown = format_value(pct, _fmt(payload, metric))
            lines.append(f"<b>{_label(payload, metric)}: {shown}</b>")
        elif _is_percentile_key(metric):
            shown = format_value(pct, _fmt(payload, metric))
            lines.append(f"<b>{short}: {shown}</b>"
                         f"<span style='opacity:.5;font-size:10px'> p</span>")
        else:
            # A raw measure on an encoding (MARKET_CAP as the marker size): its
            # own label and format, no percentile mark.
            shown = format_value(pct, _fmt(payload, metric))
            lines.append(f"<b>{_label(payload, metric)}: {shown}</b>")

    lines_left = _HOVER_MAX_LINES - len(lines)

    # Every other percentile with a value, under an "industry percentile" heading
    # (so the block is unmistakably percentiles) and packed two-per-line so it is
    # half as tall. Percentile columns only — the raw values ride along paired
    # inside each cell, not as their own rows. Null cells dropped.
    rest = [m for m in _metrics(payload)
            if _is_percentile_key(m) and m not in encoded
            and _value(row, m) is not None]
    if rest and lines_left > 2:
        fit_rows = max(1, lines_left - 2)
        max_cells = fit_rows * 2
        trimmed = max(0, len(rest) - max_cells)
        rest = rest[:max_cells]
        cells = [_cell(payload, row, m) for m in rest]
        lines.append("<span style='font-size:10.5px;opacity:.55'>"
                     "industry percentile</span>")
        lines.append(_pack(cells))
        lines_left -= (2 + (len(cells) + 1) // 2)
        if trimmed:
            lines.append(f"<span style='font-size:10.5px;opacity:.6'>+{trimmed} "
                         f"more — put one on an axis to see it</span>")

    # The tier's scores (EarningSpy Rank, conviction, abnormal-move probability),
    # each under its payload label and in its own format. Absent on a payload
    # whose tier does not carry them, or on a row that was not scored.
    scores = [m for m in _metrics(payload)
              if _is_score_key(payload, m) and m not in encoded
              and _value(row, m) is not None]
    if scores and lines_left > 1:
        cells = [f"{_label(payload, m)}: "
                 f"{format_value(_value(row, m), _fmt(payload, m))}"
                 for m in scores]
        lines.append("<span style='font-size:10.5px;opacity:.55'>Scores</span>")
        lines.append(_pack(cells, percentile=False))
        lines_left -= (1 + (len(cells) + 1) // 2)

    # Raw blocks the tier unlocks (quality, forward read), each two-per-line under
    # a heading. Absent on a bronze/silver payload that does not carry them.
    for prefix, heading in _RAW_BLOCKS:
        if lines_left <= 1:
            break
        cells = _raw_block(row, prefix, _metrics(payload))
        if not cells:
            continue
        lines.append(f"<span style='font-size:10.5px;opacity:.55'>{heading}</span>")
        lines.append(_pack(cells, percentile=False))
        lines_left -= (1 + (len(cells) + 1) // 2)

    # Valuation state — is it cheap, and newly so? The state reads as a phrase
    # ("newly cheap"), beside where the spread sits now and where it sat before.
    if lines_left > 1:
        cells = _valuation_cells(row)
        if cells:
            lines.append("<span style='font-size:10.5px;opacity:.55'>"
                         "Valuation</span>")
            lines.append(_pack(cells, percentile=False))

    return "<br>".join(lines) + "<extra></extra>"


# --- self-description -------------------------------------------------------

def calendar_metrics(payload, rankable_only=False):
    """What this calendar can plot — its percentile columns and, on a silver or
    gold calendar, the scores — as (key, label, direction).

    The first question on any calendar is "what can I put on an axis"; reading it
    from the payload's own table beats a list that goes stale.
    """
    rows = []
    for key, meta in _metrics(payload).items():
        # The percentile columns and the tier scores are plottable axes; the raw
        # values in the table are for the hover, not for x / y / metric / color.
        if not _is_axis_key(payload, key):
            continue
        direction = meta.get("higher_is_better")
        if rankable_only and direction is None:
            continue
        rows.append((
            key,
            meta.get("label", key),
            "higher" if direction else ("lower" if direction is False
                                        else "no direction"),
        ))
    return sorted(rows)


def _direction_hint(payload, metric):
    """A short 'which way is good' phrase from the payload's own orientation."""
    d = _higher_is_better(payload, metric)
    return "higher is better" if d else ("lower is better" if d is False
                                         else "direction neutral")


def _axis_title(payload, metric):
    """An axis label: the short name, with the direction hint small beneath it.

    So a reader knows at a glance whether up/right is the good end — read from
    the payload's ``higher_is_better``, never decided here.
    """
    return (f"{_short(payload, metric)}<br>"
            f"<span style='font-size:9.5px'>{_direction_hint(payload, metric)}"
            f"</span>")


def _bar_title(payload, metric):
    """A colourbar title: the short name, with 'percentile' spelled out beneath.

    So a colour ramp reads as a percentile at a glance — the big name for the
    measure, the small lowercase word for what the number is. A movement column
    says 'percentile move'.
    """
    kind = "percentile move" if _is_diverging(payload, metric) else "percentile"
    return (f"{_short(payload, metric)}<br>"
            f"<span style='font-size:9.5px'>{kind}</span>")


def _trim(text, width=24):
    """Shorten a label for a legend, keeping the full name for the hover.

    A category name can run long; a legend crams it. This clips to `width` with
    an ellipsis so the legend stays tidy — the untrimmed name still shows on
    hover, so nothing is lost.
    """
    return text if len(text) <= width else text[:width - 1].rstrip() + "…"


def _scale_note(payload, *metrics, also=(), rows=()):
    """The subtitle's scale note: industry percentiles, scores, or both.

    ``metrics`` are the axes; ``also`` the other encodings (a colour). A score
    ranked within its earnings week, on either, says so (see _rank_note), so a
    reader compares it with that in mind.
    """
    keys = [_resolve(payload, m) for m in metrics if m]
    note = "Industry percentile"
    if any(_is_score_key(payload, k) for k in keys):
        if all(_is_score_key(payload, k) for k in keys):
            note = "Score, 0–100%"
        else:
            note = "Industry percentile · score as 0–100%"
    ranked = _rank_note(payload, rows, *keys, *also)
    if ranked:
        note += f" · {ranked}"
    return note


def _subtitle(payload, n, dropped=0, filter=None, highlight_label=None,
              note=None):
    scope = payload.get("scope")
    frame = payload.get("frame")
    # `note` leads (e.g. "Industry percentile") so a reader knows what the axes
    # are before the window details.
    bits = [note] if note else []
    bits.append(f"{n} companies")
    if scope and frame:
        bits.append(f"{frame} · {scope.replace('_', ' ')}")
    if dropped:
        bits.append(f"{dropped} not plottable")
    if filter is not None:
        if isinstance(filter, dict):
            filter = Filter.from_dict(filter)
        bits.append(filter.description)
    if highlight_label:
        bits.append(f"highlighting {highlight_label}")
    market = _regime.window_summary(_rows(payload))
    if market:
        bits.append(market)
    return " · ".join(bits)


# --- scatter ----------------------------------------------------------------

def scatter(payload, x="ep_quant", y="roic_quant", size="MARKET_CAP",
            color=None, filter=None, highlight=None, labels=5, names=False, theme="light", title=None,
            **_):
    """Plot two percentile columns across the whole calendar to see who stands out.

    The workhorse. Put one percentile on each axis and read which companies sit
    apart from the field reporting this week — the top-left corner strong on one
    measure and weak on another, the name the market is ranking differently from
    its industry. Any pair of the percentile columns this calendar carries works.

    ``filter`` narrows the field to the companies you care about (an industry,
    the S&P 500, a ticker list — see :class:`~earningspy_glass.Filter`).
    ``highlight`` keeps everyone on the chart but draws a subset in the emphasis
    colour: ``highlight="sp500"``, ``highlight="Software"`` (an industry), or a
    list of tickers.

    Colour defaults to none (marks in the accent colour); pass a signed movement
    column to colour by which way a rank moved. Size defaults to company size.

    Examples
    --------
    >>> plot(cal, "scatter", x="ep_quant", y="roic_quant")
    >>> plot(cal, "scatter", x="ep_quant", y="roic_quant", highlight="sp500")
    >>> plot(cal, "scatter", ..., filter=Filter.sector("Technology"))
    """
    t = _theme(theme)
    require_served(payload, x, y)
    x, y = _resolve(payload, x), _resolve(payload, y)
    # size is often a plain row column (MARKET_CAP), not a percentile in the
    # metrics table; _resolve returns it unchanged when unknown, and _value reads
    # it straight off the row either way.
    size = _resolve(payload, size)
    color = _resolve(payload, color) if color else None

    rows = _visible_rows(payload, filter)
    usable = [r for r in rows
              if _value(r, x) is not None and _value(r, y) is not None]
    dropped = len(rows) - len(usable)
    if not usable:
        return _empty(f"no company on this calendar has both "
                      f"{_label(payload, x)} and {_label(payload, y)}", t, title)

    member = resolve_highlight(highlight)
    membership = member[0] if member else None
    highlight_label = member[1] if member else None

    sizes = _marker_size([_value(r, size) for r in usable]) \
        if size and any(_value(r, size) is not None for r in usable) \
        else [16] * len(usable)

    scale, diverging = _colorscale(payload, color, t) if color else (None, False)
    color_values = [_pos(payload, r, color) for r in usable] \
        if color and _meta(payload, color) else []
    cmin, cmax = (_color_range(color_values, diverging, payload, color)
                  if color_values else (None, None))

    fig = go.Figure()

    # Split into emphasis / muted when highlighting, else one group. One trace
    # per group keeps a single continuous hover surface per look (the peers
    # scatter's "some points show no tooltip" lesson).
    def _add(members, *, marker):
        if not members:
            return
        fig.add_trace(go.Scatter(
            x=[_pos(payload, r, x) for r, _ in members],
            y=[_pos(payload, r, y) for r, _ in members],
            mode="markers", marker=marker,
            hovertext=[_hover(payload, r, [x, y, size, color])
                       for r, _ in members],
            hovertemplate="%{hovertext}", showlegend=False,
        ))

    def _trace(members, *, emphasis):
        """Draw one look. Emphasised marks are flat-coloured; ordinary marks ramp
        on the colour metric where they carry it, and fall back to a flat colour
        where they do not (Plotly rejects a None inside a colourscale array, so
        those go in their own trace — the peers scatter's lesson)."""
        if not members:
            return
        base_opacity = 0.28 if (membership and not emphasis) else 0.86
        if emphasis:
            _add(members, marker=dict(
                size=[s for _, s in members], color=t["anchor"], opacity=1.0,
                line=dict(width=0.7, color=t["line"])))
            return
        if color and color_values:
            with_c = [(r, s) for r, s in members if _value(r, color) is not None]
            without = [(r, s) for r, s in members if _value(r, color) is None]
            _add(with_c, marker=dict(
                size=[s for _, s in with_c],
                color=[_pos(payload, r, color) for r, _ in with_c],
                colorscale=scale, cmin=cmin, cmax=cmax, opacity=base_opacity,
                line=dict(width=0.7, color=t["line"])))
            _add(without, marker=dict(
                size=[s for _, s in without], color=t["accent"],
                opacity=base_opacity, line=dict(width=0.7, color=t["line"])))
        else:
            _add(members, marker=dict(
                size=[s for _, s in members], color=t["accent"],
                opacity=base_opacity, line=dict(width=0.7, color=t["line"])))

    paired = list(zip(usable, sizes))
    if membership:
        _trace([(r, s) for r, s in paired if not membership(r)], emphasis=False)
        _trace([(r, s) for r, s in paired if membership(r)], emphasis=True)
    else:
        _trace(paired, emphasis=False)

    if color and color_values and cmin is not None:
        fig.add_trace(go.Scatter(
            x=[None], y=[None], mode="markers", showlegend=False,
            marker=dict(colorscale=scale, cmin=cmin, cmax=cmax, color=[cmin],
                        size=0.1, showscale=True,
                        colorbar=dict(title=dict(text=_bar_title(payload, color),
                                                 side="right",
                                                 font=dict(size=11.5,
                                                           color=t["muted"])),
                                      thickness=11, len=0.52, x=1.015,
                                      tickfont=dict(size=10, color=t["muted"]))),
            hoverinfo="skip",
        ))

    _name_marks(fig, payload, usable, membership or (lambda r: True), labels,
                lambda r: _pos(payload, r, x), lambda r: _pos(payload, r, y), t, names=names)
    _layout(fig, t, title or f"{_short(payload, y)} vs {_short(payload, x)}",
            _subtitle(payload, len(usable), dropped, filter, highlight_label,
                      note=_scale_note(payload, x, y, also=(color,), rows=usable)))
    fig.update_xaxes(title=_axis_title(payload, x), range=[0, 100])
    fig.update_yaxes(title=_axis_title(payload, y), range=[0, 100])
    return fig


_COMPANY_SUFFIX = re.compile(
    r"[,.]?\s+(Inc|Incorporated|Corp|Corporation|Co|Company|Ltd|Limited|plc|PLC|"
    r"Holdings?|Group|N\.?V|S\.?A|AG|SE|LP|L\.P)\.?$")


def _short_company(name):
    """A company name trimmed for a label under its ticker: no "Inc", "Corp" …"""
    name = _COMPANY_SUFFIX.sub("", str(name or "").strip()).rstrip(" &,")   # "JPMorgan Chase & Co"
    return name if len(name) <= 22 else name[:21].rstrip() + "…"


def _extremes(rows, valueof, group_col):
    """labels="extremes": the highest and lowest of each group (the whole field when
    not grouped)."""
    blocks = {}
    for r in rows:
        if valueof(r) is not None:
            blocks.setdefault(r.get(group_col) if group_col else None, []).append(r)

    def order(r):
        return valueof(r), str(r.get("TICKER"))                        # ties: by ticker

    picked = []
    for members in blocks.values():
        for r in (max(members, key=order), min(members, key=order)):
            if r not in picked:
                picked.append(r)
    return picked


def _label_bars(fig, payload, rows, membership, labels, valueof, group_col, t, names):
    """Name an eagle's-eye bar chart's companies on leader lines: the largest
    ``labels`` (of the highlight), or the best and worst of each group."""
    if labels == "extremes":
        picked = _extremes([r for r in rows if not membership or membership(r)], valueof, group_col)
        ids = {id(r) for r in picked}
        membership, labels = (lambda r: id(r) in ids), len(picked)
    _name_marks(fig, payload, rows, membership or (lambda r: True), labels,
                valueof, lambda r: r.get("TICKER"), t, names=names)


def _name_marks(fig, payload, rows, membership, labels, xof, yof, t, names=True):
    """Name the largest ``labels`` companies of the highlighted group: the ticker on
    the chart, off its mark on an arrow, and "AAPL – Apple" spelled out beneath the
    plot. Nothing is named without a highlight."""
    if not membership or not labels:
        return
    members, seen = [], set()
    for r in sorted((r for r in rows if membership(r)),
                    key=lambda r: -(_value(r, "MARKET_CAP") or 0)):
        if r.get("TICKER") not in seen:          # a company with two reports in the window, once
            seen.add(r.get("TICKER"))
            members.append(r)
        if len(members) == int(labels):
            break
    offsets = [(36, -34), (-36, -34), (36, 34), (-36, 34)]
    for i, r in enumerate(members):
        ax, ay = offsets[i % len(offsets)]
        fig.add_annotation(x=xof(r), y=yof(r), text=f"<b>{r.get('TICKER')}</b>",
                           showarrow=True, arrowhead=0, arrowwidth=1.4, arrowcolor=t["ink"],
                           ax=ax, ay=ay, font=dict(size=10, color=t["ink"]),
                           bgcolor=t["bg"], borderpad=1)
    if members and names:                # names=False: the tickers alone, more room for the plot
        fig.add_annotation(
            text=" · ".join(f"<b>{r.get('TICKER')}</b> · {_short_company(r.get('COMPANY'))}"
                            for r in members),
            xref="paper", yref="paper", x=0, y=-0.2, xanchor="left", showarrow=False,
            font=dict(size=10.5, color=t["muted"]))


# --- ranked bar -------------------------------------------------------------

def bar_pctile(payload, metric="ep_quant", top=5, filter=None, highlight=None,
               color=None, reference=None, group=None, labels=8, note=None, names=False, theme="light",
               title=None, dates=False, **_):
    """Rank the calendar on one percentile column — who is top and bottom.

    A single measure across the window's companies, sorted, so the leaders and
    laggards read at a glance: who reports this week sitting highest against
    their own industry on the measure you chose. ``top`` caps how many bars show
    (the strongest by the measure; 5 by default — pass more for a longer list). ``filter`` and ``highlight`` work as on the
    scatter — narrow the field, or emphasise a subset in place.

    Examples
    --------
    >>> plot(cal, "bar_pctile", metric="roic_quant", top=20)
    >>> plot(cal, "bar_pctile", metric="ep_quant", highlight="sp500")
    >>> plot(cal, "bar_pctile", metric="roic_quant", dates=True)   # each name's report day beside it

    ``dates=True`` writes each company's report day ("Tue 17") beside its name, in
    a quiet grey column, so the reader knows when each one reports.
    """
    t = _theme(theme)
    require_served(payload, metric)
    metric = _resolve(payload, metric)

    rows = [r for r in _visible_rows(payload, filter)
            if _value(r, metric) is not None]
    if not rows:
        return _empty(f"no company on this calendar has "
                      f"{_label(payload, metric)}", t, title)

    # Higher-is-better decides which end "top" means: for a risk measure a LOW
    # percentile is the good end. Read it, never assume descending.
    direction = _higher_is_better(payload, metric)
    reverse = direction is not False   # True or None -> highest first; False -> lowest first
    rows = sorted(rows, key=lambda r: _value(r, metric), reverse=reverse)
    if top:                              # top=0: every company in the window
        rows = rows[:top]
    group_col = {"sector": "SECTOR", "industry": "INDUSTRY"}.get(group)
    if group and not group_col:
        raise ChartError(f"group must be 'sector' or 'industry', not {group!r}.")
    if group_col:                        # every bar kept, ordered by group, then by the measure
        rows = sorted(rows, key=lambda r: str(r.get(group_col) or "~"))

    member = resolve_highlight(highlight)
    membership = member[0] if member else None
    highlight_label = member[1] if member else None

    # Draw bottom-to-top so the strongest sits at the top of the chart.
    rows = list(reversed(rows))
    tickers = [r.get("TICKER") for r in rows]
    values = [_pos(payload, r, metric) for r in rows]
    if color:
        # The bar is one measure, the colour another: each bar shaded by `color`.
        require_served(payload, color)
        color = _resolve(payload, color)
        scale, diverging = _colorscale(payload, color, t)
        shades = [_pos(payload, r, color) for r in rows]
        cmin, cmax = _color_range([v for v in shades if v is not None], diverging, payload, color)
        marker = dict(color=[v if v is not None else cmin for v in shades], colorscale=scale,
                      cmin=cmin, cmax=cmax, line=dict(width=0.4, color=t["line"]),
                      colorbar=dict(title=dict(text=_bar_title(payload, color), side="right",
                                               font=dict(size=11.5, color=t["muted"])),
                                    thickness=11, len=0.5, tickfont=dict(size=10, color=t["muted"])))
    else:
        marker = dict(color=[t["anchor"] if (membership and membership(r)) else t["accent"]
                             for r in rows], line=dict(width=0.4, color=t["line"]))

    fig = go.Figure(go.Bar(
        x=values, y=tickers, orientation="h", marker=marker,
        hovertext=[_hover(payload, r, [metric, color]) for r in rows],
        hovertemplate="%{hovertext}", showlegend=False,
    ))
    if group_col:
        # Each group named at the right edge of its block, a hairline between blocks.
        blocks, start = [], 0
        for i, r in enumerate(rows):
            if i == len(rows) - 1 or rows[i + 1].get(group_col) != r.get(group_col):
                blocks.append((r.get(group_col) or "—", start, i))
                start = i + 1
        for name, a, b in blocks:
            fig.add_annotation(x=1, xref="paper", y=tickers[(a + b) // 2], yref="y", text=f"<b>{name}</b>",
                               xanchor="right", showarrow=False, font=dict(size=11, color=t["muted"]))
            if a > 0:
                fig.add_hline(y=a - 0.5, line=dict(color=t["line"], width=1))
    cut = _top_cut(payload, rows, metric) if reference == "decile_10" else None
    ref = cut["value"] if cut else (
        reference if isinstance(reference, (int, float)) and not isinstance(reference, bool) else None)
    if ref is not None:
        # The week's top-10% line (or a value the caller gives), across every bar.
        x = ref * 100.0 if _is_score_key(payload, metric) else ref
        fig.add_vline(x=x, line=dict(color=t["ink"], width=1.6, dash="dash"),
                      annotation_text=("Top 10% of the week · " if cut and cut["complete"]
                                       else "Top 10% of the week so far · " if cut
                                       else "Percentile " if _is_percentile_key(metric) else "")
                      + format_value(ref, _fmt(payload, metric)),
                      annotation_position="top left", annotation_font_size=11)
    if not top:                          # eagle's-eye: name the largest on leader lines
        _label_bars(fig, payload, rows, membership, labels,
                    lambda r: _pos(payload, r, metric), group_col, t, names)
    if note:                             # the caller's line on how to read the measure
        fig.add_annotation(text=note, xref="paper", yref="paper", x=0, y=-0.08,
                           xanchor="left", yanchor="top", showarrow=False,
                           font=dict(size=11, color=t["muted"]))
    _layout(fig, t, title or _short(payload, metric),
            _subtitle(payload, len(rows), 0, filter, highlight_label,
                      note=_scale_note(payload, metric, rows=rows)),
            height=max(320, 40 + 22 * len(rows)))
    fig.update_xaxes(title=_axis_title(payload, metric), range=[0, 100])
    fig.update_yaxes(title=None, automargin=True)
    if dates:
        fig.update_yaxes(tickmode="array", tickvals=tickers,
                         ticktext=[f"{tk}  <span style='color:{t['muted']};font-size:10px'>{_report_day(r)}</span>"
                                   for tk, r in zip(tickers, rows)])
    return fig


def _report_day(row):
    """The report's weekday and day of the month, "Tue 17"; empty when the date can't be read."""
    try:
        day = datetime.date.fromisoformat(str(row.get("EARNINGS_DATE"))[:10])
    except ValueError:
        return ""
    return f"{day:%a} {day.day}"


# --- change scatter (post only) ---------------------------------------------

def change_scatter(payload, metric="ep_quant", filter=None, highlight=None,
                   labels=5, names=False, theme="light", title=None, **_):
    """See who moved on their industry across the event, and in which direction.

    Post calendars only. One axis is where a company sits now (its level), the
    other is how far that standing moved when it reported (the across-event
    change). Together they separate the company that was already strong and held,
    from the one that jumped, from the one that slipped:

        right and up    strong now, and gained ground reporting
        right and down  strong now, but gave ground back
        left and up     weak now, but climbing
        left and down   weak now, and still falling

    ``metric`` names the base measure (e.g. ``"ep_quant"``); the chart pairs it
    with its own across-event change automatically. Needs a post calendar — the
    pre frame has no prior snapshot to move from, so asking there is an error,
    not an empty chart.

    Examples
    --------
    >>> cal = es.calendar("quarter_to_date", frame="post")
    >>> cal.plot("change_scatter", metric="ep_quant")
    """
    t = _theme(theme)
    # Accept either the base measure ("ep_quant") or the change column itself
    # ("ep_quant_change"); derive the pair and resolve both to canonical keys.
    base = metric[:-len("_change")] if metric.lower().endswith("_change") \
        else metric
    level = _resolve(payload, base)
    change = _resolve(payload, f"{base}_change")

    if change not in _metrics(payload):
        # Either the wrong frame (pre has no _CHANGE columns) or a bad metric.
        if payload.get("frame") == "pre":
            raise ChartError(
                "the change view needs a post calendar — the pre frame has no "
                "across-event movement to plot. Fetch with frame='post'."
            )
        require_served(payload, f"{base}_change")   # raises with the naming message
    require_served(payload, base)

    rows = [r for r in _visible_rows(payload, filter)
            if _value(r, level) is not None and _value(r, change) is not None]
    if not rows:
        return _empty(f"no company on this calendar has a "
                      f"{_label(payload, level)} move", t, title)

    member = resolve_highlight(highlight)
    membership = member[0] if member else None
    highlight_label = member[1] if member else None

    fig = go.Figure()

    def _trace(subset, *, emphasis):
        if not subset:
            return
        fig.add_trace(go.Scatter(
            x=[_value(r, level) for r in subset],
            y=[_value(r, change) for r in subset],
            mode="markers",
            marker=dict(size=13 if emphasis else 11,
                        color=t["anchor"] if emphasis else t["accent"],
                        line=dict(width=0.7, color=t["line"]),
                        opacity=1.0 if emphasis else (0.3 if membership else 0.82)),
            hovertext=[_hover(payload, r, [level, change]) for r in subset],
            hovertemplate="%{hovertext}", showlegend=False,
        ))

    if membership:
        _trace([r for r in rows if not membership(r)], emphasis=False)
        _trace([r for r in rows if membership(r)], emphasis=True)
    else:
        _trace(rows, emphasis=False)

    fig.add_hline(y=0, line=dict(color=t["line"], width=1.1, dash="dash"))
    _name_marks(fig, payload, rows, membership or (lambda r: True), labels,
                lambda r: _value(r, level), lambda r: _value(r, change), t, names=names)

    _layout(fig, t, title or f"How the print moved {_short(payload, level)}",
            _subtitle(payload, len(rows), 0, filter, highlight_label,
                      note="Industry percentile"))
    fig.update_xaxes(
        title=f"{_short(payload, level)} (now)<br>"
              f"<span style='font-size:9.5px'>{_direction_hint(payload, level)}"
              f"</span>", range=[0, 100])
    fig.update_yaxes(title="rank move across the event")
    return fig


# --- treemap ----------------------------------------------------------------

def treemap(payload, size="MARKET_CAP", color="ep_quant", filter=None,
            highlight=None, note=None, theme="light", title=None, **_):
    """See the whole window as a market map, sized by how big each company is.

    Every company reporting in the window is a tile whose area is its size,
    shaded by one percentile column — so the field's weight and where each name
    sits against its industry read together: which big names report this week,
    and how they rank. ``color`` takes any percentile column; ``size`` is a plain
    size measure (company size by default). ``filter`` narrows the field.

    Examples
    --------
    >>> cal.plot("treemap", color="roic_quant")
    >>> cal.plot("treemap", color="ep_quant", filter=Filter.sector("Technology"))

    Every tile is coloured. To look at one group (the S&P 500, a sector), make it
    its own treemap with ``filter``; ``highlight`` belongs to the scatter.
    """
    if highlight is not None:
        raise ChartError("a treemap colours every company; for one group, narrow it with filter= (e.g. Filter.sp500()) — highlight is for the scatter.")
    t = _theme(theme)
    if color:
        require_served(payload, color)
        color = _resolve(payload, color)
    size = _resolve(payload, size)

    # A tile cannot have negative area — the one place a value is tested against
    # zero, and it is about the drawing surface, not the company.
    rows = [r for r in _visible_rows(payload, filter)
            if isinstance(_value(r, size), (int, float)) and _value(r, size) > 0]
    if not rows:
        return _empty(f"no company in this window carries a positive {size}",
                      t, title)
    # A company with no value for the colour measure is left off: a grey tile would
    # read as the middle of the scale. The subtitle counts them.
    dropped = sum(1 for r in rows if color and _value(r, color) is None)
    if dropped:
        rows = [r for r in rows if _value(r, color) is not None]

    scale, diverging = _colorscale(payload, color, t) if color \
        else (t["sequential"], False)
    values = [_value(r, color) for r in rows] if color else []
    shown_values = [v for v in values if v is not None]
    cmin, cmax = _color_range(shown_values, diverging, payload, color) \
        if shown_values else (None, None)
    if color and not diverging and cmin is not None \
            and (_meta(payload, color) or {}).get("higher_is_better") is not None:
        # Weak to strong from terracotta through grey to teal, so a low tile never
        # reads as a pale green; the grey sits at zero when the range crosses it,
        # else at its middle.
        hues = _DONUT_HUES.get(theme, _DONUT_HUES["light"])
        low, high = hues[6], hues[0]
        if (_meta(payload, color) or {}).get("higher_is_better") is False:
            low, high = high, low
        mid = -cmin / (cmax - cmin) if cmin < 0 < cmax else 0.5
        scale = [[0.0, low], [mid, _TREEMAP_MID.get(theme, _TREEMAP_MID["light"])], [1.0, high]]

    fig = go.Figure(go.Treemap(
        labels=[r.get("TICKER") for r in rows],
        parents=[""] * len(rows),
        values=[_value(r, size) for r in rows],
        marker=dict(
            colors=values if color else None,
            colorscale=scale if color else None,
            cmin=cmin, cmax=cmax,
            line=dict(width=1.2, color=t["bg"]),
            colorbar=dict(
                title=dict(text=_bar_title(payload, color)
                           + (" · lower is better" if (_meta(payload, color) or {}).get("higher_is_better") is False else ""),
                           side="right",
                           font=dict(size=11.5, color=t["muted"])),
                thickness=11, len=0.5, tickfont=dict(size=10, color=t["muted"]),
            ) if color else None,
        ),
        text=[format_value(_value(r, color), _fmt(payload, color)) if color else ""
              for r in rows],
        textinfo="label+text",
        textfont=dict(family=_BODY_FONT, size=12),
        hovertemplate=[_hover(payload, r, [size, color]) for r in rows],
    ))
    if not color:
        colour = ""
    elif _is_score_key(payload, color):
        colour = f", colour is {_short(payload, color)}"
    else:
        colour = f", colour is {_short(payload, color)} industry percentile"
    ranked = _rank_note(payload, rows, color)
    if note:
        fig.add_annotation(text=note, xref="paper", yref="paper", x=0, y=-0.06,
                           xanchor="left", yanchor="top", showarrow=False,
                           font=dict(size=11, color=t["muted"]))
    _layout(fig, t, title or (_short(payload, color) if color else f"{size} map"),
            f"area is {size}{colour}" + (f" · {ranked}" if ranked else "")
            + (f" · {dropped} not plottable" if dropped else ""))
    return fig


# --- radar ------------------------------------------------------------------

_RADAR_SERIES = 4    # shapes on one radar; more overlap into noise
_RADAR_HUES = (0, 6, 1, 3)   # indices into _DONUT_HUES, one per shape


def radar(payload, tickers=None, metrics=None, filter=None, by="MARKET_CAP", top=3,
          group=None, groups=None, theme="light", title=None, **_):
    """A shape-at-a-glance profile across several measures.

    Each spoke is one percentile column on a common "better is further out"
    scale — so a standing reads as a shape, well-rounded or spiky where it leads
    and lags. Higher is better on every spoke regardless of the underlying
    measure. Up to four shapes; more overlap into noise.

    Companies: ``tickers`` names them; without it, the ``top`` (3) companies of
    the filtered window ranked by ``by`` — the biggest by default, or
    ``by="gold_conviction"`` for the week's strongest conviction.

    Groups: ``group="sector"`` or ``"industry"`` draws each group's median
    percentile — how a whole sector of this week's reporters stands. ``groups``
    names them (default: the largest). Read it on a narrowed field (the S&P 500):
    across a whole week every industry's median sits near the middle by design.

    ``metrics`` picks the spokes (defaults to a spread of ranked columns).

    Examples
    --------
    >>> cal.plot("radar", tickers=["NVDA", "AMD"],
    ...          metrics=["roic_quant", "ep_quant", "eps_surprise_quant"])
    >>> cal.plot("radar", filter=Filter.sp500(), by="gold_conviction")
    >>> cal.plot("radar", group="sector", filter=Filter.sp500(),
    ...          groups=["Technology", "Healthcare"])
    """
    t = _theme(theme)
    if group not in (None, "sector", "industry"):
        raise ChartError(f"group must be 'sector' or 'industry', not {group!r}.")
    if metrics:
        require_served(payload, *metrics)
        metrics = [_resolve(payload, m) for m in metrics]
    else:
        metrics = [m for m in _metrics(payload)
                   if _is_oriented(payload, m) and m.endswith("_QUANT")
                   and not m.startswith("PRE_")][:6]
    metrics = [m for m in metrics if _is_oriented(payload, m)]
    if len(metrics) < 3:
        return _empty("this profile needs at least three ranked measures", t,
                      title)

    shown = _visible_rows(payload, filter)
    group_col = {"sector": "SECTOR", "industry": "INDUSTRY"}.get(group)
    shapes = []                               # (name, raw oriented values, hover label)
    if group_col:
        members = {}
        for r in shown:
            members.setdefault(r.get(group_col) or "—", []).append(r)
        if groups:
            missing = [g for g in groups if g not in members]
            if missing:
                raise ChartError(f"no company in this window is in {', '.join(map(repr, missing))}.")
            names = list(groups)
        else:
            names = sorted(members, key=lambda g: -len(members[g]))
        for name in names[:_RADAR_SERIES]:
            raw = [_oriented(payload, m, quantile([_pos(payload, r, m) for r in members[name]], 0.5))
                   for m in metrics]
            shapes.append((f"{name} ({len(members[name])})", raw))
        rows = []
    else:
        if tickers:
            want = [str(s).upper() for s in ([tickers] if isinstance(tickers, str) else tickers)]
            by_ticker = {str(r.get("TICKER") or "").upper(): r for r in _rows(payload)}
            missing = [k for k in want if k not in by_ticker]
            if missing:
                raise ChartError(f"not on this calendar: {', '.join(missing)}.")
            rows = [by_ticker[k] for k in want]
        else:
            key = _resolve(payload, by)
            rows = sorted((r for r in shown if _value(r, key) is not None),
                          key=lambda r: -_value(r, key))[:int(top or 3)]
        rows = rows[:_RADAR_SERIES]
        shapes = [(r.get("TICKER"), [_oriented(payload, m, _pos(payload, r, m)) for m in metrics])
                  for r in rows]
    if not shapes:
        return _empty("no companies selected", t, title)

    labels = [_short(payload, m) for m in metrics]
    fig = go.Figure()
    # Teal, terracotta, blue, sand: the hues furthest apart, since the shapes overlap.
    hues = _DONUT_HUES.get(theme, _DONUT_HUES["light"])
    palette = [hues[i] for i in _RADAR_HUES]

    # One clean polygon per shape: straight edges (a radar's segments must be
    # honest — a spline would invent values between the axes), a thin line and a
    # light fill. No vertex dots, no smoothing.
    for index, (name, raw) in enumerate(shapes):
        colour = palette[index % len(palette)]
        # A null spoke is drawn at the centre so the shape can close, but its
        # hover says "not ranked" so a collapsed spoke is not misread as worst.
        values = [v if v is not None else 0 for v in raw]
        hovers = [(f"<b>{name}</b> · {lab}<br>"
                   + (f"<b>{v:.0f}</b>{_P}" if v is not None else "<i>n.m. — not ranked here</i>")
                   + "<extra></extra>") for lab, v in zip(labels, raw)]
        fig.add_trace(go.Scatterpolar(
            r=values + values[:1], theta=labels + labels[:1],
            fill="toself", name=name, mode="lines",
            line=dict(color=colour, width=2),
            fillcolor=_rgba(colour, 0.12),
            hovertext=hovers + hovers[:1], hovertemplate="%{hovertext}",
        ))

    ranked = _rank_note(payload, rows, *metrics) if rows else None
    scale = ("median industry percentile" if group_col
             else "oriented industry percentile — the outer ring is best in industry")
    _layout(fig, t, title or "Percentile radar", scale + (f" · {ranked}" if ranked else ""))
    fig.update_layout(
        showlegend=True,
        legend=dict(orientation="h", y=1.06, x=0.5, xanchor="center",
                    font=dict(size=11, color=t["muted"])),
        polar=dict(
            bgcolor=t["panel"],
            # Just three faint rings (25/50/75) — enough to read a value against,
            # few enough to stay quiet. Outer edge (100) is the axis line itself.
            radialaxis=dict(range=[0, 100], gridcolor=t["grid"],
                            tickvals=[25, 50, 75], showticklabels=False,
                            angle=90),
            angularaxis=dict(gridcolor=t["grid"], linecolor=t["line"],
                             tickfont=dict(size=11.5, color=t["ink"])),
        ),
    )
    return fig


# --- donut ------------------------------------------------------------------

# Seven muted hues for a donut's slices, ordered so neighbouring slices stay
# apart for colour-blind readers too; "other" is neutral grey.
# The neutral middle of the treemap's terracotta-to-teal ramp.
_TREEMAP_MID = {"light": "#e4e1da", "dark": "#6a6d74"}
_DONUT_HUES = {"light": ['#0f5753', '#7c9cc0', '#5f6b2e', '#d9bf8c', '#2f8f83', '#5b3f66', '#b4643c'], "dark": ['#6fcabd', '#4f6f96', '#c9c47a', '#8a6f45', '#3a9e92', '#b99cc4', '#d9865a']}


def _donut_hues(theme, n):
    hues = _DONUT_HUES.get(theme, _DONUT_HUES["light"])
    return [hues[i % len(hues)] for i in range(n)]


# A donut names this many slices; the rest fold into "other", so each slice keeps
# its own hue.
_DONUT_SLICES = 7


def donut(payload, mode="sector", filter=None, size=None, theme="light", title=None, **_):
    """How the window divides up — by sector, industry, or size.

    A part-to-whole view of the field reporting in this window. ``"sector"`` and
    ``"industry"`` count companies per group, so you see what kind of names the
    week is weighted toward; ``"MARKET_CAP"`` (or any size column) shows how
    concentrated the window's size is in a few names. With ``"sector"`` or
    ``"industry"``, ``size`` (e.g. ``"MARKET_CAP"``) weighs each group by how
    much of that size it holds instead of how many names it has — where the
    money reporting this week sits. ``filter`` narrows the field first.

    Examples
    --------
    >>> cal.plot("donut", mode="sector")
    >>> cal.plot("donut", mode="industry", filter=Filter.sp500())
    >>> cal.plot("donut", mode="sector", size="MARKET_CAP")
    """
    t = _theme(theme)
    rows = _visible_rows(payload, filter)
    if not rows:
        return _empty("no companies in this window", t, title)

    categorical = mode in ("sector", "industry", "SECTOR", "INDUSTRY")
    if categorical:
        field = mode.upper()
        weight = _resolve(payload, size) if size else None
        counts, members = {}, {}
        for row in rows:
            v = 1.0 if weight is None else _value(row, weight)
            if not isinstance(v, (int, float)) or v <= 0:
                continue
            key = row.get(field) or "unknown"
            counts[key] = counts.get(key, 0) + float(v)
            members[key] = members.get(key, 0) + 1
        pairs = sorted(counts.items(), key=lambda kv: kv[1], reverse=True)
        head, tail = pairs[:_DONUT_SLICES], pairs[_DONUT_SLICES:]
        labels = [f"{k} ({members[k]})" for k, _ in head]       # the group's companies
        values = [float(v) for _, v in head]
        if tail:
            labels.append(f"other ({len(tail)})")
            values.append(float(sum(v for _, v in tail)))
        subtitle = (f"{len(rows)} companies by {mode.lower()}" if weight is None
                    else f"share of {weight} by {mode.lower()}")
        # A category donut reads best in the categorical palette — one distinct
        # hue per slice, from the same family the multi-series charts use.
        colors = _donut_hues(theme, len(head)) + ([t["muted"]] if tail else [])    # "other" stays neutral
        if weight is None:
            centre_big, centre_small, unit = str(len(rows)), "companies", "companies"
        else:
            centre_big, centre_small, unit = _compact(sum(values)), "total", ""
    else:
        # A size-style share. mode is a plain row column (MARKET_CAP by default).
        size = _resolve(payload, mode)
        usable = [r for r in rows
                  if isinstance(_value(r, size), (int, float))
                  and _value(r, size) > 0]
        if not usable:
            return _empty(f"no company in this window carries a positive {size}",
                          t, title)
        usable.sort(key=lambda r: _value(r, size), reverse=True)
        head, tail = usable[:_DONUT_SLICES], usable[_DONUT_SLICES:]
        labels = [r.get("TICKER") for r in head]
        values = [float(_value(r, size)) for r in head]
        if tail:
            labels.append(f"other ({len(tail)})")
            values.append(float(sum(_value(r, size) for r in tail)))
        subtitle = f"share of window {size}"
        # A magnitude reads best on the sequential ramp, deepening with rank so
        # the biggest name is the darkest wedge — a size cue in the colour itself.
        colors = _donut_hues(theme, len(head)) + ([t["muted"]] if tail else [])
        total = sum(values)
        centre_big = _compact(total)
        centre_small = "total"
        unit = ""

    # Pull the largest wedge out a touch so the eye lands on the leader — the
    # small polish that separates a report-ready donut from a default pie.
    pulls = [0.0] * len(values)
    if values:
        pulls[values.index(max(values))] = 0.06

    # The legend and the wedge share the same label, and a category name can run
    # long ("Drug Manufacturers - Specialty & Generic") — which crams a legend.
    # Show a trimmed label on the chart; keep the full name for the hover.
    short_labels = [_trim(str(lab)) for lab in labels]

    hovertemplate = ("<b>%{customdata}</b><br>%{value:,.0f}"
                     + (f" {unit}" if unit else "")
                     + " · %{percent}<extra></extra>")

    fig = go.Figure(go.Pie(
        labels=short_labels, values=values, customdata=labels, hole=0.62,
        pull=pulls, sort=True, direction="clockwise", rotation=0,
        marker=dict(colors=colors, line=dict(color=t["bg"], width=2)),
        # Percent inside the wedge; the name lives in the legend and the hover, so
        # a long category never has to fit on a thin slice.
        textinfo="percent", texttemplate="%{percent}",
        textposition="inside", insidetextorientation="horizontal",
        textfont=dict(family=_BODY_FONT, size=11.5, color=t["bg"]),
        hovertemplate=hovertemplate, opacity=0.95,
    ))

    # The centre number — the signature of a good donut, and the first thing read.
    fig.add_annotation(
        text=(f"<span style='font-family:{_BODY_FONT};font-size:26px;"
              f"color:{t['ink']}'>{centre_big}</span><br>"
              f"<span style='font-size:11px;color:{t['muted']}'>{centre_small}</span>"),
        showarrow=False, x=0.5, y=0.5, xref="paper", yref="paper",
        align="center",
    )

    _layout(fig, t, title or (f"{mode.title()} concentration" if not (categorical and size)
                              else f"{size} by {mode.lower()}"), subtitle,
            height=560)
    # A vertical legend down the right reads long category names cleanly, where a
    # horizontal strip would wrap them into a cramped block.
    fig.update_layout(
        showlegend=True,
        legend=dict(orientation="v", y=0.5, yanchor="middle", x=1.02,
                    font=dict(size=10.5, color=t["muted"])),
        margin=dict(r=180),
    )
    return fig


# --- signed bars (post change columns) --------------------------------------

def bar_signed(payload, metric="ep_quant", top=5, filter=None, highlight=None, group=None,
               reference=None, labels=8, note=None, names=False, theme="light", title=None, by="MARKET_CAP", **_):
    """How far each company's standing moved across its report — gains right,
    losses left.

    Post calendars only. ``metric`` names the base measure; the chart reads its
    across-event change: how many percentile points the company gained or lost
    against its industry. ``top`` keeps that many companies (5 by default;
    ``top=0`` draws every company — the eagle's-eye view of the whole window,
    naming only the highlighted group): the largest by ``by`` (Market Cap by
    default, so the biggest names are never missed), or with ``by=None`` the
    largest moves either way. ``group="sector"`` or
    ``"industry"`` orders the bars by group. ``reference="p10_p90"`` draws the
    10th and 90th percentile of the moves shown: the band between them (the
    interpercentile range) is where the usual moves sit, so a bar past either
    line is an unusual one. On the eagle's-eye view ``labels`` names the largest
    companies (of the highlighted group, if any) on leader lines (8; 0: none;
    ``"extremes"``: the best and the worst of each group).
    ``note`` is a line under the chart.

    Examples
    --------
    >>> cal = es.calendar("quarter_to_date", frame="post")
    >>> cal.plot("bar_signed", metric="ep_quant")              # the 5 biggest companies
    >>> cal.plot("bar_signed", metric="ep_quant", by=None)     # the 5 largest moves
    >>> cal.plot("bar_signed", metric="ep_quant", top=0, group="sector",
    ...          reference="p10_p90", highlight="sp500")
    """
    t = _theme(theme)
    if group not in (None, "sector", "industry"):
        raise ChartError(f"group must be 'sector' or 'industry', not {group!r}.")
    if reference not in (None, "p10_p90"):
        raise ChartError(f"reference must be 'p10_p90', not {reference!r}.")
    base = metric[:-len("_change")] if metric.lower().endswith("_change") \
        else metric
    change = _resolve(payload, f"{base}_change")
    if change not in _metrics(payload):
        if payload.get("frame") == "pre":
            raise ChartError(
                "signed movement bars need a post calendar — the pre frame has "
                "no across-event move. Fetch with frame='post'."
            )
        require_served(payload, f"{base}_change")

    usable = [r for r in _visible_rows(payload, filter)
              if _value(r, change) is not None]
    if not usable:
        return _empty(f"no company in this window has a "
                      f"{_label(payload, change)}", t, title)

    # top: the largest by `by` (Market Cap: the biggest names are never missed), or
    # with by=None the largest moves either way.
    rank_by = _resolve(payload, by) if by else None
    rows = sorted(usable, key=(lambda r: _value(r, rank_by) or 0) if rank_by
                  else (lambda r: abs(_value(r, change))), reverse=True)
    if top:                              # top=0: every company in the window
        if rank_by:                      # a company with two reports in the window, once
            seen = set()
            rows = [r for r in rows if not (r.get("TICKER") in seen or seen.add(r.get("TICKER")))]
        rows = rows[:top]
    rows.sort(key=lambda r: _value(r, change), reverse=True)
    group_col = {"sector": "SECTOR", "industry": "INDUSTRY"}.get(group)
    if group_col:                        # every bar kept, ordered by group, then by the move
        rows = sorted(rows, key=lambda r: str(r.get(group_col) or "~"))
    rows = list(reversed(rows))          # drawn bottom-up: the first row at the top

    member = resolve_highlight(highlight)
    membership = member[0] if member else None
    highlight_label = member[1] if member else None

    values = [_value(r, change) for r in rows]
    gain, loss = _DONUT_HUES.get(theme, _DONUT_HUES["light"])[0], _DONUT_HUES.get(theme, _DONUT_HUES["light"])[6]

    # Teal for a gain, terracotta for a fall (the other way round where less is
    # better: volatility, VaR); with a highlight, the rest grey.
    rise, fall = (loss, gain) if (_meta(payload, _resolve(payload, base)) or {}) \
        .get("higher_is_better") is False else (gain, loss)

    def _bar_color(r, v):
        if membership and not membership(r):
            return t["muted"]
        return gain if v == 0 else rise if v > 0 else fall

    many = len(rows) > 40
    fig = go.Figure(go.Bar(
        x=values, y=[r.get("TICKER") for r in rows], orientation="h",
        marker=dict(color=[_bar_color(r, v) for r, v in zip(rows, values)],
                    line=dict(width=0.4, color=t["line"])),
        text=None if many else [format_value(v, _fmt(payload, change)) for v in values],
        textposition="outside", textfont=dict(size=10.5, color=t["muted"]),
        hovertext=[_hover(payload, r, [change]) for r in rows],
        hovertemplate="%{hovertext}", showlegend=False,
    ))
    fig.add_vline(x=0, line=dict(color=t["line"], width=1.1))
    if reference == "p10_p90":
        moves = [_value(r, change) for r in usable]
        lo, hi = quantile(moves, 0.10), quantile(moves, 0.90)
        # The band reads from its grey shade alone; p10 / p90 named at its edges,
        # the value as the axis writes it, in its side's bar colour.
        fig.add_vrect(x0=lo, x1=hi, fillcolor=t["grid"], opacity=0.8, line_width=0, layer="below")
        for q, x in (("p10", lo), ("p90", hi)):
            fig.add_annotation(x=x, y=1, yref="paper", yanchor="bottom", showarrow=False,
                               text=f"<b>{q} {x:+.0f}</b>" if x else f"<b>{q} 0</b>",
                               xanchor="right" if x < 0 else "left",
                               font=dict(size=11, color=rise if x > 0 else fall if x < 0 else gain))
    if group_col:
        tickers = [r.get("TICKER") for r in rows]
        blocks, start = [], 0
        for i, r in enumerate(rows):
            if i == len(rows) - 1 or rows[i + 1].get(group_col) != r.get(group_col):
                blocks.append((r.get(group_col) or "—", start, i))
                start = i + 1
        for name, a, b in blocks:
            fig.add_annotation(x=1, xref="paper", y=tickers[(a + b) // 2], yref="y", text=f"<b>{name}</b>",
                               xanchor="right", showarrow=False, font=dict(size=11, color=t["muted"]))
            if a > 0:
                fig.add_hline(y=a - 0.5, line=dict(color=t["line"], width=1))
    if not top:                          # eagle's-eye: name the largest on leader lines
        _label_bars(fig, payload, rows, membership, labels,
                    lambda r: _value(r, change), group_col, t, names)
    if note:
        fig.add_annotation(text=note, xref="paper", yref="paper", x=0, y=-0.08,
                           xanchor="left", yanchor="top", showarrow=False,
                           font=dict(size=11, color=t["muted"]))
    _layout(fig, t, title or f"Δ {_short(payload, base)}",
            _subtitle(payload, len(rows), 0, filter, highlight_label,
                      note="Industry percentile move"
                      + (" · band: interpercentile range (p10–p90)" if reference else "")),
            height=max(340, 40 + (12 if many else 22) * len(rows)))
    fig.update_xaxes(title=_short(payload, change))
    fig.update_yaxes(title=None, automargin=True, showticklabels=not many)
    return fig


# --- dispatch ---------------------------------------------------------------

_REGISTRY = {
    "scatter": scatter,
    "bar_pctile": bar_pctile,
    "change_scatter": change_scatter,
    "treemap": treemap,
    "radar": radar,
    "donut": donut,
    "bar_signed": bar_signed,
}

KINDS = tuple(_REGISTRY)


def plot(payload, kind, **kwargs):
    """Draw one calendar chart.

    ``kind`` picks the chart; the keywords pick what it shows. ``help`` on each
    builder explains it — e.g.
    ``help(earningspy_glass.calendar_charts.scatter)``.

    Examples
    --------
    >>> plot(cal, "scatter", x="ep_quant", y="roic_quant").show()
    >>> plot(cal, "bar_pctile", metric="roic_quant").show()
    """
    builder = _REGISTRY.get(kind)
    if builder is None:
        raise ChartError(
            f"there is no {kind!r} calendar chart — choose one of: "
            f"{', '.join(KINDS)}."
        )
    check_options(builder, f"calendar {kind}", kwargs)
    return builder(payload, **kwargs)


def describe(payload):
    """Print the menu — every option for every argument — and return the fields.

    The point is to spend no time hunting for argument values. It prints, up
    front: the chart kinds, every field you can pass as ``x`` / ``y`` /
    ``metric`` / ``color`` (grouped by which direction is the good one), and the
    ``highlight`` / ``filter`` options. Reads the payload's own metrics table, so
    it never goes stale. Returns the field rows ``(key, label, direction)``.
    """
    frame = payload.get("frame")
    scope = payload.get("scope")
    n = len(_rows(payload))
    metrics = calendar_metrics(payload)

    header = f"Calendar · {frame} · {scope} · {n} companies" \
        if frame and scope else f"Calendar · {n} companies"
    print(header)
    print("=" * max(len(header), 60))

    print("\nCHART KINDS  (cal.plot('<kind>', ...))")
    print("  " + ", ".join(KINDS))

    # Fields, grouped by direction — the values to pass as x / y / metric / color.
    # Show the exact key to type and the short display name beside it.
    by_dir = {"higher": [], "lower": [], "no direction": []}
    for key, _label_txt, direction in metrics:
        by_dir.setdefault(direction, []).append(key)
    heads = {
        "higher": "higher is better",
        "lower":  "lower is better (risk / volatility)",
        "no direction": "direction neutral, context implications",
    }
    print(f"\nFIELDS for x / y / metric / color  ({len(metrics)} — pass the KEY)")
    for direction in ("higher", "lower", "no direction"):
        keys = by_dir.get(direction) or []
        if not keys:
            continue
        print(f"\n  {heads[direction]}:")
        for key in keys:
            disp = _short(payload, key.lower())
            note = "  (change)" if key.endswith("_CHANGE") else \
                   "  (carried pre-event)" if key.startswith("PRE_") else ""
            print(f"    {key.lower():<34}→ {disp}{note}")

    print("\nhighlight=  'sp500' | an industry name | ['NVDA','AMD'] | a Filter")
    print("filter=     Filter.sector('Technology') | Filter.sp500() | "
          "Filter.tickers([...])  (combine with & | ~)")
    # Real list, quiet echo — the menu was just printed above.
    return PrintedList(metrics, "measures")
