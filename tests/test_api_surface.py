"""Glass wraps the whole API: every published route is called by some method, and
every published parameter and view can be asked for. Read from a saved copy of the
public catalogue, so a new route or parameter fails here until it is wrapped."""
import json
from pathlib import Path

import pytest

import earningspy_glass as g
from test_http import Response, Session

CATALOG = json.loads((Path(__file__).parent / "fixtures" / "catalog.json").read_text())
ENDPOINTS = CATALOG["api"]["endpoints"]
BASE = "https://api.earningspy.ai/v1"
PEERS = json.loads((Path(__file__).parent / "fixtures" / "peers_min.json").read_text())
PAGE = {"data": [{"TICKER": "AAPL", "EARNINGS_DATE": "2026-11-02"}], "next_cursor": None}


def calls():
    """Every request the client can make, as {path: parameters sent}."""
    seen = {}

    def record(method, body, *args, **kw):
        s = Session(Response(200, body), Response(200, body))
        es = g.EarningSpy(api_key="k", session=s, verbose=False)
        getattr(es, method)(*args, **kw)
        for c in s.calls:
            path = c["url"].removeprefix(BASE).replace("AAPL", "{ticker}")
            seen.setdefault(path, set()).update(c["params"])

    for tier in ("bronze", "silver", "gold"):
        record("rows", PAGE, tier=tier, period="week", sort="asc", limit=5)
        record("rows", PAGE, tier=tier, start="2026-11-02", end="2026-11-08")
        record("rows", PAGE, tier=tier, ticker="AAPL", limit=5)
        record("peers", PEERS, "AAPL", frame="pre", tier=tier, peers="reported", horizon="next",
               limit=9, metrics=["pe"], cohort="auto", season="2026Q2")
    record("rows", PAGE, frame="post", period="month", sort="desc")
    record("rows", PAGE, frame="post", ticker="AAPL")
    record("peers", PEERS, "AAPL", frame="delta", tier="gold", peers="upcoming", horizon="next",
           limit=9, metrics="pe")
    record("schema", {"columns": []})
    s = Session(Response(200, {**PAGE, "next_cursor": "c"}), Response(200, PAGE))
    g.fetch_rows(api_key="k", session=s)
    cursor = set(s.calls[1]["params"])
    return {p: v | (cursor if p.endswith("calendar") else set()) for p, v in seen.items()}


CALLS = calls()


@pytest.mark.parametrize("endpoint", ENDPOINTS, ids=lambda e: e["operation_id"])
def test_every_published_route_is_wrapped(endpoint):
    assert endpoint["path"] in CALLS


@pytest.mark.parametrize("endpoint", ENDPOINTS, ids=lambda e: e["operation_id"])
def test_every_published_parameter_can_be_sent(endpoint):
    asked = {p["name"] for p in endpoint["parameters"]} - {"ticker"}
    assert asked <= CALLS[endpoint["path"]], asked - CALLS[endpoint["path"]]


def test_nothing_unpublished_is_called():
    assert set(CALLS) <= {e["path"] for e in ENDPOINTS}


@pytest.mark.parametrize("view", CATALOG["views"], ids=lambda v: f"{v['frame']}-{v['name']}")
def test_every_published_view_can_be_read(view):
    s = Session(Response(200, PAGE))
    out = g.fetch_rows(view["name"], view["frame"], api_key="k", session=s)
    assert out["data"] == PAGE["data"] and s.calls[0]["params"]["view"] == view["name"]


@pytest.mark.parametrize("error", CATALOG["api"]["errors"], ids=lambda e: e["name"])
def test_every_published_error_is_readable(error, monkeypatch):
    from earningspy_glass import _http
    monkeypatch.setattr(_http, "_sleep", lambda s: None)
    status = int(error["status"] or {"OutOfPhase": 409, "PeerNotFound": 404}[error["name"]])
    with pytest.raises(g.EarningSpyError) as exc:
        g.fetch_peer_analysis("AAPL", api_key="k", session=Session(Response(status, {"error": error["example"]})))
    assert exc.value.status_code == status and str(exc.value)


class TestRows:
    def ask(self, **kw):
        s = Session(Response(200, PAGE))
        out = g.fetch_rows(api_key="k", session=s, **kw)
        return out, s.calls[0]

    def test_rows_come_back_as_served(self):
        out, sent = self.ask()
        assert out["data"] == PAGE["data"] and out["count"] == 1
        assert sent["url"] == f"{BASE}/pre/bronze/calendar"

    def test_after_the_report_there_is_one_route(self):
        assert self.ask(frame="post", tier="gold")[1]["url"] == f"{BASE}/post/bronze/calendar"

    def test_dates_in_either_form(self):
        import datetime
        _, sent = self.ask(start=datetime.date(2026, 11, 2), end=datetime.datetime(2026, 11, 8, 5))
        assert (sent["params"]["start"], sent["params"]["end"]) == ("2026-11-02", "2026-11-08")

    def test_a_limit_past_one_page_reads_several(self):
        page = Response(200, {"data": [{}] * 1000, "next_cursor": "a"})
        s = Session(page, Response(200, {"data": [{}] * 1000, "next_cursor": "b"}))
        assert len(g.fetch_rows(api_key="k", session=s, limit=1500)["data"]) == 1500

    @pytest.mark.parametrize("kw,words", [
        ({"frame": "delta"}, "frame"), ({"tier": "platinum"}, "tier"), ({"period": "year"}, "period"),
        ({"sort": "up"}, "sort"), ({"period": "week", "start": "2026-11-02"}, "not both"),
        ({"ticker": "AAPL", "period": "week"}, "no window"), ({"limit": 0}, "limit"),
        ({"limit": True}, "limit"), ({"start": "soon"}, "start"),
    ])
    def test_a_bad_option_is_refused_before_a_request(self, kw, words):
        s = Session(Response(200, PAGE))
        with pytest.raises(g.RequestError, match=words):
            g.fetch_rows(api_key="k", session=s, **kw)
        assert s.calls == []


@pytest.mark.parametrize("kw", [{"peers": "some"}, {"horizon": "year"}, {"cohort": "x"}])
def test_peers_options_are_checked(kw):
    with pytest.raises(g.RequestError):
        g.fetch_peer_analysis("AAPL", api_key="k", **kw)


def test_the_calendar_checks_its_tier():
    with pytest.raises(g.RequestError, match="tier"):
        g.fetch_calendar(tier="platinum", api_key="k")
