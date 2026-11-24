"""The single-ticker timeline: the quarter shift, the continuous axis with gaps,
the mixed-format refusal, the percentile head-to-head, and the pre/post compare.

Offline, fixture-driven — no network. Charts return plotly Figures; assertions
check they build (or raise) and that meaning is READ from the payload.
"""
import datetime

import pytest

from earningspy_glass import ChartError
from earningspy_glass.timeline_charts import (
    bars,
    compare,
    describe,
    radar,
    _continuous_quarters,
    _family,
    _radar_radius,
    _series,
    _short,
)


# --- the quarter shift ------------------------------------------------------

class TestQuarterShift:
    def test_a_row_is_labelled_with_the_quarter_the_service_gives(self, timeline_pre):
        row = timeline_pre["data"][0]
        key = f"{row['TICKER']}|{row['EARNINGS_DATE'][:10]}"
        timeline_pre["facts"]["rows"][key]["values_quarter"] = "1999/Q1"
        assert "1999/Q1" in _series(timeline_pre, "EPS")

    def test_pre_post_align_one_event_apart(self, timeline_pre, timeline_post):
        # post at one event == pre at the NEXT event, per the spec's proof; so
        # joining on the shifted quarter label lines the reporting periods up.
        pre = _series(timeline_pre, "EPS")
        post = _series(timeline_post, "EPS")
        shared = set(pre) & set(post)
        assert shared                                   # they overlap on quarters


# --- continuous axis with gaps ----------------------------------------------

class TestContinuousAxis:
    def test_fills_gaps_between_quarters(self):
        # a missing 2025/Q2 must appear as a slot, not be closed up
        got = _continuous_quarters(["2025/Q1", "2025/Q3", "2025/Q4"])
        assert got == ["2025/Q1", "2025/Q2", "2025/Q3", "2025/Q4"]

    def test_spans_year_boundary(self):
        got = _continuous_quarters(["2024/Q4", "2025/Q2"])
        assert got == ["2024/Q4", "2025/Q1", "2025/Q2"]


# --- bars -------------------------------------------------------------------

class TestBars:
    def test_single_measure_over_full_history(self, timeline_pre):
        fig = bars(timeline_pre, "EPS")
        assert len(fig.data) == 1
        assert len(fig.data[0].x) >= 9                  # ~9 quarters, gaps included

    def test_same_family_measures_group(self, timeline_pre):
        fig = bars(timeline_pre, ["ROIC", "ROA", "ROE"])  # all percent_fraction
        assert len(fig.data) == 3

    def test_mixed_format_refused_loudly(self, timeline_pre):
        # currency-per-share beside billions-currency would hide the small one
        with pytest.raises(ChartError, match="do not share a scale"):
            bars(timeline_pre, ["EPS", "FCF"])

    def test_percentiles_always_share_the_axis(self, timeline_pre_quant):
        fig = bars(timeline_pre_quant, ["ROIC_QUANT", "EPS_QUANT", "FCF_QUANT"])
        # one real bar trace per measure (ghost traces for null slots may add more)
        named = [tr for tr in fig.data if tr.name]
        assert len(named) == 3
        assert list(fig.layout.yaxis.range) == [0, 100]

    def test_unknown_measure_raises(self, timeline_pre):
        with pytest.raises(ChartError, match="not_a_measure"):
            bars(timeline_pre, "not_a_measure")

    def test_show_dates_annotates_axis(self, timeline_pre):
        plain = bars(timeline_pre, "EPS")
        dated = bars(timeline_pre, "EPS", show_dates=True)
        # the dated axis carries the event date beneath the quarter
        assert any("<br>" in str(t) for t in dated.layout.xaxis.ticktext)
        assert not any("<br>" in str(t) for t in plain.layout.xaxis.ticktext)

    def test_family_of_quant_is_percentile(self, timeline_pre_quant):
        assert _family(timeline_pre_quant, "ROIC_QUANT") == "percentile"

    def test_null_slot_gets_a_hoverable_ghost(self, timeline_pre_quant):
        # in a head-to-head, a quarter present via one measure but NULL for
        # another renders no bar for the second — a near-invisible full-height
        # ghost fills that slot so hovering it still shows the frame, instead of
        # a dead patch with no tooltip.
        payload = {**timeline_pre_quant,
                   "data": [dict(r) for r in timeline_pre_quant["data"]]}
        # EPS_QUANT keeps the quarters on the axis; FCF_QUANT is null in one of
        # them, so that quarter has an EPS bar but a missing FCF bar.
        for i, r in enumerate(payload["data"]):
            r["EPS_QUANT"] = 60.0
            r["FCF_QUANT"] = None if i == 0 else 70.0
        fig = bars(payload, ["EPS_QUANT", "FCF_QUANT"])
        ghosts = [tr for tr in fig.data if tr.name is None]
        assert ghosts                               # a ghost trace exists
        g = ghosts[0]
        assert all(bool(h) for h in g.hovertext)    # every ghost bar hoverable
        assert all(y == 100 for y in g.y)           # full-height on a 0-100 axis
        # shares the FCF measure's offset group so it fills that exact slot
        fcf = next(tr for tr in fig.data if tr.name and "FCF" in tr.name)
        assert g.offsetgroup == fcf.offsetgroup

    def test_hover_carries_the_whole_frame(self, timeline_pre):
        # the moat: one bar's hover shows every measure the row holds, not just
        # the charted one — identity header, encoded measure, then the rest.
        fig = bars(timeline_pre, "EPS")
        h = next(ht for ht in fig.data[0].hovertext if "n.m." not in ht)
        assert "ZZAP" in h                          # identity header
        assert "<b>EPS:" in h                       # the encoded measure, emphasised
        assert "FCF" in h and "ROIC" in h           # other measures ride along
        # values formatted from metrics{}, not raw
        assert "$" in h and "%" in h

    def test_hover_skips_identity_and_nulls(self, timeline_pre):
        fig = bars(timeline_pre, "EPS")
        h = next(ht for ht in fig.data[0].hovertext if "n.m." not in ht)
        # identity plumbing is in the header, not repeated as a measure row
        assert "DAYS_LEFT" not in h and "IS_AMC" not in h

    def test_hover_pairs_raw_and_percentile(self, timeline_pre_quant):
        # on the calendar_quant view each measure shows raw value AND its
        # percentile on ONE line (calendar-style), not as two separate rows.
        fig = bars(timeline_pre_quant, "ROIC_QUANT")
        h = next(ht for ht in fig.data[0].hovertext if "n.m." not in ht)
        # a paired cell: "ROIC: <pct>% <NN>p" — raw then the greyed rank token
        import re
        plain = re.sub("<[^>]+>", "", h.replace("&nbsp;", " "))
        assert re.search(r"ROIC: [\d.\-]+% \d+p", plain)
        # the percentile is NOT listed as its own separate "ROIC pctile" row
        assert "pctile" not in plain

    def test_hover_hides_nothing(self, timeline_pre_quant):
        # a normal frame shows every measure — no "+N more" trim (the cap only
        # bites a pathologically wide frame).
        fig = bars(timeline_pre_quant, "ROIC_QUANT")
        h = next(ht for ht in fig.data[0].hovertext if "n.m." not in ht)
        assert "more measure" not in h

    def test_chart_grows_to_fit_the_tallest_hover(self, timeline_pre_quant):
        # the rich full-frame hover would overflow a short plot and be suppressed
        # near the edge; the chart height scales with the tallest tooltip so the
        # last bars still render one.
        fig = bars(timeline_pre_quant, ["ROIC_QUANT", "EPS_QUANT", "FCF_QUANT"])
        tallest = max(ht.count("<br>") + 1 for ht in fig.data[0].hovertext)
        # enough vertical room for the tallest tooltip (~15px/line over a floor)
        assert fig.layout.height >= min(900, 150 + tallest * 15)

    def test_hover_shows_undescribed_columns_formatted(self, timeline_pre_quant):
        # MARKET_CAP / P/E are in the row but NOT in metrics{} (the API has no
        # format for them). They must still appear — hiding real data is wrong —
        # and render formatted (a currency, not a bare 3350000000000.0) via the
        # client's plain-column fallback.
        import re
        fig = bars(timeline_pre_quant, "ROIC_QUANT")
        h = next(ht for ht in fig.data[0].hovertext if "n.m." not in ht)
        plain = re.sub("<[^>]+>", "", h.replace("&nbsp;", " "))
        assert "MARKET CAP" in plain
        assert re.search(r"MARKET CAP: \$[\d.]+[TBMK]", plain)   # abbreviated $

    def test_hover_shows_quality_and_forward_blocks(self, timeline_pre_quant):
        # the ticker route now carries QUALITY_*/GOLD_* across history (null on
        # unscored quarters); the hover shows them as their own tier blocks on a
        # scored quarter, like the calendar.
        import re
        # the newest quarter is the scored one in the gold fixture
        fig = bars(timeline_pre_quant, "EP_QUANT")
        h = fig.data[0].hovertext[-1]
        plain = re.sub("<[^>]+>", "", h.replace("&nbsp;", " "))
        assert "Quality read (silver)" in plain
        assert "Forward read (gold)" in plain
        assert "Abnormal" in plain                       # the served gold model
        assert "Direction" not in plain                  # the shadow model is never served

    def test_unscored_quarter_has_no_tier_blocks(self, timeline_pre_quant):
        # an older quarter with no score shows no quality/gold block —
        # nothing invented where the data is null.
        import re
        fig = bars(timeline_pre_quant, "EP_QUANT")
        h = fig.data[0].hovertext[0]           # oldest quarter
        plain = re.sub("<[^>]+>", "", h.replace("&nbsp;", " "))
        assert "Quality read" not in plain and "Forward read" not in plain

    def test_thin_percentile_shows_pit_note(self, timeline_pre_quant):
        # a measure whose raw value is present but whose _QUANT is null (a thin
        # peer set — the only null branch once the raw is in hand) must say why,
        # not drop the rank silently.
        from earningspy_glass.timeline_charts import _row_cell
        row = dict(timeline_pre_quant["data"][0])
        row["FCF"] = 1.0e11          # raw present
        row["FCF_QUANT"] = None      # percentile thin
        cell = _row_cell(timeline_pre_quant, row, "FCF")
        assert "(PIT: thin)" in cell

    def test_whole_thin_quarter_notes_once_in_header(self, timeline_pre_quant):
        import re
        # a row with every _QUANT null -> one header note, no per-line PIT tags
        row = dict(timeline_pre_quant["data"][0])
        for k in list(row):
            if k.endswith("_QUANT"):
                row[k] = None
        payload = {**timeline_pre_quant, "data": [row]}
        fig = bars(payload, "EPS")
        h = fig.data[0].hovertext[0]
        plain = re.sub("<[^>]+>", "", h.replace("&nbsp;", " "))
        assert "not ranked — thin industry peer set" in plain
        assert "(PIT:" not in plain            # not repeated per line

    def test_change_format_not_inherited_from_level(self, timeline_post):
        # EPS is currency, EPS_CHANGE is percent — the change must NOT borrow the
        # level's format (that would render 0.0544 as $0.05). Read per column.
        from earningspy_glass.timeline_charts import _fmt
        assert _fmt(timeline_post, "EPS") == "currency_per_share"
        assert _fmt(timeline_post, "EPS_CHANGE") == "percent_fraction"


# --- short names ------------------------------------------------------------

class TestShortNames:
    def test_ep_displays_as_inverted_pe(self, timeline_pre):
        assert _short(timeline_pre, "E/P") == "E/P"

    def test_quant_marked_pctile(self, timeline_pre_quant):
        assert _short(timeline_pre_quant, "ROIC_QUANT") == "ROIC pctile"


# --- compare ----------------------------------------------------------------

class TestCompare:
    def test_on_file_and_reported_bars(self, timeline_pre, timeline_post):
        fig = compare(timeline_pre, timeline_post, "EPS")
        assert len(fig.data) == 2
        names = {tr.name for tr in fig.data}
        assert names == {"on file", "reported"}

    def test_bars_move_matches_the_change_label(self, timeline_pre, timeline_post):
        # THE REGRESSION: the two bars must show exactly the move the API's
        # EPS_CHANGE measures — join on the event, not the reporting quarter, so
        # (reported - on_file)/on_file == the label above the pair.
        fig = compare(timeline_pre, timeline_post, "EPS")
        labels = list(fig.data[0].x)
        on_file = list(fig.data[0].y)
        reported = list(fig.data[1].y)
        ann = {a.x: a.text for a in fig.layout.annotations}
        checked = 0
        for q, pv, qv in zip(labels, on_file, reported):
            if pv is None or qv is None or q not in ann:
                continue
            # parse "+5.4%" -> 0.054
            pct = float(ann[q].replace("<b>", "").replace("</b>", "")
                        .replace("%", "").replace("+", "")) / 100.0
            assert abs((qv - pv) / pv - pct) < 5e-3, f"{q}: bars {pv}->{qv} vs {ann[q]}"
            checked += 1
        assert checked >= 2                              # actually verified some

    def test_older_reports_show_on_file_only(self, timeline_pre, timeline_post):
        # pre has more history; those reports show an on-file bar and an empty
        # reported slot rather than truncating the axis.
        fig = compare(timeline_pre, timeline_post, "EPS")
        assert any(v is None for v in fig.data[1].y)

    def test_api_delta_labels_present(self, timeline_pre, timeline_post):
        fig = compare(timeline_pre, timeline_post, "EPS")
        assert fig.layout.annotations
        assert any("%" in a.text for a in fig.layout.annotations)

    def test_no_delta_label_when_change_absent(self, timeline_pre, timeline_post):
        # a measure whose _CHANGE the frame does not carry -> bars only, no labels,
        # no crash. Strip the change columns to simulate.
        post = {**timeline_post,
                "metrics": {k: v for k, v in timeline_post["metrics"].items()
                            if not k.endswith("_CHANGE")}}
        post["data"] = [{k: v for k, v in r.items() if not k.endswith("_CHANGE")}
                        for r in timeline_post["data"]]
        fig = compare(timeline_pre, post, "EPS")
        assert not fig.layout.annotations
        assert len(fig.data) == 2


# --- radar ------------------------------------------------------------------

class TestRadar:
    def test_percentile_radar_overlays_last_quarters(self, timeline_pre_quant):
        fig = radar(timeline_pre_quant, quarters=3)
        # one shape per quarter (up to 3), each with 5 spokes + the closing point
        assert 1 <= len(fig.data) <= 3
        assert len(fig.data[0].theta) == 6
        assert list(fig.layout.polar.radialaxis.range) == [0, 100]

    def test_radar_shows_the_most_recent_quarters(self, timeline_pre_quant):
        # newest quarter must be present (sorted by date, tail taken) — not the
        # oldest, regardless of payload order.
        from earningspy_glass import facts
        from earningspy_glass.timeline_charts import _row_date
        newest = max(timeline_pre_quant["data"], key=_row_date)
        newest_q = facts.quarter(timeline_pre_quant, newest)
        fig = radar(timeline_pre_quant, quarters=3)
        assert newest_q in [tr.name for tr in fig.data]

    def test_lower_is_better_spoke_is_oriented(self, timeline_pre_quant):
        # a risk metric (lower better) is flipped so the outer ring is still good
        assert _radar_radius(timeline_pre_quant,
                             "FHS_GARCH_ANNZD_VOL_QUANT", 30.0, "percentile") == 70.0
        assert _radar_radius(timeline_pre_quant,
                             "ROIC_QUANT", 30.0, "percentile") == 30.0

    def test_change_radius_centres_on_no_change(self, timeline_post):
        # a flat quarter sits at the mid-ring (0.5); improvement pushes outward
        assert _radar_radius(timeline_post, "EPS_CHANGE", 0.0, "change") == 0.5
        assert _radar_radius(timeline_post, "EPS_CHANGE", 0.5, "change") == 1.0
        assert _radar_radius(timeline_post, "EPS_CHANGE", -0.5, "change") == 0.0
        # an outlier clamps rather than blowing the axis
        assert _radar_radius(timeline_post, "EPS_CHANGE", 5.0, "change") == 1.0

    def test_change_radar_needs_post(self, timeline_pre_quant):
        with pytest.raises(ChartError, match="post timeline"):
            radar(timeline_pre_quant, mode="change")

    def test_bad_mode_raises(self, timeline_pre_quant):
        with pytest.raises(ChartError, match="percentile.*change|mode"):
            radar(timeline_pre_quant, mode="nope")


# --- describe ---------------------------------------------------------------

class TestDescribe:
    def test_lists_measures_and_quarters(self, timeline_pre, capsys):
        rows = describe(timeline_pre)
        out = capsys.readouterr().out
        assert "ZZAP" in out and "quarters" in out
        assert any(k == "EPS" for k, _, _ in rows)

    def test_returned_rows_echo_quietly(self, timeline_pre, capsys):
        # describe() PRINTS the menu; the returned value must not re-dump every
        # tuple when a notebook echoes it — one-line repr, still a usable list.
        rows = describe(timeline_pre)
        assert isinstance(rows, list)
        assert repr(rows) == f"<{len(rows)} measures — printed above>"
        assert rows[0][0]                                # still indexable

    def test_groups_by_scale_family(self, timeline_pre, capsys):
        rows = describe(timeline_pre)
        out = capsys.readouterr().out
        # the output names the scale groups, so a reader can pick fields that
        # share one and never hit the "do not share a scale" refusal
        assert "grouped by scale" in out
        assert "percent (%)" in out and "currency" in out
        # each row now carries its family
        fam = dict((k, f) for k, _, f in rows)
        assert fam["EPS"] == "currency_per_share"
        assert fam["ROIC"] == "percent"
