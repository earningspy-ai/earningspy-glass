# earningspy-glass

The official Python client for the [EarningSpy](https://earningspy.ai) API.

Fetch a company against its peer group and turn the comparison into a chart. The
API resolves every financial judgement before it reaches you; this package
transmits that judgement without adding to it — it positions the marks and reads
the labels, and leaves the finance to the API.

## Install

```bash
pip install earningspy-glass
```

Python 3.10 to 3.13.

## Try it, no key needed

```python
from earningspy_glass import sample

sample().plot("grade_card").show()                            # a company vs its industry
sample("calendar").plot("bar_pctile", metric="roic_quant").show()
sample("timeline").bars("EPS").show()
```

The sample ships with the package. Its companies and figures are invented, and every
chart says so. Live data needs an API key.

## Use

```python
from earningspy_glass import EarningSpy

es = EarningSpy()                 # reads EARNINGSPY_API_KEY from the environment
peers = es.peers("AMBA")          # the bronze view of AMBA vs its industry

peers.describe()                  # what companies and measures can I chart?
peers.plot("scatter", x="pe", y="roic").show()   # value vs quality
peers.plot("grade_card").show()                   # a report card for AMBA
```

`describe()` prints the measures you can put on a chart and — if your key is on a
lower tier — what a higher tier would add.

## The chart kinds

Every chart works on any measures the payload carries; run `peers.describe()` to
see them for the company in hand.

| Kind | What it answers |
|---|---|
| `scatter` | Who stands out when you plot two measures against each other |
| `treemap` | The whole peer group at a glance, sized by company size |
| `bar` | The peer group ranked on one measure |
| `bar_signed` | Who is in the black and who is in the red |
| `bar_pctile` | Every company on a common 0–100 "better" scale |
| `radar` | One company's strengths and weaknesses as a shape |
| `donut` | How the group divides up — by size, or by who is making money |
| `grade_card` | A one-company report card, a letter grade per measure |
| `dumbbell` | How a company's standing moved before vs after its report |
| `shift_scatter` | Who actually gained ground on peers after reporting |

`dumbbell` and `shift_scatter` need the before-and-after view: fetch with
`frame="delta"` and a gold key.

## Tiers

Live data needs a key from [earningspy.ai](https://earningspy.ai). **bronze** is every
company's raw values and changes;
**silver** adds where each company stands in its industry — percentiles, grades,
industry distributions and medians (`bar_pctile`, `radar` and `grade_card` need
it) — and a live read of each company's quality; **gold** adds the full forward
view and the before-and-after comparison. Pick a tier per call:

```python
es.peers("AMBA")                        # bronze key
es.peers("AMBA", tier="silver")         # silver key
es.peers("AMBA", frame="delta", tier="gold")
```

Ask for a tier your key does not carry and you get a clear message saying so —
nothing silently gives you less. Get a key at [earningspy.ai](https://earningspy.ai).

## Everything the API serves

The charts read three views: `es.peers(...)` (a company against its group),
`es.calendar(...)` (a window of reports) and `es.timeline(...)` (a company's own
quarters). The rest of the API is one call away:

```python
es.rows("sp500", period="next_week")["data"]                 # a view's rows
es.rows("fundamentals", frame="post", ticker="AAPL")["data"] # one company's history
es.schema()["columns"]                                       # every column
es.catalog("roic")                                           # the field menu
```

## Configuration

- `EARNINGSPY_API_KEY` — your key. Or pass `EarningSpy(api_key="...")`.
- `EARNINGSPY_API_BASE` — override the API base URL (rarely needed).

## Errors

Everything that goes wrong raises a readable exception rather than returning a
half-answer. All of them derive from `EarningSpyError`:

- `AuthError` — no key, or one the service does not accept.
- `TierError` — the key does not carry the tier you asked for (an `AuthError`).
- `NotFoundError` — no such company.
- `RequestError` — a bad parameter or the wrong view.
- `RateLimitError` — too many requests, or the quota is used up (a `RequestError`).
- `ServiceError` — the service could not be reached or failed to answer.
- `ContractError` — the API returned a shape this version cannot read.
- `ChartError` — you asked a chart for something the payload does not, or cannot,
  provide, or for an option it does not have.

A call the service refused carries `status_code` and `request_id`: quote both when
you ask for help. A throttled or briefly unavailable service is retried twice for
you. A `Filter` built from invalid values raises Python's own `ValueError` or
`TypeError`.

Your key travels in a header, only over HTTPS, and is never put in a figure, a
message or a saved file.

## License

The code is Apache-2.0: see `LICENSE`. The API's data and the EarningSpy
name are not covered by it: see `NOTICE`. Nothing here is investment advice.
