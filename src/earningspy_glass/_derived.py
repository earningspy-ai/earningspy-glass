"""What a chart works out for itself, all of it, in one place.

The API resolves every financial judgement, and the rest of this package only
positions marks and reads labels. The few things below are computed here because
no response carries them yet: summaries of the marks on the chart, and which
companies to draw or name. None of them touches a company's own figures, and
none may be added anywhere else (the test suite holds the package to that).

A function leaves this module when the service delivers its answer.
"""

import math

OUTLIER_LABELS = 4


def quantile(values, q):
    """The q-quantile of the values shown, linearly interpolated (0.5: the median)."""
    vals = sorted(v for v in values if v is not None)
    if not vals:
        return None
    pos = q * (len(vals) - 1)
    lo = int(pos)
    hi = min(lo + 1, len(vals) - 1)
    return vals[lo] + (vals[hi] - vals[lo]) * (pos - lo)


def outlier_tickers(rows, log_x=False, log_y=False, cap=OUTLIER_LABELS):
    """The companies stretching a scatter's axes: outside the 1.5×IQR fences on
    either axis (in log terms on a log axis), the ``cap`` most extreme first.

    ``rows`` are (ticker, x, y). Shared rule — EarningSpy Studio applies the same one, so
    both label the same outliers.
    """
    scores = {}
    for axis, log in ((1, log_x), (2, log_y)):
        vals = [(r[0], math.log10(r[axis]) if log else r[axis]) for r in rows
                if r[axis] is not None and (r[axis] > 0 or not log)]
        if len(vals) < 4:
            continue
        shown = [v for _, v in vals]
        q1, q3 = quantile(shown, 0.25), quantile(shown, 0.75)
        iqr = q3 - q1
        if iqr <= 0:
            continue
        lo, hi = q1 - 1.5 * iqr, q3 + 1.5 * iqr
        for ticker, v in vals:
            gap = (lo - v) if v < lo else (v - hi) if v > hi else 0
            if gap > 0:
                scores[ticker] = max(scores.get(ticker, 0), gap / iqr)
    return [t for t, _ in sorted(scores.items(), key=lambda kv: -kv[1])[:cap]]


def nearest_in_size(size, pool, n, size_of):
    """The ``n`` of ``pool`` closest to ``size`` on a log scale, closest first."""
    target = math.log(size)
    return sorted(pool, key=lambda p: abs(math.log(size_of(p)) - target))[:n]
