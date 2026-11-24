"""The API's rows as it serves them: no scope, no reshaping, no chart.

:meth:`EarningSpy.calendar` and :meth:`EarningSpy.timeline` are the friendly
reads behind the charts. This is the plain one: any named view, on either side of
the event, for a window of the calendar or one company — for a spreadsheet, a
table or an analysis of your own.
"""

import datetime

from earningspy_glass._http import (
    TIERS,
    check_choice,
    clean_ticker,
    get_json,
    get_pages,
    resolve_api_key,
    resolve_base,
)
from earningspy_glass.errors import RequestError

FRAMES = ("pre", "post")
PERIODS = ("day", "week", "next_week", "month", "month_to_date", "quarter", "biquarter")
SORTS = ("asc", "desc")


def fetch_rows(view="calendar", frame="pre", *, ticker=None, tier="bronze", period=None,
               start=None, end=None, sort=None, limit=None, base_url=None, api_key=None,
               timeout=30, session=None):
    """Fetch a view's rows, every page of them, as the service delivers them.

    Parameters
    ----------
    view : str
        The named set of columns, e.g. ``"calendar"``, ``"fundamentals"``,
        ``"sp500"``, ``"calendar_quant"`` (silver key). :func:`catalog` lists them.
    frame : {"pre", "post"}
        Before the report, or after it.
    ticker : str, optional
        One company's history. Left out, a window of the calendar.
    tier : {"bronze", "silver", "gold"}
        How much of the analysis rides along on the run-up rows. After the report
        there is one route for every key.
    period : str, optional
        A named window from today: ``"day"``, ``"week"``, ``"next_week"``,
        ``"month"``, ``"month_to_date"``, ``"quarter"``, ``"biquarter"``.
    start, end : date or str, optional
        An explicit window, both days included, instead of ``period``.
    sort : {"asc", "desc"}, optional
        By report date.
    limit : int, optional
        At most this many rows. Left out, all of them.

    Returns
    -------
    dict
        The service's answer, with every page's rows under ``"data"``.

    Examples
    --------
    >>> fetch_rows("sp500", period="next_week")["data"]
    >>> fetch_rows("fundamentals", frame="post", ticker="AAPL")["data"]
    """
    check_choice("frame", frame, FRAMES)
    check_choice("tier", tier, TIERS)
    if period is not None:
        check_choice("period", period, PERIODS)
    if sort is not None:
        check_choice("sort", sort, SORTS)
    if period is not None and (start or end):
        raise RequestError("pass period, or start and end — not both.")
    if ticker and (period or start or end or sort):
        raise RequestError("one company's history has no window: leave out period, start, "
                           "end and sort.")
    if limit is not None and (isinstance(limit, bool) or not isinstance(limit, int) or limit < 1):
        raise RequestError(f"limit must be a positive whole number — got {limit!r}")

    segment = tier if frame == "pre" else "bronze"
    path = f"ticker/{clean_ticker(ticker)}" if ticker else "calendar"
    url = f"{resolve_base(base_url)}/{frame}/{segment}/{path}"
    params = {"view": view, "period": period, "start": _day("start", start),
              "end": _day("end", end), "sort": sort, "limit": min(limit or 1000, 1000)}
    params = {k: v for k, v in params.items() if v is not None}

    rows, envelope = get_pages(
        url, params, resolve_api_key(api_key), timeout=timeout, session=session, tier=tier,
        what=f"{ticker}'s history" if ticker else "the calendar", max_rows=limit)
    return {**envelope, "data": rows, "count": len(rows)}


def fetch_schema(*, base_url=None, api_key=None, timeout=30, session=None):
    """Every column the service can deliver, with its type and how fresh it is kept.

    Examples
    --------
    >>> fetch_schema()["columns"][0]
    """
    return get_json(f"{resolve_base(base_url)}/meta/schema", {}, resolve_api_key(api_key),
                    timeout=timeout, session=session, what="the column list")


def _day(name, value):
    if value is None:
        return None
    if isinstance(value, datetime.datetime):
        value = value.date()
    try:
        return datetime.date.fromisoformat(str(value)).isoformat()
    except ValueError:
        raise RequestError(f"{name} must be a date like '2026-11-02' — got {value!r}") from None
