"""The small parts: what a chart works out for itself, filters, the market lines, the
notebook defaults and the public catalogue."""
import importlib
import math
import random
import sys
import types

import pytest
import requests

import earningspy_glass as g
from earningspy_glass import _defaults, _derived, regime
from earningspy_glass.filters import _truthy_flag, earnings_week, resolve_highlight, state_label

catalog_module = importlib.import_module("earningspy_glass.catalog")


class TestDerived:
    @pytest.mark.parametrize("seed", range(5))
    def test_quantile_agrees_with_the_standard_library(self, seed):
        import statistics
        values = [random.Random(seed).gauss(0, 1) for _ in range(31)]
        cuts = statistics.quantiles(values, n=20, method="inclusive")
        for i, cut in enumerate(cuts, 1):
            assert math.isclose(_derived.quantile(values, i / 20), cut)

    def test_quantile_edges(self):
        assert _derived.quantile([], 0.5) is None and _derived.quantile([None], 0.5) is None
        assert _derived.quantile([3, None], 0.9) == 3

    def test_outliers_on_either_axis_most_extreme_first(self):
        rows = [(f"T{i}", float(i), float(i)) for i in range(10)] + [("FAR", 90.0, 5.0), ("FARTHER", 5.0, 900.0)]
        assert _derived.outlier_tickers(rows)[0] == "FARTHER" and "FAR" in _derived.outlier_tickers(rows)
        assert _derived.outlier_tickers(rows, cap=1) == ["FARTHER"]

    def test_too_few_or_all_equal_have_no_outliers(self):
        assert _derived.outlier_tickers([("A", 1, 1), ("B", 99, 1)]) == []
        assert _derived.outlier_tickers([(str(i), 5, 5) for i in range(9)]) == []

    def test_a_log_axis_ignores_what_it_cannot_place(self):
        rows = [(str(i), 10.0 ** (i % 3), 1.0 + i) for i in range(8)] + [("NEG", -5.0, 2.0), ("NONE", None, None)]
        assert "NEG" not in _derived.outlier_tickers(rows, log_x=True)


class TestFilters:
    @pytest.mark.parametrize("value,week", [("2026-11-04T10:00:00", "2026-11-02"), (None, None), ("soon", None),
                                            (__import__("datetime").datetime(2026, 11, 8, 3), "2026-11-02")])
    def test_earnings_week(self, value, week):
        assert (earnings_week(value).isoformat() if week else earnings_week(value)) == (week or None)

    @pytest.mark.parametrize("value,on", [(1, True), (1.0, True), ("1", True), ("True", True), (True, True),
                                          (0, False), ("0", False), (None, False), ([], False)])
    def test_flags_in_any_form(self, value, on):
        assert _truthy_flag(value) is on

    @pytest.mark.parametrize("bad", [None, {}, {"sector": "a", "sp500": True}, {"all": []}, {"any": "x"}, {"sp500": 1},
                                     {"tickers": "AAPL"}, {"tickers": []}, {"planet": "x"}, {"sector": " "}, {"week": 3}])
    def test_a_broken_data_form_is_refused(self, bad):
        with pytest.raises(ValueError):
            g.Filter.from_dict(bad)

    def test_the_algebra_round_trips_as_data(self):
        f = (g.Filter.sector("Tech") & g.Filter.sp500() & ~g.Filter.tickers(["AA"])) | g.Filter.state("newly cheap")
        again = g.Filter.from_dict(f.to_dict())
        rows = [{"SECTOR": "tech", "IS_S&P500": 1, "TICKER": "BB"}, {"SECTOR": "tech", "IS_S&P500": 1, "TICKER": "AA"},
                {"SECTOR": "x", "VALUATION_STATE": "NEWLY_CHEAP"}, {}]
        assert again.keep(rows) == f.keep(rows) == [rows[0], rows[2]]
        assert again.matches(rows[0]) and "Filter" in repr(again)

    def test_a_custom_test_draws_but_does_not_travel(self):
        custom = g.Filter(lambda row: True)
        assert not custom.travels and not (custom & g.Filter.sp500()).travels and not (~custom).travels
        with pytest.raises(ValueError, match="custom test"):
            custom.to_dict()

    def test_week_needs_a_date(self):
        with pytest.raises(ValueError, match="2026-11-02"):
            g.Filter.week("soon")

    def test_long_ticker_lists_are_counted(self):
        assert "9 chosen" in g.Filter.tickers([str(i) for i in range(9)]).description

    @pytest.mark.parametrize("value", ["sp500", "Software", "newly_cheap", ["A"], {"A"}, {"sector": "X"}])
    def test_highlight_forms(self, value):
        member, label = resolve_highlight(value)
        assert callable(member) and label

    def test_highlight_of_nothing_and_of_nonsense(self):
        assert resolve_highlight(None) is None
        with pytest.raises(TypeError, match="highlight"):
            resolve_highlight(3)

    def test_an_unknown_state_keeps_its_own_words(self):
        assert state_label("brand_new") == "brand new"


class TestRegime:
    FULL = {"MARKET_STATE": "ELEVATED", "VIX_TERM": "FLAT", "VIX_PRE_2W": 21.04, "EARNING_VIX": 30.0}

    def test_a_line_reads_every_part(self):
        assert regime.line(self.FULL) == ("Market 2 wks before report: mood Elevated · VIX curve Flat · VIX 21.0"
                                          " — VIX on report day 30.0")
        assert "latest close" in regime.line(self.FULL, reported=False)

    @pytest.mark.parametrize("row", [None, {}, {"MARKET_STATE": None, "VIX_PRE_2W": None}])
    def test_nothing_read_says_nothing(self, row):
        assert regime.line(row) is None and regime.reading(row) is None

    def test_parts_alone(self):
        assert regime.line({"VIX_PRE_2W": 15.0}) == "Market 2 wks before report: VIX 15.0"
        assert regime.hover_text({}) == "" and regime.short_text({"MARKET_STATE": "CALM"}) == "Calm"
        assert regime.hover_text(self.FULL).count("<br>") == 3
        assert regime.hover_text({"MARKET_STATE": "NEW", "VIX_TERM": "ODD"}).count("NEW") == 1

    def test_attach_joins_on_the_report_date(self):
        out = regime.attach({"data": [{"EARNINGS_DATE": "2026-11-02T00:00"}, {"EARNINGS_DATE": "2026-08-01"}]},
                            {"data": [{"EARNINGS_DATE": "2026-11-02", **self.FULL}]})
        assert out["data"][0]["VIX_PRE_2W"] == 21.04 and out["data"][1]["MARKET_STATE"] is None
        assert regime.carried(out["data"]) and not regime.carried([{}])

    def test_the_window_counts_states_known_and_new(self):
        rows = [{"MARKET_STATE": s} for s in ("CALM", "CALM", "ELEVATED", "PANIC", None)]
        assert regime.window_summary(rows) == ("Market 2 weeks pre-report: Elevated 1 · Calm 2 · PANIC 1 · 1 not read yet")
        assert regime.window_summary([{}]) is None and regime.window_summary([{"MARKET_STATE": None}]) is None

    def test_an_unknown_state_has_a_colour(self):
        from earningspy_glass.graph_client import _THEMES
        assert all(regime.state_color(s, _THEMES["light"]) for s in ("CALM", "NORMAL", "ELEVATED", "PANIC"))


class TestNotebookDefaults:
    @pytest.fixture
    def kernel(self, monkeypatch):
        shell = types.SimpleNamespace(config={"IPKernelApp": {}})
        monkeypatch.setitem(sys.modules, "IPython", types.SimpleNamespace(get_ipython=lambda: shell))
        import plotly.io as pio
        before = pio.renderers.default
        yield pio
        pio.renderers.default = before

    def test_charts_go_inline_in_vs_code(self, kernel, monkeypatch):
        monkeypatch.setenv("VSCODE_PID", "1")
        kernel.renderers.default = "browser"
        _defaults._inline_charts()
        assert kernel.renderers.default == "vscode"

    def test_elsewhere_plotly_decides(self, kernel, monkeypatch):
        for k in [k for k in __import__("os").environ if k.startswith("VSCODE_")]:
            monkeypatch.delenv(k)
        kernel.renderers.default = "browser"
        _defaults._inline_charts()
        assert kernel.renderers.default == "browser"

    def test_a_plain_python_is_not_a_notebook(self, monkeypatch):
        monkeypatch.setitem(sys.modules, "IPython", None)
        assert not _defaults._in_notebook_kernel()
        monkeypatch.setitem(sys.modules, "IPython", types.SimpleNamespace(get_ipython=lambda: None))
        assert not _defaults._in_notebook_kernel()


class TestPublicCatalogue:
    @pytest.fixture(autouse=True)
    def _fresh(self):
        catalog_module._CACHE.clear()
        yield
        catalog_module._CACHE.clear()

    def serve(self, monkeypatch, body=None, error=None):
        asked = []

        def get(url, timeout=None, headers=None):
            asked.append((url, headers))
            if error:
                raise error
            return types.SimpleNamespace(raise_for_status=lambda: None, json=lambda: body)

        monkeypatch.setattr(catalog_module.requests, "get", get)
        return asked

    def test_read_once_without_a_key(self, monkeypatch, capsys):
        asked = self.serve(monkeypatch, {"version": "1", "fields": [{"name": "ROIC", "group": "x", "tier": "bronze"}]})
        assert g.catalog() and g.EarningSpy(api_key="k").catalog("roic")
        assert len(asked) == 1 and asked[0][0] == "https://earningspy.ai/api/catalog.json"
        assert "x-api-key" not in asked[0][1]

    @pytest.mark.parametrize("error", [requests.ConnectionError("x"), requests.Timeout("x"), ValueError("x")])
    def test_a_failure_is_ours(self, monkeypatch, error):
        self.serve(monkeypatch, error=error)
        with pytest.raises(g.ServiceError, match="field catalogue"):
            g.catalog()

    def test_something_else_at_that_address(self, monkeypatch):
        self.serve(monkeypatch, body=["not", "a", "catalogue"])
        with pytest.raises(g.ServiceError):
            g.catalog()


def test_the_version_of_a_tree_never_installed(monkeypatch):
    import importlib.metadata as md
    import earningspy_glass._version as v

    def missing(name):
        raise md.PackageNotFoundError(name)

    real = md.version
    monkeypatch.setattr(md, "version", missing)
    assert importlib.reload(v).__version__ == "0.0.0.dev0"
    monkeypatch.setattr(md, "version", real)
    assert importlib.reload(v).__version__ == g.__version__


def test_contract_versions():
    from earningspy_glass.api import _version_tuple
    assert [_version_tuple(v) for v in (None, "x", "peers_pctile_v2", "a_vb")] == [None, None, 2, None]


# --- exact boundaries ---------------------------------------------------------------

def test_the_outlier_fence_is_one_and_a_half_ranges():
    # 0..8: quartiles 2 and 6, so the fences sit at -4 and 12.
    inside = [(str(i), float(i), 1.0) for i in range(9)]
    q = lambda v: [(t, x, 1.0) for t, x in [*((t, x) for t, x, _ in inside), ("X", v)]]     # noqa: E731
    assert _derived.outlier_tickers(q(20.0)) == ["X"] and _derived.outlier_tickers(q(11.0)) == []


def test_a_grade_band_starts_at_its_own_minimum(payload):
    from earningspy_glass.graph_client import grade_for
    assert [grade_for(p, payload, "roic") for p in (90, 89.9, 75, 60, 40, 20, 0)] == ["A+", "A", "A", "B", "C", "D", "F"]


@pytest.mark.parametrize("complete,words", [(True, "Top 10% of the week · "), (False, "Top 10% of the week so far · ")])
def test_the_weeks_cut_is_the_services(calendar_pre, complete, words):
    from glass_testkit import answer
    for row in calendar_pre["data"]:
        row.update(EARNINGS_DATE="2026-11-04")              # a midweek window: the cut is the whole week's
    cuts = {"2026-11-02": {"complete": complete, "measures": {"EP_QUANT": {"top_10": 91.0, "n": 300}}}}
    fig = g.plot_calendar(answer(calendar_pre, week_cuts=cuts), "bar_pctile", metric="ep_quant", reference="decile_10")
    (line,) = [a.text for a in fig.layout.annotations if "Top 10%" in (a.text or "")]
    assert line.startswith(words) and "91" in line


def test_a_window_of_several_weeks_has_no_cut(calendar_pre):
    from glass_testkit import answer
    from earningspy_glass.calendar_charts import _top_cut
    cuts = {"2026-11-02": {"complete": True, "measures": {"EP_QUANT": {"top_10": 91.0, "n": 300}}}}
    for row in calendar_pre["data"]:
        row["EARNINGS_DATE"] = "2026-11-04"
    answer(calendar_pre, week_cuts=cuts)
    assert _top_cut(calendar_pre, calendar_pre["data"], "EP_QUANT") == {"value": 91.0, "complete": True}
    assert _top_cut(calendar_pre, calendar_pre["data"], "ROIC_QUANT") is None
    calendar_pre["data"][0]["EARNINGS_DATE"] = "2026-11-11"
    assert _top_cut(calendar_pre, calendar_pre["data"], "EP_QUANT") is None


class TestServicesWords:
    """Notes, marks and states are the service's: their words, order and weight."""

    def test_a_note_it_adds_later_is_drawn_in_its_words(self, payload):
        from earningspy_glass import graph_client as P
        payload["metrics"]["roic"]["basis"] = "new_note"
        payload["basis"]["new_note"] = "measured over a leap year"
        assert P._label(payload, "roic") == "ROIC\u2020"
        assert P.footnote(payload, "new_note") == "\u2020 Measured over a leap year"
        assert P.named_marks(payload, ["roic"]) == ["ROIC: measured over a leap year"]

    def test_no_measure_is_marked_unless_it_says_so(self, payload):
        from earningspy_glass import graph_client as P
        payload["metrics"]["sortino"]["basis"] = None
        assert P._label(payload, "sortino") == payload["metrics"]["sortino"]["label"]

    def test_states_follow_its_order_and_weight(self, payload):
        from earningspy_glass import graph_client as P
        payload["valuation_states"] = [{"key": "normal", "label": "x", "notable": True, "strong": True},
                                       {"key": "brand_new", "label": "brand new", "notable": True, "strong": False}]
        point = {"valuation": {"VALUATION_STATE": "brand_new"}}
        assert P._state_rank(payload, point) == 1
        assert P._tag_for(payload, point, "valuation_state") == {"text": "Brand new", "strong": False}
        assert P._state_rank(payload, {"valuation": {"VALUATION_STATE": "exiting"}}) == 2
        assert P._tag_for(payload, {"valuation": {"VALUATION_STATE": "exiting"}}, "valuation_state", focus=True) is None
