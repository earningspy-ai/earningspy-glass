"""Every chart kind against its options: what it draws, and what it refuses."""
import copy

import plotly.graph_objects as go
import pytest

import earningspy_glass as g
from earningspy_glass import graph_client as P
from earningspy_glass import timeline_charts as T
from earningspy_glass.client import Calendar, Peers, Timeline

METRICS = ("pe", "roic", "eps_change", "sortino", "valuation_spread")


def texts(fig):
    """Everything a reader can see on a figure, as one string."""
    return str(fig.to_dict())


@pytest.fixture
def ranked(payload):
    """The recorded payload with a full set of ranks, states, medians and reads."""
    states = ["newly_cheap", "still_cheap", "exiting", "normal"]
    for i, p in enumerate(payload["points"]):
        p["percentiles"] = {"industry": {m: 20 + 15 * i + j for j, m in enumerate(METRICS)}}
        p["values"].update({"pe": 10.0 + 9 * i, "roic": 0.1 * i - 0.1, "eps_change": 0.2 * i - 0.2,
                            "sortino": i - 1.0, "valuation_spread": 0.1 * i - 0.1})
        p["flags"]["plot_pe"] = True
        p["valuation"] = {"VALUATION_STATE": states[i % 4], "VALUATION_SPREAD_PRIOR": -0.2}
        p["outcomes"] = {"CAR_3": 0.01, "CAR_60_CAL": None, "1+60_CAL_RET": 0.04, "SPX_60_CAL_RET": 0.02,
                         "1+60_RET": 0.03, "EXP.RET_30": 0.01}
        p["changes"] = {"EPS_CHANGE": 0.1, "ROIC_CHANGE": -0.02}
        p["profile"] = {"INDUSTRY": "Semis", "SECTOR": "Technology"}
        p["market"] = {"MARKET_STATE": "CALM", "VIX_TERM": "CONTANGO", "VIX_PRE_2W": 15.0, "EARNING_VIX": 18.0}
    payload["reference"] = {"industry": {"quadrant": {"x": {"metric": "pe", "value": 20.0},
                                                      "y": {"metric": "roic", "value": 0.1}}}}
    return payload


@pytest.fixture
def delta(ranked):
    tickers = [p["ticker"] for p in ranked["points"]]
    row = lambda t, s: {"ticker": t, "value_delta": {"valuation_spread": 0.1 * s, "roic": 0.02},   # noqa: E731
                        "percentile_delta": {"industry": {"valuation_spread": 8.0 * s, "roic": -5.0, "pe": 3.0}},
                        "state_delta": {"VALUATION_STATE": ["normal", "newly_cheap"]} if s > 0 else {}}
    ranked["delta"] = {"anchor": row("ZZAM", 1), "points": [row(t, -1) for t in tickers if t != "ZZAM"]}
    return ranked


# --- peers ----------------------------------------------------------------------

KINDS = {"scatter": {"x": "pe", "y": "roic"}, "treemap": {}, "bar": {"metric": "roic"},
         "bar_signed": {"metrics": ["sortino", "valuation_spread"]}, "bar_pctile": {}, "radar": {},
         "donut": {}, "grade_card": {}}
OPTIONS = {
    "scatter": [{"hover": "all"}, {"hover": "encoded", "changes": True}, {"tickers": ["ZZNV"]}, {"peers": 1},
                {"highlight": "ZZNV"}, {"hints": False}, {"color": "valuation_spread"}, {"outliers": True}],
    "treemap": [{"size": "avg_volume"}, {"color": "roic"}, {"hover": "all", "changes": True}],
    "bar": [{"top": 2}, {"tickers": ["ZZNV"]}, {"group": "industry"}, {"group": "sector"}],
    "bar_signed": [{"metrics": "sortino"}, {"tickers": ["ZZNV"]}],
    "bar_pctile": [{"metrics": list(METRICS)}, {"hide_nm": True}, {"valuation": "state"}, {"tags": ["valuation_state"]},
                   {"tickers": ["ZZNV"]}],
    "radar": [{"metrics": list(METRICS[:4])}, {"tickers": ["ZZNV"]}],
    "donut": [{"mode": "profitability"}, {"mode": "avg_volume"}, {"slice_text": "none"}, {"slice_text": "share"}],
    "grade_card": [{"ticker": "ZZNV"}, {"hide_nm": True}, {"valuation": "state"}, {"metrics": ["roic", "pe"]}],
}


@pytest.mark.parametrize("theme", ["light", "dark"])
@pytest.mark.parametrize("kind", KINDS)
def test_peers_kinds_draw_in_both_themes(ranked, kind, theme):
    fig = g.plot(ranked, kind, theme=theme, **KINDS[kind])
    assert isinstance(fig, go.Figure) and len(fig.data) and "Nothing to plot" not in texts(fig)
    assert g.recipe(fig)["kind"] == kind


@pytest.mark.parametrize("kind,options", [(k, o) for k, v in OPTIONS.items() for o in v],
                         ids=lambda x: str(x)[:50])
def test_peers_options_draw(ranked, kind, options):
    fig = g.plot(ranked, kind, title="My title", **{**KINDS[kind], **options})
    assert "My title" in texts(fig) and "Nothing to plot" not in texts(fig)


def test_drawing_never_changes_the_payload(delta):
    before = copy.deepcopy(delta)
    for kind, options in {**KINDS, "dumbbell": {}, "shift_scatter": {}}.items():
        g.plot(delta, kind, **options)
    assert delta == before


@pytest.mark.parametrize("kind", [*KINDS, "dumbbell", "shift_scatter"])
def test_an_unknown_option_is_refused_with_a_suggestion(delta, kind):
    with pytest.raises(g.ChartError, match="did you mean 'theme'"):
        g.plot(delta, kind, **{**KINDS.get(kind, {}), "themee": "dark"})


@pytest.mark.parametrize("kind", KINDS)
def test_an_unknown_theme_is_refused(ranked, kind):
    with pytest.raises(g.ChartError, match="theme"):
        g.plot(ranked, kind, theme="sepia", **KINDS[kind])


@pytest.mark.parametrize("kind", ["scatter", "treemap", "bar", "bar_pctile", "radar", "donut"])
def test_an_empty_group_is_an_empty_figure_not_a_crash(ranked, kind):
    ranked["points"] = []
    try:
        assert isinstance(g.plot(ranked, kind, **KINDS[kind]), go.Figure)
    except g.ChartError as exc:
        assert str(exc)


def test_two_unknowns_and_the_kind_list():
    with pytest.raises(g.ChartError, match="choose one of"):
        g.plot({}, "pie3d")
    with pytest.raises(g.ChartError, match="one company"):
        g.plot({"points": []}, "scatter", highlight=["A", "B"])


class TestDumbbell:
    def test_before_and_after_for_each_measure(self, delta):
        fig = g.plot(delta, "dumbbell", metrics=["roic", "valuation_spread"])
        amba = next(p for p in delta["points"] if p["ticker"] == "ZZAM")["percentiles"]["industry"]
        line = fig.data[0]
        assert list(line.x) == [amba["roic"] + 5.0, amba["roic"]]
        assert fig.measures() == ["roic", "valuation_spread"]

    def test_colour_reads_the_measures_own_direction(self, delta):
        t = P._THEMES["light"]
        colours = {f.y[0]: f.marker.color for f in g.plot(delta, "dumbbell").data[2::3]}
        labels = {m: P._name(delta, m) for m in ("roic", "pe")}
        match = lambda m: next(c for y, c in colours.items() if labels[m] in y)      # noqa: E731
        assert match("roic") == t["anchor"]                  # fell, higher is better
        assert match("pe") == t["anchor"]                    # rose, lower is better
        delta["metrics"]["pe"]["higher_is_better"] = None
        colours = {f.y[0]: f.marker.color for f in g.plot(delta, "dumbbell").data[2::3]}
        assert match("pe") == t["muted"]

    def test_a_measure_without_a_shift_is_named(self, delta):
        assert "no before/after standing" in texts(g.plot(delta, "dumbbell", metrics=["roic", "sortino"]))

    def test_nothing_shifted_says_so(self, delta):
        delta["delta"]["anchor"]["percentile_delta"] = {"industry": {}}
        assert "no measure shifted" in texts(g.plot(delta, "dumbbell"))

    def test_an_anchor_without_a_standing_draws_nothing_false(self, delta):
        next(p for p in delta["points"] if p["ticker"] == "ZZAM")["percentiles"] = {}
        assert len(g.plot(delta, "dumbbell").data) == 0


class TestShiftScatter:
    def test_one_mark_per_company_ringed_when_its_state_changed(self, delta):
        fig = g.plot(delta, "shift_scatter")
        assert len(fig.data) == len(delta["points"])
        anchor = next(d for d in fig.data if d.text and d.text[0] == "ZZAM")
        assert (anchor.x[0], anchor.y[0], anchor.marker.line.width) == (0.1, 8.0, 2.4)
        assert "normal → newly_cheap" in anchor.hovertemplate

    def test_companies_without_a_shift_are_left_out(self, delta):
        fig = g.plot(delta, "shift_scatter", metric="sortino")
        assert "no company has a" in texts(fig)

    def test_an_empty_block_says_so(self, delta):
        delta["delta"] = {"anchor": None, "points": [], "basis": "x"}
        assert "carries no companies" in texts(g.plot(delta, "shift_scatter"))


@pytest.mark.parametrize("kind", ["dumbbell", "shift_scatter"])
def test_the_before_and_after_charts_need_their_view(ranked, kind):
    with pytest.raises(g.ChartError, match="frame='delta'"):
        g.plot(ranked, kind)


class TestPeersReads:
    @pytest.mark.parametrize("reported,days,session,expected", [
        (False, 3, "AMC", "NOT REPORTED"), (True, -2, "BMO", "REPORTED"), (True, 0, "BMO", "REPORTED"),
        (False, 0, "AMC", "NOT REPORTED"), (None, None, None, "REPORT NOT CONFIRMED")])
    def test_report_status(self, reported, days, session, expected):
        head, when, said = P.report_status({"reported": reported, "role": "reported", "days_left": days,
                                            "session": session, "earnings_date": "2026-11-02"})
        assert head == expected and said is reported
        assert "2026-11-02" in when and (session or "") in when

    @pytest.mark.parametrize("value,fmt,shown", [
        (None, "percent", "n.m."), (float("nan"), "percent", "n.m."), (float("inf"), "ratio", "n.m."),
        (0, "percent", "0.0%"), (-2.3e9, "currency", "-$2.30B"), (1.5e12, "currency_compact", "$1.50T"),
        (0.0631, "percent_fraction", "6.3%"), (-0.3, "currency_per_share", "-$0.30"), (1580000, "count", "1.58M"),
        (12.34, "percentile_points_signed", "+12.3"), (3.14159, "unknown", "3.14"), ("x", "unknown", "x"),
        ("x", "percent", "x"), (12, "ratio_x", "12.0x"), (950, "shares_count", "950")])
    def test_formats(self, value, fmt, shown):
        assert P.format_value(value, fmt) == shown

    def test_grades_come_from_the_payloads_bands(self, ranked):
        assert P.grade_for(95, ranked, "roic") == "A+" and P.grade_for(95, ranked, "pe") == "F"
        assert P.grade_for(None, ranked, "roic") == "n.m." and P.grade_for(95, ranked, "avg_volume") == "n.m."
        ranked["grade_scale"] = {"bands": [{"grade": "Z", "min": 0}]}
        assert P.grade_for(95, ranked, "roic") == "Z"

    def test_a_flipped_direction_flips_the_drawing(self, ranked):
        assert P.oriented(30, ranked, "roic") == 30
        ranked["metrics"]["roic"]["higher_is_better"] = False
        assert P.oriented(30, ranked, "roic") == 70

    def test_wrapper_helpers(self, ranked):
        peers = Peers(ranked, request={"ticker": "ZZAM", "tier": "silver"})
        assert "ZZAM" not in peers.largest(10)
        caps = {p["ticker"]: p["values"]["market_cap"] for p in ranked["points"]}
        assert [caps[t] for t in peers.largest(2)] == sorted((caps[t] for t in caps if t != "ZZAM"), reverse=True)[:2]
        assert len(peers.nearest(1)) == 1
        market = peers.market()
        assert market[0]["role"] == "anchor" and market[0]["VIX_PRE_2W"] == 15.0
        small = peers.only(["ZZNV"])
        assert {p["ticker"] for p in small.payload["points"]} == {"ZZAM", "ZZNV"}
        fig = small.plot("scatter", x="pe", y="roic")
        assert g.recipe(fig)["only"] == ["ZZAM", "ZZNV"] or "ZZNV" in g.recipe(fig)["only"]
        assert peers.describe() and "measures you can plot" in str(peers.describe())

    @pytest.mark.parametrize("bad", [0, -1, "3", 1.5])
    def test_nearest_needs_a_whole_number(self, ranked, bad):
        with pytest.raises(g.ChartError, match="whole number"):
            Peers(ranked).nearest(bad)

    def test_nearest_needs_a_size(self, ranked):
        ranked["points"][0]["values"]["market_cap"] = None
        with pytest.raises(g.ChartError, match="no market cap"):
            Peers(ranked).nearest(1)

    def test_bronze_is_told_what_a_ranking_needs(self, ranked):
        ranked["withheld"] = {"industry_percentiles": "silver"}
        for kind in ("bar_pctile", "radar", "grade_card"):
            with pytest.raises(g.ChartError, match="silver"):
                g.plot(ranked, kind)

    def test_a_focus_that_cannot_be_placed_is_said(self, ranked):
        ranked["points"][0]["flags"]["plot_pe"] = False
        with pytest.warns(UserWarning, match="cannot be placed"):
            fig = g.plot(ranked, "scatter", x="pe", y="roic")
        assert "ZZAM not placed" in texts(fig)


# --- calendar -------------------------------------------------------------------

CAL = {
    "scatter": [{}, {"highlight": "sp500"}, {"highlight": ["ZZIP"]}, {"labels": 2, "names": True}, {"color": "roa_quant"},
                {"size": None}],
    "bar_pctile": [{}, {"top": 0}, {"top": 0, "labels": "extremes"}, {"top": 0, "group": "sector"},
                   {"color": "roa_quant"}, {"reference": "decile_10"}, {"reference": 75}, {"note": "A note"},
                   {"dates": True}, {"group": "industry"}, {"highlight": "Biotechnology", "top": 0, "names": True}],
    "treemap": [{}, {"color": "roa_quant"}, {"color": "var_h_quant"}, {"color": None}, {"note": "A note"}],
    "radar": [{}, {"group": "sector"}, {"group": "industry"}, {"by": "EPS_QUANT", "top": 2},
              {"metrics": ["ep_quant", "roa_quant", "eps_quant"]}],
    "donut": [{}, {"mode": "industry"}, {"mode": "sector", "size": "MARKET_CAP"}, {"mode": "MARKET_CAP"}],
}


@pytest.mark.parametrize("frame", ["pre", "post"])
@pytest.mark.parametrize("theme", ["light", "dark"])
@pytest.mark.parametrize("kind,options", [(k, o) for k, v in CAL.items() for o in v], ids=lambda x: str(x)[:50])
def test_calendar_options_draw(calendar_pre, calendar_post, frame, theme, kind, options):
    payload = calendar_pre if frame == "pre" else calendar_post
    before = copy.deepcopy(payload)
    fig = Calendar(payload).plot(kind, theme=theme, **options)
    assert len(fig.data) and payload == before
    assert g.recipe(fig)["graph"] == "calendar"


@pytest.mark.parametrize("options", [{}, {"by": None}, {"top": 0, "labels": "extremes"}, {"group": "sector", "top": 0},
                                     {"reference": "p10_p90", "top": 0}, {"note": "n"}, {"highlight": "sp500"},
                                     {"metric": "fhs_garch_annzd_vol_quant"}, {"metric": "ep_quant_change"}])
def test_calendar_moves_draw(calendar_post, options):
    assert len(Calendar(calendar_post).plot("bar_signed", **options).data)


@pytest.mark.parametrize("options", [{}, {"highlight": "sp500"}, {"names": True, "highlight": ["ZZIP"]},
                                     {"metric": "var_h_quant"}])
def test_calendar_change_scatter_draws(calendar_post, options):
    assert len(Calendar(calendar_post).plot("change_scatter", **options).data)


@pytest.mark.parametrize("kind", ["bar_signed", "change_scatter"])
def test_moves_need_the_post_frame(calendar_pre, kind):
    with pytest.raises(g.ChartError, match="post"):
        Calendar(calendar_pre).plot(kind)


@pytest.mark.parametrize("kind,options,words", [
    ("pie", {}, "choose one of"), ("scatter", {"x": "nope"}, "not on this calendar"),
    ("bar_pctile", {"group": "planet"}, "group"), ("radar", {"group": "planet"}, "group"),
    ("radar", {"group": "sector", "groups": ["Nope"]}, "no company"), ("bar_signed", {"group": "planet"}, "group"),
    ("bar_signed", {"reference": "p50"}, "p10_p90"), ("treemap", {"highlight": "sp500"}, "filter="),
    ("treemap", {"colour": "ep_quant"}, "did you mean 'color'"), ("donut", {"filtr": None}, "did you mean 'filter'")])
def test_calendar_refusals(calendar_post, kind, options, words):
    with pytest.raises(g.ChartError, match=words):
        Calendar(calendar_post).plot(kind, **options)


@pytest.mark.parametrize("kind", [*CAL, "bar_signed", "change_scatter"])
def test_a_filter_that_keeps_nobody_is_an_empty_figure(calendar_post, kind):
    fig = Calendar(calendar_post).plot(kind, filter=g.Filter.tickers(["ZZZZ"]))
    assert "Nothing to plot" in texts(fig) or not len(fig.data) or kind == "radar"


def test_a_calendar_radar_of_named_companies(calendar_pre):
    names = [r["TICKER"] for r in calendar_pre["data"][:2]]
    assert [d.name for d in Calendar(calendar_pre).plot("radar", tickers=names).data] == names
    with pytest.raises(g.ChartError, match="ZZZZ"):
        Calendar(calendar_pre).plot("radar", tickers=[names[0], "ZZZZ"])


def test_a_filter_must_be_a_filter(calendar_post):
    with pytest.raises(TypeError, match="Filter"):
        Calendar(calendar_post).plot("scatter", filter="Technology")


def test_two_long_names_that_shorten_alike_are_two_companies(calendar_post):
    a, b = calendar_post["data"][:2]
    a.update(TICKER="AAA1", COMPANY="First American Financial Corporation East", MARKET_CAP=9e12, EP_QUANT_CHANGE=3.0)
    b.update(TICKER="AAA2", COMPANY="First American Financial Services West", MARKET_CAP=8e12, EP_QUANT_CHANGE=2.0)
    shown = Calendar(calendar_post).plot("bar_signed", metric="ep_quant", top=2).data[0].y
    assert set(shown) == {"AAA1", "AAA2"}


def test_one_company_in_two_share_classes_is_counted_once(calendar_post):
    from glass_testkit import answer
    a, b = calendar_post["data"][:2]
    a.update(TICKER="GOOGL", EARNINGS_DATE="2026-09-17")
    b.update(TICKER="GOOG", EARNINGS_DATE="2026-09-17")
    shown = Calendar(answer(calendar_post)).plot("treemap", color=None).data[0].labels
    assert {"GOOGL", "GOOG"} <= set(shown)                  # nothing is merged unless the service says so
    shown = Calendar(answer(calendar_post, listing_of={"GOOG": "GOOGL"})).plot("treemap", color=None).data[0].labels
    assert "GOOGL" in shown and "GOOG" not in shown


@pytest.mark.parametrize("draw", [
    lambda cal, tl: Calendar(cal).plot("treemap"), lambda cal, tl: T.bars(tl, "EPS"),
    lambda cal, tl: T.radar(tl)])
def test_a_payload_without_the_services_facts_is_refused(calendar_pre, timeline_pre_quant, draw):
    del calendar_pre["facts"], timeline_pre_quant["facts"]
    with pytest.raises(g.ContractError, match="Fetch it again"):
        draw(calendar_pre, timeline_pre_quant)


def test_calendar_describe_and_repr(calendar_post, capsys):
    cal = Calendar(calendar_post)
    assert cal.describe() and "CHART KINDS" in capsys.readouterr().out
    assert "companies" in repr(cal)


# --- timeline -------------------------------------------------------------------

@pytest.mark.parametrize("theme", ["light", "dark"])
def test_timeline_bars(timeline_pre_quant, timeline_post, theme):
    for payload, measures in ((timeline_pre_quant, ["ROIC_QUANT", "EP_QUANT"]), (timeline_post, "EPS")):
        for options in ({}, {"show_dates": True}, {"last": 0}, {"last": 2}):
            assert len(T.bars(payload, measures, theme=theme, **options).data)


@pytest.mark.parametrize("mode,frame", [("percentile", "quant"), ("change", "post"), ("level", "pre")])
def test_timeline_radars(timeline_pre, timeline_pre_quant, timeline_post, mode, frame):
    payload = {"quant": timeline_pre_quant, "post": timeline_post, "pre": timeline_pre}[frame]
    fig = Timeline(payload).radar(mode=mode)
    assert isinstance(fig, go.Figure) and g.recipe(fig)["params"]["mode"] == mode


def test_timeline_refusals(timeline_pre, timeline_post):
    with pytest.raises(g.ChartError, match="mode"):
        T.radar(timeline_pre, mode="sideways")
    with pytest.raises(g.ChartError, match="post timeline"):
        T.radar(timeline_pre, mode="change")
    with pytest.raises(g.ChartError, match="not on this timeline"):
        T.bars(timeline_pre, "NOPE")
    with pytest.raises(RuntimeError, match="both frames"):
        Timeline(timeline_pre).compare("EPS")


def test_compare_pairs_the_two_frames_once(timeline_pre, timeline_post):
    fetched = []
    tl = Timeline(timeline_pre, _fetch_other=lambda: fetched.append(1) or timeline_post)
    for _ in range(2):
        fig = tl.compare("EPS", show_dates=True)
    assert len(fig.data) == 2 and fetched == [1]
    assert len(Timeline(timeline_post, _fetch_other=lambda: timeline_pre).compare("EPS").data) == 2


def test_the_client_wires_both_fetches(timeline_pre, timeline_post, monkeypatch):
    import earningspy_glass.client as client
    asked = []

    def fetch(ticker, frame="pre", view=None, **kw):
        asked.append((frame, view))
        return copy.deepcopy(timeline_pre if frame == "pre" else timeline_post)

    monkeypatch.setattr(client, "fetch_timeline", fetch)
    tl = g.EarningSpy(api_key="k", verbose=False).timeline("aapl")
    tl.compare("EPS")
    tl._fetch_regime()
    assert asked == [("pre", None), ("post", None), ("pre", "fundamentals")]


def test_timeline_warnings_name_the_gaps(timeline_post, capsys):
    tl = Timeline(timeline_post)
    for row in tl.payload["data"][:1]:
        row["EPS"] = None
    tl.bars("EPS")
    assert any(n["code"] == "missing_quarter" for n in tl.warnings())
    assert "No EPS" in capsys.readouterr().out
    fresh = Timeline({"data": []})
    assert fresh.warnings() == [] and "no notes" in capsys.readouterr().out


def test_timeline_describe_and_repr(timeline_pre, capsys):
    assert Timeline(timeline_pre).describe() is not None
    assert "quarters" in repr(Timeline(timeline_pre))


@pytest.mark.parametrize("values", [[], [0.0], [1.0, 2.0, 3.7], [-1.2, 4.0], [-30.0, -5.0], [1e-9, 2e-9], [1e6, 5e6]])
def test_a_level_axis_holds_every_value(values):
    lo, hi = T.nice_level_axis(values)
    assert lo < hi and all(lo <= v <= hi + 1e-9 for v in values) and lo <= 0


# --- client-side odds and ends ----------------------------------------------------

def test_the_calendar_client_threads_its_options(calendar_pre, monkeypatch):
    import earningspy_glass.client as client
    seen = {}
    monkeypatch.setattr(client, "fetch_calendar", lambda **kw: seen.update(kw) or calendar_pre)
    cal = g.EarningSpy(api_key="k", timeout=9, verbose=False).calendar("week", from_monday=True, tier="gold")
    assert isinstance(cal, Calendar)
    assert (seen["scope"], seen["tier"], seen["from_monday"], seen["timeout"]) == ("week", "gold", True, 9)
