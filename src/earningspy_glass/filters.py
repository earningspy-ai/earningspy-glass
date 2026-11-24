"""Composable row filters for the calendar charts.

A calendar is the whole window at once — often more companies than one chart
wants. A :class:`Filter` narrows it: ``Filter.industry("Software")``,
``Filter.sp500()``, ``Filter.tickers(["NVDA", "AMD"])``, combined with ``&``,
``|`` and ``~``. The same filter object works on every calendar plot, so a set
of companies you care about is defined once and reused across a scatter, a bar
and a change chart.

A filter reads plain row fields — an industry name, an index flag, a ticker. It
carries no finance: it never tests a value for being good or bad, never ranks,
never decides orientation. It selects rows; the chart reads meaning from the
payload's ``metrics`` table, exactly as it does without a filter.
"""

import datetime as _dt

# A longer list of tickers is counted, not spelled out in a chart's subtitle.
NAMED_TICKERS = 6


def earnings_week(value):
    """The Monday that opens the earnings week (Monday to Sunday) holding a date.

    Accepts a date, or an ISO string with or without a time part. None when the
    value is missing or unreadable.
    """
    if value is None:
        return None
    if isinstance(value, _dt.datetime):
        value = value.date()
    if not isinstance(value, _dt.date):
        try:
            value = _dt.date.fromisoformat(str(value)[:10])
        except ValueError:
            return None
    return value - _dt.timedelta(days=value.weekday())


class Filter:
    """A predicate over calendar rows, composable with ``&``, ``|`` and ``~``.

    Build one with the constructors below and pass it to any calendar plot as
    ``filter=``. Combine freely::

        tech = Filter.sector("Technology")
        big = Filter.sp500()
        plot(payload, "scatter", x="roic_quant", y="ep_quant",
             filter=tech & big)                 # S&P 500 tech names only
        plot(payload, "scatter", ..., filter=~big)   # everything NOT in the S&P 500

    You rarely instantiate ``Filter`` directly; use :meth:`industry`,
    :meth:`sector`, :meth:`sp500`, :meth:`tickers` or :meth:`week`.
    """

    def __init__(self, predicate, describe="custom filter", spec=None):
        #: A callable ``row -> bool``. Reads row fields only.
        self._predicate = predicate
        #: A short human phrase for the chart subtitle and repr.
        self._describe = describe
        #: The filter as data (see :meth:`to_dict`), or None for one built from
        #: a custom test — that one draws, but cannot travel in a recipe.
        self._spec = spec

    # --- constructors -------------------------------------------------------

    @classmethod
    def industry(cls, name):
        """Keep companies in one industry (matched case-insensitively)."""
        want = str(name).strip().lower()
        return cls(lambda r: str(r.get("INDUSTRY") or "").strip().lower() == want,
                   f"industry = {name}", {"industry": str(name)})

    @classmethod
    def sector(cls, name):
        """Keep companies in one sector (matched case-insensitively)."""
        want = str(name).strip().lower()
        return cls(lambda r: str(r.get("SECTOR") or "").strip().lower() == want,
                   f"sector = {name}", {"sector": str(name)})

    @classmethod
    def sp500(cls):
        """Keep only S&P 500 constituents."""
        return cls(lambda r: _truthy_flag(r.get("IS_S&P500")), "S&P 500 only",
                   {"sp500": True})

    @classmethod
    def tickers(cls, symbols):
        """Keep a specific set of companies by ticker (case-insensitive)."""
        want = {str(s).strip().upper() for s in symbols}
        pretty = ", ".join(sorted(want)) if len(want) <= NAMED_TICKERS else f"{len(want)} chosen"
        return cls(lambda r: str(r.get("TICKER") or "").strip().upper() in want,
                   f"tickers: {pretty}", {"tickers": sorted(want)})

    @classmethod
    def state(cls, name):
        """Keep companies in one valuation state.

        Accepts the API's own token or a friendly phrase, matched loosely:
        ``Filter.state("newly_cheap")`` and ``Filter.state("newly cheap")`` both
        work. The states are ``newly_cheap``, ``still_cheap``, ``exiting`` and
        ``normal`` — the "is it cheap, and newly so?" read the calendar carries.
        """
        want = _norm_state(name)
        return cls(
            lambda r: _norm_state(r.get("VALUATION_STATE")) == want,
            f"valuation: {state_label(name)}", {"state": want})

    @classmethod
    def week(cls, date):
        """Keep companies reporting in the earnings week (Monday to Sunday) that
        holds ``date`` — e.g. ``Filter.week("2026-11-02")``.

        The EarningSpy Rank and conviction compare companies reporting the same
        week; this is how a calendar that spans two weeks is narrowed to one.
        """
        monday = earnings_week(date)
        if monday is None:
            raise ValueError(f"Filter.week needs a date like '2026-11-02', not {date!r}.")
        sunday = monday + _dt.timedelta(days=6)
        return cls(lambda r: earnings_week(r.get("EARNINGS_DATE")) == monday,
                   f"week of {monday.isoformat()} – {sunday.isoformat()}",
                   {"week": monday.isoformat()})

    # --- composition --------------------------------------------------------

    def __and__(self, other):
        return Filter(lambda r: self._predicate(r) and other._predicate(r),
                      f"{self._describe} AND {other._describe}",
                      _combine("all", self, other))

    def __or__(self, other):
        return Filter(lambda r: self._predicate(r) or other._predicate(r),
                      f"{self._describe} OR {other._describe}",
                      _combine("any", self, other))

    def __invert__(self):
        inner = self._describe
        return Filter(lambda r: not self._predicate(r), f"NOT ({inner})",
                      {"not": self._spec} if self._spec is not None else None)

    # --- as data --------------------------------------------------------------

    @property
    def travels(self):
        """Whether this filter can be written as data (and so ride in a recipe).

        Every filter built from the constructors and ``& | ~`` travels; one built
        from a custom test (``Filter(lambda row: ...)``) draws here but cannot.
        """
        return self._spec is not None

    def to_dict(self):
        """The filter as plain data, e.g. ``{"all": [{"sector": "Technology"},
        {"sp500": True}]}`` — what a chart's recipe carries, and what
        :meth:`from_dict` rebuilds."""
        if self._spec is None:
            raise ValueError(
                f"the filter '{self._describe}' was built from a custom test and "
                f"cannot be written as data — build it from Filter.industry, "
                f"sector, sp500, tickers, state or week, combined with & | ~."
            )
        return _copy_spec(self._spec)

    @classmethod
    def from_dict(cls, data):
        """Rebuild a filter from :meth:`to_dict`'s data."""
        if not isinstance(data, dict) or len(data) != 1:
            raise ValueError(f"a filter is one key and its value, e.g. "
                             f"{{'sector': 'Technology'}} — got {data!r}.")
        (key, value), = data.items()
        if key in ("all", "any"):
            if not isinstance(value, (list, tuple)) or len(value) < 2:
                raise ValueError(f"'{key}' takes a list of two or more filters.")
            parts = [cls.from_dict(v) for v in value]
            out = parts[0]
            for part in parts[1:]:
                out = (out & part) if key == "all" else (out | part)
            return out
        if key == "not":
            return ~cls.from_dict(value)
        if key == "sp500":
            if value is not True:
                raise ValueError("'sp500' takes true.")
            return cls.sp500()
        if key == "tickers":
            if isinstance(value, str) or not isinstance(value, (list, tuple)) or not value:
                raise ValueError("'tickers' takes a list of tickers.")
            return cls.tickers(value)
        builders = {"industry": cls.industry, "sector": cls.sector,
                    "state": cls.state, "week": cls.week}
        if key not in builders:
            raise ValueError(
                f"no filter called {key!r} — use industry, sector, sp500, tickers, "
                f"state, week, all, any or not.")
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"'{key}' takes a name, not {value!r}.")
        return builders[key](value)

    # --- application --------------------------------------------------------

    def keep(self, rows):
        """The rows that pass this filter, in their original order."""
        return [r for r in rows if self._predicate(r)]

    def matches(self, row):
        """Whether one row passes this filter."""
        return bool(self._predicate(row))

    @property
    def description(self):
        """A short phrase naming what this filter keeps, for chart subtitles."""
        return self._describe

    def __repr__(self):
        return f"<Filter {self._describe}>"


def _combine(op, left, right):
    """The data form of ``left op right``, flattening a chain (a & b & c is one
    "all" of three), or None when either side cannot travel."""
    if left._spec is None or right._spec is None:
        return None
    parts = []
    for side in (left._spec, right._spec):
        parts.extend(side[op] if op in side and len(side) == 1 else [side])
    return {op: [_copy_spec(p) for p in parts]}


def _copy_spec(spec):
    """A deep copy of a filter's data form (lists and dicts only)."""
    if isinstance(spec, dict):
        return {k: _copy_spec(v) for k, v in spec.items()}
    if isinstance(spec, list):
        return [_copy_spec(v) for v in spec]
    return spec


def _truthy_flag(value):
    """Read an index flag that may arrive as 1/0, True/False, or "1"/"0"."""
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value == 1
    if isinstance(value, str):
        return value.strip() in ("1", "true", "True")
    return False


# --- valuation state --------------------------------------------------------
#
# The API's VALUATION_STATE string, and its reader-facing phrasing. Shared by the
# state filter and the calendar hover so the same words appear in both. Utility
# only — what the state means for the reader, never how it is derived.

_STATE_LABELS = {
    "newly_cheap": "newly cheap",
    "still_cheap": "still cheap",
    "exiting": "exiting cheap",
    "normal": "fairly valued",
}


def _norm_state(value):
    """A valuation-state string normalised for comparison: 'Newly Cheap' -> 'newly_cheap'."""
    return str(value or "").strip().lower().replace(" ", "_").replace("-", "_")


def state_label(value):
    """The reader-facing phrase for a valuation state, or the tidied raw string.

    An unknown state (a new one the API adds before the client knows it) falls
    back to its own words rather than being dropped — the same graceful degrade
    the rest of the client uses.
    """
    key = _norm_state(value)
    return _STATE_LABELS.get(key, key.replace("_", " "))


# --- highlight resolution ---------------------------------------------------
#
# `highlight=` on a calendar plot emphasises a subset without hiding the rest —
# an industry, the S&P 500 members, or a named list of tickers. It resolves to a
# membership test the chart uses to decide which marks to draw in the emphasis
# style. Like a Filter, it reads row fields only.

def resolve_highlight(highlight):
    """Turn a ``highlight=`` argument into a ``row -> bool`` membership test.

    Accepts:

    - ``None`` — nothing highlighted (returns ``None``, so the chart skips the
      emphasis pass entirely).
    - ``"sp500"`` — the S&P 500 constituents.
    - a valuation state (``"newly_cheap"``, ``"still_cheap"``, ``"exiting"``,
      ``"normal"``) — the companies in that state.
    - a :class:`Filter` — anything a filter can express — or its data form
      (:meth:`Filter.to_dict`), as a chart's recipe carries it.
    - a string — treated as an industry name (the common case:
      ``highlight="Software"``).
    - a list/tuple/set of strings — treated as a set of tickers.

    Returns ``(membership, label)`` or ``None``.
    """
    if highlight is None:
        return None

    if isinstance(highlight, dict):
        highlight = Filter.from_dict(highlight)

    if isinstance(highlight, Filter):
        return highlight.matches, highlight.description

    if isinstance(highlight, str):
        if highlight.strip().lower() == "sp500":
            f = Filter.sp500()
            return f.matches, f.description
        if _norm_state(highlight) in _STATE_LABELS:
            f = Filter.state(highlight)
            return f.matches, f.description
        f = Filter.industry(highlight)
        return f.matches, f.description

    if isinstance(highlight, (list, tuple, set)):
        f = Filter.tickers(highlight)
        return f.matches, f.description

    raise TypeError(
        "highlight must be None, 'sp500', an industry name, a list of tickers, "
        f"or a Filter — got {type(highlight).__name__}."
    )
