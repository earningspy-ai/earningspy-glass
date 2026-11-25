# Changelog

All notable changes to earningspy-glass, which uses
[semantic versioning](https://semver.org/). Before 1.0, a breaking change raises the minor
version. This file is written by the release process.

## [0.1.0]

First release.

### Added
- **Peers** (`es.peers`): a company against its industry or sector, before and after
  its report, in ten charts.
- **Calendar** (`es.calendar`): a window of reports in seven charts, with filters.
- **Timeline** (`es.timeline`): a company across its own quarters, as bars, before
  against after, and as a radar.
- **Rows** (`es.rows`, `es.schema`): every view and column the API serves, as it
  serves them, every page of them.
- **Catalogue** (`es.catalog`): the fields on offer, no key needed.
- **Sample** (`sample`): invented companies inside the package, to try the charts without a key.
- **Errors** that say what happened and what to do, with the service's request id.
  A throttled or briefly unavailable service is retried.
- **Recipes**: every figure carries how it was made, without data or key.
