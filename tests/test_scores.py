"""The tier scores on a calendar and a timeline: the EarningSpy Rank, conviction
and the abnormal-move probability arrive as 0–1 fractions described in the
payload's metrics table. They must plot beside the percentiles and read on hover
in their own label and format.

Offline: the recorded fixtures plus the three metric entries exactly as the API
describes them on the silver and gold routes.
"""
import copy
import re

import pytest

from earningspy_glass.calendar_charts import (
    bar_pctile,
    calendar_metrics,
    radar,
    scatter,
    _hover,
    _short,
)
from earningspy_glass.filters import Filter, earnings_week
from earningspy_glass.timeline_charts import bars, _row_hover

SCORE_METRICS = {
    "SILVER_PCTL": {"label": "EarningSpy Rank", "higher_is_better": True,
                    "format": "percent", "scale": "linear", "color_clip_pct": None,
                    "ranked_within": "earnings_week"},
    "GOLD_CONVICTION": {"label": "Conviction", "higher_is_better": True,
                        "format": "percent", "scale": "linear", "color_clip_pct": None,
                        "ranked_within": "earnings_week"},
    "GOLD_IS_ABNORMAL_PROB": {"label": "Abnormality Probability",
                              "higher_is_better": None, "format": "percent",
                              "scale": "linear", "color_clip_pct": None},
}


def _plain(hover):
    """The hover as plain text, padding collapsed, so a cell reads 'A: B'."""
    return re.sub(r"\s+", " ", hover.replace("&nbsp;", " "))


def _with_scores(payload):
    scored = copy.deepcopy(payload)
    scored["metrics"] = {**scored.get("metrics", {}), **SCORE_METRICS}
    for i, row in enumerate(scored["data"]):
        row["SILVER_PCTL"] = round(0.1 + 0.1 * (i % 9), 2)
        row["GOLD_CONVICTION"] = 0.8 if i == 0 else None
        row["GOLD_IS_ABNORMAL_PROB"] = 0.7
    return scored


@pytest.fixture
def cal(calendar_pre):
    return _with_scores(calendar_pre)


@pytest.fixture
def tl(timeline_pre_quant):
    return _with_scores(timeline_pre_quant)


class TestCalendar:
    def test_scores_are_plottable(self, cal):
        keys = {key for key, _, _ in calendar_metrics(cal)}
        assert {"SILVER_PCTL", "GOLD_CONVICTION", "GOLD_IS_ABNORMAL_PROB"} <= keys

    def test_a_score_sits_on_the_percentile_axis(self, cal):
        fig = scatter(cal, x="silver_pctl", y="roic_quant")
        xs = [x for trace in fig.data for x in (trace.x or ()) if x is not None]
        assert xs and max(xs) > 1.0          # 0.9 placed at 90, not at 0.9
        assert "score" in fig.layout.title.text.lower() or "score" in str(fig.layout)

    def test_a_score_ranks_on_its_own(self, cal):
        fig = bar_pctile(cal, metric="silver_pctl", top=5)
        top = max(row["SILVER_PCTL"] for row in cal["data"])
        assert max(fig.data[0].x) == pytest.approx(top * 100)
        assert "EarningSpy Rank" in fig.layout.title.text

    def test_a_score_reads_in_its_own_format_on_hover(self, cal):
        row = cal["data"][0]
        hover = _plain(_hover(cal, row, ["ROIC_QUANT"]))
        assert "EarningSpy Rank: 10.0%" in hover
        assert "Conviction: 80.0%" in hover
        assert "Abnormality Probability: 70.0%" in hover
        assert "Gold Conviction" not in hover   # not repeated as a raw key

    def test_an_encoded_score_carries_no_percentile_mark(self, cal):
        hover = _hover(cal, cal["data"][0], ["SILVER_PCTL"])
        assert "<b>EarningSpy Rank: 10.0%</b>" in hover

    def test_an_encoded_raw_measure_carries_no_percentile_mark(self, cal):
        sized = copy.deepcopy(cal)
        sized["metrics"]["MARKET_CAP"] = {"label": "Market Cap", "format": "currency",
                                          "higher_is_better": None}
        hover = _hover(sized, sized["data"][0], ["MARKET_CAP"])
        assert "<b>Market Cap: " in hover
        assert "> p</span>" not in hover

    def test_score_short_name_is_its_label(self, cal):
        assert _short(cal, "GOLD_CONVICTION") == "Conviction"

    def test_radar_takes_an_oriented_score(self, cal):
        fig = radar(cal, metrics=["ROIC_QUANT", "EP_QUANT", "SILVER_PCTL"])
        assert "EarningSpy Rank" in list(fig.data[0].theta)


def _spread(cal, days):
    """The same calendar with its last company reporting `days` after the first."""
    wide = copy.deepcopy(cal)
    for row in wide["data"]:
        row["EARNINGS_DATE"] = "2026-04-07T00:00:00"
    wide["data"][-1]["EARNINGS_DATE"] = f"2026-04-{7 + days:02d}T00:00:00"
    return wide


class TestWeekRankNote:
    # The fixture reports on Tue 2026-04-07; its earnings week is Mon 04-06 to Sun 04-12.
    def test_one_week_says_how_it_is_ranked(self, cal):
        fig = scatter(_spread(cal, 5), x="ep_quant", y="silver_pctl")   # to Sunday
        assert "ranked within its earnings week" in fig.layout.title.text

    def test_several_weeks_plot_and_say_so(self, cal):
        fig = bar_pctile(_spread(cal, 20), metric="silver_pctl")
        assert fig.data
        assert "ranked within each earnings week (2 weeks shown)" in fig.layout.title.text

    def test_a_week_filter_narrows_it(self, cal):
        fig = bar_pctile(_spread(cal, 6), metric="silver_pctl",
                         filter=Filter.week("2026-04-07"))
        assert "ranked within its earnings week" in fig.layout.title.text

    def test_a_week_ranked_colour_says_so(self, cal):
        fig = scatter(_spread(cal, 8), x="ep_quant", y="roic_quant",
                      color="gold_conviction")
        assert "2 weeks shown" in fig.layout.title.text

    def test_radar_spokes_say_so(self, cal):
        wide = _spread(cal, 8)
        pair = [wide["data"][0]["TICKER"], wide["data"][-1]["TICKER"]]
        fig = radar(wide, tickers=pair, metrics=["ROIC_QUANT", "EP_QUANT", "SILVER_PCTL"])
        assert "2 weeks shown" in fig.layout.title.text

    def test_a_probability_is_not_a_rank(self, cal):
        fig = bar_pctile(_spread(cal, 20), metric="gold_is_abnormal_prob")
        assert "ranked within" not in fig.layout.title.text

    def test_percentiles_carry_no_rank_note(self, cal):
        fig = scatter(_spread(cal, 20), x="ep_quant", y="roic_quant")
        assert "ranked within" not in fig.layout.title.text


class TestEarningsWeek:
    def test_monday_opens_the_week(self):
        assert earnings_week("2026-11-08T00:00:00") == earnings_week("2026-11-02")
        assert earnings_week("2026-11-01") != earnings_week("2026-11-02")

    def test_unreadable_dates_are_none(self):
        assert earnings_week(None) is None and earnings_week("n/a") is None


class TestTimeline:
    def test_a_score_plots_over_time(self, tl):
        fig = bars(tl, "SILVER_PCTL")
        assert fig.data

    def test_tier_block_uses_label_and_format(self, tl):
        row = tl["data"][0]
        hover = _plain(_row_hover(tl, "2026/Q1", row, ["EPS"]))
        assert "Conviction: 80.0%" in hover
        assert "Abnormality Probability: 70.0%" in hover

    def test_a_pre_report_read_is_named_by_its_label(self, tl):
        from earningspy_glass.timeline_charts import _short as tl_short
        read = copy.deepcopy(tl)
        read["metrics"]["PRE_IS_ABNORMAL_PROB"] = {
            "label": "Abnormality Probability (pre-event)", "format": "percent_fraction"}
        assert tl_short(read, "PRE_IS_ABNORMAL_PROB") == "Abnormality Probability (pre-event)"

    def test_a_carried_percentile_keeps_its_short_name(self, tl):
        from earningspy_glass.timeline_charts import _short as tl_short
        carried = copy.deepcopy(tl)
        carried["metrics"]["PRE_ROIC_QUANT"] = {"label": "ROIC pre-event industry percentile",
                                                "format": "percentile"}
        assert tl_short(carried, "PRE_ROIC_QUANT") == "ROIC pctile"
