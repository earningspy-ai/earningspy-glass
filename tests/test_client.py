"""The client wrapper: it threads config to the fetch layer and returns a Peers
object that can describe and plot. No network — the fetch is monkeypatched."""
import pytest
import plotly.graph_objects as go

import earningspy_glass.client as client_mod
from earningspy_glass import EarningSpy
from earningspy_glass.client import Peers


def test_peers_returns_wrapper_and_threads_tier(payload, monkeypatch):
    seen = {}

    def fake_fetch(ticker, **kw):
        seen.update(ticker=ticker, **kw)
        return payload

    monkeypatch.setattr(client_mod, "fetch_peer_analysis", fake_fetch)

    es = EarningSpy(api_key="k")
    peers = es.peers("zzam", tier="gold", frame="pre")

    assert isinstance(peers, Peers)
    assert seen["ticker"] == "zzam"
    assert seen["tier"] == "gold"
    assert seen["frame"] == "pre"
    assert seen["api_key"] == "k"


def test_peers_plot_and_describe(payload, monkeypatch):
    monkeypatch.setattr(client_mod, "fetch_peer_analysis",
                        lambda ticker, **kw: payload)
    peers = EarningSpy(api_key="k").peers("ZZAM")
    assert isinstance(peers.plot("scatter", x="pe", y="roic"), go.Figure)
    assert peers.describe().anchor == "ZZAM"


def test_repr_is_readable(payload, monkeypatch):
    monkeypatch.setattr(client_mod, "fetch_peer_analysis",
                        lambda ticker, **kw: payload)
    peers = EarningSpy(api_key="k").peers("ZZAM")
    assert "ZZAM" in repr(peers)


def test_version_is_exposed():
    import earningspy_glass
    assert isinstance(earningspy_glass.__version__, str)


def test_the_client_refuses_to_start_without_a_key(monkeypatch):
    from earningspy_glass.api import AuthError
    monkeypatch.delenv("EARNINGSPY_API_KEY", raising=False)
    with pytest.raises(AuthError, match="EARNINGSPY_API_KEY"):
        EarningSpy()


def test_the_client_reads_the_key_from_the_environment(monkeypatch):
    monkeypatch.setenv("EARNINGSPY_API_KEY", "env-key")
    assert EarningSpy()._api_key == "env-key"


def test_warnings_prints_each_service_note_once(payload, capsys):
    payload["warnings"] = [{"code": "thin_distribution", "detail": "fwd_pe: 1 usable values"},
                           {"code": "thin_distribution", "detail": "fwd_pe: 1 usable values"},
                           {"code": "stale_figures", "detail": "prior-quarter figures"}]
    notes = Peers(payload).warnings()
    out = capsys.readouterr().out
    assert out.count("[thin_distribution] fwd_pe: 1 usable values") == 1
    assert "[stale_figures] prior-quarter figures" in out
    assert [n["code"] for n in notes] == ["thin_distribution", "stale_figures"]


def test_a_fetch_logs_what_came_back(payload, monkeypatch, caplog):
    import logging
    monkeypatch.setattr(client_mod, "fetch_peer_analysis", lambda ticker, **kw: payload)
    with caplog.at_level(logging.INFO, logger="earningspy_glass.client"):
        EarningSpy(api_key="k").peers("zzam")
    msg = caplog.records[-1].getMessage()
    assert msg.startswith("ZZAM · Semiconductors · post view · bronze tier")
    assert f"{len(payload['points'])} companies" in msg


def test_creating_the_client_readies_a_notebook(monkeypatch):
    """Fetch summaries print on the package logger and the service notes are filtered,
    without the user configuring anything."""
    import logging
    import warnings
    from earningspy_glass import _defaults
    from earningspy_glass.api import PayloadNotice
    log = logging.getLogger("earningspy_glass")
    monkeypatch.setattr(_defaults, "_applied", False)
    monkeypatch.setattr(log, "handlers", [])
    monkeypatch.setattr(log, "level", logging.NOTSET)
    monkeypatch.setattr(logging.getLogger(), "handlers", [])
    with warnings.catch_warnings():
        EarningSpy(api_key="k")
        assert any(f[0] == "ignore" and f[2] is PayloadNotice for f in warnings.filters)
    assert len(log.handlers) == 1 and log.level == logging.INFO
    EarningSpy(api_key="k")                       # once per process
    assert len(log.handlers) == 1


def test_verbose_false_silences_the_fetch_summaries(monkeypatch):
    import logging
    log = logging.getLogger("earningspy_glass")
    monkeypatch.setattr(log, "level", logging.NOTSET)
    EarningSpy(api_key="k", verbose=False)
    assert not log.isEnabledFor(logging.INFO)


def test_a_configured_logging_setup_is_left_alone(monkeypatch):
    import logging
    from earningspy_glass import _defaults
    log = logging.getLogger("earningspy_glass")
    monkeypatch.setattr(_defaults, "_applied", False)
    monkeypatch.setattr(log, "handlers", [])
    monkeypatch.setattr(logging.getLogger(), "handlers", [logging.NullHandler()])
    EarningSpy(api_key="k")
    assert log.handlers == []
