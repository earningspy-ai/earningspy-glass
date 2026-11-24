"""The catalogue helper: reads the public field catalogue (no key, no payload)
and lists what you can ask for. Offline — loads a saved catalogue via base_url,
never the network.
"""
from pathlib import Path

import pytest

import importlib

from earningspy_glass import catalog, fields
cat_mod = importlib.import_module("earningspy_glass.catalog")  # the module, for _CACHE

FIXTURE = Path(__file__).parent / "fixtures" / "catalog.json"
FIXTURE_URL = FIXTURE.as_uri()          # file:// URL — urlopen reads it, no network


@pytest.fixture(autouse=True)
def _clear_cache():
    cat_mod._CACHE.clear()
    yield
    cat_mod._CACHE.clear()


class TestCatalog:
    def test_lists_all_fields(self, capsys):
        rows = catalog(base_url=FIXTURE_URL)
        out = capsys.readouterr().out
        assert "EarningSpy catalogue" in out
        assert rows                                     # returns the rows too
        # deduped across frames: a field appears once, its frames merged
        names = [r[0] for r in rows]
        assert len(names) == len(set(names))

    def test_query_filters(self, capsys):
        rows = catalog("roic", base_url=FIXTURE_URL)
        assert rows
        assert all("roic" in r[0].lower() or "roic" in r[1].lower()
                   for r in rows)

    def test_group_filter(self):
        rows = catalog(group="quantiles", base_url=FIXTURE_URL)
        assert rows
        assert all(r[2] == "quantiles" for r in rows)

    def test_tier_filter(self):
        rows = catalog(tier="silver", base_url=FIXTURE_URL)
        assert rows
        assert all(r[3] == "silver" for r in rows)

    def test_frame_filter_merges_frames_in_badge(self):
        # a field on both frames shows "pre,post"; frame filter narrows to one
        allrows = {r[0]: r[4] for r in catalog(base_url=FIXTURE_URL)}
        assert any("," in fr for fr in allrows.values())   # some span both

    def test_fields_is_an_alias(self):
        assert fields is catalog

    def test_returned_rows_echo_quietly(self, capsys):
        # the menu is printed; the returned value must NOT re-dump every tuple
        # when a notebook echoes it — its repr is a one-line summary, though it
        # stays a real, usable list.
        rows = catalog(group="quantiles", base_url=FIXTURE_URL)
        assert isinstance(rows, list)
        assert repr(rows) == f"<{len(rows)} catalogue fields — printed above>"
        assert rows[0][0]                                # still indexable

    def test_views_listed_without_filter(self, capsys):
        catalog(base_url=FIXTURE_URL)
        out = capsys.readouterr().out
        assert "views" in out and "calendar" in out

    def test_bad_url_raises_readable(self):
        with pytest.raises(RuntimeError, match="could not load the field catalogue"):
            catalog(base_url="file:///no/such/catalogue.json", refresh=True)

    def test_client_method_exists(self):
        from earningspy_glass import EarningSpy
        es = EarningSpy(api_key="x")
        assert hasattr(es, "catalog") and hasattr(es, "fields")
