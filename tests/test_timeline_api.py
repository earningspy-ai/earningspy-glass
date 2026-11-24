"""The timeline fetch layer: bronze-route history depth, view/frame validation,
cursor paging. No network — a fake session returns canned pages.
"""
import pytest

from earningspy_glass.timeline_api import fetch_timeline
from earningspy_glass.api import AuthError, RequestError, EarningSpyError


class _Response:
    def __init__(self, status_code=200, body=None, text=""):
        self.status_code = status_code
        self._body = body
        self.text = text

    def json(self):
        if self._body is None:
            raise ValueError("no json")
        return self._body


class _Session:
    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = []

    def get(self, url, params=None, headers=None, timeout=None, **_):
        self.calls.append({"url": url, "params": params, "headers": headers})
        return self._responses.pop(0)


def _env(rows, cursor=None):
    return _Response(200, {"data": rows, "next_cursor": cursor,
                           "metrics": {"EPS": {"label": "EPS", "format":
                                               "currency_per_share",
                                               "higher_is_better": True}}})


def _row(date):
    return {"TICKER": "AAPL", "EARNINGS_DATE": f"{date}T00:00:00", "EPS": 6.0}


class TestRouting:
    def test_pre_routes_to_tier_segment(self):
        # the pre ticker route LEFT-joins the score layers, so every segment has
        # the full history; the tier picks how much enrichment rides along.
        s = _Session([_env([_row("2026-01-29")])])
        fetch_timeline("aapl", frame="pre", tier="silver", api_key="k", session=s)
        assert s.calls[0]["url"].endswith("/pre/silver/ticker/AAPL")

    def test_pre_default_tier_is_silver(self):
        s = _Session([_env([_row("2026-01-29")])])
        fetch_timeline("AAPL", frame="pre", api_key="k", session=s)
        assert s.calls[0]["url"].endswith("/pre/silver/ticker/AAPL")

    def test_post_always_bronze_even_with_tier(self):
        # post ticker exists only on bronze (silver/gold 404 there), so post
        # ignores the tier segment and always fetches bronze.
        s = _Session([_env([_row("2026-01-29")])])
        fetch_timeline("AAPL", frame="post", tier="gold", api_key="k", session=s)
        assert s.calls[0]["url"].endswith("/post/bronze/ticker/AAPL")

    def test_bad_tier_raises(self):
        with pytest.raises(RequestError, match="tier"):
            fetch_timeline("AAPL", tier="platinum", api_key="k",
                           session=_Session([]))

    def test_view_is_a_query_param(self):
        s = _Session([_env([_row("2026-01-29")])])
        fetch_timeline("AAPL", view="calendar_quant", api_key="k", session=s)
        assert s.calls[0]["params"]["view"] == "calendar_quant"

    def test_default_view_is_frame_specific(self):
        # pre wants percentiles (calendar_quant, a superset); post wants the raw
        # across-event changes (fundamentals) — the two post views are disjoint.
        s = _Session([_env([_row("2026-01-29")])])
        fetch_timeline("AAPL", frame="pre", api_key="k", session=s)
        assert s.calls[0]["params"]["view"] == "calendar_quant"

        s = _Session([_env([_row("2026-01-29")])])
        fetch_timeline("AAPL", frame="post", api_key="k", session=s)
        assert s.calls[0]["params"]["view"] == "fundamentals"

    def test_key_in_header_never_url(self):
        s = _Session([_env([_row("2026-01-29")])])
        fetch_timeline("AAPL", api_key="secret", session=s)
        assert s.calls[0]["headers"]["x-api-key"] == "secret"
        assert "secret" not in s.calls[0]["url"]


class TestValidation:
    def test_bad_frame_raises(self):
        with pytest.raises(RequestError, match="frame"):
            fetch_timeline("AAPL", frame="mid", api_key="k", session=_Session([]))

    def test_bad_view_raises(self):
        with pytest.raises(RequestError, match="view"):
            fetch_timeline("AAPL", view="nope", api_key="k", session=_Session([]))

    def test_missing_key_raises(self, monkeypatch):
        monkeypatch.delenv("EARNINGSPY_API_KEY", raising=False)
        with pytest.raises(AuthError, match="no API key"):
            fetch_timeline("AAPL", session=_Session([]))

    def test_empty_history_raises(self):
        s = _Session([_env([])])
        with pytest.raises(EarningSpyError, match="no history"):
            fetch_timeline("AAPL", api_key="k", session=s)


class TestPaging:
    def test_follows_cursor(self):
        s = _Session([
            _env([_row("2026-01-29")], cursor="c1"),
            _env([_row("2025-10-30")], cursor=None),
        ])
        payload = fetch_timeline("AAPL", api_key="k", session=s)
        assert len(s.calls) == 2
        assert s.calls[1]["params"]["cursor"] == "c1"
        assert len(payload["data"]) == 2
        assert payload["frame"] == "pre" and payload["ticker"] == "AAPL"


class TestOutcomesView:
    def test_outcomes_is_a_post_view(self):
        s = _Session([_env([_row("2026-07-30")])])
        payload = fetch_timeline("AAPL", frame="post", view="outcomes",
                                 tier="gold", api_key="k", session=s)
        assert s.calls[0]["params"]["view"] == "outcomes"
        assert s.calls[0]["url"].endswith("/post/bronze/ticker/AAPL")
        assert payload["view"] == "outcomes"

    def test_outcomes_on_pre_is_refused_before_any_request(self):
        s = _Session([])
        with pytest.raises(RequestError, match="only on the post timeline"):
            fetch_timeline("AAPL", frame="pre", view="outcomes", api_key="k",
                           session=s)
        assert not s.calls
