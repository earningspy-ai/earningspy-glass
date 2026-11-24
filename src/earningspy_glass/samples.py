"""Charts to try before you have a key: a small set of invented companies.

It ships inside the package, so nothing here reaches the network. No company,
figure or market reading in it is real: it shows what the charts look like.
"""

import copy
import json
from functools import lru_cache
from pathlib import Path

from earningspy_glass.errors import RequestError

KINDS = ("peers", "calendar", "timeline")
_FILE = Path(__file__).with_name("sample.json")


@lru_cache(maxsize=1)
def _data():
    return json.loads(_FILE.read_text(encoding="utf-8"))


def _payload(name):
    payload = copy.deepcopy(_data()[name])
    payload["sample"] = {"as_of": _data()["as_of"]}
    return payload


def sample(kind="peers"):
    """A ready-made comparison, calendar or history to try the charts on. No key needed.

    The companies and figures are invented, and every chart says so in its title.

    Parameters
    ----------
    kind : {"peers", "calendar", "timeline"}
        One company against its industry, one week of reports, or one
        company across its quarters.

    Returns
    -------
    Peers, Calendar or Timeline
        What :class:`EarningSpy` would return, so everything you try here works the
        same on live data.

    Examples
    --------
    >>> from earningspy_glass import sample
    >>> sample().plot("scatter", x="pe", y="roic").show()
    >>> sample("calendar").plot("bar_pctile", metric="roic_quant").show()
    >>> sample("timeline").bars("EPS").show()
    """
    from earningspy_glass.client import Calendar, Peers, Timeline

    if kind not in KINDS:
        raise RequestError(f"there is a sample of {', '.join(KINDS)}; got {kind!r}")
    if kind == "peers":
        return Peers(_payload("peers"))
    if kind == "calendar":
        return Calendar(_payload("calendar"))
    return Timeline(_payload("timeline_pre"), _fetch_other=lambda: _payload("timeline_post"))


def mark(fig, payload):
    """Say in the title that a chart shows sample data; any other chart is untouched."""
    about = payload.get("sample")
    if about:
        head, line, rest = (fig.layout.title.text or "").partition("<br>")
        note = "SAMPLE DATA, invented companies"
        fig.update_layout(title_text=f"{head} · {note}{line}{rest}" if head else note)
    return fig
