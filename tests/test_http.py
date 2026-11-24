"""The transport: what is sent, what is retried, and the error each failure becomes."""
import pytest
import requests

import earningspy_glass as g
from earningspy_glass import _http

URL = "https://api.test/v1/x"


class Response:
    def __init__(self, status_code=200, body=None, text="", headers=None):
        self.status_code, self._body, self.text = status_code, body, text
        self.headers = headers or {}

    def json(self):
        if self._body is None:
            raise ValueError("not json")
        return self._body


class Session:
    """Answers from a queue (the last answer repeats); an exception is raised."""

    def __init__(self, *answers):
        self.answers, self.calls = list(answers), []

    def get(self, url, **kw):
        self.calls.append({"url": url, **kw})
        answer = self.answers.pop(0) if len(self.answers) > 1 else self.answers[0]
        if isinstance(answer, Exception):
            raise answer
        return answer


@pytest.fixture
def waits(monkeypatch):
    slept = []
    monkeypatch.setattr(_http, "_sleep", slept.append)
    return slept


def call(session, **kw):
    return _http.get_json(URL, {"a": 1}, "esk_secret", session=session, **kw)


class TestWhatIsSent:
    def test_key_in_a_header_never_in_the_url_and_no_redirect_followed(self):
        s = Session(Response(200, {"ok": 1}))
        assert call(s, timeout=7) == {"ok": 1}
        sent = s.calls[0]
        assert sent["headers"]["x-api-key"] == "esk_secret"
        assert "esk_secret" not in sent["url"] + str(sent["params"])
        assert sent["allow_redirects"] is False and sent["timeout"] == 7

    def test_the_client_version_travels(self):
        s = Session(Response(200, {}))
        call(s)
        assert s.calls[0]["headers"]["User-Agent"] == f"earningspy-glass/{g.__version__}"

    def test_one_reused_session_per_thread(self):
        import threading
        mine, theirs = _http._session(), []
        t = threading.Thread(target=lambda: theirs.append(_http._session()))
        t.start()
        t.join()
        assert _http._session() is mine and theirs[0] is not mine

    @pytest.mark.parametrize("base", ["http://api.earningspy.ai/v1", "ftp://x/v1", "api.earningspy.ai", "https://"])
    def test_the_key_is_never_sent_in_the_clear(self, base):
        with pytest.raises(g.RequestError, match="https"):
            _http.resolve_base(base)

    @pytest.mark.parametrize("base", ["https://x.test/v1/", "http://localhost:8000/v1"])
    def test_https_and_a_local_server_are_accepted(self, base):
        assert _http.resolve_base(base) == base.rstrip("/")

    def test_base_from_the_environment_then_the_default(self, monkeypatch):
        monkeypatch.setenv("EARNINGSPY_API_BASE", "https://staging.test/v1")
        assert _http.resolve_base() == "https://staging.test/v1"
        monkeypatch.delenv("EARNINGSPY_API_BASE")
        assert _http.resolve_base() == "https://api.earningspy.ai/v1"

    @pytest.mark.parametrize("key", ["", "   ", None])
    def test_a_blank_key_is_no_key(self, key, monkeypatch):
        monkeypatch.delenv("EARNINGSPY_API_KEY", raising=False)
        with pytest.raises(g.AuthError, match="no API key"):
            _http.resolve_api_key(key)

    def test_a_pasted_key_is_trimmed_and_a_broken_one_refused(self):
        assert _http.resolve_api_key("  esk_x\n") == "esk_x"
        for bad in ("esk\nx", "esk_ñ"):
            with pytest.raises(g.AuthError) as exc:
                _http.resolve_api_key(bad)
            assert bad not in str(exc.value)


class TestTickers:
    @pytest.mark.parametrize("ok,out", [("aapl", "AAPL"), (" BRK.B ", "BRK.B"), ("rds-a", "RDS-A"), ("NA", "NA")])
    def test_symbols(self, ok, out):
        assert _http.clean_ticker(ok) == out

    @pytest.mark.parametrize("bad", [None, "", "..", "../meta", "A/B", "A?x=1", "A B", "TOOLONGSYMBOL", "A%2F"])
    def test_anything_else_never_reaches_a_url(self, bad):
        with pytest.raises(g.RequestError, match="ticker"):
            _http.clean_ticker(bad)

    @pytest.mark.parametrize("fetch", [g.fetch_peer_analysis, g.fetch_timeline,
                                       lambda t, **k: g.fetch_rows(ticker=t, **k)])
    def test_every_fetch_checks_it_before_a_request(self, fetch):
        s = Session(Response(200, {}))
        with pytest.raises(g.RequestError, match="ticker"):
            fetch("../../meta/schema?x=", api_key="k", session=s)
        assert s.calls == []


class TestRetries:
    @pytest.mark.parametrize("status", [429, 502, 503, 504])
    def test_a_passing_failure_is_retried(self, status, waits):
        s = Session(Response(status, {"message": "busy"}), Response(200, {"ok": 1}))
        assert call(s) == {"ok": 1}
        assert waits == [0.5]

    @pytest.mark.parametrize("status", [400, 401, 403, 404, 409, 500, 302])
    def test_a_refusal_is_not(self, status, waits):
        s = Session(Response(status, {"error": "no"}))
        with pytest.raises(g.EarningSpyError):
            call(s)
        assert len(s.calls) == 1 and waits == []

    def test_it_gives_up_and_says_so(self, waits):
        s = Session(Response(503, {"error": "frame unavailable"}))
        with pytest.raises(g.ServiceError, match="frame unavailable") as exc:
            call(s)
        assert len(s.calls) == 3 and waits == [0.5, 1.0] and exc.value.status_code == 503

    def test_the_service_names_the_wait(self, waits):
        s = Session(Response(429, {}, headers={"Retry-After": "7"}), Response(200, {}))
        call(s)
        assert waits == [7.0]

    def test_a_long_wait_is_the_callers_decision(self, waits):
        with pytest.raises(g.RateLimitError, match="120 seconds") as exc:
            call(Session(Response(429, {}, headers={"Retry-After": "120"})))
        assert waits == [] and exc.value.retry_after == 120

    def test_a_spent_quota_is_not_retried(self, waits):
        s = Session(Response(429, {"message": "Limit Exceeded"}))
        with pytest.raises(g.RateLimitError, match="quota") as exc:
            call(s)
        assert len(s.calls) == 1 and exc.value.quota_spent

    def test_a_dropped_connection_is_retried_then_readable(self, waits):
        s = Session(requests.ConnectionError("boom"), Response(200, {"ok": 1}))
        assert call(s) == {"ok": 1}
        with pytest.raises(g.ServiceError, match="api.test"):
            call(Session(requests.ConnectionError("boom")))

    def test_a_timeout_is_not_waited_twice(self, waits):
        s = Session(requests.ReadTimeout("slow"))
        with pytest.raises(g.ServiceError, match="30 seconds"):
            call(s)
        assert len(s.calls) == 1

    def test_any_other_request_failure_is_ours(self):
        with pytest.raises(g.EarningSpyError, match="InvalidURL"):
            call(Session(requests.exceptions.InvalidURL("x")))


class TestErrors:
    @pytest.mark.parametrize("status,body,error,words", [
        (401, {"message": "Unauthorized"}, g.AuthError, "key was not accepted"),
        (403, {"message": "Forbidden"}, g.TierError, "does not carry that tier, or it is no longer active"),
        (403, {"message": "User is not authorized to access this resource with an explicit deny"},
         g.TierError, "no longer active"),
        (403, {"error": "tier does not include 'gold' data"}, g.TierError, "does not include 'gold'"),
        (404, {}, g.NotFoundError, "AAPL's timeline"),
        (404, {"error": "no rows for 'ZZ'"}, g.NotFoundError, "no rows for 'ZZ'"),
        (400, {"error": "unknown view"}, g.RequestError, "unknown view"),
        (409, {"error": {"nested": 1}}, g.RequestError, "HTTP 409"),
        (500, ["a list"], g.ServiceError, "HTTP 500"),
        (500, None, g.ServiceError, "HTTP 500"),
        (301, {}, g.RequestError, "redirects elsewhere"),
    ])
    def test_each_failure_reads_as_a_sentence(self, status, body, error, words):
        with pytest.raises(error, match=".") as exc:
            call(Session(Response(status, body, text="<html>gateway</html>")),
                 what="AAPL's timeline", tier="gold")
        assert words in str(exc.value)
        assert "<html" not in str(exc.value) and exc.value.status_code == status

    def test_the_request_id_is_kept_for_support(self):
        with pytest.raises(g.EarningSpyError) as exc:
            call(Session(Response(500, {"error": "x", "request_id": "r-1"})))
        assert exc.value.request_id == "r-1"
        with pytest.raises(g.EarningSpyError) as exc:
            call(Session(Response(500, {}, headers={"x-amzn-RequestId": "r-2"})))
        assert exc.value.request_id == "r-2"

    @pytest.mark.parametrize("body", [None, [1], "text"])
    def test_a_200_that_is_not_an_object_is_a_contract_error(self, body):
        with pytest.raises(g.ContractError):
            call(Session(Response(200, body)))

    def test_every_error_is_an_earningspy_error(self):
        for name in ("AuthError", "TierError", "NotFoundError", "RequestError", "RateLimitError",
                     "ServiceError", "ContractError", "ChartError"):
            assert issubclass(getattr(g, name), g.EarningSpyError)
        assert issubclass(g.ChartError, ValueError)

    def test_a_timeline_names_its_ticker_and_its_tier(self):
        with pytest.raises(g.NotFoundError, match="ZZZZ"):
            g.fetch_timeline("ZZZZ", api_key="k", session=Session(Response(404, {})))
        with pytest.raises(g.TierError, match="gold view"):
            g.fetch_timeline("AAPL", tier="gold", api_key="k",
                             session=Session(Response(403, {"message": "Forbidden"})))
        with pytest.raises(g.TierError, match="silver view"):
            g.fetch_calendar(api_key="k", session=Session(Response(403, {"message": "Forbidden"})))


class TestPages:
    def page(self, rows, cursor=None):
        return Response(200, {"data": rows, "next_cursor": cursor})

    def test_every_page_in_order(self):
        s = Session(self.page([1], "a"), self.page([2], "b"), self.page([3]))
        assert _http.get_pages(URL, {"view": "v"}, "k", session=s)[0] == [1, 2, 3]
        assert [c["params"].get("cursor") for c in s.calls] == [None, "a", "b"]
        assert all(c["params"]["view"] == "v" for c in s.calls)

    def test_a_repeated_cursor_stops_instead_of_looping(self):
        s = Session(self.page([1], "same"))
        with pytest.raises(g.ContractError, match="repeated"):
            _http.get_pages(URL, {}, "k", session=s)
        assert len(s.calls) == 2

    def test_a_page_without_rows_is_a_contract_error(self):
        with pytest.raises(g.ContractError, match="list of rows"):
            _http.get_pages(URL, {}, "k", session=Session(Response(200, {"next_cursor": None})))

    def test_max_rows_stops_early(self):
        s = Session(self.page([1, 2], "a"), self.page([3, 4], "b"))
        assert _http.get_pages(URL, {}, "k", session=s, max_rows=3)[0] == [1, 2, 3]
        assert len(s.calls) == 2

    def test_a_failure_halfway_returns_nothing(self, waits):
        s = Session(self.page([1], "a"), Response(500, {"error": "x"}))
        with pytest.raises(g.ServiceError):
            _http.get_pages(URL, {}, "k", session=s)


class TestTheKeyStaysPut:
    def test_not_in_the_repr_nor_in_a_pickle(self):
        import pickle
        es = g.EarningSpy(api_key="esk_secret")
        assert "esk_secret" not in repr(es)
        with pytest.raises(TypeError, match="API key"):
            pickle.dumps(es)

    def test_not_in_any_error(self, waits):
        for answer in (Response(401, {}), Response(403, {}), Response(500, {}),
                       requests.ConnectionError("x"), Response(200, None)):
            with pytest.raises(g.EarningSpyError) as exc:
                call(Session(answer))
            assert "esk_secret" not in str(exc.value)

    def test_not_in_a_figure(self, payload, monkeypatch):
        import earningspy_glass.client as client
        monkeypatch.setattr(client, "fetch_peer_analysis", lambda *a, **k: payload)
        fig = g.EarningSpy(api_key="esk_secret").peers("ZZAM").plot("treemap")
        assert "esk_secret" not in fig.to_json()
