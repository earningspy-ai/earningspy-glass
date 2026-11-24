"""Helpers the Glass tests share."""


def answer(payload, reported=None, week_cuts=None, horizon=None, listing_of=None):
    """Give a hand-made payload the facts a service would, for the rows it holds."""
    rows, post = {}, payload.get("frame") == "post"
    for r in payload.get("data") or []:
        day = str(r.get("EARNINGS_DATE"))[:10]
        fact = {"reported": post if reported is None else reported}
        if day[:4].isdigit():
            q = int(day[:4]) * 4 + (int(day[5:7]) - 1) // 3 - (0 if post else 1)
            fact["values_quarter"] = f"{q // 4}/Q{q % 4 + 1}"
        if (listing_of or {}).get(r.get("TICKER")):
            fact["listing_of"] = listing_of[r["TICKER"]]
        rows[f"{r.get('TICKER')}|{day}"] = fact
    payload["facts"] = {"as_of": "2026-09-20", "rows": rows, "week_cuts": week_cuts or {},
                        **({"horizon": horizon} if horizon else {})}
    return payload
