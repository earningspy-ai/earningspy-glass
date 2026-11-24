"""Calendar and timeline figures carry their recipe — the window's dates, the view,
the tier, the chart and its options, a filter as data — so EarningSpy Studio can
render the same chart. Filters write themselves as data and rebuild from it."""
import json

import pytest

from earningspy_glass import Filter, recipe
from earningspy_glass.calendar_charts import bar_pctile
from earningspy_glass.client import Calendar, Timeline


@pytest.fixture
def cal(calendar_pre):
    payload = dict(calendar_pre)
    payload.update(view="calendar_quant", tier="silver",
                   window={"scope": "week", "start": "2026-04-06", "end": "2026-04-13"})
    return Calendar(payload)


# --- filters as data -------------------------------------------------------------

class TestFilterData:
    def test_each_constructor_writes_itself(self):
        assert Filter.industry("Software").to_dict() == {"industry": "Software"}
        assert Filter.sector("Technology").to_dict() == {"sector": "Technology"}
        assert Filter.sp500().to_dict() == {"sp500": True}
        assert Filter.tickers(["zznv", "ZZAD"]).to_dict() == {"tickers": ["ZZAD", "ZZNV"]}
        assert Filter.state("newly cheap").to_dict() == {"state": "newly_cheap"}
        assert Filter.week("2026-11-05").to_dict() == {"week": "2026-11-02"}

    def test_chains_flatten(self):
        f = Filter.sector("Technology") & Filter.sp500() & Filter.week("2026-11-02")
        assert f.to_dict() == {"all": [{"sector": "Technology"}, {"sp500": True},
                                       {"week": "2026-11-02"}]}
        g = ~(Filter.industry("Software") | Filter.tickers(["ZZAD"]))
        assert g.to_dict() == {"not": {"any": [{"industry": "Software"}, {"tickers": ["ZZAD"]}]}}

    def test_round_trip_keeps_the_same_companies(self, calendar_pre):
        rows = calendar_pre["data"]
        f = (Filter.sp500() | Filter.tickers([rows[0]["TICKER"]])) & ~Filter.industry("Banks - Regional")
        back = Filter.from_dict(json.loads(json.dumps(f.to_dict())))
        assert back.keep(rows) == f.keep(rows)
        assert back.description == f.description

    def test_a_custom_test_draws_but_does_not_travel(self):
        custom = Filter(lambda row: True, "my screen")
        assert not custom.travels
        assert not (custom & Filter.sp500()).travels
        with pytest.raises(ValueError, match="custom test"):
            custom.to_dict()

    @pytest.mark.parametrize("bad", [{}, {"colour": "red"}, {"all": [{"sp500": True}]},
                                     {"tickers": "ZZAD"}, {"sector": ""}, {"week": "soon"}])
    def test_bad_data_is_refused_in_words(self, bad):
        with pytest.raises(ValueError):
            Filter.from_dict(bad)

    def test_charts_take_the_data_form(self, calendar_pre):
        some = [r["TICKER"] for r in calendar_pre["data"][:4]]
        as_object = bar_pctile(calendar_pre, metric="ep_quant", filter=Filter.tickers(some))
        as_data = bar_pctile(calendar_pre, metric="ep_quant", filter={"tickers": some},
                             highlight={"tickers": some[:1]})
        assert list(as_data.data[0].y) == list(as_object.data[0].y)
        assert "tickers:" in as_data.layout.title.text


# --- calendar recipes ------------------------------------------------------------

class TestCalendarRecipe:
    def test_the_window_view_tier_and_chart_travel(self, cal):
        r = recipe(cal.plot("scatter", x="ep_quant", y="roic_quant"))
        assert r == {"graph": "calendar", "frame": "pre", "tier": "silver",
                     "view": "calendar_quant",
                     "window": {"scope": "week", "start": "2026-04-06", "end": "2026-04-13"},
                     "kind": "scatter", "params": {"x": "ep_quant", "y": "roic_quant"}}

    def test_a_filter_and_highlight_travel_as_data(self, cal):
        f = Filter.sector("Technology") & Filter.sp500()
        r = recipe(cal.plot("bar_pctile", metric="ep_quant", filter=f,
                            highlight=Filter.week("2026-04-07")))
        assert r["params"]["filter"] == {"all": [{"sector": "Technology"}, {"sp500": True}]}
        assert r["params"]["highlight"] == {"week": "2026-04-06"}
        assert recipe(json.loads(cal.plot("bar_pctile", metric="ep_quant",
                                          filter=f).to_json()))["params"]["filter"] == f.to_dict()

    def test_a_custom_filter_is_named_not_dropped(self, cal):
        r = recipe(cal.plot("bar_pctile", metric="ep_quant",
                            filter=Filter(lambda row: True, "my screen")))
        assert r["params"]["filter"] == {"custom": "my screen"}

    def test_no_rows_ride_along(self, cal):
        r = recipe(cal.plot("bar_pctile", metric="ep_quant"))
        assert "TICKER" not in json.dumps(r)


# --- timeline recipes ------------------------------------------------------------

class TestTimelineRecipe:
    def test_bars(self, timeline_pre_quant):
        r = recipe(Timeline(timeline_pre_quant).bars(["EPS"], show_dates=True))
        assert r == {"graph": "timeline", "ticker": "ZZAP", "frame": "pre",
                     "view": "calendar_quant", "tier": "gold", "kind": "bars",
                     "params": {"measures": ["EPS"], "show_dates": True,
                                "theme": "light", "regime": False, "last": 5}}

    def test_compare_names_the_frame_it_was_opened_on(self, timeline_pre, timeline_post):
        r = recipe(Timeline(timeline_post, _fetch_other=lambda: timeline_pre).compare("EPS"))
        assert (r["kind"], r["frame"], r["params"]["measure"]) == ("compare", "post", "EPS")

    def test_radar(self, timeline_pre_quant):
        r = recipe(Timeline(timeline_pre_quant).radar(quarters=2))
        assert r["kind"] == "radar" and r["params"]["quarters"] == 2
