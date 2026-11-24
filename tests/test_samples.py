"""The sample data: no key, no network, nothing that must not ship, and every chart labelled."""
import json
import re
import socket

import pytest

import earningspy_glass
from earningspy_glass import RequestError, sample, samples


@pytest.fixture(autouse=True)
def _no_key_no_network(monkeypatch):
    monkeypatch.delenv("EARNINGSPY_API_KEY", raising=False)

    def refuse(*a, **k):
        raise AssertionError("the sample reached the network")
    monkeypatch.setattr(socket.socket, "connect", refuse)


PEERS = [("scatter", {"x": "pe", "y": "roic"}), ("treemap", {}), ("bar", {"metric": "roic"}), ("bar_pctile", {}),
         ("bar_signed", {}), ("radar", {}), ("donut", {}), ("grade_card", {})]
CALENDAR = [("scatter", {}), ("bar_pctile", {}), ("treemap", {}), ("radar", {}), ("donut", {})]


def _labelled(fig):
    title = fig.layout.title.text
    assert "SAMPLE DATA" in title.split("<br>")[0] and "Nothing to plot" not in title
    assert len(fig.data)


@pytest.mark.parametrize("kind,options", PEERS)
def test_peers_charts(kind, options):
    _labelled(sample().plot(kind, **options))


@pytest.mark.parametrize("kind,options", CALENDAR)
def test_calendar_charts(kind, options):
    _labelled(sample("calendar").plot(kind, **options))


def test_timeline_charts():
    timeline = sample("timeline")
    for fig in (timeline.bars("EPS"), timeline.compare("EPS"), timeline.radar()):
        _labelled(fig)


def test_a_subset_stays_labelled():
    peers = sample()
    _labelled(peers.only(peers.largest(2)).plot("bar", metric="roic"))


def test_live_data_is_never_labelled(payload):
    fig = earningspy_glass.Peers(payload).plot("scatter", x="pe", y="roic")
    assert "SAMPLE" not in fig.layout.title.text


def test_changing_a_sample_does_not_change_the_next_one():
    first = sample()
    first.payload["points"].clear()
    assert sample().payload["points"]


def test_an_unknown_sample_says_what_exists():
    with pytest.raises(RequestError, match="peers, calendar, timeline"):
        sample("gold")


def test_no_score_or_outcome_ships():
    text = json.dumps(samples._data())
    assert not re.findall(r"quality_|silver_|gold_|conviction|abnormal|event_score|espy|qs_vs|car_|_ret\b|outcome", text, re.I)


def test_no_real_company_is_in_it():
    data = samples._data()
    tickers = {p["ticker"] for p in data["peers"]["points"]}
    for name in ("calendar", "timeline_pre", "timeline_post"):
        tickers |= {r["TICKER"] for r in data[name]["data"]}
    assert data["fictional"] and all(t.startswith("ZZ") for t in tickers)


def test_everything_in_it_is_past():
    as_of = samples._data()["as_of"]
    for name in ("calendar", "timeline_pre", "timeline_post"):
        assert all(r["EARNINGS_DATE"][:10] <= as_of for r in samples._data()[name]["data"])
    assert all(p["earnings_date"] <= as_of for p in samples._data()["peers"]["points"])


def test_the_default_calendar_radar_reads_percentiles_only():
    for trace in sample("calendar").plot("radar").data:
        assert len(set(trace.theta)) >= 5
        assert all(v == 0 or v >= 1 for v in trace.r)
