"""The fetch layer: routing, tiers, error translation, contract checks.

No network. A fake session stands in for requests, so every path is exercised
offline and the assertions are about what the client DOES with a response, not
about the live API.
"""
import json
from pathlib import Path

import pytest

from earningspy_glass.api import (
    AuthError,
    ContractError,
    NotFoundError,
    RequestError,
    fetch_peer_analysis,
)

FIXTURE = json.loads(
    (Path(__file__).parent / "fixtures" / "peers_min.json").read_text()
)


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
    """Records the last GET and returns a canned response."""

    def __init__(self, response):
        self._response = response
        self.calls = []

    def get(self, url, params=None, headers=None, timeout=None, **_):
        self.calls.append({"url": url, "params": params, "headers": headers})
        return self._response


def _ok():
    return _Session(_Response(200, FIXTURE))


class TestRouting:
    def test_bronze_post_route_and_key_header(self):
        s = _ok()
        fetch_peer_analysis("zzam", api_key="esk_secret_123", session=s)
        call = s.calls[0]
        assert call["url"].endswith("/post/bronze/ticker/ZZAM/peers")
        assert call["headers"]["x-api-key"] == "esk_secret_123"
        # the key is never placed in the URL or the query params
        assert "esk_secret_123" not in call["url"]
        assert "esk_secret_123" not in str(call["params"])

    def test_silver_and_gold_mount_under_pre(self):
        for tier in ("silver", "gold"):
            s = _ok()
            fetch_peer_analysis("zzam", tier=tier, api_key="k", session=s)
            assert s.calls[0]["url"].endswith(f"/pre/{tier}/ticker/ZZAM/peers")

    def test_delta_is_a_gold_query_param(self):
        s = _ok()
        fetch_peer_analysis("zzam", frame="delta", tier="gold",
                            api_key="k", session=s)
        call = s.calls[0]
        assert call["params"].get("frame") == "delta"
        assert "/post/bronze/" in call["url"]

    def test_delta_without_gold_is_a_request_error(self):
        with pytest.raises(RequestError, match="gold"):
            fetch_peer_analysis("zzam", frame="delta", tier="silver",
                                api_key="k", session=_ok())


class TestValidation:
    def test_missing_key_raises_autherror(self, monkeypatch):
        monkeypatch.delenv("EARNINGSPY_API_KEY", raising=False)
        with pytest.raises(AuthError, match="no API key"):
            fetch_peer_analysis("zzam", session=_ok())

    def test_bad_tier_raises(self):
        with pytest.raises(RequestError, match="tier must be"):
            fetch_peer_analysis("zzam", tier="platinum", api_key="k",
                                session=_ok())

    def test_bad_frame_raises(self):
        with pytest.raises(RequestError, match="frame must be"):
            fetch_peer_analysis("zzam", frame="sideways", api_key="k",
                                session=_ok())


class TestErrorTranslation:
    def test_403_becomes_a_readable_tier_message(self):
        s = _Session(_Response(403, {"message": "Forbidden"}))
        with pytest.raises(AuthError) as exc:
            fetch_peer_analysis("zzam", tier="gold", api_key="k", session=s)
        text = str(exc.value)
        assert "gold" in text and "tier" in text.lower()

    def test_404_becomes_not_found(self):
        s = _Session(_Response(404, {"error": "unknown ticker"}))
        with pytest.raises(NotFoundError):
            fetch_peer_analysis("zzzz", api_key="k", session=s)

    def test_400_becomes_request_error_and_keeps_server_detail(self):
        s = _Session(_Response(400, {"error": "bad peer_group"}))
        with pytest.raises(RequestError, match="bad peer_group"):
            fetch_peer_analysis("zzam", api_key="k", session=s)


class TestContract:
    def test_wrong_schema_major_raises(self):
        body = dict(FIXTURE, schema_version="2.0")
        with pytest.raises(ContractError, match="schema"):
            fetch_peer_analysis("zzam", api_key="k",
                                session=_Session(_Response(200, body)))

    def test_older_methodology_raises(self):
        # a lower _vN than the client's floor
        body = dict(FIXTURE, methodology_version="peers_pctile_v0")
        with pytest.raises(ContractError):
            fetch_peer_analysis("zzam", api_key="k",
                                session=_Session(_Response(200, body)))

    def test_newer_methodology_warns_but_returns(self):
        body = dict(FIXTURE, methodology_version="peers_pctile_v2")
        with pytest.warns(UserWarning, match="newer"):
            out = fetch_peer_analysis("zzam", api_key="k",
                                      session=_Session(_Response(200, body)))
        assert out["request"]["anchor_ticker"] == "ZZAM"

    def test_payload_warnings_surface_as_payload_notice(self):
        from earningspy_glass.api import PayloadNotice
        body = dict(FIXTURE, warnings=[{"code": "STALE", "detail": "old data"}])
        with pytest.warns(PayloadNotice, match="old data"):
            fetch_peer_analysis("zzam", api_key="k",
                                session=_Session(_Response(200, body)))

    def test_duplicate_payload_notes_emit_once(self):
        # the API can repeat a code per metric; the client de-dups identical notes
        import warnings as _w
        body = dict(FIXTURE, warnings=[
            {"code": "thin_distribution", "detail": "quality_prob: 0 usable values"},
            {"code": "thin_distribution", "detail": "quality_prob: 0 usable values"},
            {"code": "thin_distribution", "detail": "is_abnormal_prob: 0 usable values"},
        ])
        with _w.catch_warnings(record=True) as caught:
            _w.simplefilter("always")
            fetch_peer_analysis("zzam", api_key="k",
                                session=_Session(_Response(200, body)))
        notes = [w for w in caught if "thin_distribution" in str(w.message)]
        assert len(notes) == 2   # the duplicate collapsed to one

    def test_payload_notice_is_silenceable_without_hiding_skew(self):
        from earningspy_glass.api import PayloadNotice
        import warnings as _w
        body = dict(FIXTURE, methodology_version="peers_pctile_v2",
                    warnings=[{"code": "STALE", "detail": "old data"}])
        with _w.catch_warnings(record=True) as caught:
            _w.simplefilter("always")
            _w.filterwarnings("ignore", category=PayloadNotice)
            fetch_peer_analysis("zzam", api_key="k",
                                session=_Session(_Response(200, body)))
        msgs = [str(w.message) for w in caught]
        assert not any("old data" in m for m in msgs)      # payload note silenced
        assert any("newer" in m for m in msgs)             # skew warning survives


class TestPeersFrameResolution:
    """frame=None picks the view each tier is served from; frame="post" is one
    route at every tier, and the key decides how deep it goes."""

    def test_default_frame_is_post_on_bronze_and_pre_on_paid_tiers(self):
        s = _ok()
        fetch_peer_analysis("zzam", api_key="k", session=s)
        assert s.calls[0]["url"].endswith("/post/bronze/ticker/ZZAM/peers")
        for tier in ("silver", "gold"):
            s = _ok()
            fetch_peer_analysis("zzam", tier=tier, api_key="k", session=s)
            assert s.calls[0]["url"].endswith(f"/pre/{tier}/ticker/ZZAM/peers")

    @pytest.mark.parametrize("tier", ["silver", "gold"])
    def test_explicit_post_with_a_paid_tier_calls_the_one_post_route(self, tier):
        # layers_included names the tiers of the columns carried, not the key's: the post
        # frame's pre-event reads are bronze, so a gold key's post view says ["bronze"].
        body = dict(FIXTURE, layers_included=["bronze"])
        s = _Session(_Response(200, body))
        assert fetch_peer_analysis("zzam", frame="post", tier=tier, api_key="k", session=s)
        assert s.calls[0]["url"].endswith("/post/bronze/ticker/ZZAM/peers")

    def test_explicit_pre_with_a_paid_tier_is_allowed(self):
        s = _ok()
        fetch_peer_analysis("zzam", frame="pre", tier="gold", api_key="k", session=s)
        assert s.calls[0]["url"].endswith("/pre/gold/ticker/ZZAM/peers")

    def test_explicit_post_on_bronze_still_uses_the_post_route(self):
        s = _ok()
        fetch_peer_analysis("zzam", frame="post", api_key="k", session=s)
        assert s.calls[0]["url"].endswith("/post/bronze/ticker/ZZAM/peers")


class TestCohort:
    def test_cohort_is_sent_only_when_asked_for(self):
        s = _ok()
        fetch_peer_analysis("zzam", api_key="k", session=s)
        assert "cohort" not in s.calls[0]["params"]          # the service default (auto)
        fetch_peer_analysis("zzam", api_key="k", session=s, cohort="lookback")
        assert s.calls[1]["params"]["cohort"] == "lookback"

    def test_an_unknown_cohort_is_refused_before_any_call(self):
        s = _ok()
        with pytest.raises(RequestError, match="cohort"):
            fetch_peer_analysis("zzam", api_key="k", session=s, cohort="everything")
        assert not s.calls

    def test_how_the_season_was_filled_is_kept_but_not_printed(self):
        import warnings as _w
        body = dict(FIXTURE, warnings=[
            {"code": "lookback_cohort", "detail": "2 peers reported in 2026Q4; 9 carried"},
            {"code": "thin_season", "detail": "only 2 peers reported in 2026Q4"},
        ])
        with _w.catch_warnings(record=True) as caught:
            _w.simplefilter("always")
            out = fetch_peer_analysis("zzam", api_key="k",
                                      session=_Session(_Response(200, body)))
        assert not [w for w in caught if "2026Q4" in str(w.message)]
        assert {w["code"] for w in out["warnings"]} == {"lookback_cohort", "thin_season"}


class TestSeason:
    def test_season_is_sent_normalised(self):
        s = _ok()
        fetch_peer_analysis("zzam", api_key="k", session=s, season="2026q2")
        assert s.calls[0]["params"]["season"] == "2026Q2"

    def test_a_malformed_season_is_refused_before_any_call(self):
        s = _ok()
        with pytest.raises(RequestError, match="2026Q2"):
            fetch_peer_analysis("zzam", api_key="k", session=s, season="Q2-2026")
        assert not s.calls
