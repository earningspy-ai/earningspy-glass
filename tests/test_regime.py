"""Market regime on the charts: read from the rows, captioned, never computed.

Offline, fixture-driven — the recorded timeline / calendar payloads with regime
columns added the way the API serves them (newest rows not read yet).
"""
import copy

import pytest

from earningspy_glass import ChartError
from earningspy_glass.calendar_charts import scatter
from earningspy_glass.regime import carried, hover_text, short_text, window_summary
from earningspy_glass.timeline_charts import bars

_READINGS = [  # newest first, like the payload
    {"MARKET_STATE": None, "VIX_TERM": None, "VIX_PRE_2W": None, "EARNING_VIX": None},
    {"MARKET_STATE": "CALM", "VIX_TERM": "CONTANGO", "VIX_PRE_2W": 14.8, "EARNING_VIX": 15.2},
    {"MARKET_STATE": "ELEVATED", "VIX_TERM": "BACKWARDATION", "VIX_PRE_2W": 45.3, "EARNING_VIX": 30.1},
    {"MARKET_STATE": "ELEVATED", "VIX_TERM": "FLAT", "VIX_PRE_2W": 21.0, "EARNING_VIX": 19.4},
]


def _with_regime(payload):
    p = copy.deepcopy(payload)
    for i, row in enumerate(p["data"]):
        row.update(_READINGS[i] if i < len(_READINGS) else _READINGS[1])
    return p


def _regime_trace(fig):
    return next(tr for tr in fig.data if tr.name == "VIX 2w pre-report")


def test_bars_regime_adds_a_captioned_vix_line_on_a_second_axis(timeline_pre):
    fig = bars(_with_regime(timeline_pre), "EPS", regime=True)
    tr = _regime_trace(fig)
    assert tr.yaxis == "y2" and fig.layout.yaxis2.overlaying == "y"
    values = [v for v in tr.y if v is not None]
    assert 45.3 in values and 14.8 in values
    assert "Elevated · Backwardation" in tr.text and "Calm · Contango" in tr.text
    assert "of" in fig.layout.title.text and "2 weeks before each report" in fig.layout.title.text


def test_bars_regime_without_regime_columns_fails_loudly(timeline_pre):
    with pytest.raises(ChartError, match="market regime is not on this timeline"):
        bars(timeline_pre, "EPS", regime=True)
    assert all(tr.name != "VIX 2w pre-report" for tr in bars(timeline_pre, "EPS").data)   # off by default


def test_labels_and_hover_read_the_payload():
    r = _READINGS[3]
    assert short_text(r) == "Elevated · Flat"
    h = hover_text(r)
    assert "VIX 2 weeks before report  21.0" in h and "VIX on report day          19.4" in h
    assert not carried([{"EPS": 1.0}]) and carried([r])


def test_calendar_subtitle_counts_states_and_unread(calendar_pre):
    p = copy.deepcopy(calendar_pre)
    for i, row in enumerate(p["data"]):
        row.update(_READINGS[i % 3])
    note = window_summary(p["data"])
    assert note.startswith("Market 2 weeks pre-report: Elevated") and "not read yet" in note
    fig = scatter(p, x="ep_quant", y="roic_quant")
    assert "Market 2 weeks pre-report" in fig.layout.title.text
    assert window_summary(calendar_pre["data"]) is None                    # not served → no note


def test_peers_hover_shows_the_market_line(payload):
    from earningspy_glass.graph_client import _hover
    point = dict(next(p for p in payload["points"] if p.get("role") == "anchor"))
    point["market"] = {"MARKET_STATE": "ELEVATED", "VIX_TERM": "FLAT", "VIX_PRE_2W": 22.4, "EARNING_VIX": 19.8}
    # every measure in the payload is listed, so the depth budget is exhausted: the line must survive
    h = _hover(payload, point, list(payload["metrics"]), "industry", view="all").replace("&nbsp;", " ")
    assert ("Market 2 wks before report: mood Elevated · VIX curve Flat · VIX 22.4"
            " — VIX on report day 19.8") in h                   # this anchor has reported
    upcoming = dict(point, reported=False, days_left=5)
    assert "— VIX latest close 19.8" in _hover(payload, upcoming, ["pe"], "industry", view="all").replace("&nbsp;", " ")
    point["market"] = {"MARKET_STATE": None, "VIX_TERM": None, "VIX_PRE_2W": None, "EARNING_VIX": None}
    assert "Market 2 wks before report" not in _hover(payload, point, ["pe", "roic"], "industry",
                                                      view="all")


def test_the_client_timeline_forwards_regime(timeline_pre):
    """The public wrapper (es.timeline(...).bars) must reach the regime overlay."""
    from earningspy_glass.client import Timeline
    fig = Timeline(_with_regime(timeline_pre)).bars("EPS", regime=True)
    assert any(tr.name == "VIX 2w pre-report" for tr in fig.data)
    with pytest.raises(ChartError):
        Timeline(timeline_pre).bars("EPS", regime=True)


def test_peers_market_rows_anchor_first(payload):
    from earningspy_glass.client import Peers
    p = copy.deepcopy(payload)
    for i, point in enumerate(p["points"]):
        point["market"] = dict(_READINGS[i % len(_READINGS)])
    rows = Peers(p).market()
    assert rows[0]["role"] == "anchor" and len(rows) == len(p["points"])
    assert set(rows[0]) == {"ticker", "role", "earnings_date", "MARKET_STATE", "VIX_TERM", "VIX_PRE_2W", "EARNING_VIX"}
