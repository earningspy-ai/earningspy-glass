"""Reach the EarningSpy peers endpoint and hand back a usable payload.

Thin on purpose. This module knows how to call the endpoint and how to tell a
usable response from a broken one; it knows nothing about what the numbers mean.
The payload is returned unchanged so the renderer and any other consumer read
identical bytes.

Everything that can go wrong here raises — a readable, actionable exception, not
a log line and a `None`. A caller should never get a half-answer it mistakes for
a real one.
"""

import re
import warnings

from earningspy_glass._http import (
    DEFAULT_BASE_URL,
    TIERS,
    check_choice,
    clean_ticker,
    get_json,
    resolve_api_key,
    resolve_base,
)
from earningspy_glass.errors import (
    AuthError,
    ContractError,
    EarningSpyError,
    NotFoundError,
    PayloadNotice,
    RequestError,
)

__all__ = ["AuthError", "ContractError", "DEFAULT_BASE_URL", "EarningSpyError", "NotFoundError",
           "PayloadNotice", "RequestError", "TIERS", "fetch_peer_analysis", "resolve_api_key"]

# The contract this client was built against.
#
# schema_version — the shape of the payload. A different major means the shape
# moved and this client's assumptions need rechecking: a hard error.
#
# methodology_version — how the numbers inside that shape were computed. The
# client supports everything at or above a floor. Above the floor and newer than
# this client expected: the shape still renders, so it warns rather than fails —
# but the warning is one the caller sees. Below the floor: the client is newer
# than the service and must not assume more than it will get, so it fails.
SUPPORTED_SCHEMA_MAJOR = "1."
MIN_SCHEMA_MINOR = 2
EXPECTED_METHODOLOGY = "peers_pctile_v1"
MIN_METHODOLOGY = "peers_pctile_v1"

FRAMES = ("pre", "post", "delta")

# Which peers fill the anchor's reporting season. The service decides: ``auto``
# widens a thin season with last season's reports (``carried`` peers), and the
# payload says what it resolved to in ``request["cohort"]``.
COHORTS = ("auto", "quarter", "lookback")
PEER_MODES = ("all", "reported", "upcoming")
HORIZONS = ("quarter", "next")

# Service notes kept in payload["warnings"] but not printed: how the season's
# cohort was filled is for the payload (a later banner), not a notebook warning.
_QUIET_NOTICES = frozenset({"lookback_cohort", "thin_season"})

# A season names a calendar quarter of report dates, e.g. "2026Q2".
_SEASON = re.compile(r"^\d{4}Q[1-4]$")


def fetch_peer_analysis(ticker, frame=None, peer_group="industry",
                        metrics=None, base_url=None, api_key=None,
                        timeout=30, session=None, tier="bronze", cohort=None,
                        season=None, peers=None, horizon=None, limit=None):
    """Fetch one company, its peer group, and the comparison already resolved.

    This is the raw call under the client object; most users go through
    ``EarningSpy.peers`` instead. It returns the payload as delivered.

    Parameters
    ----------
    ticker : str
        The company to centre the comparison on, e.g. ``"AMBA"``.
    frame : {"pre", "post", "delta"}, optional
        Which side of the earnings event to look at. ``"pre"`` is the run-up,
        ``"post"`` the aftermath, ``"delta"`` how the company's standing moved
        from before the report to after it (a gold-tier view). Left out, it is
        ``"post"`` on the bronze view and ``"pre"`` on silver and gold —
        the silver and gold comparisons are served from the run-up view, which
        already includes the companies that have reported. ``frame="post"`` works
        at every tier: there is one after-the-report view, and your key's tier
        decides how deep it goes (industry percentiles from silver).
    peer_group : str
        How the peer set is drawn — by ``"industry"`` (default) or ``"sector"``.
    tier : {"bronze", "silver", "gold"}
        How much of the analysis to request. ``"bronze"`` is the view of the
        reported figures; ``"silver"`` adds the live quality read; ``"gold"`` adds
        the full forward view. Your key must carry the tier or the call is
        refused with a clear message — nothing downgrades silently.
    cohort : {"auto", "quarter", "lookback"}, optional
        Which peers fill the company's reporting season. ``"quarter"`` is only
        the peers that reported in it; ``"lookback"`` adds, for each peer with
        no report yet, its report from the previous season (flagged
        ``carried``); ``"auto"`` — the service default — looks back only when
        fewer than five peers have reported.
    season : str, optional
        A specific season to research, e.g. ``"2026Q2"``: the company's report in
        that season against that season's reporters. How far back depends on
        the key — bronze the current season, silver one back, gold every season.
        Left out, the company's own latest season.
    peers : {"all", "reported", "upcoming"}, optional
        Which side of the group to keep: the companies that already reported
        (``"reported"``, run-up view) or those still to report (``"upcoming"``,
        after-the-report view). Left out, all of them.
    horizon : {"quarter", "next"}, optional
        How far ahead upcoming peers are read: this quarter, or the next one too.
    limit : int, optional
        At most this many companies. Left out, the whole group.

    Credentials come from the environment (``EARNINGSPY_API_BASE``,
    ``EARNINGSPY_API_KEY``) unless passed explicitly. Nothing is hardcoded and the
    key is never placed in the URL.

    Examples
    --------
    >>> payload = fetch_peer_analysis("AMBA")                 # bronze view
    >>> payload = fetch_peer_analysis("AMBA", tier="gold")    # gold key required
    """
    check_choice("tier", tier, TIERS)
    if cohort is not None:
        check_choice("cohort", cohort, COHORTS)
    if peers is not None:
        check_choice("peers", peers, PEER_MODES)
    if horizon is not None:
        check_choice("horizon", horizon, HORIZONS)
    if season is not None and not _SEASON.match(str(season).strip().upper()):
        raise RequestError(f"season must look like '2026Q2' — got {season!r}")
    frame = resolve_peers_frame(frame, tier)

    ticker = clean_ticker(ticker)
    base = resolve_base(base_url)
    key = resolve_api_key(api_key)

    url, params = _route(base, ticker, frame, peer_group, tier)
    if metrics:
        params["metrics"] = metrics if isinstance(metrics, str) \
            else ",".join(metrics)
    if cohort:
        params["cohort"] = cohort
    if season:
        params["season"] = str(season).strip().upper()

    for name, value in (("peers", peers), ("horizon", horizon), ("limit", limit)):
        if value is not None:
            params[name] = value

    payload = get_json(url, params, key, timeout=timeout, session=session,
                       what=f"{ticker} and its peers", tier=tier)
    _check_contract(payload, ticker)
    return payload


def resolve_peers_frame(frame, tier):
    """The frame a peers call will actually be answered from.

    A left-out frame means ``pre`` on silver and gold (their comparisons are served from the
    run-up view) and ``post`` on bronze. An explicit ``frame="post"`` is answered from the
    after-the-report view at every tier.
    """
    if frame is None:
        return "post" if tier == "bronze" else "pre"
    check_choice("frame", frame, FRAMES)
    return frame


def _route(base, ticker, frame, peer_group, tier):
    """The URL and params for one call. Tier and frame decide the route.

    The before-and-after (``delta``) view is a query parameter on the post route,
    not a frame of its own. It is served from the post route itself and the gold
    entitlement is what ``?frame=delta`` requires — so it uses that URL directly
    while ``tier="gold"`` gates access (enforced by the service). The silver and
    gold routes are otherwise mounted under ``pre``: that segment names which
    frame's read scored the row, not where the peers come from — peers span both
    frames either way.
    """
    t = ticker.upper()

    if frame == "delta":
        # Served from the post route with the delta query flag; gold-gated.
        if tier != "gold":
            raise RequestError(
                "the before-and-after view is a gold view — call "
                "peers(..., frame='delta', tier='gold')"
            )
        url = f"{base}/post/bronze/ticker/{t}/peers"
        return url, {"peer_group": peer_group.lower(), "frame": "delta"}

    if frame == "post":
        # One after-the-report route for every key: the service widens it by the key's tier
        # (industry percentiles from silver), so silver and gold call it too.
        url = f"{base}/post/bronze/ticker/{t}/peers"
    elif tier == "bronze":
        url = f"{base}/{frame}/bronze/ticker/{t}/peers"
    else:
        # silver/gold run-up views are served under their own tier route.
        url = f"{base}/pre/{tier}/ticker/{t}/peers"

    return url, {"peer_group": peer_group.lower()}


def _version_tuple(methodology):
    """The trailing integer of a ``..._vN`` methodology tag, for comparison.

    Returns None when the tag does not end in ``_v<int>`` — an unrecognised shape
    is treated as "cannot compare", handled by the caller.
    """
    if not methodology:
        return None
    tail = methodology.rsplit("_v", 1)
    if len(tail) != 2 or not tail[1].isdigit():
        return None
    return int(tail[1])


def _check_contract(payload, ticker):
    """Refuse a payload this client cannot trust; warn on one it can only mostly.

    Loud where it matters: a shape mismatch or a too-old service raises. A
    newer-than-expected methodology warns through ``warnings.warn`` — which the
    caller sees — because the shape still renders but the numbers may read
    differently.
    """
    schema = str(payload.get("schema_version", ""))
    minor = schema[len(SUPPORTED_SCHEMA_MAJOR):].split(".")[0]
    if not schema.startswith(SUPPORTED_SCHEMA_MAJOR) or not minor.isdigit() or int(minor) < MIN_SCHEMA_MINOR:
        raise ContractError(
            f"the API returned schema {schema!r} for {ticker.upper()}, which "
            f"this version of earningspy-glass does not read (it expects "
            f"{SUPPORTED_SCHEMA_MAJOR}{MIN_SCHEMA_MINOR} or a later "
            f"{SUPPORTED_SCHEMA_MAJOR}x). Upgrade to the latest release."
        )

    methodology = payload.get("methodology_version")
    served = _version_tuple(methodology)
    floor = _version_tuple(MIN_METHODOLOGY)
    if served is not None and floor is not None and served < floor:
        raise ContractError(
            f"the API computed this response with {methodology!r}, older than "
            f"this version of earningspy-glass supports ({MIN_METHODOLOGY}). "
            f"This usually means the service has not caught up; if it persists, "
            f"install an older package version."
        )
    if methodology != EXPECTED_METHODOLOGY:
        warnings.warn(
            f"this response was computed with {methodology!r}, newer than the "
            f"{EXPECTED_METHODOLOGY!r} this package was built for. Charts still "
            f"render, but percentiles and grades may read differently — upgrade "
            f"to the latest release.",
            stacklevel=3,
        )

    # Payload notes (thin distributions, stale figures, …) are informational and
    # the API can repeat a code once per affected metric. Emit each DISTINCT note
    # once, under PayloadNotice so a caller can silence just these, and point the
    # stack at the caller — not library internals. describe() lists them in full
    # and they remain in payload["warnings"] for anyone who wants every instance.
    seen = set()
    for warning in payload.get("warnings") or []:
        if warning.get("code") in _QUIET_NOTICES:
            continue
        text = f"[{warning.get('code')}] {warning.get('detail')}"
        if text in seen:
            continue
        seen.add(text)
        warnings.warn(text, PayloadNotice, stacklevel=4)
