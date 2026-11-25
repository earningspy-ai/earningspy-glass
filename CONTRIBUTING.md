# Contributing

Issues are welcome: a wrong chart, an unclear error, a missing option. Please include
the version (`earningspy_glass.__version__`) and the request id an error carries.
Support is best effort, and not every pull request will be accepted. For anything larger
than a fix, open an issue first.

## The one rule

**The API decides; this package draws.** It places marks and reads labels. If a change
would compute a ranking, a threshold, a grade or a judgement about a company, it belongs
in the API. The few summaries a chart needs of its own marks live in `_derived.py`, and
the tests hold the package to that.

Also held by tests: dependencies are plotly, requests and the standard library; the key
travels in a header over HTTPS and never reaches a figure, a message or a file; only
`_http.py` and `catalog.py` reach the network.

## Working on it

```bash
python -m venv .venv && . .venv/bin/activate
pip install -e ".[test]" -r .github/requirements/quality.txt
pytest
ruff check .
```

The tests use no network and no key. Every company in them is invented (tickers start
with `ZZ`): never add real data to this repository.

Title the pull request as a [Conventional Commit](https://www.conventionalcommits.org/)
(`fix(peers): keep negative margins on the axis`): the title becomes the commit and the
release note. Leave the version and the changelog alone; [RELEASING.md](RELEASING.md) says
how they are made.

By contributing you agree that your work is licensed under Apache-2.0.
