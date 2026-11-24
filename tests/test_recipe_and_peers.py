"""The recipe every figure carries, and the peers / highlight options shared with
EarningSpy Studio (same names, same meaning, so a preview here and a render
there show the same chart)."""
import json

import pytest

from earningspy_glass import PEER_COUNTS, ChartError, Peers, plot, recipe
from earningspy_glass.graph_client import select_peers


def _labels(fig):
    return [t for tr in fig.data if tr.mode == "text" for t in tr.text]


def test_every_figure_carries_its_recipe(payload):
    fig = plot(payload, "scatter", x="pe", y="roic", peers=2)
    r = recipe(fig)
    assert r["graph"] == "peers" and r["kind"] == "scatter"
    assert r["ticker"] == payload["request"]["anchor_ticker"]
    assert r["params"] == {"x": "pe", "y": "roic", "peers": 2}
    assert r["tier"] in ("bronze", "silver", "gold")
    # It survives the JSON round trip, and holds no data.
    assert recipe(json.loads(fig.to_json())) == r
    assert "points" not in json.dumps(r)


def test_the_client_object_records_the_tier_it_asked_for(payload):
    peers = Peers(payload, request={"ticker": "X", "frame": "pre", "tier": "silver", "peer_group": "sector"})
    r = recipe(peers.plot("bar", metric="roic"))
    assert (r["tier"], r["peer_group"], r["params"]) == ("silver", "sector", {"metric": "roic"})


def test_peers_are_the_closest_in_market_cap(payload):
    anchor = payload["request"]["anchor_ticker"]
    cap = lambda p: (p.get("values") or {}).get("market_cap")
    chosen = select_peers(payload, anchor, peers=1)
    others = [p for p in payload["points"] if p["ticker"] != anchor and cap(p)]
    import math
    a = cap(next(p for p in payload["points"] if p["ticker"] == anchor))
    assert chosen[0] is min(others, key=lambda p: abs(math.log(cap(p)) - math.log(a)))
    with pytest.raises(ChartError, match="peers must be one of"):
        select_peers(payload, anchor, peers=4)
    with pytest.raises(ChartError, match="is not in this peer group"):
        select_peers(payload, anchor, tickers=["NOPE"])
    assert PEER_COUNTS == (1, 2, 3, 5, 8)


def test_scatter_names_peers_and_bar_pctile_compares_them(payload):
    anchor = payload["request"]["anchor_ticker"]
    fig = plot(payload, "scatter", x="pe", y="roic", peers=1)
    assert len(_labels(fig)) <= 1
    other = next(p["ticker"] for p in payload["points"] if p["ticker"] != anchor)
    fig = plot(payload, "bar_pctile", tickers=[other])
    assert [tr.name for tr in fig.data] == [anchor, other]


def test_bar_highlight_moves_the_focus_colour(payload):
    other = next(p["ticker"] for p in payload["points"]
                 if p["ticker"] != payload["request"]["anchor_ticker"])
    fig = plot(payload, "bar", metric="roic", highlight=other)
    colours = dict(zip(fig.data[0].y, fig.data[0].marker.color))
    assert len({c for t, c in colours.items() if t == other} | set()) == 1
    assert colours.get(other) != colours.get(payload["request"]["anchor_ticker"])
    with pytest.raises(ChartError, match="not in this peer group"):
        plot(payload, "bar", metric="roic", highlight="NOPE")


def test_pre_report_reads_are_marked_on_the_chart(payload):
    """A reported peer's pre-report read (flagged by the API in point["reads"]):
    on a chart about that one measure the title stays plain and the footnote
    names it ("Quality Probability: computed pre event"); the hover says
    "(pre-report read)"; company names stay plain."""
    peer = next(p for p in payload["points"] if p["ticker"] != payload["request"]["anchor_ticker"])
    payload["metrics"]["quality_prob"] = dict(payload["metrics"]["roic"], label="Quality Probability")
    for p in payload["points"]:
        p.setdefault("values", {})["quality_prob"] = 0.5
    peer["reads"] = {"quality_prob": "pre_report"}
    fig = plot(payload, "bar", metric="quality_prob")
    assert peer["ticker"] in list(fig.data[0].y)                      # plain name
    assert "Quality Probability</span>" in fig.layout.title.text and "*" not in fig.layout.title.text
    assert any(a.text == "Quality Probability: computed pre event" for a in fig.layout.annotations)
    assert "pre-report" not in fig.layout.title.text
    assert any("pre-report read" in h for h in fig.data[0].hovertemplate)
    assert "pre-report" not in plot(payload, "bar", metric="roic").layout.title.text


def test_only_narrows_every_chart_and_keeps_the_ranks(payload):
    from earningspy_glass import only
    anchor = payload["request"]["anchor_ticker"]
    other = next(p["ticker"] for p in payload["points"] if p["ticker"] != anchor)
    pair = only(payload, [other])
    assert [p["ticker"] for p in pair["points"]] == [p["ticker"] for p in payload["points"]
                                                      if p["ticker"] in (anchor, other)]
    assert pair["only"] == {"tickers": [anchor, other], "of": len(payload["points"])}
    assert pair["points"][0].get("percentiles") == next(
        p for p in payload["points"] if p["ticker"] == pair["points"][0]["ticker"]).get("percentiles")
    fig = plot(pair, "bar", metric="roic")
    assert set(fig.data[0].y) <= {anchor, other}
    assert "ranks are against" not in fig.layout.title.text          # logged, not drawn
    assert recipe(fig)["only"] == [anchor, other]
    peers = Peers(payload, request={"ticker": anchor, "tier": "bronze"}).only([other])
    assert recipe(peers.plot("scatter", x="pe", y="roic"))["only"] == [anchor, other]
    with pytest.raises(ChartError, match="not in this peer group"):
        only(payload, ["NOPE"])


def test_bubble_area_is_proportional_to_size_from_zero():
    import math
    from earningspy_glass.graph_client import _marker_size
    small, big = _marker_size([1.22e12, 1.70e12])
    assert big == 46 and small == pytest.approx(46 * math.sqrt(1.22 / 1.70))   # not stretched to the floor
    assert _marker_size([1e9, 4e12])[0] == 9                                   # the floor keeps micro-caps visible


def test_describe_prints_once_in_a_notebook(payload, capsys):
    from earningspy_glass import describe
    d = describe(payload)
    printed = capsys.readouterr().out
    assert "measures" in printed and printed.count("measures you can plot") == 1
    assert "printed above" in repr(d) and len(repr(d).splitlines()) == 1   # the echo stays one line
    assert str(d).strip() == printed.strip()                              # print(d) shows it again


def test_largest_names_the_biggest_peers_without_the_anchor(payload):
    peers = Peers(payload, request={"ticker": payload["request"]["anchor_ticker"], "tier": "bronze"})
    anchor = peers.ticker
    caps = {p["ticker"]: (p.get("values") or {}).get("market_cap") for p in payload["points"]}
    expected = sorted((t for t, c in caps.items() if t != anchor and isinstance(c, (int, float)) and c > 0),
                      key=lambda t: -caps[t])
    assert anchor == payload["request"]["anchor_ticker"]
    assert peers.largest(2) == expected[:2]
    assert anchor not in peers.largest(50)


def test_nearest_names_the_peers_closest_in_size_and_feeds_tickers(payload):
    import math
    peers = Peers(payload)
    anchor = peers.ticker
    caps = {p["ticker"]: (p.get("values") or {}).get("market_cap") for p in payload["points"]}
    target = math.log(caps[anchor])
    expected = sorted((t for t, c in caps.items() if t != anchor and isinstance(c, (int, float)) and c > 0),
                      key=lambda t: abs(math.log(caps[t]) - target))
    near = peers.nearest(3)
    assert near == expected[:3] and anchor not in near
    assert near != peers.largest(3) or expected[:3] == peers.largest(3)
    # Straight into tickers=: the chart draws exactly those companies.
    fig = peers.plot("bar_pctile", tickers=near)
    assert {tr.name for tr in fig.data if tr.name} >= set(near)


def test_nearest_refuses_a_focus_without_a_market_cap(payload):
    import copy
    from earningspy_glass.graph_client import ChartError
    p = copy.deepcopy(payload)
    anchor = next(x for x in p["points"] if x["role"] == "anchor")
    anchor["values"]["market_cap"] = None
    with pytest.raises(ChartError, match="no market cap"):
        Peers(p).nearest(3)


def test_fig_measures_lists_what_the_chart_drew(payload, capsys):
    peers = Peers(payload)
    fig = peers.plot("scatter", x="pe", y="roic")
    assert fig.measures()[:2] == ["pe", "roic"]
    assert "pe" in capsys.readouterr().out
    radar = peers.plot("radar", metrics=["roic", "pe", "market_cap", "sortino"])
    assert radar.measures() == ["roic", "pe", "market_cap", "sortino"]   # neutral market cap drawn too
    # The default view says what it chose, ready to pass back as metrics=.
    card = peers.plot("grade_card")
    again = peers.plot("grade_card", metrics=card.measures())
    assert again.measures() == card.measures()
