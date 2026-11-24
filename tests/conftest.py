"""Shared fixtures. No network, no credentials — a recorded payload loaded from
disk, exactly as the API would have delivered it."""
import json
from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def payload():
    """A minimal but contract-shaped bronze peers payload."""
    return json.loads((FIXTURES / "peers_min.json").read_text())


@pytest.fixture
def post_resolved_payload():
    """A post payload whose reported peers carry the resolved (pre_event_*)
    reads while the live ones are empty — the real-world post-earnings case."""
    return json.loads((FIXTURES / "peers_post_resolved.json").read_text())


@pytest.fixture
def calendar_pre():
    """A pre (upcoming) calendar_quant payload: flat rows + the metrics table,
    recorded live. 31 percentile levels, no change columns."""
    payload = json.loads((FIXTURES / "calendar_pre_quant.json").read_text())
    payload.setdefault("frame", "pre")
    payload.setdefault("scope", "month_from_date")
    return payload


@pytest.fixture
def calendar_post():
    """A post (reported) calendar_quant payload: 23 levels, 23 across-event
    changes, 2 carried pre-event ranks, recorded live."""
    payload = json.loads((FIXTURES / "calendar_post_quant.json").read_text())
    payload.setdefault("frame", "post")
    payload.setdefault("scope", "quarter_to_date")
    return payload


@pytest.fixture
def timeline_pre():
    """ZZAP pre fundamentals timeline — 9 quarters of raw figures, recorded live."""
    return json.loads((FIXTURES / "timeline_pre_fund.json").read_text())


@pytest.fixture
def timeline_pre_quant():
    """ZZAP pre calendar_quant timeline — raw figures plus industry percentiles."""
    return json.loads((FIXTURES / "timeline_pre_quant.json").read_text())


@pytest.fixture
def timeline_post():
    """ZZAP post fundamentals timeline — 5 quarters, reported-at-event figures."""
    return json.loads((FIXTURES / "timeline_post_fund.json").read_text())
