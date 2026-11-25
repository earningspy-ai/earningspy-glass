# Releasing

Nobody types a version, writes a changelog or pushes a tag. Two decisions are made by hand: merging the release pull request, and approving the publish.

```
pull request "feat: …"  ─ squash ─▶  main
                                      │  release-please keeps one release PR open
                                      ▼
                          "chore(main): release 0.2.0"      version + CHANGELOG
                                      │  1. the maintainer merges it
                                      ▼
                       tag v0.2.0  +  draft GitHub release
                                      │  build ▶ test the wheel ▶ check the gate
                                      ▼
                              2. the maintainer approves
                                      │
                                      ▼
              PyPI ▶ install it back from PyPI ▶ GitHub release made public
```

## A release

1. Read the open release pull request as a client would. To reword a line, edit the description of the pull request it came from (a `BEGIN_COMMIT_OVERRIDE` block, see `CLAUDE.md`) and run the *Release PR* workflow.
2. Merge it.
3. When *Release* waits: Actions → the run → **Review deployments** → `pypi` → **Approve and deploy**. The draft release can still be edited until then.

## The version

`fix`, `perf` and `deps` raise the patch; `feat` the minor; `!` the minor before 1.0 and the major after. To choose one (1.0.0, say), merge a pull request whose description holds:

```
BEGIN_COMMIT_OVERRIDE
chore: release 1.0.0

Release-As: 1.0.0
END_COMMIT_OVERRIDE
```

## When something goes wrong

| What | Do |
|---|---|
| Build, tests or gate fail | Nothing went out. Passing trouble: re-run the failed jobs. A real fault: delete the draft release, leave the tag, fix it; the next release takes the next number. |
| You decide not to publish | **Reject** the deployment and delete the draft release. That number is skipped. |
| PyPI says `invalid-publisher` | Nothing went out. The publisher must be `earningspy-ai` / `earningspy-glass` / `release.yml` / `pypi`. Re-run. |
| Install check fails after PyPI | It is published. Re-run if PyPI was slow; otherwise yank it and release a fix. |
| A bad version is out | PyPI → the release → **Yank** (pinned installs keep working, new ones skip it). Then release a fix. A version is never replaced. |

## What protects it

- `main` takes pull requests only, squashed, with their checks passed.
- The `pypi` environment waits for one approver and takes only `v*` tags. *Release* reads that setting before every publish and stops if it changed.
- PyPI trusts this workflow in this environment; no token exists to steal.
- The App that writes the release pull request and the tag keeps its key in an environment only `main` can use.
- Release tags cannot be moved or deleted, and only the App creates them. Published releases cannot be changed.
- Every file carries provenance: `gh attestation verify <file> --repo earningspy-ai/earningspy-glass`.
