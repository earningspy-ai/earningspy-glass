"""Reach the EarningSpy calendar endpoint and hand back a usable payload.

The calendar is the whole earnings window at once — every company reporting in a
stretch of the calendar, each already ranked against its own industry — where
the peers endpoint is one company against its group. Same discipline as
``api.py``: this module knows how to call the endpoint and how to tell a usable
response from a broken one, and nothing about what the numbers mean.

The payload comes back flat (one row per company) with a ``metrics`` table that
describes each percentile column — its label, which way is better, how to format
it. The renderer reads that table; it never decides any of it here.

A ``scope`` (``"week"``, ``"two_weeks"``, …) is a window of days reckoned from
today and sent to the service as an explicit ``start``/``end``, so the rows are
exactly the scope's days — never a calendar month or quarter that happens to
end before the scope does.

The upcoming calendar only reaches as far as the data does: this month's
reports plus next week's (the source lists no further ahead than that). In the
last days of a month that is this month plus the whole of next week. A scope
that would run past it is cut at that horizon, and the payload says so.
"""

import datetime
import warnings

from earningspy_glass import facts
from earningspy_glass._http import (
    TIERS,
    check_choice,
    get_pages,
    resolve_api_key,
    resolve_base,
)
from earningspy_glass.errors import PayloadNotice, RequestError

# The two frames a calendar can be about: the run-up to earnings (companies that
# have NOT yet reported) and the aftermath (companies that HAVE). The frame
# decides both the route and which direction a scope looks.
FRAMES = ("pre", "post")

# Scopes, and the frames each is meaningful on. A scope is a date slice of a
# larger fetched window (see _SCOPE_FETCH). `week`/`two_weeks` read forward on a
# pre calendar and backward on a post one; `month_from_date` only makes sense
# looking forward (upcoming), `quarter_to_date` only looking back (reported).
_SCOPES = {
    "week": ("pre", "post"),
    "two_weeks": ("pre", "post"),
    "month_from_date": ("pre",),
    "quarter_to_date": ("post",),
}

# How many days each scope spans from today. Sign is applied by the frame:
# forward on pre, backward on post. None spans the whole of what the frame
# holds: every upcoming report on pre (month_from_date runs to the horizon),
# the quarter so far on post (quarter_to_date).
_SCOPE_DAYS = {
    "week": 7,
    "two_weeks": 14,
    "month_from_date": None,
    "quarter_to_date": None,
}


# Further ahead than the upcoming calendar ever reaches, so the service's horizon is
# what ends a window that runs to the end of it.
_FAR = 45


def _scope_window(scope, frame, today=None, from_monday=False):
    """The (start, end) dates a scope asks for, inclusive.

    Forward from today on a pre calendar (``month_from_date`` as far as the service
    reaches); backward to today on a post one. ``quarter_to_date`` runs from the first
    day of the current quarter. ``from_monday`` makes a pre window whole earnings
    weeks, Monday to Friday: ``week`` is this week, ``two_weeks`` this week and the
    next, so the week's earlier reports stay in it; on a weekend the week is over and
    the coming one is the first. ``month_from_date`` then runs from that Monday.
    """
    today = today or datetime.date.today()
    days = _SCOPE_DAYS[scope]
    if frame == "pre":
        start = today
        if from_monday:
            start -= datetime.timedelta(days=today.weekday())
            if today.weekday() >= 5:
                start += datetime.timedelta(days=7)
        if days is None:
            return start, today + datetime.timedelta(days=_FAR)
        span = 7 * (days // 7) - 3 if from_monday else days      # to the last week's Friday
        return start, start + datetime.timedelta(days=span)
    if days is None:
        return datetime.date(today.year, 3 * ((today.month - 1) // 3) + 1, 1), today
    return today - datetime.timedelta(days=days), today


def _cut(scope, frame, end, payload):
    """The window's last day as the service reaches it, and whether that cut the scope short."""
    horizon = facts.horizon(payload) if frame == "pre" else None
    if horizon is None or end <= horizon:
        return end, False
    return horizon, _SCOPE_DAYS[scope] is not None


def _route(base, frame, tier):
    """The calendar URL for a frame. Mirrors the peers route split.

    The pre calendar is served under its tier's own segment — ``/pre/silver/``
    adds the live quality read on top of the reported figures, ``/pre/gold/``
    adds the forward read on top of that. Each higher segment carries the lower
    ones' columns too, so a gold call returns the quality columns as well. The
    post calendar is served only under ``/post/bronze/`` (its quant columns are
    silver-*gated* by the view, but the route segment is bronze, and it carries
    no tier-enriched columns). ``tier`` selects the segment; the service is the
    authority on whether the key may use it.
    """
    if frame == "pre":
        layer = tier if tier in ("silver", "gold") else "bronze"
        return f"{base}/pre/{layer}/calendar"
    return f"{base}/post/bronze/calendar"


def fetch_calendar(scope="week", frame="pre", *, view="calendar_quant",
                   tier="silver", base_url=None, api_key=None, timeout=30,
                   session=None, limit=1000, today=None, from_monday=False):
    """Fetch a window of the earnings calendar, ranked against industry.

    This is the raw call under :meth:`EarningSpy.calendar`; most users go through
    that. It fetches one calendar-anchored window for the frame, follows the
    pages, and returns the payload with its rows sliced to the requested scope —
    the ``metrics`` table and every other envelope key left as delivered.

    Parameters
    ----------
    scope : str
        How much of the calendar to show. ``"week"`` and ``"two_weeks"`` work on
        both frames — the upcoming week on a pre calendar, the past week on a
        post one. ``"month_from_date"`` is every upcoming event the calendar holds —
        the rest of this month, plus next week in the last days of a month
        (pre only); ``"quarter_to_date"`` is the quarter of reported events so
        far (post only).
    frame : {"pre", "post"}
        The run-up to earnings (``"pre"``, companies that have not yet reported)
        or the aftermath (``"post"``, companies that have).
    tier : {"bronze", "silver", "gold"}
        How much of the analysis to request. The percentile view needs a silver
        key; your key must carry the tier or the call is refused with a clear
        message — nothing downgrades silently.
    from_monday : bool
        Pre only. Whole earnings weeks, Monday to Friday: ``"week"`` is this
        week, ``"two_weeks"`` this week and the next. The companies that already
        reported this week stay on the board beside the ones still to come, with
        the figures they carried into the report. On a weekend the window starts
        with the coming week.

    The window is sent as an explicit ``start``/``end`` (see
    :func:`_scope_window`); ``today`` is injectable for tests and defaults to the
    real today.

    Credentials come from the environment (``EARNINGSPY_API_BASE``,
    ``EARNINGSPY_API_KEY``) unless passed. Nothing is hardcoded; the key never
    goes in the URL.

    Examples
    --------
    >>> payload = fetch_calendar("week", "pre")                 # upcoming week
    >>> payload = fetch_calendar("quarter_to_date", "post")     # reported this quarter
    >>> payload = fetch_calendar("week", "pre", from_monday=True)  # the whole week, Monday on
    """
    check_choice("frame", frame, FRAMES)
    check_choice("scope", scope, _SCOPES)
    check_choice("tier", tier, TIERS)
    if frame not in _SCOPES[scope]:
        ok = ", ".join(_SCOPES[scope])
        raise RequestError(
            f"scope {scope!r} is not available on the {frame!r} calendar — it "
            f"applies to: {ok}. (An upcoming-only scope on the reported "
            f"calendar, or the reverse, has no window to describe.)"
        )

    base = resolve_base(base_url)
    key = resolve_api_key(api_key)

    url = _route(base, frame, tier)
    start, end = _scope_window(scope, frame, today, from_monday)
    params = {"view": view, "start": start.isoformat(), "end": end.isoformat(),
              "limit": limit, **facts.ASK}

    rows, envelope = get_pages(url, params, key, timeout=timeout, session=session,
                               what="the calendar", tier=tier)

    payload = dict(envelope)
    end, capped = _cut(scope, frame, end, payload)
    payload["data"] = _slice_to_scope(rows, start, end)
    payload["frame"] = frame
    payload["scope"] = scope
    payload["view"] = view
    payload["tier"] = tier
    # The window as resolved today, so a chart's recipe redraws these same days.
    payload["window"] = {"scope": scope, "start": start.isoformat(),
                         "end": end.isoformat()}
    if capped:
        payload.setdefault("warnings", [])
        payload["warnings"] = list(payload["warnings"]) + [{
            "code": "horizon",
            "detail": (f"the {scope!r} window is cut at {end.isoformat()}: the "
                       f"upcoming calendar holds this month's reports and next "
                       f"week's, nothing further ahead"),
        }]
    _emit_notices(payload)
    return payload


def _slice_to_scope(rows, start, end):
    """Keep the rows whose earnings date falls in ``start..end`` inclusive.

    The service already filters to the window; this is the guard that keeps
    the payload honest if it ever does not. A row whose date cannot be read is
    KEPT, never silently dropped: losing a company because its date string was
    unexpected is the kind of quiet wrong answer this package refuses.
    """
    kept = []
    for row in rows:
        date = _row_date(row)
        if date is None or start <= date <= end:
            kept.append(row)
    return kept


def _row_date(row):
    """The row's earnings date as a date, or None if it cannot be read."""
    raw = row.get("EARNINGS_DATE")
    if not raw:
        return None
    text = str(raw)[:10]
    try:
        return datetime.date.fromisoformat(text)
    except ValueError:
        return None


def _emit_notices(payload):
    """Surface the API's own payload notes once each, as PayloadNotice.

    Same contract as the peers path: informational notes (a thin industry, a
    stale figure) are warnings a caller can silence as a group, not errors — the
    chart still renders. They stay in ``payload["warnings"]`` for anyone who
    wants every instance.
    """
    seen = set()
    for warning in payload.get("warnings") or []:
        text = f"[{warning.get('code')}] {warning.get('detail')}"
        if text in seen:
            continue
        seen.add(text)
        warnings.warn(text, PayloadNotice, stacklevel=3)
