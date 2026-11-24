"""The friendly front door: one object that fetches and plots.

Wraps the plain functions so a user configures a key once and then works in
whole companies and whole charts, not URLs and payloads. The functions still
work on their own; this is the shape most people want.
"""

import logging

from earningspy_glass import _defaults
from earningspy_glass import regime as _regime
from earningspy_glass.api import fetch_peer_analysis, resolve_api_key
from earningspy_glass.catalog import catalog as _catalog
from earningspy_glass.calendar_api import fetch_calendar
from earningspy_glass.calendar_charts import describe as describe_calendar
from earningspy_glass.calendar_charts import plot as plot_calendar
from earningspy_glass.graph_client import (
    describe,
    nearest_peers,
    only,
    plot,
    service_notes,
    stamp,
    stamp_calendar,
    stamp_timeline,
)
from earningspy_glass.rows import fetch_rows, fetch_schema
from earningspy_glass.samples import mark
from earningspy_glass.timeline_api import fetch_timeline
from earningspy_glass.timeline_charts import bars as _timeline_bars
from earningspy_glass.timeline_charts import compare as _timeline_compare
from earningspy_glass.timeline_charts import describe as _describe_timeline
from earningspy_glass.timeline_charts import gaps as _timeline_gaps
from earningspy_glass.timeline_charts import radar as _timeline_radar

log = logging.getLogger(__name__)


class Peers:
    """One company set against its peers, ready to describe or plot.

    You rarely build this yourself — :meth:`EarningSpy.peers` returns it. It
    holds the fetched comparison and offers the two things you do with it: ask
    what is in it, and draw it.

    Examples
    --------
    >>> peers = EarningSpy().peers("AMBA")
    >>> peers.describe()                      # what can I plot?
    >>> peers.plot("scatter", x="pe", y="roic").show()
    """

    def __init__(self, payload, request=None):
        #: The comparison exactly as the API delivered it. Read it directly if
        #: you want the raw figures; nothing here hides it from you.
        self.payload = payload
        # How it was fetched (ticker, frame, tier, peer_group): stamped on every
        # figure as its recipe, so EarningSpy Studio can render the same chart.
        self._request = dict(request or {})

    @property
    def ticker(self):
        """The anchor company's ticker — the one this comparison is about."""
        return (self.payload.get("request") or {}).get("anchor_ticker")

    def largest(self, n=3):
        """The ``n`` biggest peers by market cap (anchor excluded), largest first.

        A ticker-agnostic way to pick companies for an example or a recipe:
        ``peers.only(peers.largest(2))`` is the anchor against its two giants.
        Companies without a market cap are skipped.
        """
        anchor = (self.ticker or "").upper()

        def cap(p):
            v = (p.get("values") or {}).get("market_cap")
            return v if isinstance(v, (int, float)) and v > 0 else None

        sized = [p for p in self.payload.get("points") or []
                 if (p.get("ticker") or "").upper() != anchor and cap(p)]
        return [p["ticker"] for p in sorted(sized, key=cap, reverse=True)[:n]]

    def nearest(self, n=3):
        """The ``n`` peers closest to the anchor in market cap, closest first.

        Not the industry's biggest names (that is :meth:`largest`): the companies
        nearest the anchor's own size, compared on a log scale - the same rule the
        charts use to pick peers on their own. Returns tickers, ready to pass as
        ``tickers=``: ``peers.plot("bar_pctile", tickers=peers.nearest(3))``.
        """
        return [p["ticker"] for p in nearest_peers(self.payload, self.ticker, n)]

    def market(self):
        """The market each company reported into, one row per company, anchor first.

        Each row: ``ticker``, ``role``, ``earnings_date``, the market state and the
        shape of the VIX curve two weeks before that report (``MARKET_STATE``,
        ``VIX_TERM``), and the VIX then and on the report day (``VIX_PRE_2W``,
        ``EARNING_VIX``). A reading not taken yet is ``None``. Pass the rows to
        ``pandas.DataFrame`` for a table:
        ``pd.DataFrame(peers.market())``.
        """
        keys = ("MARKET_STATE", "VIX_TERM", "VIX_PRE_2W", "EARNING_VIX")
        points = self.payload.get("points") or []
        rows = [{"ticker": p.get("ticker"), "role": p.get("role"), "earnings_date": p.get("earnings_date"),
                 **{k: (p.get("market") or {}).get(k) for k in keys}} for p in points]
        return sorted(rows, key=lambda r: (r["role"] != "anchor", r["earnings_date"] or ""))

    def only(self, tickers):
        """The same comparison showing only ``tickers`` (and the anchor), for every chart.

        ``peers.only(["AVGO"]).plot("scatter", x="ep", y="roic")`` draws MU and
        AVGO alone, the axes fitted to them. Ranks stay against the whole group.
        The subset travels in each figure's recipe. See :func:`earningspy_glass.only`.
        """
        return Peers(only(self.payload, tickers), request=self._request)

    def describe(self):
        """Print what this comparison offers and return it as an object.

        Lists the companies, the measures you can put on a chart, the extra
        detail shown on hover, and — if your key is on a lower tier — what a
        higher tier would add. See :func:`earningspy_glass.describe`.
        """
        return describe(self.payload)

    def warnings(self):
        """Print the notes the service attached to this comparison, once each.

        Measures too thin to rank among these peers, reported peers shown with their
        pre-report read, prior-quarter figures — what to know about the numbers before
        reading a chart. Returns them as a list of ``{"code", "detail"}``.
        """
        return service_notes(self.payload.get("warnings"))

    def plot(self, kind, **kwargs):
        """Draw one chart. ``kind`` names it; keywords say what it shows.

        ``peers.plot("scatter", x="pe", y="roic")`` reads value against quality;
        ``peers.plot("grade_card")`` gives the anchor a report card. Ask for a
        measure the comparison does not carry and you get a clear error naming
        it, not a quietly wrong chart. See :func:`earningspy_glass.plot`.
        """
        fig = mark(plot(self.payload, kind, **kwargs), self.payload)
        return stamp(fig, self.payload, kind, kwargs, **self._request) if self._request else fig

    def __repr__(self):
        request = (self.payload.get("request") or {})
        return (f"<Peers {request.get('anchor_ticker')} "
                f"{request.get('frame')} · "
                f"{len(self.payload.get('points') or [])} companies>")


class Calendar:
    """A window of the earnings calendar, ranked against industry, ready to plot.

    You rarely build this yourself — :meth:`EarningSpy.calendar` returns it. It
    holds every company reporting in the window, each already sitting at its
    industry percentile on every measure, and offers the two things you do with
    it: ask what is in it, and draw it.

    Examples
    --------
    >>> cal = EarningSpy().calendar("week")            # the upcoming week
    >>> cal.describe()                                 # what can I plot?
    >>> cal.plot("scatter", x="ep_quant", y="roic_quant").show()
    >>> cal.plot("bar_pctile", metric="roic_quant", highlight="sp500").show()
    """

    def __init__(self, payload):
        #: The calendar exactly as the API delivered it (rows sliced to scope).
        #: Read it directly for the raw figures; nothing here hides it.
        self.payload = payload

    def describe(self):
        """Print what this calendar offers and return the plottable columns.

        Lists the frame, the scope, how many companies, and the percentile
        measures you can put on an axis (with which direction is the good one).
        See :func:`earningspy_glass.describe_calendar`.
        """
        return describe_calendar(self.payload)

    def plot(self, kind, **kwargs):
        """Draw one calendar chart. ``kind`` names it; keywords say what it shows.

        ``cal.plot("scatter", x="ep_quant", y="roic_quant")`` reads one percentile
        against another across the window; ``cal.plot("bar_pctile",
        metric="roic_quant")`` ranks the field on one. ``filter`` narrows the
        companies, ``highlight`` emphasises a subset. See
        :func:`earningspy_glass.plot_calendar`.

        The figure carries its recipe (the window's dates, the chart and its
        options, a filter as data), so EarningSpy Studio can render it.
        """
        fig = mark(plot_calendar(self.payload, kind, **kwargs), self.payload)
        return stamp_calendar(fig, self.payload, kind, kwargs)

    def __repr__(self):
        p = self.payload
        return (f"<Calendar {p.get('frame')} · {p.get('scope')} · "
                f"{len(p.get('data') or [])} companies>")


class Timeline:
    """One company across its quarters, ready to chart over time.

    You rarely build this yourself — :meth:`EarningSpy.timeline` returns it. It
    holds the company's history (newest-first) and offers the two things you do
    with it: bar one or several measures across the quarters, and compare a
    measure's pre-print and post-print readings.

    Examples
    --------
    >>> tl = EarningSpy().timeline("AAPL")
    >>> tl.describe()                                   # measures + quarters
    >>> tl.bars("EPS").show()                           # one measure over time
    >>> tl.bars(["ROIC_QUANT", "EPS_QUANT"]).show()     # head to head (silver view)
    >>> tl.compare("EPS").show()                        # on file vs reported
    >>> tl.bars("EPS", regime=True).show()              # + the market at each report
    """

    def __init__(self, payload, *, _fetch_other=None, _fetch_regime=None):
        #: The history exactly as the API delivered it (rows newest-first).
        self.payload = payload
        # A callable that fetches the OPPOSITE frame's payload, for compare();
        # supplied by EarningSpy.timeline so the wrapper can pair pre with post
        # without the user managing two objects.
        self._fetch_other = _fetch_other
        self._other = None
        # For a view that does not serve the market regime (the outcomes): a callable
        # fetching one that does, so bars(regime=True) works on every view.
        self._fetch_regime = _fetch_regime
        self._with_regime = None
        # What the last chart drawn from this timeline left empty, for warnings().
        self._notes = []

    def describe(self):
        """Print the measures and quarters this timeline offers, and return the
        measure rows. See :func:`earningspy_glass.describe_timeline`."""
        return _describe_timeline(self.payload)

    def warnings(self):
        """Print what to know about the numbers before reading the last chart: the
        service's own notes, then the quarters that chart left empty ("No EPS for
        2025/Q4."). Returns them as a list of ``{"code", "detail"}``."""
        notes = service_notes(self.payload.get("warnings"), echo=False) \
            + [{"code": "missing_quarter", "detail": line} for line in self._notes]
        print("\n".join(["notes:"] + [f"  [{n.get('code')}] {n.get('detail')}" for n in notes])
              if notes else "no notes")
        return notes

    def bars(self, measures, show_dates=False, theme="light", title=None, regime=None, last=5):
        """Bar one or several measures across the quarters.

        One measure reads as "how has this moved"; several share the Y axis only
        when they share a unit (all currency, all percent). Percentiles
        (``*_QUANT``, all 0–100) compare across families. A skipped quarter shows
        empty (listed by :meth:`warnings`); ``show_dates`` prints the event date under
        each quarter; ``last`` keeps the latest quarters (5; 0: every one).
        The market each report landed in rides along by default — the VIX two weeks
        before it, the shape of the VIX curve and the market state — on every view,
        the outcomes included (read from the same reports' fundamentals).
        ``regime=False`` leaves the bars alone; ``regime=True`` insists on it (an
        error when the market regime cannot be read). See
        :func:`earningspy_glass.timeline_bars`.
        """
        if regime is None:               # by default: the market, whenever it can be read
            regime = (_regime.carried(self.payload.get("data") or [])
                      or self._fetch_regime is not None)
        payload = self._regime_payload() if regime else self.payload
        fig = _timeline_bars(payload, measures, show_dates=show_dates,
                             theme=theme, title=title, regime=regime, last=last)
        self._notes = _timeline_gaps(self.payload, measures, last=last)
        return stamp_timeline(mark(fig, self.payload), self.payload, "bars",
                              {"measures": measures, "show_dates": show_dates,
                               "theme": theme, "title": title, "regime": regime, "last": last})

    def compare(self, measure, show_dates=False, theme="light", title=None, last=5):
        """Compare a measure's pre-print and post-print reading, per quarter.

        Two bars a quarter — what was on file going into the event, and what was
        reported at it — joined on the reporting quarter. Fetches the opposite
        frame automatically. See :func:`earningspy_glass.timeline_compare`.
        """
        pre, post = self._frame_pair()
        fig = _timeline_compare(pre, post, measure, show_dates=show_dates,
                                theme=theme, title=title, last=last)
        self._notes = []
        return stamp_timeline(mark(fig, self.payload), self.payload, "compare",
                              {"measure": measure, "show_dates": show_dates,
                               "theme": theme, "title": title, "last": last})

    def radar(self, mode="percentile", metrics=None, quarters=3, theme="light",
              title=None):
        """Overlay the last few quarters as radar shapes — how the profile moves.

        ``mode="percentile"`` (default) plots the industry percentiles, oriented
        so the outer ring is best; ``mode="change"`` plots the across-event raw
        changes centred on zero (post frame only). ``quarters`` sets how many to
        overlay; ``metrics`` overrides the five default spokes. See
        :func:`earningspy_glass.timeline_radar`.
        """
        fig = _timeline_radar(self.payload, mode=mode, metrics=metrics,
                              quarters=quarters, theme=theme, title=title)
        return stamp_timeline(mark(fig, self.payload), self.payload, "radar",
                              {"mode": mode, "metrics": metrics, "quarters": quarters,
                               "theme": theme, "title": title})

    def _regime_payload(self):
        """The history with the market regime on every row: as served, or joined from
        the same frame's fundamentals once when this view does not carry it."""
        if _regime.carried(self.payload.get("data") or []) or self._fetch_regime is None:
            return self.payload
        if self._with_regime is None:
            self._with_regime = _regime.attach(self.payload, self._fetch_regime())
        return self._with_regime

    def _frame_pair(self):
        """(pre_payload, post_payload), fetching the missing frame once."""
        if self._other is None:
            if self._fetch_other is None:
                raise RuntimeError(
                    "compare() needs both frames; build the timeline through "
                    "EarningSpy.timeline() so the other frame can be fetched.")
            self._other = self._fetch_other()
        this, other = self.payload, self._other
        if this.get("frame") == "pre":
            return this, other
        return other, this

    def __repr__(self):
        p = self.payload
        return (f"<Timeline {p.get('ticker')} · {p.get('frame')} · "
                f"{len(p.get('data') or [])} quarters>")


class EarningSpy:
    """The EarningSpy client. Configure a key once, then fetch and plot.

    The one setup step is the API key: it is read from the ``EARNINGSPY_API_KEY``
    environment variable, or passed here. Without one the client raises AuthError
    when it is created. A bronze key unlocks the reported figures;
    silver and gold keys unlock more (see :meth:`peers`). No key yet:
    :func:`earningspy_glass.sample`. The field catalogue
    needs no key: :func:`earningspy_glass.catalog`.

    Creating the client also readies a notebook: charts draw inline, each fetch
    prints a one-line summary of what came back (``verbose=False`` turns it off),
    and the service's notes wait in ``peers.warnings()`` instead of the output.

    Examples
    --------
    >>> es = EarningSpy()                       # reads EARNINGSPY_API_KEY
    >>> es = EarningSpy(api_key="...")          # or pass it in
    >>> peers = es.peers("AMBA")                # the bronze view
    >>> peers.plot("bar_pctile").show()
    """

    def __init__(self, api_key=None, base_url=None, timeout=30, session=None, verbose=True):
        self._api_key = resolve_api_key(api_key)
        _defaults.apply(verbose=verbose)
        self._base_url = base_url
        self._timeout = timeout
        self._session = session

    def catalog(self, query=None, *, group=None, frame=None, tier=None,
                refresh=False):
        """Print every field the API offers — the menu, before you fetch anything.

        Unlike a payload's ``describe()``, this needs no key and no fetch: it
        reads the public catalogue and lists every field with its group, tier and
        frame, plus the views that bundle them. Filter with ``query`` (a term),
        ``group``, ``frame`` or ``tier``. See :func:`earningspy_glass.catalog`.

        Examples
        --------
        >>> es.catalog()                    # the whole menu
        >>> es.catalog("roic")              # fields mentioning ROIC
        >>> es.catalog(group="quantiles")
        """
        return _catalog(query, group=group, frame=frame, tier=tier,
                        refresh=refresh)

    # `fields` — the same helper under the name people reach for first.
    fields = catalog

    def peers(self, ticker, frame=None, peer_group="industry", tier="bronze",
              metrics=None, cohort=None, season=None, peers=None, horizon=None, limit=None):
        """Fetch one company against its peer group.

        Parameters
        ----------
        ticker : str
            The company to centre on, e.g. ``"AMBA"``.
        frame : {"pre", "post", "delta"}, optional
            Which side of the earnings event to look at — the run-up (``"pre"``),
            the aftermath (``"post"``), or how the company's standing moved from
            before the report to after it (``"delta"``, a gold view). Left out,
            it is ``"post"`` on the bronze view and ``"pre"`` on silver and gold,
            whose comparisons are served from the run-up view. ``frame="post"``
            works at every tier; your key's tier decides how deep it goes.
        peer_group : {"industry", "sector"}
            How the peer set is drawn.
        tier : {"bronze", "silver", "gold"}
            How much of the analysis to request. ``"bronze"`` is the view of
            the reported figures. ``"silver"`` adds a live read of each company's
            quality. ``"gold"`` adds the full forward view and the before-and-
            after comparison. Your key must carry the tier, or the call fails with
            a message that says so — it never quietly gives you less.
        cohort : {"auto", "quarter", "lookback"}, optional
            Which peers fill the company's reporting season: only those that
            reported in it (``"quarter"``), or also last season's report for
            each peer still to report (``"lookback"``, drawn hollow like other
            prior-quarter figures). Left out, the service decides (``"auto"``:
            it looks back when fewer than five peers have reported).
        season : str, optional
            A season to research, e.g. ``"2026Q2"``: the company's report in that
            season against that season's reporters — a closed landscape. Bronze
            keys read the current season, silver one back, gold every season.

        peers : {"all", "reported", "upcoming"}, optional
            Keep only the peers that already reported (``"reported"``, run-up view)
            or those still to report (``"upcoming"``, after-the-report view).
        horizon : {"quarter", "next"}, optional
            How far ahead upcoming peers are read: this quarter, or the next too.
        limit : int, optional
            At most this many companies.

        Returns
        -------
        Peers
            The comparison, ready to :meth:`~Peers.describe` or
            :meth:`~Peers.plot`.

        Examples
        --------
        >>> es.peers("AMBA")                          # bronze key
        >>> es.peers("AMBA", tier="silver")           # silver key
        >>> es.peers("AMBA", frame="delta", tier="gold")
        """
        payload = fetch_peer_analysis(
            ticker, frame=frame, peer_group=peer_group, metrics=metrics,
            tier=tier, base_url=self._base_url, api_key=self._api_key,
            timeout=self._timeout, session=self._session, cohort=cohort,
            season=season, peers=peers, horizon=horizon, limit=limit,
        )
        request = payload.get("request") or {}
        log.info("%s · %s · %s view · %s tier · %d companies · %d measures · %d service notes",
                 request.get("anchor_ticker"), (request.get("peer_group") or {}).get("value"),
                 request.get("frame"), tier,
                 len(payload.get("points") or []), len(payload.get("metrics") or {}),
                 len(service_notes(payload.get("warnings"), echo=False)))
        return Peers(payload, request={"ticker": str(ticker).upper(), "frame": frame,
                                       "tier": tier, "peer_group": peer_group})

    def calendar(self, scope="week", frame="pre", tier="silver", from_monday=False):
        """Fetch a window of the earnings calendar, ranked against industry.

        Where :meth:`peers` centres on one company, this is the whole field at
        once — every company reporting in the window, each already sitting at its
        industry percentile on every measure — so you can scatter the field,
        rank it, or see who moved across the event.

        Parameters
        ----------
        scope : str
            How much of the calendar to show. ``"week"`` and ``"two_weeks"`` work
            on both frames — the upcoming stretch on a pre calendar, the just-past
            stretch on a post one. ``"month_from_date"`` is the next month of
            upcoming events (pre only); ``"quarter_to_date"`` is the reported
            events so far this quarter (post only).
        frame : {"pre", "post"}
            The run-up to earnings (``"pre"``, not yet reported) or the aftermath
            (``"post"``, reported).
        tier : {"bronze", "silver", "gold"}
            The percentile view needs a silver key. Your key must carry the tier
            or the call fails with a message that says so.
        from_monday : bool
            Pre only: whole earnings weeks, Monday to Friday (``"week"`` this
            week, ``"two_weeks"`` this week and the next), so the companies that
            already reported this week stay on the board beside the ones still to
            come. On a weekend the window starts with the coming week.

        Returns
        -------
        Calendar
            The window, ready to :meth:`~Calendar.describe` or
            :meth:`~Calendar.plot`.

        Examples
        --------
        >>> es.calendar("week")                              # upcoming week
        >>> es.calendar("month_from_date")                   # upcoming month
        >>> es.calendar("quarter_to_date", frame="post")     # reported this quarter
        >>> es.calendar("week", from_monday=True)            # the whole week, Monday on
        """
        payload = fetch_calendar(
            scope=scope, frame=frame, tier=tier, from_monday=from_monday, base_url=self._base_url,
            api_key=self._api_key, timeout=self._timeout, session=self._session,
        )
        log.info("%s calendar · %s · %s tier · %d companies · %d measures",
                 payload.get("frame") or frame, payload.get("scope") or scope, tier,
                 len(payload.get("data") or []), len(payload.get("metrics") or {}))
        return Calendar(payload)

    def rows(self, view="calendar", frame="pre", *, ticker=None, tier="bronze", period=None,
             start=None, end=None, sort=None, limit=None):
        """Any view's rows as the service delivers them — for a table or an analysis.

        The charts read :meth:`peers`, :meth:`calendar` and :meth:`timeline`; this
        reads everything else the API offers, every page of it. See
        :func:`earningspy_glass.fetch_rows` for the options, and :meth:`catalog`
        for the views.

        Examples
        --------
        >>> es.rows("sp500", period="next_week")["data"]
        >>> es.rows("fundamentals", frame="post", ticker="AAPL")["data"]
        """
        return fetch_rows(
            view, frame, ticker=ticker, tier=tier, period=period, start=start, end=end,
            sort=sort, limit=limit, base_url=self._base_url, api_key=self._api_key,
            timeout=self._timeout, session=self._session)

    def schema(self):
        """Every column the service can deliver, with its type and freshness.

        Examples
        --------
        >>> es.schema()["columns"][0]
        """
        return fetch_schema(base_url=self._base_url, api_key=self._api_key,
                            timeout=self._timeout, session=self._session)

    def __repr__(self):
        return "<EarningSpy client>"            # never the key

    def __getstate__(self):
        raise TypeError("an EarningSpy client holds your API key, so it cannot be saved "
                        "or copied to disk. Create a new one where you need it.")

    def timeline(self, ticker, frame="pre", view=None, tier="silver"):
        """Fetch one company's quarter-by-quarter history.

        Where :meth:`peers` and :meth:`calendar` compare companies, this follows
        one company over time — a measure event after event, so you can see how
        it has moved and how the print changed it.

        Parameters
        ----------
        ticker : str
            The company, e.g. ``"AAPL"``.
        frame : {"pre", "post"}
            ``"pre"`` (default) is the run-up snapshot — the deeper, wider
            history (~two years, more columns). A pre row carries the
            *previously* reported figures, so the charts label it one quarter
            back. ``"post"`` is the reported-at-event frame.
        view : {"fundamentals", "calendar_quant"}, optional
            Defaults per frame: **pre → calendar_quant** (raw figures + industry
            percentiles, so a bar's hover pairs each measure with its rank and
            several `*_QUANT` share a 0–100 axis), **post → fundamentals** (raw
            figures + the across-event `*_CHANGE` family). On post the two views
            are mutually exclusive — fundamentals carries the raw changes,
            calendar_quant the percentile moves. Pass explicitly to override.
        tier : {"bronze", "silver", "gold"}
            How much enrichment rides along with the full history. ``"silver"``
            (default) adds each quarter's quality read; ``"gold"`` adds the
            forward read too — shown in the hover on the quarters it
            covers, absent on the rest. ``"bronze"`` is the leanest fetch. Your
            key must carry the tier.

        Returns
        -------
        Timeline
            The history, ready to :meth:`~Timeline.describe`,
            :meth:`~Timeline.bars` or :meth:`~Timeline.compare`.

        Examples
        --------
        >>> es.timeline("AAPL").bars("EPS")
        >>> es.timeline("AAPL", view="calendar_quant").bars(
        ...     ["ROIC_QUANT", "EPS_QUANT"])
        >>> es.timeline("AAPL", tier="gold").compare("EPS")   # forward read in hover
        """
        payload = fetch_timeline(
            ticker, frame=frame, view=view, tier=tier, base_url=self._base_url,
            api_key=self._api_key, timeout=self._timeout, session=self._session,
        )
        log.info("%s timeline · %s view · %s tier · %d quarters",
                 str(ticker).upper(), frame, tier, len(payload.get("data") or []))
        other = "post" if frame == "pre" else "pre"

        def _fetch_other():
            return fetch_timeline(
                ticker, frame=other, view=view, tier=tier,
                base_url=self._base_url, api_key=self._api_key,
                timeout=self._timeout, session=self._session,
            )

        def _fetch_regime():
            # the same frame's fundamentals: the view that carries the market regime
            return fetch_timeline(
                ticker, frame=frame, view="fundamentals", tier=tier,
                base_url=self._base_url, api_key=self._api_key,
                timeout=self._timeout, session=self._session,
            )

        return Timeline(payload, _fetch_other=_fetch_other, _fetch_regime=_fetch_regime)
