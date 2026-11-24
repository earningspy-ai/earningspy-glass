"""What Glass is, held by tests: a wrapper with no business logic, nothing private,
and nothing that says how a value is made."""
import ast
import re
from pathlib import Path

import pytest

import earningspy_glass

PACKAGE = Path(earningspy_glass.__file__).parent
MODULES = sorted(PACKAGE.glob("*.py"))
SOURCE = {p.name: p.read_text(encoding="utf-8") for p in MODULES}

# What a chart still works out for itself. Adding one here is a decision: the rule
# is that the service answers. A name leaves when the API delivers its value.
DERIVED = {"quantile", "outlier_tickers", "nearest_in_size"}

imports = lambda tree: [(n.module or "").split(".")[0] if isinstance(n, ast.ImportFrom) else a.name.split(".")[0]   # noqa: E731
                        for n in ast.walk(tree) if isinstance(n, (ast.Import, ast.ImportFrom))
                        for a in (n.names if isinstance(n, ast.Import) else [None])]


@pytest.mark.parametrize("name", SOURCE)
def test_only_plotly_requests_and_the_standard_library(name):
    import sys
    allowed = set(sys.stdlib_module_names) | {"plotly", "requests", "earningspy_glass", "IPython"}
    assert set(imports(ast.parse(SOURCE[name]))) <= allowed


@pytest.mark.parametrize("name", SOURCE)
def test_no_statistics_outside_the_one_module(name):
    tree = ast.parse(SOURCE[name])
    assert not {"statistics", "numpy", "pandas", "scipy", "random"} & set(imports(tree))
    if name == "_derived.py":
        return
    defined = {n.name for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)}
    computes = {d for d in defined if re.search(r"quantile|median|mean|stdev|variance|zscore|outlier|regress", d)}
    assert not computes, f"{name} computes: {computes}"


def test_the_derived_list_only_changes_on_purpose():
    tree = ast.parse(SOURCE["_derived.py"])
    assert {n.name for n in tree.body if isinstance(n, ast.FunctionDef)} == DERIVED


def test_only_the_transport_reaches_the_network():
    callers = {n for n, s in SOURCE.items() if re.search(r"requests\.(get|post|Session|request)\(|urlopen\(", s)}
    assert callers == {"_http.py", "catalog.py"}      # the catalogue is public and takes no key


@pytest.mark.parametrize("name", SOURCE)
def test_nothing_private_ships(name):
    private = (r"s3://|arn:aws|amazonaws|\b\d{12}\b|/Users/"
               r"|esk_(live|test)_\w{6,}")
    assert not re.findall(private, SOURCE[name])


@pytest.mark.parametrize("name", SOURCE)
def test_no_word_says_how_a_value_is_made(name):
    words = r"\b(machine learning|classifier|trained on|training set|the models?)\b"
    assert not re.findall(words, SOURCE[name], re.I)


@pytest.mark.parametrize("name", SOURCE)
def test_nothing_promises_free_data(name):
    assert not re.findall(r"free (key|bronze|view|tier)|\(free\)", SOURCE[name], re.I)


def test_the_public_names_all_exist_and_are_documented():
    for name in earningspy_glass.__all__:
        obj = getattr(earningspy_glass, name)
        if callable(obj):
            assert (obj.__doc__ or "").strip(), name


def test_importing_the_package_changes_nothing(tmp_path):
    import subprocess
    import sys
    code = ("import logging, warnings; f, h = list(warnings.filters), list(logging.getLogger().handlers)\n"
            "import earningspy_glass\n"
            "from earningspy_glass.errors import PayloadNotice\n"
            "assert not any(x[2] is PayloadNotice for x in warnings.filters)\n"
            "assert logging.getLogger().handlers == h and not logging.getLogger('earningspy_glass').handlers\n")
    subprocess.run([sys.executable, "-c", code], check=True, cwd=PACKAGE.parent)


def test_no_real_company_is_in_the_recorded_payloads():
    import json
    for path in sorted((Path(__file__).parent / "fixtures").glob("*.json")):
        if path.name == "catalog.json":
            continue
        payload = json.loads(path.read_text(encoding="utf-8"))
        tickers = {r.get("TICKER") or r.get("ticker") for r in payload.get("data") or payload.get("points")}
        assert all(t.startswith("ZZ") for t in tickers), path.name
