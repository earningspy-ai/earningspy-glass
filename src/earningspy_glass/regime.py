"""Market-regime readings carried on each event row — read and labelled, never computed.

The API attaches three readings to every event, all taken two weeks before the report:

  MARKET_STATE   CALM · NORMAL · ELEVATED — the headline state of the market
  VIX_TERM       CONTANGO · FLAT · BACKWARDATION — the shape of the VIX curve
                 (near-term vs 3-month implied volatility): calm and upward-sloping,
                 near-term worry catching up, or near-term fear above the longer view
  VIX_PRE_2W     the VIX close two weeks before the report

and ``EARNING_VIX``, the VIX on the report day. Use them to see what kind of market
each report landed in — and whether a quarter's move came in a calm or a stressed
tape. Events whose two-week mark has not passed yet carry no reading. This module
only reads those values and supplies display names and colours; the states arrive
already decided in the payload.
"""

# Subscribed regime columns: a chart that asks for the regime needs the payload to carry them.
REGIME_KEYS = ("MARKET_STATE", "VIX_TERM", "VIX_PRE_2W", "EARNING_VIX")


def carried(rows):
    """True when the payload's rows carry the regime columns (served by this view)."""
    return any("MARKET_STATE" in row and "VIX_PRE_2W" in row for row in rows)


def attach(payload, source):
    """``payload`` with the market each report landed in, read from ``source`` — another
    view of the same company and frame — matched on the report date. For a view that
    does not serve the regime itself (the outcomes)."""
    by_date = {str(r.get("EARNINGS_DATE"))[:10]: r for r in (source or {}).get("data") or []}
    rows = []
    for row in payload.get("data") or []:
        match = by_date.get(str(row.get("EARNINGS_DATE"))[:10]) or {}
        rows.append({**row, **{k: match.get(k) for k in REGIME_KEYS}})
    return {**payload, "data": rows}


STATE_LABELS = {"CALM": "Calm", "NORMAL": "Normal", "ELEVATED": "Elevated"}
TERM_LABELS = {"CONTANGO": "Contango", "FLAT": "Flat", "BACKWARDATION": "Backwardation"}


def state_color(state, t):
    """Display colour for a market state in theme ``t`` (calm green, elevated red)."""
    return {"CALM": t["series"][2], "NORMAL": t["muted"], "ELEVATED": t["anchor"]}.get(state, t["line"])


def reading(row):
    """The regime reading on one event row as a dict, or None when the row has none."""
    if not row:
        return None
    values = {k: row.get(k) for k in REGIME_KEYS}
    if values["MARKET_STATE"] is None and values["VIX_PRE_2W"] is None:
        return None
    return values


def short_text(r):
    """'Elevated · Flat' — the two labels of a reading, for a marker caption."""
    parts = [STATE_LABELS.get(r.get("MARKET_STATE")), TERM_LABELS.get(r.get("VIX_TERM"))]
    return " · ".join(p for p in parts if p)


def hover_text(r, format_number=lambda v: f"{v:.1f}"):
    """Multi-line hover for one reading: both VIX values and both labels."""
    lines = []
    if r.get("VIX_PRE_2W") is not None:
        lines.append(f"VIX 2 weeks before report  {format_number(r['VIX_PRE_2W'])}")
    if r.get("EARNING_VIX") is not None:
        lines.append(f"VIX on report day          {format_number(r['EARNING_VIX'])}")
    if r.get("MARKET_STATE"):
        lines.append(f"Market state               {STATE_LABELS.get(r['MARKET_STATE'], r['MARKET_STATE'])}")
    if r.get("VIX_TERM"):
        lines.append(f"VIX term structure         {TERM_LABELS.get(r['VIX_TERM'], r['VIX_TERM'])}")
    return "<br>".join(lines)


def line(r, format_number=lambda v: f"{v:.1f}", reported=True):
    """One labelled line for a reading, or None when nothing has been read yet:

      'Market 2 wks before report: mood Normal · VIX curve Contango · VIX 14.9 — VIX on report day 20.7'

    Everything up to the first VIX is read two weeks before the report. The second VIX is the
    report session's close for a company that has ``reported``; before that it is the latest
    close at the last data refresh, and says so."""
    if not r or (r.get("MARKET_STATE") is None and r.get("VIX_PRE_2W") is None):
        return None
    bits = []
    if STATE_LABELS.get(r.get("MARKET_STATE")):
        bits.append(f"mood {STATE_LABELS[r['MARKET_STATE']]}")
    if TERM_LABELS.get(r.get("VIX_TERM")):
        bits.append(f"VIX curve {TERM_LABELS[r['VIX_TERM']]}")
    if r.get("VIX_PRE_2W") is not None:
        bits.append(f"VIX {format_number(r['VIX_PRE_2W'])}")
    text = "Market 2 wks before report: " + " · ".join(bits)
    if r.get("EARNING_VIX") is not None:
        text += f" — VIX {'on report day' if reported else 'latest close'} {format_number(r['EARNING_VIX'])}"
    return text


def window_summary(rows):
    """'Market 2 weeks pre-report: Elevated 34 · Normal 12 · 5 not read yet' for a
    set of event rows, or None when no row carries a reading (the payload predates
    the regime columns, or the view does not serve them)."""
    counts, unread = {}, 0
    carried = False
    for row in rows:
        if "MARKET_STATE" in row:
            carried = True
        state = row.get("MARKET_STATE")
        if state is None:
            unread += 1
        else:
            counts[state] = counts.get(state, 0) + 1
    if not carried or not counts:
        return None
    order = [s for s in ("ELEVATED", "NORMAL", "CALM") if s in counts] + \
        sorted(s for s in counts if s not in STATE_LABELS)
    bits = [f"{STATE_LABELS.get(s, s)} {counts[s]}" for s in order]
    if unread:
        bits.append(f"{unread} not read yet")
    return "Market 2 weeks pre-report: " + " · ".join(bits)
