"""The render layer: field subscription, hard-fail on the unknown, and that a
valid-but-empty request still renders rather than raising.

Charts return plotly Figures; the assertions check they build (or raise) rather
than checking pixels.
"""
import plotly.graph_objects as go
import pytest

from earningspy_glass import ChartError, available_metrics, describe, plot
from earningspy_glass.graph_client import Description, _hover


def _anchor(payload):
    return next(p for p in payload["points"] if p.get("role") == "anchor")


class TestSubscriptionHardFail:
    def test_unknown_axis_raises_naming_the_field(self, payload):
        with pytest.raises(ChartError, match="not_a_metric"):
            plot(payload, "scatter", x="pe", y="not_a_metric")

    def test_served_but_unsubscribed_field_raises(self, payload):
        # inject a field the payload serves but the client has not subscribed
        payload["metrics"]["mystery_score"] = {
            "label": "Mystery", "format": "ratio_2dp", "scale": "linear",
            "higher_is_better": True, "gradeable": False,
        }
        with pytest.raises(ChartError, match="does not present it yet"):
            plot(payload, "bar", metric="mystery_score")

    # Every measure the peers API serves (the API registry,
    # as deployed 2026-09-29; debt_eq / ltdebt_eq and silver_pctl added
    # 2026-10-06). A measure added there must be subscribed here too,
    # or charting it is refused as "does not present it yet".
    SERVED_BY_PEERS_API = {
        "pe", "fwd_pe", "ep", "eps", "eps_change", "eps_qoq", "eps_yoy_ttm",
        "eps_surprise", "rev_surprise", "eps_revision", "sales_growth", "valuation_spread",
        "roic", "roe", "fcf", "fcf_yield", "book_yield", "gross_margin", "net_margin", "ebit", "ebitda",
        "debt_eq", "ltdebt_eq", "debt", "current_ratio", "quick_ratio", "cash_per_share",
        "espy_risk",
        "sortino", "starr", "sharpe", "information_ratio", "sector_information_ratio",
        "vol", "beta", "perf_half", "short_float", "short_ratio", "market_cap",
        "quality_prob", "is_abnormal_prob", "conviction",
        "pre_event_quality_prob", "pre_event_is_abnormal_prob", "pre_event_conviction",
        "silver_pctl", "pre_event_silver_pctl",
        "sales", "net_income", "book_per_share", "oper_margin", "avg_volume",
        "float_shares", "float_pct", "inst_own", "insider_own", "var", "cvar",
    }

    def test_every_measure_the_peers_api_serves_is_subscribed(self):
        from earningspy_glass.graph_client import _SUBSCRIBED
        assert not self.SERVED_BY_PEERS_API - _SUBSCRIBED

    def test_a_new_peers_measure_charts(self, payload):
        # information_ratio went live on the API before glass subscribed it.
        payload["metrics"]["information_ratio"] = {
            "label": "Information Ratio", "format": "ratio_2dp", "scale": "linear",
            "higher_is_better": True, "gradeable": True,
        }
        for i, p in enumerate(payload["points"]):
            p.setdefault("values", {})["information_ratio"] = 0.1 * i - 0.5
            p.setdefault("percentiles", {}).setdefault("industry", {})["information_ratio"] = 5.0 * i
        assert isinstance(plot(payload, "bar_pctile", metrics=["information_ratio"]), go.Figure)

    def test_unknown_chart_kind_raises(self, payload):
        with pytest.raises(ChartError, match="no 'wombat' chart"):
            plot(payload, "wombat")

    def test_unknown_theme_raises(self, payload):
        with pytest.raises(ChartError, match="unknown theme"):
            plot(payload, "bar", metric="roic", theme="neon")


class TestEmptyIsNotAnError:
    def test_empty_selection_renders_not_raises(self, payload):
        # a valid measure, but no company matches this ticker filter -> empty fig
        fig = plot(payload, "bar", metric="roic", tickers=["NOSUCH"])
        assert isinstance(fig, go.Figure)

    def test_delta_chart_without_delta_block_raises(self, payload):
        # asking for the before/after view on a payload that has none is a
        # contract error, not an empty chart
        with pytest.raises(ChartError, match="before-and-after"):
            plot(payload, "dumbbell")


class TestBuildsCleanly:
    @pytest.mark.parametrize("kind,kw", [
        ("scatter", {"x": "pe", "y": "roic"}),
        ("treemap", {}),
        ("bar", {"metric": "roic"}),
        ("bar_signed", {"metrics": ("sortino",)}),
        ("bar_pctile", {}),
        ("radar", {}),
        ("donut", {}),
        ("donut", {"mode": "profitability"}),
        ("grade_card", {}),
    ])
    def test_bronze_charts_build(self, payload, kind, kw):
        fig = plot(payload, kind, **kw)
        assert isinstance(fig, go.Figure)

    @pytest.mark.parametrize("kind,kw", [
        ("scatter", {"x": "pe", "y": "roic"}),
        ("treemap", {}),
        ("bar", {"metric": "roic"}),
        ("bar_signed", {"metrics": ("sortino",)}),
        ("bar_pctile", {}),
        ("radar", {}),
        ("donut", {}),
        ("donut", {"mode": "profitability"}),
        ("grade_card", {}),
    ])
    def test_hover_is_left_aligned_on_every_kind(self, payload, kind, kw):
        # Left alignment is applied orthogonally in _layout (every builder calls
        # it), on the layout AND per trace — Plotly does not propagate the layout
        # setting to traces on its own. Right/centre alignment would break the
        # space-padded two-column tooltip, so assert it holds for every kind.
        fig = plot(payload, kind, **kw)
        assert fig.layout.hoverlabel.align == "left"
        for tr in fig.data:
            if tr.hoverlabel is not None:
                assert tr.hoverlabel.align == "left"

    def test_grade_card_unknown_ticker_raises(self, payload):
        with pytest.raises(ChartError, match="not in this peer group"):
            plot(payload, "grade_card", ticker="ZZZ")


class TestDescribe:
    def test_describe_returns_inspectable_object(self, payload, capsys):
        d = describe(payload)
        assert isinstance(d, Description)
        assert d.anchor == "ZZAM"
        assert ("pe", "P/E", "log", "lower is better") in d.metrics
        # it also printed a human-readable summary
        assert "ZZAM" in capsys.readouterr().out

    def test_describe_shows_the_key_season_industry_and_who_reported(self, payload):
        import copy
        p = copy.deepcopy(payload)
        p["request"].update(season="2026Q3", cohort="lookback")
        p["season"] = {"label": "2026Q3", "cohort": "lookback", "lookback": {"label": "2026Q2"}}
        p["generated_at"] = "2026-10-02T09:00:00Z"
        peers = [q for q in p["points"] if q.get("role") != "anchor"]
        peers[0].update(role="reported", earnings_date="2026-10-20")   # pre route: still ahead
        peers[1].update(carried=True)
        d = Description(p)
        text = str(d)
        assert text.splitlines()[0] == f"ZZAM · {p['request']['frame']} view · {d.key_tier} key"
        assert "season 2026Q3 (cohort: lookback)" in text and p["request"]["peer_group"]["value"] in text
        assert d.not_reported >= 1 and d.reported + d.not_reported == len(peers)
        assert f"{d.reported} reported, {d.not_reported} not reported yet" in text
        assert "lookback: 1 of the reported are carried from 2026Q2" in text
        # Method-free, and nothing that cannot be plotted.
        for banned in ("model", "feature", "AUC", "ML", "shown on hover", "a silver key adds"):
            assert banned.lower() not in text.lower()

    def test_describe_points_at_the_service_notes_without_listing_them(self, payload):
        import copy
        p = copy.deepcopy(payload)
        p["warnings"] = [{"code": "lookback_cohort", "detail": "2 peers reported in 2026Q4; 9 carried"},
                         {"code": "lookback_cohort", "detail": "2 peers reported in 2026Q4; 9 carried"},
                         {"code": "not_in_tier", "detail": "industry percentiles need a silver key"}]
        text = str(Description(p))
        assert "[lookback_cohort]" not in text and "[not_in_tier]" not in text
        assert "2 notes from the service: peers.warnings()" in text   # distinct notes

    def test_describe_lists_only_measures_with_values(self, payload):
        import copy
        p = copy.deepcopy(payload)
        p["metrics"]["empty_one"] = {"label": "Empty", "higher_is_better": True}
        assert "empty_one" not in [k for k, *_ in Description(p).metrics]

    def test_available_metrics_reads_orientation(self, payload):
        rows = dict((k, d) for k, _, _, d in available_metrics(payload))
        assert rows["roic"] == "higher is better"
        assert rows["pe"] == "lower is better"
        assert rows["market_cap"] == "neutral"

    def test_describe_groups_by_direction(self, payload):
        text = str(describe(payload))
        assert "higher is better" in text
        assert "lower is better (risk / valuation)" in text
        assert "direction neutral, context implications" in text


class TestPeersHoverAndAxes:
    def _with_outcomes(self, payload):
        return next(p for p in payload["points"] if p.get("outcomes"))

    def test_outcomes_block_is_named_abnormality_scores(self, payload):
        h = _hover(payload, self._with_outcomes(payload), ["pe", "roic"],
                   "industry", view="all")
        assert "Abnormality Scores" in h
        assert "What happened after" not in h

    def test_car_shown_as_percent(self, payload):
        point = self._with_outcomes(payload)
        car = point["outcomes"]["CAR_3"]
        h = _hover(payload, point, ["pe", "roic"], "industry", view="all")
        # rendered as a percentage, not a raw fraction
        assert f"CAR 3d: {car * 100:.1f}%".replace(" ", "&nbsp;") in h \
            or f"{car * 100:.1f}%" in h.replace("&nbsp;", " ")

    def test_missing_horizon_shows_nm_not_dropped(self, payload):
        # inject a null 60-day CAR: it must render n.m., proving the horizon shows
        # even before it matures rather than vanishing.
        point = dict(self._with_outcomes(payload))
        point["outcomes"] = {**point["outcomes"], "CAR_60": None}
        h = _hover(payload, point, ["pe", "roic"], "industry", view="all")
        assert "CAR 60d" in h.replace("&nbsp;", " ")

    def test_hover_has_industry_percentile_heading(self, payload):
        h = _hover(payload, self._with_outcomes(payload), ["pe", "roic"],
                   "industry")
        assert "industry percentile" in h

    def test_scatter_axes_carry_direction_hint(self, payload):
        fig = plot(payload, "scatter", x="pe", y="roic")
        assert "lower is better" in fig.layout.xaxis.title.text
        assert "higher is better" in fig.layout.yaxis.title.text

    def test_ep_is_subscribed_and_higher_is_better(self, payload):
        # E/P (earnings yield) is the higher-is-better inverse of P/E; it must be
        # plottable (subscribed) and orient "up is good". Inject it if the fixture
        # predates the API change so the client-side contract is what's tested.
        payload["metrics"]["ep"] = {
            "label": "Earnings Yield (E/P)", "format": "percent",
            "scale": "linear", "higher_is_better": True, "gradeable": True,
        }
        for pt in payload["points"]:
            pt.setdefault("values", {})["ep"] = 0.04
            (pt.setdefault("percentiles", {}).setdefault("industry", {}))["ep"] = 60.0
        fig = plot(payload, "scatter", x="ep", y="roic")   # must not raise
        assert "higher is better" in fig.layout.xaxis.title.text


class TestHover:
    def test_industry_is_on_hover(self, payload):
        html = _hover(payload, _anchor(payload))
        assert "Semiconductors" in html   # the peer group / industry

    def test_ownership_and_volume_on_hover(self, payload):
        # the two-column layout pads cells with &nbsp; — normalise back to spaces
        # so the assertions check rendered content, not the padding mechanism.
        html = _hover(payload, _anchor(payload), view="all").replace("&nbsp;", " ")
        # the three requested fields, by label AND with correctly-formatted values
        assert "Inst. Own.: 71.0%" in html               # percent of a fraction
        assert "Insider Own.: 3.0%" in html
        assert "Avg Vol.: 1.20M" in html                 # shares_count, no $ prefix
        assert "$1.20M" not in html                       # not currency

    def test_bar_pctile_null_reads_as_not_ranked_not_zero(self, payload):
        # ZZIN has no pe (eps not positive); its pe bar must say "not ranked",
        # never render as a silent zero the reader mistakes for worst-in-group.
        fig = plot(payload, "bar_pctile", tickers=["ZZIN"], metrics=["pe", "roic"], hide_nm=False)
        hovers = [h for tr in fig.data for h in (tr.hovertext or [])]
        joined = " ".join(hovers)
        assert "not ranked" in joined

    def test_radar_builds_with_enriched_hover(self, payload):
        fig = plot(payload, "radar",
                   metrics=["pe", "roic", "sortino"], tickers=["ZZAM"])
        # every spoke carries a hovertext (company + measure), not a bare value
        assert any(tr.hovertext for tr in fig.data)

    def test_hover_height_capped_so_tooltip_always_renders(self, payload):
        # A tooltip taller than the plot is dropped by the browser — the point
        # then shows nothing on hover. The full hover must stay under the ceiling
        # regardless of how many measures the payload serves.
        from earningspy_glass.graph_client import _hover, _HOVER_MAX_LINES
        for point in payload["points"]:
            h = _hover(payload, point, ["pe", "roic", "market_cap", "eps_change"])
            lines = h.replace("<extra></extra>", "").count("<br>") + 1
            assert lines <= _HOVER_MAX_LINES, (
                f"{point.get('ticker')} hover is {lines} lines "
                f"(> {_HOVER_MAX_LINES}) — it would fail to render near an edge"
            )

    def test_hover_shows_every_measure_and_still_fits(self):
        # Every measure with a value is shown — never trimmed — and the tooltip still fits the
        # height budget (a taller one is dropped by the browser): the blocks widen instead.
        from earningspy_glass.graph_client import _hover, _HOVER_MAX_LINES
        n = _HOVER_MAX_LINES * 4   # far past a two-column fit
        metrics = {f"m{i}": {"label": f"M{i}", "format": "ratio_2dp",
                             "scale": "linear", "higher_is_better": True}
                   for i in range(n)}
        values = {f"m{i}": float(i) for i in range(n)}
        point = {"ticker": "ZZZ", "company": "Z", "role": "reported",
                 "earnings_date": "2025-01-01", "days_left": -10,
                 "values": values, "percentiles": {"industry": {}},
                 "flags": {}, "nm": {}, "capped": {}}
        pl = {"request": {"peer_group": {"value": "X"}}, "metrics": metrics,
              "points": [point]}
        h = _hover(pl, point, ["m0", "m1"], view="all").replace("&nbsp;", " ")
        lines = h.replace("<extra></extra>", "").count("<br>") + 1
        assert lines <= _HOVER_MAX_LINES
        assert "more measure" not in h
        assert all(f"M{i}:" in h for i in range(n))

    def test_a_marked_measure_puts_its_key_at_the_bottom(self, payload):
        from earningspy_glass.graph_client import _hover
        payload = dict(payload, metrics=dict(payload["metrics"], pre_event_quality_prob={
            "label": "Quality Probability (pre-event)", "format": "pct_1dp", "scale": "linear",
            "higher_is_better": True}))
        point = dict(payload["points"][0])
        point["values"] = dict(point.get("values") or {}, pre_event_quality_prob=0.61)
        h = _hover(payload, point, ["pe", "roic"], view="all")
        body = h.replace("<extra></extra>", "").replace("&nbsp;", " ")
        last = body.split("<span style='font-size:10px;opacity:.6'>")[-1]
        assert "Quality Prob. (pre)*" in body                      # the mark on the measure
        assert "* Computed pre event" in last and body.endswith("</span>")   # its key, last


class TestResolvedIsExplicit:
    """On a post payload the live model reads are empty and the resolved
    (pre_event_*) reads carry the data. The client must NAME the resolved key,
    never alias or mask it — the user plots pre_event_quality_prob deliberately.
    """

    def test_describe_names_the_resolved_key(self, post_resolved_payload):
        text = str(describe(post_resolved_payload))
        # the exact key is spelled out, with an ex-ante / pre-event explanation
        assert "quality_prob  →  pre_event_quality_prob" in text
        assert "pre-event" in text

    def test_empty_live_chart_points_at_resolved_key(self, post_resolved_payload):
        # plotting the empty live metric is refused, and the error names the
        # resolved key to plot instead (an error, never an empty chart)
        with pytest.raises(ChartError, match="pre_event_quality_prob.*ex-ante"):
            plot(post_resolved_payload, "bar", metric="quality_prob")

    def test_resolved_key_charts_over_reported_peers(self, post_resolved_payload):
        # the resolved key is plottable and ranks the reported peers
        fig = plot(post_resolved_payload, "bar", metric="pre_event_quality_prob")
        assert isinstance(fig, go.Figure)
        assert any(len(tr.y) >= 2 for tr in fig.data)

    def test_no_aliasing_keys_stay_distinct(self, post_resolved_payload):
        # both keys are independently available; neither stands in for the other
        keys = {m[0] for m in available_metrics(post_resolved_payload)}
        assert "quality_prob" in keys
        assert "pre_event_quality_prob" in keys

    def test_describe_hides_thin_notes_it_already_explained(
            self, post_resolved_payload):
        # the thin_* note for a redirected metric is not printed twice; the
        # plain-language guidance replaces it, but the raw note stays available.
        post_resolved_payload["warnings"] = [
            {"code": "thin_distribution",
             "detail": "quality_prob: 0 usable values"},          # covered
            {"code": "stale_figures", "detail": "prior-quarter figures"},  # not
        ]
        d = describe(post_resolved_payload)
        text = str(d)
        assert "quality_prob: 0 usable values" not in text        # notes are not printed
        assert "stale_figures" not in text
        assert "2 notes from the service: peers.warnings()" in text   # only pointed at
        assert len(d.warnings) == 2                                # all kept on object


class TestHoverViews:
    """hover="compact" (default) shows what a reader scans first, "all" everything,
    "encoded" only the plotted measures; changes=True adds what the report changed."""

    def _point(self, payload):
        point = dict(_anchor(payload))
        point["outcomes"] = {"CAR_3": 0.041, "EXP.RET_3": 0.012, "1+60_CAL_RET": 1.08,
                             "SPX_60_CAL_RET": 0.05, "1+60_RET": 0.97, "CAR_60": 0.9}
        point["market"] = {"MARKET_STATE": "ELEVATED", "VIX_TERM": "FLAT",
                           "VIX_PRE_2W": 22.4, "EARNING_VIX": 19.8}
        point["profile"] = {"SECTOR": "Technology", "COUNTRY": "USA",
                            "IS_S&P500": 0, "IS_RUSSELL": 1}
        point["changes"] = {"EPS_CHANGE": 0.0544, "FWD_P/E_CHANGE": -0.12,
                            "BETA_CHANGE": 0.02, "ROIC_CHANGE": None}
        return point

    def _text(self, payload, point, **kw):
        return _hover(payload, point, ["pe", "roic"], "industry", **kw).replace("&nbsp;", " ")

    def test_compact_keeps_the_scan_set_and_all_has_the_rest(self, payload):
        point = self._point(payload)
        compact, full = self._text(payload, point), self._text(payload, point, view="all")
        assert "Avg Vol.: 1.20M" in compact and "Inst. Own." in compact
        for dropped in ("Insider Own.", "Val. Spread***"):
            assert dropped not in compact and dropped in full

    def test_compact_outcomes_keep_expected_and_raw_returns_not_car(self, payload):
        point = self._point(payload)
        compact, full = self._text(payload, point), self._text(payload, point, view="all")
        assert "Exp. return 3d: 1.2%" in compact and "Realized 60d: 108.0%" in compact
        assert "S&P 500 60d: 5.0%" in compact
        assert "CAR 3d" not in compact and "CAR 3d: 4.1%" in full

    def test_business_day_windows_are_named_as_such_and_only_in_full(self, payload):
        point = self._point(payload)
        compact, full = self._text(payload, point), self._text(payload, point, view="all")
        assert "business days" not in compact
        assert "Realized 60d (business days): 97.0%" in full and "CAR 60d (business days): 90.0%" in full
        # the calendar windows lead: they are the 30 / 60-day returns a reader means
        assert full.index("Realized 60d: 108.0%") < full.index("Realized 60d (business days)")

    def test_the_market_line_is_on_the_full_view_only(self, payload):
        point = self._point(payload)
        assert "VIX curve" not in self._text(payload, point)
        assert "VIX curve Flat" in self._text(payload, point, view="all")

    def test_profile_line_names_sector_country_and_indexes(self, payload):
        h = self._text(payload, self._point(payload))
        assert "Technology · USA · Russell" in h
        # not an S&P 500 member: the index is absent from the profile line (the
        # outcome block may still name the S&P 500 as the 60-day benchmark)
        profile = next(line for line in h.split("<br>") if "Technology · USA" in line)
        assert "S&P 500" not in profile

    def test_encoded_shows_only_the_plotted_measures(self, payload):
        h = self._text(payload, self._point(payload), view="encoded")
        assert "P/E" in h and "Avg Volume" not in h and "industry percentile" not in h

    def test_changes_block_compact_and_all(self, payload):
        point = self._point(payload)
        compact = self._text(payload, point, changes=True)
        full = self._text(payload, point, view="all", changes=True)
        assert "What the report changed" in compact
        assert "EPS: +5.4%" in compact and "Fwd P/E: -12.0%" in compact
        assert "ROIC: n.m." in compact                      # carried, not computable
        assert "Beta: +2.0%" not in compact and "Beta: +2.0%" in full
        assert "What the report changed" not in self._text(payload, point)

    def test_changes_need_a_company_that_has_reported(self, payload):
        from earningspy_glass.graph_client import hover_options
        with pytest.raises(ChartError, match="frame='post'"):
            hover_options(payload, changes=True)            # no point carries changes
        pre = dict(payload, request=dict(payload["request"], frame="pre"))
        pre["points"] = [dict(pt, changes={"EPS_CHANGE": 0.1}) for pt in payload["points"]]
        assert hover_options(pre, changes=True) == ("compact", True)   # reported peers on pre
        with pytest.raises(ChartError, match="hover must be one of"):
            hover_options(payload, hover="short")
        assert hover_options(payload, hover_all=True) == ("all", False)
        assert hover_options(payload, hover_all=False) == ("encoded", False)

    def test_scatter_draws_the_changes_on_hover(self, payload):
        anchor = _anchor(payload)
        anchor["changes"] = {"EPS_CHANGE": 0.0544}
        fig = plot(payload, "scatter", x="pe", y="roic", changes=True)
        hovers = " ".join(h for tr in fig.data for h in (tr.hovertext or []))
        assert "What the report changed" in hovers


class TestForwardPE:
    """Forward P/E follows next year's estimate (its own nm reason), never the
    trailing-P/E flag: a trailing loss with an expected profit still plots."""

    def _setup(self, payload):
        import copy
        p = copy.deepcopy(payload)
        p["metrics"]["fwd_pe"] = {"label": "Fwd P/E", "format": "ratio_x", "scale": "log",
                                  "higher_is_better": False, "positive_only": True}
        p["metrics"]["ep"] = {"label": "Earnings Yield (E/P)", "format": "percent",
                              "scale": "linear", "higher_is_better": True}
        peers = [pt for pt in p["points"] if pt.get("role") != "anchor"]
        loss_now, no_forward = peers[0], peers[1]
        for pt in p["points"]:
            pt["values"].update(fwd_pe=15.0, ep=0.05)
        loss_now["flags"] = dict(loss_now.get("flags") or {}, plot_pe=False)
        loss_now["values"].update(fwd_pe=16.2, ep=-0.01)
        no_forward["values"].update(fwd_pe=None, ep=-1.55)
        no_forward["nm"] = dict(no_forward.get("nm") or {}, fwd_pe="nonpositive_forward_earnings")
        return p, loss_now["ticker"], no_forward["ticker"]

    def test_a_trailing_loss_with_a_forward_pe_is_placed(self, payload):
        from earningspy_glass.graph_client import _plottable, _unplottable_reason
        p, loss_now, no_forward = self._setup(payload)
        point = next(pt for pt in p["points"] if pt["ticker"] == loss_now)
        assert _plottable(point, "fwd_pe") and _unplottable_reason(point, "fwd_pe") is None
        assert not _plottable(point, "pe")                    # trailing P/E still gated

    def test_a_non_positive_forward_estimate_is_left_off_and_named(self, payload, caplog):
        import logging
        p, _, no_forward = self._setup(payload)
        with caplog.at_level(logging.INFO, logger="earningspy_glass.graph_client"):
            fig = plot(p, "scatter", x="fwd_pe", y="ep")
        assert "non-positive forward earnings" in fig.layout.title.text
        assert f"fwd_pe: non-positive forward earnings ({no_forward})" in caplog.text


def test_hover_columns_never_collide():
    """Each column is as wide as its widest cell plus a gap: a long cell pushes its
    column out instead of running into the next one."""
    from earningspy_glass.graph_client import column_rows
    rows = [r.replace("&nbsp;", " ") for r in column_rows(
        ["Sales (TTM): $466.82B p100", "Oper. Margin: 33.2% p100", "Avg Vol.: 55.62M p100",
         "E/P: 2.8% p75", "ROIC: 72.1% p100", "FCF: $136.70B p100"], 3)]
    assert rows[0].index("Oper.") == rows[1].index("ROIC")       # columns line up
    assert rows[0].index("Avg") == rows[1].index("FCF")
    assert "p100  Oper." in rows[0] and "p100  Avg" in rows[0]   # always a gap


def test_the_footnote_keys_count_toward_the_line_budget(payload):
    """Two or three key lines at the bottom must not push the tooltip past the cap."""
    from earningspy_glass.graph_client import _HOVER_MAX_LINES
    import copy
    p = copy.deepcopy(payload)
    point = _anchor(p)
    for i in range(80):
        p["metrics"][f"m{i}"] = {"label": f"Measure {i}", "format": "ratio_2dp",
                                 "scale": "linear", "higher_is_better": True}
        point["values"][f"m{i}"] = float(i)
    point["values"].update(sortino=1.2, valuation_spread=-0.1)   # ** and *** keys
    h = _hover(p, point, ["pe", "roic"], "industry", view="all")
    assert h.replace("<extra></extra>", "").count("<br>") + 1 <= _HOVER_MAX_LINES


class TestShortlists:
    """tickers=peers.nearest(n) / peers.largest(n) is a shortlist drawn against the
    company the chart is about; highlight takes a one-company list as well."""

    def _bars(self, fig):
        return {str(y) for tr in fig.data for y in (tr.y if tr.y is not None else [])}

    @pytest.mark.parametrize("kind,kw", [("bar", {"metric": "roic"}),
                                         ("bar_signed", {"metrics": ("sortino",)})])
    def test_the_anchor_stays_on_a_shortlisted_chart(self, payload, kind, kw):
        from earningspy_glass.client import Peers
        peers = Peers(payload)
        other = peers.largest(1)
        fig = plot(payload, kind, tickers=other, **kw)
        names = " ".join(self._bars(fig))
        assert peers.ticker in names and other[0] in names

    def test_highlight_takes_a_one_company_list(self, payload):
        from earningspy_glass.client import Peers
        biggest = Peers(payload).largest(1)
        plot(payload, "scatter", x="pe", y="roic", highlight=biggest)      # must not raise
        with pytest.raises(ChartError, match="highlight names one company"):
            plot(payload, "scatter", x="pe", y="roic", highlight=Peers(payload).largest(2))


def test_a_highlighted_peer_without_the_measure_is_noted_not_refused(payload):
    """The chart draws everyone it can place and says, on the chart and as a warning,
    that the company it is about could not be placed."""
    no_pe = next(p["ticker"] for p in payload["points"]
                 if p.get("role") != "anchor" and (p.get("nm") or {}).get("pe"))
    with pytest.warns(UserWarning, match=f"{no_pe} cannot be placed"):
        fig = plot(payload, "scatter", x="pe", y="roic", highlight=no_pe)
    assert f"{no_pe} not placed: no usable P/E" in fig.layout.title.text
    assert "1 left off" not in fig.layout.title.text            # named once, not counted again
    assert any(len(tr.x or []) for tr in fig.data)               # the rest is drawn


class TestRadarSaysWhatItCannotDraw:
    def _ranked(self, payload):
        import copy
        p = copy.deepcopy(payload)
        for pt in p["points"]:
            pt.setdefault("percentiles", {}).setdefault("industry", {}).update(
                pe=50.0, roic=60.0, sortino=40.0)
        return p

    def test_the_anchor_leads_and_a_shortlist_of_three_peers_fits(self, payload):
        from earningspy_glass.client import Peers
        p = self._ranked(payload)
        peers = Peers(p)
        fig = plot(p, "radar", tickers=peers.largest(3), metrics=["pe", "roic", "sortino"])
        assert len(fig.data) == 4 and peers.ticker in fig.data[0].name

    def test_companies_past_the_cap_are_named(self, payload, caplog):
        import logging
        from earningspy_glass.client import Peers
        import copy
        p = self._ranked(payload)
        extra = copy.deepcopy(next(pt for pt in p["points"] if pt.get("role") != "anchor"))
        extra.update(ticker="ZZZZ", company="Extra Co")
        p["points"].append(extra)
        many = Peers(p).largest(4)
        with caplog.at_level(logging.INFO, logger="earningspy_glass.graph_client"):
            fig = plot(p, "radar", tickers=many, metrics=["pe", "roic", "sortino"])
        assert len(fig.data) == 4
        assert "4 companies at most" in fig.layout.title.text and "not drawn" in caplog.text

    def test_a_company_without_standing_is_named_with_the_reason(self, payload):
        p = self._ranked(payload)
        peer = next(pt for pt in p["points"] if pt.get("role") != "anchor")
        for m in ("pe", "roic", "sortino"):
            peer["percentiles"]["industry"][m] = None
        peer["nm"] = dict(peer.get("nm") or {}, pe="nonpositive_earnings")
        fig = plot(p, "radar", tickers=[peer["ticker"]], metrics=["pe", "roic", "sortino"])
        text = fig.layout.title.text
        assert f"{peer['ticker']} not ranked on P/E (non-positive earnings)" in text
        assert "at the centre" in text
