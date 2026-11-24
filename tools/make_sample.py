"""Make the sample data Glass ships, so its charts can be tried without a key.

    python tools/make_sample.py            write src/earningspy_glass/sample.json  (needs EARNINGSPY_API_KEY and the sample extra)

Every company, ticker, figure and market reading is invented from a fixed seed: no real company's data is in it. Only
the description of the measures (label, format, which end is better) is read from the API, so the charts read the same as
on live data. Scores and forward reads never ship.
"""
from __future__ import annotations

import datetime as dt
import json
import re
import warnings
from pathlib import Path

import numpy as np
from earningspy_glass import EarningSpy

SEASON, START, END, AS_OF = "2026Q2", "2026-04-01", "2026-06-30", "2026-06-30"
WEEK = ("2026-06-01", "2026-06-05")
OUT = Path(__file__).resolve().parents[1] / "src" / "earningspy_glass" / "sample.json"
INDUSTRY, SECTOR = "Sample Beverages", "Consumer Defensive"
NEVER = re.compile(r"QUALITY_|SILVER_|GOLD_|CONVICTION|ABNORMAL|EVENT_SCORE|ESPY|QS_VS|CAR_|_RET\b|OUTCOME", re.I)

# ticker-like codes start with ZZ, which no listed company uses
PEERS = ["Northfield Springs", "Amber Valley Drinks", "Bluewater Bottling", "Cedar Fizz", "Harbor Brew Soda", "Juniper Juice",
         "Larkspur Tonics", "Meadow Cola", "Orchard Pressed", "Pinecrest Waters", "Quillon Energy Drinks", "Riverbend Teas",
         "Sundale Sparkling"]
FIRST = ["Alder", "Birch", "Cobalt", "Dune", "Ember", "Fjord", "Granite", "Hazel", "Iris", "Jasper", "Kestrel", "Linden"]
SECOND = {"Technology": ["Systems", "Circuits"], "Healthcare": ["Therapeutics", "Devices"], "Financial": ["Capital", "Trust"],
          "Industrials": ["Machines", "Freight"], "Consumer Cyclical": ["Apparel", "Motors"], SECTOR: ["Foods", "Drinks"]}

MEASURES = ("market_cap", "pe", "fwd_pe", "ep", "roic", "roe", "gross_margin", "oper_margin", "net_margin", "sales_growth",
            "eps_surprise", "eps_yoy_ttm", "debt_eq", "current_ratio", "sharpe", "sortino", "starr", "information_ratio",
            "sector_information_ratio", "vol")
QUANT = ("EP", "ROIC", "ROE", "ROA", "EPS", "EPS_SURPRISE", "EPS_YOY_TTM", "EPS_Q/Q", "FCF", "QUICK_R", "CURR_R", "DEBT/EQ",
         "SHARPE_ANNZD", "SORTINO_RATIO", "FHS_GARCH_ANNZD_VOL")
RAW = ("MARKET_CAP", "P/E", "FWD_P/E", "E/P", "ROIC", "ROE", "ROA", "EPS", "EPS_SURPRISE", "EPS_YOY_TTM", "EPS_Q/Q", "FCF",
       "QUICK_R", "CURR_R", "DEBT/EQ", "SHARPE_ANNZD", "SORTINO_RATIO", "FHS_GARCH_ANNZD_VOL", "AVG_VOLUME", "VIX_PRE_2W",
       "EARNING_VIX", "DAYS_LEFT")
COLUMNS = set(RAW) | {f"{q}_QUANT" for q in QUANT}

rng = np.random.default_rng(42)


def pick(low, high, digits=4):
    return round(float(rng.uniform(low, high)), digits)


def code(i):
    return "ZZ" + chr(65 + i // 26) + chr(65 + i % 26)


def figures(anchor=False):
    """One invented company, its figures consistent with each other."""
    margin, loss = pick(0.04, 0.30), (not anchor and rng.random() < 0.18)
    ep = pick(-0.12, -0.01) if loss else pick(0.015, 0.075)
    sharpe = round(float(rng.normal(1.2 if anchor else 0.6, 0.9)), 2)
    return {"market_cap": 1.2e11 if anchor else round(float(np.exp(rng.normal(22.5, 1.5))), -6), "ep": ep,
            "pe": None if loss else round(1 / ep, 2), "fwd_pe": None if loss else round(1 / ep * rng.uniform(0.75, 1.05), 2),
            "roic": pick(-0.2, -0.02) if loss else pick(0.03, 0.28), "roe": pick(-0.3, -0.03) if loss else pick(0.05, 0.4),
            "gross_margin": round(margin + pick(0.2, 0.35), 4), "oper_margin": -margin / 3 if loss else round(margin, 4),
            "net_margin": -margin / 2 if loss else round(margin * 0.78, 4), "sales_growth": pick(-0.06, 0.24),
            "eps_surprise": pick(-0.15, 0.22), "eps_yoy_ttm": pick(-0.3, 0.45), "debt_eq": pick(0.1, 1.8, 2),
            "current_ratio": pick(0.8, 2.8, 2), "sharpe": sharpe, "sortino": round(sharpe * pick(1.3, 1.8), 2),
            "starr": round(sharpe * pick(0.5, 0.7), 2), "information_ratio": round(sharpe * pick(0.4, 1.0), 2),
            "sector_information_ratio": round(sharpe * pick(0.4, 1.1), 2), "vol": pick(0.16, 0.55)}


def ranks(values):
    """0-100 standing of each value among the ones there are; a missing value has none."""
    known = sorted(v for v in values if v is not None)
    return [None if v is None else round(100 * known.index(v) / max(1, len(known) - 1), 1) for v in values]


def market(_=None):
    state = "ELEVATED" if rng.random() < 0.2 else "NORMAL"
    vix = pick(20, 24, 2) if state == "ELEVATED" else pick(14, 19, 2)
    return {"MARKET_STATE": state, "VIX_TERM": "CONTANGO", "VIX_PRE_2W": vix, "EARNING_VIX": round(vix + pick(-1.5, 1.5), 2)}


def distribution(values):
    known = [v for v in values if v is not None]
    q = lambda p: round(float(np.quantile(known, p)), 4)  # noqa: E731
    return {"n": len(known), "n_null": len(values) - len(known), "n_excluded": 0, "p10": q(.1), "p25": q(.25), "p50": q(.5),
            "p75": q(.75), "p90": q(.9), "min": q(0), "max": q(1), "mean": round(float(np.mean(known)), 4)}


def peers(described):
    book = [figures(i == 0) for i in range(len(PEERS))]
    pct = {m: ranks([b[m] for b in book]) for m in MEASURES}
    points = []
    for i, (name, b) in enumerate(zip(PEERS, book)):
        day = dt.date(2026, 4, 20) + dt.timedelta(days=int(rng.integers(0, 25)))
        day -= dt.timedelta(days=max(0, day.weekday() - 4))
        profit = b["ep"] > 0
        points.append({"ticker": code(i), "company": name, "logo": None, "role": "anchor" if i == 0 else "reported", "reported": True,
                       "stale": False, "earnings_date": str(day), "days_left": (day - dt.date(2026, 6, 30)).days,
                       "session": "BMO" if i % 2 else "AMC", "values": b, "market": market(),
                       "profile": {"SECTOR": SECTOR, "INDUSTRY": INDUSTRY, "COUNTRY": "USA", "IS_S&P500": float(i < 4),
                                   "IS_RUSSELL": float(i >= 4)},
                       "nm": {} if profit else {"pe": "nonpositive_earnings", "fwd_pe": "nonpositive_forward_earnings"},
                       "capped": {}, "reads": {}, "carried": False,
                       "flags": {"eps_positive": profit, "ebit_positive": profit, "fcf_positive": profit,
                                 "pe_meaningful": profit, "eps_growing": b["eps_yoy_ttm"] > 0, "plot_pe": profit},
                       "percentiles": {"industry": {m: pct[m][i] for m in MEASURES}}, "cohort": {}})
    return {"schema_version": described["schema_version"], "methodology_version": described["methodology_version"],
            "request": {"anchor_ticker": code(0), "frame": "pre", "peer_group": {"column": "INDUSTRY", "value": INDUSTRY},
                        "window": {"start": START, "end": END}, "season": SEASON, "cohort": "quarter"},
            "layers_included": ["bronze", "silver"], "grade_scale": described["grade_scale"], "cohort": {"industry": {}},
            "peers_mode": "all", "horizon": "quarter", "peer_count": len(points) - 1,
            "season": {"label": SEASON, "start": START, "end": END, "cohort": "quarter", "cohort_requested": "auto",
                       "lookback": None, "counts": {"in_season": len(points) - 1, "carried": 0}, "requested": True},
            "anchor_frame": "post", "metrics": {m: described["metrics"][m] for m in MEASURES},
            "distributions": {"industry": {m: distribution([b[m] for b in book]) for m in MEASURES}},
            "basis": described["basis"], "valuation_states": described["valuation_states"],
            "points": points, "warnings": []}


def row(ticker, company, sector, industry, day, b, eps=None):
    eps = eps if eps is not None else round(b["ep"] * 80, 2)
    return {"EARNINGS_DATE": f"{day}T00:00:00", "TICKER": ticker, "COMPANY": company, "SECTOR": sector, "INDUSTRY": industry,
            "DAYS_LEFT": (day - dt.date(2026, 6, 30)).days, "IS_AMC": 1, "IS_BMO": 0,
            "IS_S&P500": int(b["market_cap"] > 2e10), "MARKET_CAP": b["market_cap"], "P/E": b["pe"], "FWD_P/E": b["fwd_pe"],
            "E/P": b["ep"], "ROIC": b["roic"], "ROE": b["roe"], "ROA": round(b["roe"] * 0.45, 4), "EPS": eps,
            "EPS_SURPRISE": b["eps_surprise"], "EPS_YOY_TTM": b["eps_yoy_ttm"], "EPS_Q/Q": pick(-0.2, 0.3),
            "FCF": round(b["market_cap"] * pick(-0.01, 0.06), -5), "QUICK_R": round(b["current_ratio"] * 0.8, 2),
            "CURR_R": b["current_ratio"], "DEBT/EQ": b["debt_eq"], "SHARPE_ANNZD": b["sharpe"], "SORTINO_RATIO": b["sortino"],
            "FHS_GARCH_ANNZD_VOL": b["vol"], "AVG_VOLUME": round(b["market_cap"] / 8e3, -4), **market()}


def standing(r, around=None):
    """Industry percentiles of one row: where the company stands, invented, steady over time when ``around`` is given."""
    for q in QUANT:
        mid = rng.uniform(5, 95) if around is None else around[q]
        r[f"{q}_QUANT"] = round(float(np.clip(mid + (0 if around is None else rng.normal(0, 6)), 0, 100)), 1)
    return r


def calendar(described):
    rows, i = [], 60
    for sector, n in (("Technology", 14), ("Consumer Cyclical", 13), ("Industrials", 8), ("Healthcare", 7), (SECTOR, 7),
                      ("Financial", 5)):
        for k in range(n):
            name = f"{FIRST[k % len(FIRST)]} {SECOND[sector][k // len(FIRST)]}"
            day = dt.date(2026, 6, 1) + dt.timedelta(days=int(rng.integers(0, 5)))
            rows.append(standing(row(code(i), name, sector, f"Sample {SECOND[sector][k % 2]}", day, figures())))
            i += 1
    rows.sort(key=lambda r: (r["EARNINGS_DATE"], r["TICKER"]))
    return frame(rows, described, frame="pre", scope="week", view="calendar_quant", tier="silver",
                 window={"scope": "week", "start": WEEK[0], "end": WEEK[1]})


def timeline(pre, post):
    days = [dt.date(2024, 7, 23), dt.date(2024, 10, 22), dt.date(2025, 2, 11), dt.date(2025, 4, 29), dt.date(2025, 7, 22),
            dt.date(2025, 10, 21), dt.date(2026, 2, 10), dt.date(2026, 4, 28)]
    eps = [2.31, 2.38, 2.42, 2.55, 2.61, 2.74, 2.86, 2.93, 3.05]
    steady = {q: float(rng.uniform(40, 88)) for q in QUANT}
    before, after = [], []
    for i, day in enumerate(days):
        b = {**figures(True), "market_cap": round(1e11 * (1 + 0.03 * i), -8)}
        base = row(code(0), PEERS[0], SECTOR, INDUSTRY, day, b, eps[i])
        before.append(standing(dict(base), steady))
        if i >= 3:
            after.append({**base, "EPS": eps[i + 1]})
    return (frame(before[::-1], pre, frame="pre", view="calendar_quant", tier="silver", ticker=code(0)),
            frame(after[::-1], post, frame="post", view="fundamentals", tier="silver", ticker=code(0)))


def facts(rows, post, weeks=False):
    """What the service would say about these rows: every report in the sample is in the past."""
    said = {}
    for r in rows:
        y, m = int(r["EARNINGS_DATE"][:4]), int(r["EARNINGS_DATE"][5:7])
        q = y * 4 + (m - 1) // 3 - (0 if post else 1)
        said[f"{r['TICKER']}|{r['EARNINGS_DATE'][:10]}"] = {"reported": True, "values_quarter": f"{q // 4}/Q{q % 4 + 1}"}
    out = {"as_of": AS_OF, "rows": said}
    if weeks:
        lows = {"FHS_GARCH_ANNZD_VOL_QUANT", "DEBT/EQ_QUANT"}
        cuts = {}
        for key in (f"{q}_QUANT" for q in QUANT):
            ordered = sorted((r[key] for r in rows), reverse=key not in lows)
            cuts[key] = {"top_10": ordered[max(1, len(ordered) // 10) - 1], "n": len(ordered)}
        out["week_cuts"] = {WEEK[0]: {"complete": True, "measures": cuts}}
        out["horizon"] = AS_OF
    return out


def frame(rows, described, **extra):
    return {"facts": facts(rows, extra["frame"] == "post", "window" in extra), "data": rows, "metrics": {k: v for k, v in described["metrics"].items() if k in COLUMNS}, "next_cursor": None,
            "layers_included": ["bronze", "silver"], "warnings": [], "count": len(rows), **extra}


def main():
    warnings.simplefilter("ignore")
    es = EarningSpy(verbose=False)
    week = es.rows("calendar_quant", "pre", tier="silver", start=WEEK[0], end=WEEK[0])
    pre, post = timeline(es.timeline("KO", tier="silver").payload,
                         es.timeline("KO", frame="post", view="fundamentals", tier="silver").payload)
    sample = {"as_of": AS_OF, "fictional": True, "peers": peers(es.peers("KO", tier="silver", season=SEASON).payload),
              "calendar": calendar(week), "timeline_pre": pre, "timeline_post": post}
    text = json.dumps(sample, ensure_ascii=False, separators=(",", ":"))
    leaked = sorted(set(NEVER.findall(text)))
    if leaked:
        raise SystemExit(f"the sample carries what must never ship: {', '.join(leaked)}")
    OUT.write_text(text + "\n")
    print(f"{OUT.name}: {len(text) / 1024:.0f} KB · peers {len(sample['peers']['points'])} · calendar "
          f"{sample['calendar']['count']} · timeline {pre['count']}/{post['count']}")


if __name__ == "__main__":
    main()
