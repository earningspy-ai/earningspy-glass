"""The field catalogue — what you can ask for, without fetching a payload first.

``describe()`` tells you what a payload in hand offers; this tells you what the
API offers at all — every field, its group, tier and frame, and the views that
bundle them — read from the public catalogue at earningspy.ai. No key needed:
the catalogue is public, so a newcomer can see the menu before they even have a
payload.

    from earningspy_glass import catalog
    catalog()                       # the whole menu, grouped
    catalog("roic")                 # fields matching a term
    catalog(group="quantiles")      # one group
    catalog(frame="post", tier="silver")

It returns the rows it prints, so a program can use them too.
"""

import json
from pathlib import Path
from urllib.parse import urlsplit
from urllib.request import url2pathname

import requests

from earningspy_glass._http import USER_AGENT
from earningspy_glass.errors import ServiceError

CATALOG_URL = "https://earningspy.ai/api/catalog.json"


class _CatalogRows(list):
    """The rows catalog() returns — a plain list, but with a quiet repr.

    catalog() PRINTS the readable menu; it also returns the rows so a program can
    use them. In a notebook the returned value is echoed, and a raw list of
    tuples would dump the whole menu a second time as noise. This keeps the value
    a real list (index it, iterate it, ``list(...)`` it) while its echo is a
    single summary line, not the tuple wall.
    """

    def __repr__(self):
        return f"<{len(self)} catalogue fields — printed above>"

# Fetched once per process — the catalogue changes on a deploy cadence, not per
# call, so re-fetching on every lookup would be wasteful. Pass refresh=True to
# force a re-fetch.
_CACHE = {}


def _read(url, timeout):
    """The catalogue document at ``url``: the public one, or a saved copy (file://)."""
    parts = urlsplit(url)
    if parts.scheme == "file":
        doc = json.loads(Path(url2pathname(parts.path)).read_text(encoding="utf-8"))
    else:
        response = requests.get(url, timeout=timeout, headers={"User-Agent": USER_AGENT})
        response.raise_for_status()
        doc = response.json()
    if not isinstance(doc, dict):
        raise ValueError("not a catalogue")
    return doc


def _load(base_url=None, timeout=15, refresh=False):
    url = base_url or CATALOG_URL
    if refresh or url not in _CACHE:
        try:
            _CACHE[url] = _read(url, timeout)
        except (requests.RequestException, ValueError, OSError) as exc:
            raise ServiceError(
                f"could not load the field catalogue from {url}: {exc}. "
                f"Check your connection; the catalogue is a public URL and needs "
                f"no key."
            ) from exc
    return _CACHE[url]


def _field_rows(doc, query=None, group=None, frame=None, tier=None):
    """The catalogue's fields, filtered and de-duplicated across frames.

    The catalogue lists a field once per frame it lives on; the same name on pre
    and post is one field to a reader, so rows are merged by name and the frames
    combined into one badge ('pre,post'). Returned as
    (name, label, group, tier, frame_str).
    """
    q = (query or "").strip().lower()
    merged = {}
    order = []
    for f in doc.get("fields", []):
        if group and f.get("group") != group:
            continue
        if frame and f.get("frame") not in (frame, None):
            continue
        if tier and f.get("tier") != tier:
            continue
        if q and q not in f.get("name", "").lower() \
                and q not in str(f.get("label", "")).lower():
            continue
        name = f.get("name")
        if name not in merged:
            merged[name] = {"label": f.get("label", ""),
                            "group": f.get("group", ""),
                            "tier": f.get("tier", ""), "frames": []}
            order.append(name)
        fr = f.get("frame")
        if fr and fr not in merged[name]["frames"]:
            merged[name]["frames"].append(fr)

    rows = []
    for name in order:
        m = merged[name]
        frame_str = ",".join(m["frames"]) if m["frames"] else ""
        rows.append((name, m["label"], m["group"], m["tier"], frame_str))
    return sorted(rows, key=lambda r: (r[2], r[0]))


def catalog(query=None, *, group=None, frame=None, tier=None, base_url=None,
            refresh=False, show_views=True):
    """Print the field catalogue — everything you can ask for — and return it.

    The menu the whole API offers, not just a payload in hand: every field with
    its group, the tier that unlocks it, and the frame(s) it lives on, grouped
    for reading; plus the views that bundle fields. Read from the public
    catalogue at earningspy.ai, so no key is needed.

    Parameters
    ----------
    query : str, optional
        Keep only fields whose name or label contains this (case-insensitive):
        ``catalog("roic")``, ``catalog("volatility")``.
    group : str, optional
        One group only — e.g. ``"quantiles"``, ``"risk"``, ``"valuation"``.
        Run ``catalog()`` once to see the group names.
    frame : {"pre", "post"}, optional
        Fields available on that frame (frame-agnostic fields always show).
    tier : {"bronze", "silver", "gold"}, optional
        Fields the given tier unlocks.
    show_views : bool
        Also list the views that bundle fields (default True; a query or filter
        turns it off so the field list stays the focus).

    Returns
    -------
    list of (name, label, group, tier, frame)
        The rows printed, so a program can use them too.

    Examples
    --------
    >>> catalog()                          # the whole menu
    >>> catalog("roic")                    # fields mentioning ROIC
    >>> catalog(group="quantiles")         # the percentile family
    >>> catalog(frame="post", tier="silver")
    """
    doc = _load(base_url=base_url, refresh=refresh)
    rows = _field_rows(doc, query=query, group=group, frame=frame, tier=tier)

    version = doc.get("version", "?")
    filt = ", ".join(x for x in (
        f"'{query}'" if query else "",
        f"group={group}" if group else "",
        f"frame={frame}" if frame else "",
        f"tier={tier}" if tier else "",
    ) if x)
    header = f"EarningSpy catalogue {version} — {len(rows)} fields"
    if filt:
        header += f"  ({filt})"
    print(header)
    print("=" * min(len(header), 72))

    # Fields, grouped by group. Show name (the key you pass), label, tier·frame.
    last = None
    for name, label, grp, tier_, frame_ in rows:
        if grp != last:
            print(f"\n  {grp}:")
            last = grp
        badge = "·".join(x for x in (tier_, frame_) if x)
        print(f"    {name:<28} {label}"
              + (f"   [{badge}]" if badge else ""))

    # Views — the ready-made bundles — unless a filter has narrowed the view.
    if show_views and not (query or group or frame or tier):
        views = doc.get("views", [])
        if views:
            print(f"\n  views ({len(views)}) — ready-made column bundles:")
            for v in views:
                req = v.get("requires")
                gate = f" (needs {req})" if req else ""
                cols = v.get("column_count")
                cols = f"{cols} cols · " if cols else ""
                print(f"    {v.get('name','?'):<20} {v.get('frame','')}  "
                      f"{cols}{v.get('summary','')}{gate}")

    print("\nPass a field's NAME as x / y / metric / color. Full reference: "
          "https://earningspy.ai (catalogue) and the API docs.")
    # A list, so it is still fully usable — but its echo is one line, not a
    # second dump of every row (the menu was just printed above).
    return _CatalogRows(rows)


# `fields` is an alias — the same helper under the name people reach for first.
fields = catalog
