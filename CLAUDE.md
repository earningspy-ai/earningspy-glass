# CLAUDE.md

Guidance for Claude Code and other agents in this repository. It is public: write nothing here, or anywhere in the repo, that a client should not read.

## What this is

`earningspy-glass`, the Python client for the EarningSpy API: it fetches a company against its peers, a window of reports, or a company's own quarters, and draws them with Plotly. The readers are analysts who know finance and some Python.

## Commands

```bash
python -m venv .venv && . .venv/bin/activate
pip install -e ".[test]" -r .github/requirements/quality.txt -r .github/requirements/build.txt
pytest --cov=earningspy_glass --cov-branch     # no network, no key; coverage has a floor
ruff check .
actionlint && zizmor --offline --min-severity medium .github     # after touching .github
python -m build                                # after touching pyproject.toml
```

## Layout (`src/earningspy_glass/`)

| Module | Job |
|---|---|
| `client.py` | the public surface: `EarningSpy` and what its calls return |
| `api.py`, `calendar_api.py`, `timeline_api.py`, `rows.py`, `catalog.py` | one fetch per view |
| `_http.py` | the transport: key, HTTPS, retries, pages, error wording |
| `errors.py` | every error |
| `facts.py` | what the service says about its rows; read, never worked out again |
| `graph_client.py`, `calendar_charts.py`, `timeline_charts.py` | the charts |
| `_derived.py` | the only place a chart computes anything |
| `samples.py`, `sample.json` | invented data, made by `tools/make_sample.py` |

## Rules that hold everywhere

1. **The API decides; this package draws.** No ranking, threshold, grade, median, orientation or judgement about a company is computed here. If a chart needs one, the API must deliver it: stop and say so. Adding to `_derived.py` is the maintainer's decision.
2. **Say what a value is for, never how it is made.** No method, input, weight or threshold in a docstring, label, hover, error, test, commit or pull request.
3. **Dependencies are plotly, requests and the standard library.** No pandas, no numpy.
4. **Only `_http.py` and `catalog.py` reach the network.** The key travels in a header over HTTPS and never reaches a figure, a message, a log or a file.
5. **No real data in the repository.** Test companies are invented and their tickers start with `ZZ`.
6. **A missing value is shown as missing,** with its reason. Never a zero, a guess or a silent drop.
7. **Importing changes nothing:** no global state, logging setup or network.

`tests/test_architecture.py` holds most of these. If a test there fails, change the code, not the test.

## Writing code

- Read the module and its tests first. Match its names, structure and voice.
- Follow the [Google Python Style Guide](https://google.github.io/styleguide/pyguide.html), except where `ruff` in `pyproject.toml` says otherwise (130 columns). Do not reformat code you are not changing.
- The smallest change that solves the problem. No speculative options, no abstraction with one caller, no dead code, no unrelated edits.
- Functions do one thing. Pure where possible: payloads in, figure out; never modify an argument.
- Type-hint new public functions. Give every public name a docstring: what it does for the caller, its arguments, what it returns and raises. Comments say *why*.
- Raise an `EarningSpyError` subclass whose message says what happened and what to do next. Never catch broadly or swallow an error.
- A public name, argument, default or exception is a promise. Deprecate with a `DeprecationWarning` for one minor version before removing it.
- Python 3.10 is the floor: no newer syntax.

## Testing

- A fix starts with a test that fails without it. A feature covers the normal case, missing data and the refusal.
- Test what a user sees (a figure's traces, labels, hover; an error's words), not private helpers.
- No network, no clock, no randomness without a seed.
- A chart change is looked at: render it from the sample, before and after, small and large.
- Report results as they are. Never weaken a test, lower the coverage floor or skip a check to get to green.

## Commits and pull requests

You may create a branch, commit, push that branch and open a pull request without asking, once the task is done and the commands above pass.

1. Branch from an up-to-date `main`: `feat/…`, `fix/…`, `docs/…`, `ci/…`.
2. One pull request, one purpose. Commit in steps that each make sense alone. Stage files by name and read `git diff --staged` before committing.
3. Commit email is a GitHub noreply address; check `git config user.email` before the first commit.
4. Commit messages are [Conventional Commits](https://www.conventionalcommits.org/): an imperative summary, then a body with why the change is needed.
5. Open it with `gh pr create --title … --body-file …`, filling `.github/pull_request_template.md`. The *PR title* check refuses a title in another form, and a description whose *What changes for the user*, *Why* or *Checked* is missing or empty.

**The title becomes the commit on `main` and a line in the release notes**, so write it for someone who uses the package:

| Title | Release |
|---|---|
| `feat(timeline): compare two companies on one chart` | minor |
| `fix(peers): keep negative margins on the axis` · `perf:` · `deps:` | patch |
| `feat(api)!: require a tier on every call` | breaking: minor before 1.0, major after |
| `docs:` `refactor:` `test:` `build:` `ci:` `style:` `chore:` | none |

Not `fix: bug`, not `feat: update graph_client.py`. A change that reaches a user is `feat`, `fix`, `perf` or `deps`, whatever files it touches (the README is the PyPI page: use `fix(docs)` when it must be republished).

**The description explains what the diff cannot:** what changes for the user, why, the decisions made and what was rejected, what was run with its real result, and compatibility. Plain sentences, no filler, no list of files. Say what was not done or not checked.

If the release notes need more than the title, add this to the description (it replaces the title there):

```
BEGIN_COMMIT_OVERRIDE
feat(timeline): compare two companies on one chart
fix(timeline): keep the legend inside small canvases
END_COMMIT_OVERRIDE
```

## Never

- Push to `main`, force-push, merge a pull request, or bypass a check.
- Create a tag or release, run the release workflow, approve a deployment, or upload to PyPI. `RELEASING.md` says how that happens; the maintainer does it.
- Edit the version in `pyproject.toml`, `.release-please-manifest.json` or a released section of `CHANGELOG.md`.
- Weaken a workflow's permissions, unpin an action (full commit hash, version in a comment), or add a secret.
- Commit a key, a `.env`, real data, a personal email or anything under `.internal/`.
