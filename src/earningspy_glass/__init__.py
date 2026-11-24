"""earningspy-glass — the official Python client for the EarningSpy API.

Fetch a company against its peer group and turn the comparison into a chart. The
API resolves every financial judgement before the wire; this package transmits
that judgement without adding to it — it positions marks and reads labels.

Quick start
-----------
>>> from earningspy_glass import EarningSpy
>>> es = EarningSpy()                          # reads EARNINGSPY_API_KEY
>>> peers = es.peers("AMBA")                   # the bronze view
>>> peers.describe()                           # what can I plot?
>>> peers.plot("scatter", x="pe", y="roic").show()

No key yet? ``sample().plot("scatter", x="pe", y="roic").show()`` draws from
invented companies. Live data needs a key: bronze, silver or gold. See the
field catalogue and API reference at https://earningspy.ai.
"""

from earningspy_glass._version import __version__
from earningspy_glass.api import fetch_peer_analysis
from earningspy_glass.calendar_api import fetch_calendar
from earningspy_glass.calendar_charts import calendar_metrics
from earningspy_glass.calendar_charts import describe as describe_calendar
from earningspy_glass.calendar_charts import plot as plot_calendar
from earningspy_glass.catalog import catalog, fields
from earningspy_glass.client import Calendar, EarningSpy, Peers, Timeline
from earningspy_glass.errors import (
    AuthError,
    ChartError,
    ContractError,
    EarningSpyError,
    NotFoundError,
    PayloadNotice,
    RateLimitError,
    RequestError,
    ServiceError,
    TierError,
)
from earningspy_glass.filters import Filter
from earningspy_glass.rows import fetch_rows, fetch_schema
from earningspy_glass.samples import sample
from earningspy_glass.timeline_api import fetch_timeline
from earningspy_glass.timeline_charts import bars as timeline_bars
from earningspy_glass.timeline_charts import compare as timeline_compare
from earningspy_glass.timeline_charts import radar as timeline_radar
from earningspy_glass.timeline_charts import describe as describe_timeline
from earningspy_glass.graph_client import (
    PEER_COUNTS,
    available_metrics,
    describe,
    only,
    plot,
    recipe,
)

__all__ = [
    "EarningSpy",
    "Peers",
    "Calendar",
    "Timeline",
    "Filter",
    "sample",
    "catalog",
    "fields",
    "fetch_peer_analysis",
    "fetch_calendar",
    "fetch_timeline",
    "fetch_rows",
    "fetch_schema",
    "timeline_bars",
    "timeline_compare",
    "timeline_radar",
    "describe_timeline",
    "plot",
    "recipe",
    "only",
    "PEER_COUNTS",
    "plot_calendar",
    "describe",
    "describe_calendar",
    "calendar_metrics",
    "available_metrics",
    "EarningSpyError",
    "AuthError",
    "TierError",
    "RateLimitError",
    "ServiceError",
    "NotFoundError",
    "RequestError",
    "ContractError",
    "ChartError",
    "PayloadNotice",
    "__version__",
]
