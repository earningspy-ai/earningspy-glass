"""Reach the EarningSpy single-ticker endpoint and hand back a usable payload.

Where the calendar is the whole window at once and peers is one company against
its group, the timeline is one company across its own quarters — its reported
figures event after event, so a measure can be watched over time. Same
discipline as ``api.py`` and ``calendar_api.py``: this module calls the endpoint
and tells a usable response from a broken one, and knows nothing about meaning.

Two views on the same route:

- ``fundamentals`` — the reported figures (EPS, ROIC, FCF, …), the raw timeline.
- ``calendar_quant`` — those plus the industry percentiles, the head-to-head
  surface (every ``*_QUANT`` is 0–100, so several measures share one axis).

History depth is a property of the ROUTE SEGMENT, not the view: the bronze
segment carries the full history (~9 pre quarters), while silver/gold enrich
only the most recent events. So the timeline always fetches on the ``bronze``
segment to get every quarter — the percentiles ride along on bronze here, the
tier gates enrichment, not history.
"""

from earningspy_glass import facts
from earningspy_glass._http import (
    TIERS,
    check_choice,
    clean_ticker,
    get_pages,
    resolve_api_key,
    resolve_base,
)
from earningspy_glass.errors import NotFoundError, RequestError

# The two frames a timeline can read. `pre` is the deeper, wider frame — about
# two years of history and the risk-ratio family that post lacks — and is the
# default. `post` is the reported-at-event frame, shorter but exact for the
# quarter it names.
FRAMES = ("pre", "post")

# The views the timeline understands. `fundamentals` is the raw figures (free on
# any key); `calendar_quant` adds the industry percentiles (the multi-measure
# surface).
VIEWS = ("fundamentals", "calendar_quant", "outcomes")

# Views that exist on one frame only. `outcomes` — what happened after each
# report, beside the abnormal-move probability read before it — is a record of
# reported events, so it lives on post.
_VIEW_FRAMES = {"outcomes": ("post",)}

# The default view PER FRAME — because the two views add disjoint things and each
# frame wants the other's. On PRE, calendar_quant is a strict superset of
# fundamentals (same history, every raw column, plus the industry percentiles),
# and pre has no across-event change, so calendar_quant is simply richer. On
# POST, the two are mutually exclusive: fundamentals carries the raw *_CHANGE
# family (the point of a post view — what the print changed), while calendar_quant
# carries the percentile moves instead. So post defaults to fundamentals. Pass
# `view=` explicitly to override either default.
_DEFAULT_VIEW = {"pre": "calendar_quant", "post": "fundamentals"}


def fetch_timeline(ticker, frame="pre", *, view=None, tier="silver",
                   base_url=None, api_key=None, timeout=30, session=None,
                   limit=1000):
    """Fetch one company's quarter-by-quarter history, newest-first.

    This is the raw call under :meth:`EarningSpy.timeline`; most users go through
    that. It fetches the full history on the bronze route (so no quarter is
    missing), follows the pages, and returns the payload with its ``metrics``
    block and rows as delivered.

    Parameters
    ----------
    ticker : str
        The company, e.g. ``"AAPL"``.
    frame : {"pre", "post"}
        ``"pre"`` (default) is the run-up snapshot — deeper history and more
        columns; a pre row carries the *previously* reported figures (see
        ``earningspy_glass.facts``). ``"post"`` is the reported-at-event
        frame, shorter but exact for the quarter it names.
    view : {"fundamentals", "calendar_quant", "outcomes"}, optional
        Defaults per frame: **pre → calendar_quant** (raw figures plus industry
        percentiles — a strict superset on pre), **post → fundamentals** (raw
        figures plus the across-event ``*_CHANGE`` family — what the print
        changed). On post the two views are mutually exclusive: fundamentals has
        the raw changes, calendar_quant the percentile moves. ``"outcomes"``
        (post only, gold key) is what happened after each report — the return,
        the abnormal return and the expected return at 3, 30 and 60 days, and
        whether the move was abnormal — beside the abnormal-move probability
        read before it. Pass explicitly to override.
    tier : {"bronze", "silver", "gold"}
        How much enrichment rides along with the history. Every tier returns the
        full history; ``"silver"`` (default) adds the quality read, ``"gold"``
        adds the forward read too — populated on the quarters it
        covers, null on the rest. Your key must carry the tier.

    Credentials come from the environment (``EARNINGSPY_API_BASE``,
    ``EARNINGSPY_API_KEY``) unless passed. Nothing is hardcoded; the key never
    goes in the URL.

    Examples
    --------
    >>> payload = fetch_timeline("AAPL")                    # pre + percentiles
    >>> payload = fetch_timeline("AAPL", frame="post")      # post + raw changes
    >>> payload = fetch_timeline("AAPL", view="fundamentals")   # pre, raw only
    >>> payload = fetch_timeline("AAPL", frame="post", view="outcomes", tier="gold")
    """
    check_choice("frame", frame, FRAMES)
    if view is None:
        view = _DEFAULT_VIEW[frame]     # pre -> calendar_quant, post -> fundamentals
    check_choice("view", view, VIEWS)
    if frame not in _VIEW_FRAMES.get(view, FRAMES):
        raise RequestError(
            f"view {view!r} is only on the "
            f"{' / '.join(_VIEW_FRAMES[view])} timeline — pass frame='post'"
        )
    check_choice("tier", tier, TIERS)

    ticker = clean_ticker(ticker)
    base = resolve_base(base_url)
    key = resolve_api_key(api_key)

    # The pre ticker route LEFT-JOINs the score layers, so every segment returns
    # the full history; the tier chooses how much enrichment rides along (quality
    # on silver, the forward read on gold), null on the quarters not scored. The
    # post ticker route exists only on bronze (silver/gold 404 there), so post
    # always fetches bronze — the enrichment blocks simply do not apply to it.
    segment = tier if frame == "pre" else "bronze"
    url = f"{base}/{frame}/{segment}/ticker/{ticker}"
    params = {"view": view, "limit": limit, **facts.ASK}

    rows, envelope = get_pages(url, params, key, timeout=timeout, session=session,
                               what=f"{ticker}'s timeline", tier=tier)
    if not rows:
        raise NotFoundError(
            f"no history for {ticker} on the {frame} frame — check the "
            f"ticker."
        )

    payload = dict(envelope)
    payload["data"] = rows
    payload["frame"] = frame
    payload["view"] = view
    payload["tier"] = tier
    payload["ticker"] = ticker
    return payload

