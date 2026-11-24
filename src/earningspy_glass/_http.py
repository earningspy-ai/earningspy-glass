"""The one place this package talks to the network.

Every fetch goes through :func:`get_json` or :func:`get_pages`, so the key, the
timeout, the retries and the translation of a failure are decided once. Nothing
here knows what a payload means.

- The key travels in a header, only over HTTPS, and never follows a redirect.
- A throttled or briefly unavailable service is retried; every call is a read,
  so repeating one is safe. A refusal (a bad key, a bad parameter) never is.
- Whatever goes wrong raises an :class:`~earningspy_glass.errors.EarningSpyError`.
"""

import os
import re
import threading
import time
from urllib.parse import urlsplit

import requests

from earningspy_glass._version import __version__
from earningspy_glass import facts as _facts
from earningspy_glass.errors import (
    AuthError,
    ContractError,
    EarningSpyError,
    NotFoundError,
    RateLimitError,
    RequestError,
    ServiceError,
    TierError,
)

DEFAULT_BASE_URL = "https://api.earningspy.ai/v1"
USER_AGENT = f"earningspy-glass/{__version__}"

# What a key can unlock, cheapest first. The tier selects the route; the service
# enforces whether the key is entitled to it — this client never guesses from the key.
TIERS = ("bronze", "silver", "gold")

RETRIES = 2                                  # after the first try
RETRY_STATUS = frozenset({429, 502, 503, 504})
BACKOFF = 0.5                                # seconds, doubled per retry
MAX_WAIT = 20.0                              # longer than this, the caller decides

_LOCAL_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})
_TICKER = re.compile(r"^(?=.*[A-Z0-9])[A-Z0-9.\-]{1,12}$")
_local = threading.local()
_sleep = time.sleep                          # replaced in tests


def resolve_base(base_url=None):
    """The API base a call will use: the one passed, else ``EARNINGSPY_API_BASE``,
    else the public API. HTTPS only (a local test server may be plain HTTP), so a
    key is never sent in the clear."""
    base = (base_url or os.environ.get("EARNINGSPY_API_BASE")
            or DEFAULT_BASE_URL).rstrip("/")
    parts = urlsplit(base)
    if not parts.hostname or not (
            parts.scheme == "https"
            or (parts.scheme == "http" and parts.hostname in _LOCAL_HOSTS)):
        raise RequestError(f"the API base must be an https:// URL — got {base!r}.")
    return base


def resolve_api_key(api_key=None):
    """The key a call will send: the one passed, else ``EARNINGSPY_API_KEY``.

    Raises AuthError when there is neither — every call needs one, so the client
    says so before it is used rather than at the first chart.
    """
    key = (api_key or os.environ.get("EARNINGSPY_API_KEY") or "").strip()
    if not key:
        raise AuthError(
            "no API key found. Pass api_key=... or set the EARNINGSPY_API_KEY "
            "environment variable. Keys are available at https://earningspy.ai."
        )
    if not key.isprintable() or not key.isascii():
        raise AuthError("the API key contains characters a key never has — copy it again.")
    return key


def clean_ticker(ticker):
    """The ticker as it goes in a URL: upper case, and only what a symbol can hold."""
    symbol = str(ticker or "").strip().upper()
    if not _TICKER.match(symbol):
        raise RequestError(f"ticker must be a symbol like 'AAPL' or 'BRK.B' — got {ticker!r}.")
    return symbol


def check_choice(name, value, allowed):
    """Refuse a value outside a known set before any request is made."""
    if value not in allowed:
        raise RequestError(f"{name} must be one of {', '.join(allowed)} — got {value!r}")
    return value


def _session():
    """A connection-reusing session, one per thread (a session is not thread-safe)."""
    if not hasattr(_local, "session"):
        _local.session = requests.Session()
    return _local.session


def _retry_after(response):
    """The wait the service asked for, in seconds, or None."""
    try:
        return max(0.0, float((getattr(response, "headers", None) or {}).get("Retry-After")))
    except (TypeError, ValueError):
        return None


def get_json(url, params, key, *, timeout=30, session=None, what="this call",
             tier=None, retries=RETRIES):
    """GET one JSON object, or raise a readable error.

    ``what`` names the thing asked for ("AAPL's timeline") and ``tier`` the view,
    for the message only.
    """
    get = (session or _session()).get
    host = urlsplit(url).hostname
    for attempt in range(retries + 1):
        last = attempt == retries
        try:
            response = get(url, params=params, timeout=timeout, allow_redirects=False,
                           headers={"x-api-key": key, "Accept": "application/json",
                                    "User-Agent": USER_AGENT})
        except requests.ConnectionError as exc:
            if last:
                raise ServiceError(f"could not reach the EarningSpy API at {host}: "
                                   f"{type(exc).__name__}. Check your connection.") from exc
            _sleep(BACKOFF * 2 ** attempt)
            continue
        except requests.Timeout as exc:
            raise ServiceError(f"the EarningSpy API at {host} did not answer within "
                               f"{timeout} seconds.") from exc
        except requests.RequestException as exc:
            raise EarningSpyError(f"could not reach the EarningSpy API at {host}: "
                                  f"{type(exc).__name__}.") from exc

        if response.status_code == 200:
            return _json_object(response, what)
        error = translate(response, what, tier)
        wait = _retry_after(response)
        wait = BACKOFF * 2 ** attempt if wait is None else wait
        spent = getattr(error, "quota_spent", False)
        if last or spent or response.status_code not in RETRY_STATUS or wait > MAX_WAIT:
            raise error
        _sleep(wait)
    raise AssertionError("unreachable")      # pragma: no cover


def get_pages(url, params, key, max_rows=None, **kwargs):
    """Follow ``next_cursor`` until it runs out (or ``max_rows`` are in); return
    (the rows, the last envelope, with every page's facts)."""
    rows, cursor, seen, facts = [], None, set(), []
    while True:
        page = dict(params, **({"cursor": cursor} if cursor else {}))
        envelope = get_json(url, page, key, **kwargs)
        data = envelope.get("data")
        if not isinstance(data, list):
            raise ContractError(
                "the API answered without a list of rows, which this version of "
                "earningspy-glass does not read. Upgrade to the latest release.")
        rows.extend(data)
        facts.append(envelope.get("facts"))
        cursor = envelope.get("next_cursor")
        if max_rows is not None and len(rows) >= max_rows:
            return rows[:max_rows], _with_facts(envelope, facts)
        if not cursor:
            return rows, _with_facts(envelope, facts)
        if cursor in seen:
            raise ContractError("the API repeated a page, so the list cannot be completed.")
        seen.add(cursor)


def _with_facts(envelope, pages):
    facts = _facts.merge(pages)
    return {**envelope, "facts": facts} if facts else envelope


def _json_object(response, what):
    try:
        body = response.json()
    except ValueError as exc:
        raise ContractError(f"the API's answer for {what} is not JSON — a proxy or "
                            f"firewall may have replaced it.") from exc
    if not isinstance(body, dict):
        raise ContractError(f"the API's answer for {what} is not the object expected.")
    return body


def _detail(response):
    """The service's own words about a failure, plus its request id."""
    try:
        body = response.json()
    except ValueError:
        body = None
    request_id = (getattr(response, "headers", None) or {}).get("x-amzn-RequestId")
    if not isinstance(body, dict):
        text = "" if body is not None else str(getattr(response, "text", "") or "").strip()
        # An HTML error page is not words for a person.
        return ("" if text.startswith("<") else text[:300]), request_id
    words = body.get("error") or body.get("message") or ""
    words = words.strip() if isinstance(words, str) else ""
    return words, body.get("request_id") or request_id


def translate(response, what, tier=None):
    """Turn an HTTP failure into an error a person can act on.

    The service's own words are kept where they say something. A bare status word
    ("Forbidden") is replaced with what it means and what to do.
    """
    code = response.status_code
    detail, request_id = _detail(response)
    meta = {"status_code": code, "request_id": request_id}
    view = f"the {tier} view" if tier else "this view"
    if code == 401:
        return AuthError("your API key was not accepted. Check it is complete and still "
                         "active: https://earningspy.ai.", **meta)
    if code == 403:
        # The gateway answers a bare "Forbidden" for a key it does not know, and the
        # service explains itself when the tier is what is missing. Only the second
        # is certain, so the first names both.
        if detail and not re.match(r"(forbidden|user is not authorized)", detail, re.I):
            return TierError(f"{detail} — {view} needs a higher plan: see "
                             f"https://earningspy.ai.", **meta)
        return TierError(f"your key was refused for {view}: it does not carry that tier, "
                         f"or it is no longer active. See https://earningspy.ai.", **meta)
    if code == 404:
        return NotFoundError(detail or f"nothing on file for {what} — check the ticker.", **meta)
    if code == 429:
        wait = _retry_after(response)
        if re.search(r"limit exceeded|quota", detail, re.I):
            return RateLimitError("your key's quota for the period is used up. See "
                                  "https://earningspy.ai for a higher plan.",
                                  retry_after=wait, quota_spent=True, **meta)
        return RateLimitError("too many requests too quickly — wait "
                              + (f"{wait:.0f} seconds" if wait else "a moment")
                              + " and retry.", retry_after=wait, **meta)
    if 300 <= code < 400:
        return RequestError("the API base redirects elsewhere; your key was not sent on. "
                            "Set the base URL to the API itself.", **meta)
    if 400 <= code < 500:
        return RequestError(detail or f"the request for {what} was rejected (HTTP {code}).", **meta)
    return ServiceError(detail or f"the EarningSpy API could not answer (HTTP {code}) — "
                        f"try again in a moment.", **meta)
