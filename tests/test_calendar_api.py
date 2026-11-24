"""The calendar fetch layer: routing per frame, scope validation, cursor paging,
scope date-slicing, and that the metrics table survives untouched.

No network. A fake session returns canned pages, so every path runs offline.
"""
import datetime

import pytest

from earningspy_glass.calendar_api import (
    fetch_calendar,
    _scope_window,
    _slice_to_scope,
)
from earningspy_glass.api import AuthError, RequestError


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
    """Returns queued responses in order, recording each GET."""

    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = []

    def get(self, url, params=None, headers=None, timeout=None, **_):
        self.calls.append({"url": url, "params": params, "headers": headers})
        return self._responses.pop(0)


def _envelope(rows, cursor=None, metrics=None, facts=None):
    return _Response(200, {
        "data": rows,
        "next_cursor": cursor,
        "metrics": metrics or {"EP_QUANT": {
            "label": "Earnings yield industry percentile",
            "higher_is_better": True, "format": "percentile_points",
            "scale": "linear", "color_clip_pct": None}},
        "window": {"period": "month"},
        "facts": facts or {"rows": {r["TICKER"]: {} for r in rows}, "horizon": "2099-12-31"},
    })


def _row(ticker, date):
    return {"TICKER": ticker, "EARNINGS_DATE": f"{date}T00:00:00", "EP_QUANT": 50.0}


class TestRouting:
    def test_pre_quant_routes_under_pre_silver(self):
        s = _Session([_envelope([_row("AAA", "2026-09-24")])])
        fetch_calendar("week", "pre", api_key="k", session=s)
        assert s.calls[0]["url"].endswith("/pre/silver/calendar")
        assert s.calls[0]["headers"]["x-api-key"] == "k"
        assert "k" not in s.calls[0]["url"]

    def test_post_quant_routes_under_post_bronze(self):
        s = _Session([_envelope([_row("BBB", "2026-09-10")])])
        fetch_calendar("quarter_to_date", "post", api_key="k", session=s)
        assert s.calls[0]["url"].endswith("/post/bronze/calendar")

    def test_window_is_sent_as_explicit_start_end(self):
        s = _Session([_envelope([_row("AAA", "2026-09-24")])])
        fetch_calendar("week", "pre", api_key="k", session=s,
                       today=datetime.date(2026, 9, 20))
        params = s.calls[0]["params"]
        assert params["view"] == "calendar_quant"
        assert "period" not in params
        assert (params["start"], params["end"]) == ("2026-09-20", "2026-09-27")


class TestValidation:
    def test_unknown_frame_raises(self):
        with pytest.raises(RequestError, match="frame"):
            fetch_calendar("week", "sideways", api_key="k", session=_Session([]))

    def test_unknown_scope_raises(self):
        with pytest.raises(RequestError, match="scope"):
            fetch_calendar("fortnight", "pre", api_key="k", session=_Session([]))

    def test_pre_only_scope_on_post_raises(self):
        with pytest.raises(RequestError, match="not available on the 'post'"):
            fetch_calendar("month_from_date", "post", api_key="k",
                           session=_Session([]))

    def test_post_only_scope_on_pre_raises(self):
        with pytest.raises(RequestError, match="not available on the 'pre'"):
            fetch_calendar("quarter_to_date", "pre", api_key="k",
                           session=_Session([]))

    def test_missing_key_raises(self, monkeypatch):
        monkeypatch.delenv("EARNINGSPY_API_KEY", raising=False)
        with pytest.raises(AuthError, match="no API key"):
            fetch_calendar("week", "pre", session=_Session([]))


class TestPaging:
    def test_follows_next_cursor_until_null(self):
        s = _Session([
            _envelope([_row("AAA", "2026-10-05")], cursor="c1"),
            _envelope([_row("BBB", "2026-10-06")], cursor="c2"),
            _envelope([_row("CCC", "2026-10-07")], cursor=None),
        ])
        payload = fetch_calendar("month_from_date", "pre", api_key="k", session=s,
                                 today=datetime.date(2026, 10, 1))
        assert len(s.calls) == 3
        assert [r["TICKER"] for r in payload["data"]] == ["AAA", "BBB", "CCC"]
        assert s.calls[1]["params"]["cursor"] == "c1"
        assert s.calls[2]["params"]["cursor"] == "c2"
        assert set(payload["facts"]["rows"]) == {"AAA", "BBB", "CCC"}     # every page's
        # metrics table carried through from the last envelope
        assert "EP_QUANT" in payload["metrics"]
        assert payload["frame"] == "pre" and payload["scope"] == "month_from_date"

    def test_scope_and_frame_recorded_on_payload(self):
        s = _Session([_envelope([_row("AAA", "2026-10-05")])])
        payload = fetch_calendar("week", "pre", api_key="k", session=s,
                                 today=datetime.date(2026, 10, 1))
        assert payload["scope"] == "week"
        assert payload["frame"] == "pre"


class TestScopeWindow:
    def test_a_pre_week_crosses_the_month_end(self):
        assert _scope_window("week", "pre", datetime.date(2026, 10, 30)) == (
            datetime.date(2026, 10, 30), datetime.date(2026, 11, 6))

    def test_post_week_looks_back(self):
        assert _scope_window("week", "post", datetime.date(2026, 10, 2)) == (
            datetime.date(2026, 9, 25), datetime.date(2026, 10, 2))

    def test_pre_week_from_monday_is_this_week_monday_to_friday(self):
        assert _scope_window("week", "pre", datetime.date(2026, 11, 18), from_monday=True) == (
            datetime.date(2026, 11, 16), datetime.date(2026, 11, 20))

    def test_pre_two_weeks_from_monday_ends_next_friday(self):
        assert _scope_window("two_weeks", "pre", datetime.date(2026, 11, 18), from_monday=True) == (
            datetime.date(2026, 11, 16), datetime.date(2026, 11, 27))

    def test_pre_from_monday_on_a_weekend_is_the_coming_week(self):
        assert _scope_window("week", "pre", datetime.date(2026, 11, 21), from_monday=True) == (
            datetime.date(2026, 11, 23), datetime.date(2026, 11, 27))

    def test_from_monday_leaves_a_post_window_alone(self):
        wednesday = datetime.date(2026, 11, 18)
        assert _scope_window("week", "post", wednesday, from_monday=True) == _scope_window("week", "post", wednesday)

    def test_post_quarter_to_date_starts_on_quarter(self):
        assert _scope_window("quarter_to_date", "post", datetime.date(2026, 11, 14)) == (
            datetime.date(2026, 10, 1), datetime.date(2026, 11, 14))


class TestHorizon:
    """How far the upcoming calendar reaches is the service's answer."""

    def _fetch(self, scope, horizon="2026-11-08", frame="pre", today=datetime.date(2026, 10, 30)):
        facts = {"rows": {}, **({"horizon": horizon} if horizon else {})}
        s = _Session([_envelope([_row("AAA", "2026-10-30"), _row("FAR", "2026-11-12")], facts=facts)])
        return fetch_calendar(scope, frame, api_key="k", session=s, today=today), s

    def test_a_window_past_it_is_cut_and_said(self):
        with pytest.warns(Warning, match="horizon"):
            payload, _ = self._fetch("two_weeks")
        assert payload["window"]["end"] == "2026-11-08" and payload["warnings"][-1]["code"] == "horizon"
        assert [r["TICKER"] for r in payload["data"]] == ["AAA"]

    def test_the_whole_reach_is_not_a_cut(self):
        payload, s = self._fetch("month_from_date")
        assert payload["window"]["end"] == "2026-11-08" and not payload.get("warnings")
        assert s.calls[0]["params"]["end"] > "2026-11-30"          # asked further than it ever reaches

    def test_a_window_inside_it_is_left_alone(self):
        payload, _ = self._fetch("week")
        assert payload["window"]["end"] == "2026-11-06" and not payload.get("warnings")

    def test_every_request_asks_for_the_facts(self):
        _, s = self._fetch("week")
        assert s.calls[0]["params"]["facts"] == 1


class TestScopeSlicing:
    START, END = datetime.date(2026, 9, 20), datetime.date(2026, 9, 27)

    def test_keeps_rows_inside_the_window_only(self):
        rows = [_row("PAST", "2026-09-10"), _row("SOON", "2026-09-24"),
                _row("EDGE", "2026-09-27"), _row("FAR", "2026-10-15")]
        kept = {r["TICKER"] for r in _slice_to_scope(rows, self.START, self.END)}
        assert kept == {"SOON", "EDGE"}

    def test_unreadable_date_is_kept_not_dropped(self):
        rows = [{"TICKER": "WEIRD", "EARNINGS_DATE": "not-a-date", "EP_QUANT": 1.0}]
        kept = _slice_to_scope(rows, self.START, self.END)
        assert [r["TICKER"] for r in kept] == ["WEIRD"]
