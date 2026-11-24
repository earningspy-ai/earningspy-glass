"""The calendar render layer: flat-row builders read the payload's metrics table
for orientation/label/format (no finance in the client), the Filter object
narrows rows, highlight emphasises a subset, and the frame guards hold.

Offline, fixture-driven — no network. Charts return plotly Figures; assertions
check they build (or raise) and that meaning is READ from the payload, never
decided here.
"""
import plotly.graph_objects as go
import pytest

from earningspy_glass import ChartError, Filter, describe_calendar, plot_calendar
from earningspy_glass.calendar_charts import (
    bar_pctile,
    bar_signed,
    calendar_metrics,
    change_scatter,
    donut,
    radar,
    scatter,
    treemap,
    _higher_is_better,
    _hover,
    _is_diverging,
    _oriented,
    _report_status,
    _resolve,
)


# --- the metrics table is the source of truth -------------------------------

class TestReadsMetadataFromPayload:
    def test_orientation_is_read_not_hardcoded(self, calendar_pre):
        # A risk metric is lower-is-better, a ratio higher, ownership directionless
        # — and every one of those verdicts comes from the payload, never a list here.
        assert _higher_is_better(calendar_pre, "ROIC_QUANT") is True
        assert _higher_is_better(calendar_pre, "FHS_GARCH_ANNZD_VOL_QUANT") is False
        assert _higher_is_better(calendar_pre, "INST_OWN_QUANT") is None

    def test_change_columns_are_diverging_levels_are_not(self, calendar_post):
        assert _is_diverging(calendar_post, "EP_QUANT_CHANGE") is True
        assert _is_diverging(calendar_post, "EP_QUANT") is False

    def test_names_resolve_case_insensitively(self, calendar_pre):
        assert _resolve(calendar_pre, "ep_quant") == "EP_QUANT"
        assert _resolve(calendar_pre, "EP_QUANT") == "EP_QUANT"

    def test_ep_displays_as_inverted_pe(self, calendar_pre):
        # Display only — the key stays EP_QUANT, but the reader sees E/P so it is
        # legible as the inverted P/E (and its higher-is-better direction).
        from earningspy_glass.calendar_charts import _short
        assert _short(calendar_pre, "EP_QUANT") == "E/P"
        assert _short(calendar_pre, "ROIC_QUANT") == "ROIC"   # others unchanged

    def test_describe_prints_the_full_option_menu(self, calendar_pre, capsys):
        describe_calendar(calendar_pre)
        out = capsys.readouterr().out
        assert "CHART KINDS" in out and "scatter" in out
        assert "FIELDS for x / y / metric / color" in out
        assert "higher is better" in out and "lower is better" in out
        assert "direction neutral, context implications" in out
        assert "ep_quant" in out and "E/P" in out    # key to type + display name
        assert "highlight=" in out and "filter=" in out

    def test_lower_is_better_axis_hint(self, calendar_pre):
        fig = scatter(calendar_pre, x="fhs_garch_annzd_vol_quant", y="roic_quant")
        assert "lower is better" in fig.layout.xaxis.title.text

    def test_describe_lists_served_columns(self, calendar_pre, capsys):
        rows = describe_calendar(calendar_pre)
        assert len(rows) == 31
        keys = {k for k, _, _ in rows}
        assert "ROIC_QUANT" in keys
        # pre carries no change columns
        assert not any(k.endswith("_CHANGE") for k in keys)


# --- scatter ----------------------------------------------------------------

class TestScatter:
    def test_builds_from_two_percentiles(self, calendar_pre):
        fig = scatter(calendar_pre, x="ep_quant", y="roic_quant")
        assert isinstance(fig, go.Figure)
        assert len(fig.data) >= 1

    def test_axes_labelled_and_bounded_0_100(self, calendar_pre):
        fig = scatter(calendar_pre, x="ep_quant", y="roic_quant")
        # short name plus the direction hint beneath it
        assert fig.layout.xaxis.title.text.startswith("E/P")
        assert "higher is better" in fig.layout.xaxis.title.text
        assert list(fig.layout.xaxis.range) == [0, 100]
        assert list(fig.layout.yaxis.range) == [0, 100]

    def test_unknown_axis_raises_naming_it(self, calendar_pre):
        with pytest.raises(ChartError, match="not_a_metric"):
            scatter(calendar_pre, x="ep_quant", y="not_a_metric")

    def test_pre_only_metric_absent_on_post_raises(self, calendar_post):
        # SHARPE_ANNZD_QUANT is pre-only; asking for it on a post calendar is a
        # loud, named error, not an empty chart.
        with pytest.raises(ChartError, match="SHARPE_ANNZD_QUANT|sharpe"):
            scatter(calendar_post, x="sharpe_annzd_quant", y="ep_quant")

    def test_color_by_signed_change_adds_a_colorbar_trace(self, calendar_post):
        # Colouring by a change column adds the colourbar trace, and does not blow up.
        fig = scatter(calendar_post, x="ep_quant", y="roic_quant",
                      color="ep_quant_change")
        assert len(fig.data) >= 2

    def test_color_and_highlight_together_with_null_colors(self, calendar_pre):
        # Regression: colouring by a metric that some points lack, WHILE
        # highlighting, must not feed a None into a colourscale array (Plotly
        # rejects it). Inject a null colour value to force the split path.
        payload = {**calendar_pre, "data": [dict(r) for r in calendar_pre["data"]]}
        payload["data"][0]["FCF_QUANT"] = None
        fig = scatter(payload, x="ep_quant", y="roic_quant",
                      color="fcf_quant", highlight="sp500")
        assert isinstance(fig, go.Figure)


# --- filter -----------------------------------------------------------------

class TestFilter:
    def test_sector_filter_narrows_or_empties(self, calendar_pre):
        # Filtering to a sector keeps a subset; the chart still renders either way.
        fig = scatter(calendar_pre, x="ep_quant", y="roic_quant",
                      filter=Filter.sector("Technology"))
        assert isinstance(fig, go.Figure)

    def test_composed_filter(self, calendar_pre):
        f = Filter.sector("Technology") & ~Filter.sp500()
        fig = scatter(calendar_pre, x="ep_quant", y="roic_quant", filter=f)
        assert isinstance(fig, go.Figure)

    def test_filter_keep_reads_only_row_fields(self):
        rows = [
            {"TICKER": "AAA", "INDUSTRY": "Software", "IS_S&P500": 1},
            {"TICKER": "BBB", "INDUSTRY": "Biotechnology", "IS_S&P500": 0},
        ]
        assert [r["TICKER"] for r in Filter.industry("software").keep(rows)] == ["AAA"]
        assert [r["TICKER"] for r in Filter.sp500().keep(rows)] == ["AAA"]
        assert [r["TICKER"] for r in Filter.tickers(["bbb"]).keep(rows)] == ["BBB"]

    def test_filter_state_matches_token_or_phrase(self):
        rows = [
            {"TICKER": "AAA", "VALUATION_STATE": "newly_cheap"},
            {"TICKER": "BBB", "VALUATION_STATE": "normal"},
        ]
        assert [r["TICKER"] for r in Filter.state("newly_cheap").keep(rows)] == ["AAA"]
        # a friendly phrase resolves to the same token
        assert [r["TICKER"] for r in Filter.state("newly cheap").keep(rows)] == ["AAA"]
        assert Filter.state("newly_cheap").description == "valuation: newly cheap"

    def test_bad_filter_type_raises(self, calendar_pre):
        with pytest.raises(TypeError, match="Filter"):
            scatter(calendar_pre, x="ep_quant", y="roic_quant", filter="tech")


# --- highlight --------------------------------------------------------------

class TestHighlight:
    def test_industry_highlight_splits_into_two_traces_when_both_present(
            self, calendar_pre):
        # Pick an industry that exists in the fixture so both emphasis and muted
        # groups are non-empty -> two traces.
        industry = next(r["INDUSTRY"] for r in calendar_pre["data"]
                        if r.get("INDUSTRY"))
        # only splits into two if not everyone shares that industry
        fig = scatter(calendar_pre, x="ep_quant", y="roic_quant",
                      highlight=industry)
        assert isinstance(fig, go.Figure)

    def test_ticker_list_highlight(self, calendar_pre):
        some = [r["TICKER"] for r in calendar_pre["data"][:2]]
        fig = scatter(calendar_pre, x="ep_quant", y="roic_quant", highlight=some)
        assert isinstance(fig, go.Figure)

    def test_bad_highlight_type_raises(self, calendar_pre):
        with pytest.raises(TypeError, match="highlight"):
            scatter(calendar_pre, x="ep_quant", y="roic_quant", highlight=42)

    def test_highlight_by_valuation_state(self, calendar_pre):
        # a state token as highlight resolves to the state filter, not an industry
        from earningspy_glass.filters import resolve_highlight
        member = resolve_highlight("newly_cheap")
        assert member and member[1] == "valuation: newly cheap"
        fig = scatter(calendar_pre, x="ep_quant", y="roic_quant",
                      highlight="newly_cheap")
        assert isinstance(fig, go.Figure)


class TestValuationState:
    def test_hover_shows_valuation_block(self, calendar_pre):
        row = next(r for r in calendar_pre["data"] if r.get("VALUATION_STATE"))
        h = _hover(calendar_pre, row, ["ep_quant"])
        assert "Valuation" in h
        # the state renders as a reader phrase, not the raw token
        assert "State:" in h.replace("&nbsp;", " ")
        assert "_cheap" not in h            # no raw underscore token leaked

    def test_state_label_maps_tokens_to_phrases(self):
        from earningspy_glass.filters import state_label
        assert state_label("newly_cheap") == "newly cheap"
        assert state_label("still_cheap") == "still cheap"
        assert state_label("exiting") == "exiting cheap"
        assert state_label("normal") == "fairly valued"
        # an unknown state degrades to its own tidied words, not dropped
        assert state_label("some_new_state") == "some new state"


# --- bar_pctile -------------------------------------------------------------

class TestBarPctile:
    def test_ranks_and_renders(self, calendar_pre):
        fig = bar_pctile(calendar_pre, metric="roic_quant", top=5)
        assert isinstance(fig, go.Figure)
        assert len(fig.data[0].y) <= 5

    def test_lower_is_better_ranks_low_first(self, calendar_pre):
        # For a risk metric, the "top" of the ranking is the LOW percentile end;
        # read orientation, don't assume descending. The strongest (lowest risk)
        # bar sits at the top of the chart (last in the drawn, reversed order).
        fig = bar_pctile(calendar_pre, metric="fhs_garch_annzd_vol_quant", top=5)
        vals = list(fig.data[0].x)
        # drawn bottom-to-top => the topmost bar is the strongest = lowest value
        assert vals[-1] == min(vals)


# --- change_scatter (post only) ---------------------------------------------

class TestChangeScatter:
    def test_builds_on_post(self, calendar_post):
        fig = change_scatter(calendar_post, metric="ep_quant")
        assert isinstance(fig, go.Figure)

    def test_pairs_level_with_its_change_axis(self, calendar_post):
        fig = change_scatter(calendar_post, metric="ep_quant")
        assert "now" in fig.layout.xaxis.title.text.lower()
        assert "move" in fig.layout.yaxis.title.text.lower()

    def test_refuses_pre_frame_with_a_helpful_message(self, calendar_pre):
        with pytest.raises(ChartError, match="post calendar"):
            change_scatter(calendar_pre, metric="ep_quant")

    def test_accepts_the_change_column_name_directly(self, calendar_post):
        fig = change_scatter(calendar_post, metric="ep_quant_change")
        assert isinstance(fig, go.Figure)


# --- dispatch ---------------------------------------------------------------

class TestDispatch:
    def test_plot_dispatches_each_kind(self, calendar_pre):
        assert isinstance(plot_calendar(calendar_pre, "scatter",
                                        x="ep_quant", y="roic_quant"), go.Figure)
        assert isinstance(plot_calendar(calendar_pre, "bar_pctile",
                                        metric="roic_quant"), go.Figure)
        assert isinstance(plot_calendar(calendar_pre, "treemap",
                                        color="roic_quant"), go.Figure)
        assert isinstance(plot_calendar(calendar_pre, "donut",
                                        mode="sector"), go.Figure)
        assert isinstance(plot_calendar(calendar_pre, "radar",
                                        metrics=["roic_quant", "ep_quant",
                                                 "eps_surprise_quant"]), go.Figure)

    def test_unknown_kind_raises(self, calendar_pre):
        with pytest.raises(ChartError, match="no 'wombat' calendar chart"):
            plot_calendar(calendar_pre, "wombat")


# --- hover ------------------------------------------------------------------

class TestHover:
    def test_report_status_is_the_services(self, calendar_pre):
        row = calendar_pre["data"][0]
        fact = calendar_pre["facts"]["rows"][f"{row['TICKER']}|{row['EARNINGS_DATE'][:10]}"]
        for known, words in ((True, "REPORTED"), (False, "NOT REPORTED"), (None, "REPORT NOT CONFIRMED")):
            fact["reported"] = known
            assert _report_status(calendar_pre, {**row, "DAYS_LEFT": -3}) == (known, words)

    def test_a_row_the_service_says_nothing_about_is_not_guessed(self, calendar_pre):
        assert _report_status(calendar_pre, {"TICKER": "X", "DAYS_LEFT": 3}) == (None, "REPORT NOT CONFIRMED")

    def test_hover_status_line_has_date_inline_and_no_bullet(self, calendar_pre):
        row = {**calendar_pre["data"][0], "DAYS_LEFT": 5,
               "EARNINGS_DATE": "2026-10-01T00:00:00"}
        calendar_pre["facts"]["rows"][f"{row['TICKER']}|2026-10-01"] = {"reported": False}
        h = _hover(calendar_pre, row, ["ep_quant"])
        assert "<b>NOT REPORTED</b>" in h
        assert " on 2026-10-01" in h          # date inline, with "on"
        assert "in 5d" in h
        assert "○" not in h and "✓" not in h  # bullet removed

    def test_quality_block_heading_names_the_tier(self, calendar_pre):
        # calendar_pre carries QUALITY_* columns (silver payload).
        h = _hover(calendar_pre, calendar_pre["data"][0], ["ep_quant"])
        assert "Quality read (silver)" in h

    def test_scatter_subtitle_says_industry_percentile(self, calendar_pre):
        fig = scatter(calendar_pre, x="ep_quant", y="roic_quant")
        assert "Industry percentile" in fig.layout.title.text

    def test_scatter_axis_uses_short_name_with_direction_hint(self, calendar_pre):
        fig = scatter(calendar_pre, x="ep_quant", y="roic_quant")
        title = fig.layout.xaxis.title.text
        assert title.startswith("E/P")                    # short name
        assert "higher is better" in title                # direction hint
        assert "industry percentile" not in title.lower()  # not the long label

    def test_hover_uses_short_names(self, calendar_pre):
        h = _hover(calendar_pre, calendar_pre["data"][0], ["ep_quant"])
        assert "<b>E/P:" in h                       # short name (E/P), not the long label
        # the encoded row leads before the percentile-block heading
        assert h.index("<b>E/P:") < h.index("industry percentile")

    def test_hover_has_industry_percentile_heading(self, calendar_pre):
        h = _hover(calendar_pre, calendar_pre["data"][0], ["ep_quant"])
        assert "industry percentile" in h           # the block heading

    def test_encoded_row_pairs_raw_value_with_percentile(self, calendar_pre):
        # The fixture carries raw fields, so an encoded ROIC row shows the raw
        # value (a percent) AND its percentile as a greyed rank.
        import re
        h = _hover(calendar_pre, calendar_pre["data"][0], ["roic_quant"])
        row = [l for l in h.split("<br>") if l.startswith("<b>ROIC:")][0]
        assert "%" in row                           # the raw value, formatted
        assert re.search(r"\d+ p</span>", row)      # the percentile, greyed

    def test_packed_cell_pairs_raw_and_rank(self, calendar_pre):
        # A non-encoded metric in the packed block carries "raw NNp".
        from earningspy_glass.calendar_charts import _cell
        cell = _cell(calendar_pre, calendar_pre["data"][0], "ROA_QUANT")
        assert cell.startswith("ROA: ")
        assert cell.rstrip().endswith("p")          # ends in the NNp rank token

    def test_raw_fields_are_not_plottable_axes(self, calendar_pre):
        # The raw values are in the metrics table but must not appear as x/y
        # options — only percentile columns are plottable.
        keys = {k for k, _, _ in calendar_metrics(calendar_pre)}
        assert "ROIC_QUANT" in keys
        assert "ROIC" not in keys                    # the raw twin, excluded
        assert all(k.endswith(("_QUANT", "_QUANT_CHANGE")) or k.startswith("PRE_")
                   for k in keys)


# --- treemap ----------------------------------------------------------------

class TestTreemap:
    def test_builds_sized_and_coloured(self, calendar_pre):
        fig = treemap(calendar_pre, color="roic_quant")
        assert isinstance(fig, go.Figure)
        assert len(fig.data[0].labels) >= 1

    def test_unknown_color_raises(self, calendar_pre):
        with pytest.raises(ChartError, match="not_a_metric"):
            treemap(calendar_pre, color="not_a_metric")


# --- radar ------------------------------------------------------------------

class TestRadar:
    def test_orients_every_spoke_higher_is_better(self, calendar_pre):
        # A lower-is-better column is flipped so the outer ring is best; read the
        # flip from the payload, don't assume it.
        v = _oriented(calendar_pre, "FHS_GARCH_ANNZD_VOL_QUANT", 30.0)
        assert v == 70.0                              # lower-is-better -> 100 - 30
        assert _oriented(calendar_pre, "ROIC_QUANT", 30.0) == 30.0   # higher -> as-is
        assert _oriented(calendar_pre, "INST_OWN_QUANT", 30.0) == 30.0  # directionless

    def test_builds_for_a_few_tickers(self, calendar_pre):
        picks = [r["TICKER"] for r in calendar_pre["data"][:2]]
        fig = radar(calendar_pre, tickers=picks,
                    metrics=["roic_quant", "ep_quant", "eps_surprise_quant"])
        # one clean polygon per ticker — no decorative extra traces.
        assert len(fig.data) == 2
        assert all(d.name in picks for d in fig.data)

    def test_polygon_edges_are_straight_not_splined(self, calendar_pre):
        # A radar's segments must be honest straight lines — a spline would invent
        # values between the axes. So no line.shape override (defaults to linear).
        picks = [r["TICKER"] for r in calendar_pre["data"][:2]]
        fig = radar(calendar_pre, tickers=picks,
                    metrics=["roic_quant", "ep_quant", "eps_surprise_quant"])
        assert fig.data[0].line.shape in (None, "linear")

    def test_too_few_metrics_renders_a_note_not_a_crash(self, calendar_pre):
        fig = radar(calendar_pre, metrics=["roic_quant", "ep_quant"])
        assert isinstance(fig, go.Figure)   # empty-note figure, not an exception


# --- donut ------------------------------------------------------------------

class TestDonut:
    def test_sector_composition(self, calendar_pre):
        fig = donut(calendar_pre, mode="sector")
        assert isinstance(fig, go.Figure)
        assert sum(fig.data[0].values) == len(calendar_pre["data"])

    def test_industry_composition(self, calendar_pre):
        fig = donut(calendar_pre, mode="industry")
        assert isinstance(fig, go.Figure)

    def test_market_cap_share(self, calendar_pre):
        fig = donut(calendar_pre, mode="MARKET_CAP")
        assert isinstance(fig, go.Figure)


# --- bar_signed (post only) -------------------------------------------------

class TestBarSigned:
    def test_builds_on_post(self, calendar_post):
        fig = bar_signed(calendar_post, metric="ep_quant", top=10)
        assert isinstance(fig, go.Figure)
        assert len(fig.data[0].y) <= 10

    def test_keeps_the_largest_moves_biggest_gain_on_top(self, calendar_post):
        fig = bar_signed(calendar_post, metric="ep_quant", top=5, by=None)
        vals = list(fig.data[0].x)   # drawn bottom-to-top: the biggest gain at the top
        assert vals == sorted(vals)
        every = sorted((abs(r["EP_QUANT_CHANGE"]) for r in calendar_post["data"]
                        if r.get("EP_QUANT_CHANGE") is not None), reverse=True)
        assert sorted((abs(v) for v in vals), reverse=True) == every[:5]

    def test_refuses_pre_frame(self, calendar_pre):
        with pytest.raises(ChartError, match="post calendar"):
            bar_signed(calendar_pre, metric="ep_quant")


# --- format: the percentile_points trap -------------------------------------

class TestFormat:
    def test_percentile_points_do_not_multiply(self):
        # The whole reason the format token exists: 32.1 renders as 32.1, not 3210%.
        from earningspy_glass.graph_client import format_value
        assert format_value(32.1, "percentile_points") == "32.1"
        assert format_value(0.8, "percentile_points_signed") == "+0.8"
        assert format_value(-9.4, "percentile_points_signed") == "-9.4"

    def test_raw_value_formatters(self):
        # The raw underlying values arrive with these formats; each must render
        # with the right unit and abbreviation.
        from earningspy_glass.graph_client import format_value
        assert format_value(-0.0631, "percent_fraction") == "-6.3%"
        assert format_value(-0.0068, "ratio") == "-0.01"
        assert format_value(-0.30, "currency_per_share") == "-$0.30"
        assert format_value(123484848, "currency") == "$123.48M"
        assert format_value(1360000000, "currency") == "$1.36B"
        assert format_value(-2300000000, "currency") == "-$2.30B"
        assert format_value(1580000, "count") == "1.58M"
