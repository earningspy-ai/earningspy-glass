"""What the service says about the rows it delivered.

Asked for with every calendar and timeline, and read here, never worked out: which
quarter a row's figures belong to, whether the company has reported, which listing
of a company to show, where a week's top 10% begins, how far the calendar reaches.
"""

import datetime

from earningspy_glass.errors import ContractError

ASK = {"facts": 1}                           # on every request that draws a chart


def facts_of(payload):
    """The payload's facts. A payload fetched without them is refused, not guessed at."""
    found = payload.get("facts")
    if not isinstance(found, dict) or not isinstance(found.get("rows"), dict):
        raise ContractError("this payload does not carry what the charts read (it was saved by an older "
                            "version, or fetched some other way). Fetch it again with this version.")
    return found


def of_row(payload, row):
    """What is known about one row; empty for a row whose date cannot be read."""
    return facts_of(payload)["rows"].get(f"{row.get('TICKER')}|{str(row.get('EARNINGS_DATE'))[:10]}") or {}


def listed(payload):
    """The rows to show: one listing per company, as the service chose it."""
    return [r for r in payload.get("data") or [] if "listing_of" not in of_row(payload, r)]


def quarter(payload, row):
    """The quarter the row's figures belong to ("2026/Q3"), or None."""
    return of_row(payload, row).get("values_quarter")


def reported(payload, row):
    """Whether the company has reported: True, False, or None when it is not known."""
    return of_row(payload, row).get("reported")


def week_cut(payload, monday, metric):
    """{"value", "complete"}: where the top 10% of that earnings week begins on a
    measure, and whether the week is over. None when the service has no cut for it."""
    week = (facts_of(payload).get("week_cuts") or {}).get(str(monday)) or {}
    cut = (week.get("measures") or {}).get(metric)
    return {"value": cut["top_10"], "complete": bool(week.get("complete"))} if cut else None


def horizon(payload):
    """The last day the upcoming calendar covers, or None."""
    day = facts_of(payload).get("horizon")
    return datetime.date.fromisoformat(day) if day else None


def merge(pages):
    """One facts block out of every page's: the rows together, the rest as first given."""
    found = [p for p in pages if isinstance(p, dict)]
    if not found:
        return None
    return {**found[0], "rows": {k: v for p in found for k, v in (p.get("rows") or {}).items()}}
