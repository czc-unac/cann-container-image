# CANN release generator and trigger

[English](release_generator.md) | [简体中文](release_generator_zh.md)

`tools/cann_release/generator.py` turns one CANN version into the complete
release change-set. `.github/workflows/release_cann_agent.yml` runs it from a
`/release-cann` issue comment, gates the result and pushes a branch. The code
is split into `generator.py` (CLI and orchestration), `matrix.py`,
`repo_files.py` and `docs.py`; `registry.py` and `availability.py` are shared
with the monitor. `docs/cann_release_monitor.md` covers the monitor itself.

## The contract

* Generation runs on top of the **upstream** release repository
  (`Ascend/cann-container-image@main` by default), never on this repository's
  own state, so the maintainer can open the upstream PR directly from the
  generated branch.
* The workflow **stops at the branch**. It posts the branch name and a
  `compare` link; opening the upstream PR is a deliberate human action. No
  cross-repository token is involved.
* Nothing is committed locally: the generator writes the working tree, the
  workflow commits, pushes and reports.

## Trigger

```
/release-cann <version>              (stable)
/release-cann <version> <x.y.Tn>     (beta)
```

Accepted only when all of the following hold: the comment is on an issue whose
title is `New CANN release detected: <version>`, the commenter is the
repository owner, the version equals the issue title's version, and both tokens
pass their regexes. `workflow_dispatch` (with `version` / `link_id` inputs)
bypasses the guard for rehearsals.

`tools/cann_release/prepare_upstream_state.sh <upstream-url> <ref> <branch>`:

1. copies `tools/` to `/tmp/cann-tools` (upstream's tree does not contain the
   automation and the workflow keeps using the copy; it is never committed),
2. fetches the upstream ref into the repository (full fetch, so the later push
   carries every object the fork lacks),
3. force-creates the release branch at that upstream commit,
4. restores the policy files into `tools/cann_release/` as untracked helpers.

The staging gate then adds **only release-owned paths** explicitly - never
`git add -A`, which would swallow the untracked policy files - and verifies the
staged set is non-empty and entirely within the allowlist before committing.

## The release matrix is declarative

`tools/cann_release/release_matrix.json` is the single source of truth for what
a release contains:

```jsonc
{
  "cann": {
    "chips": ["310p", "910", "910b", "A3", "950"],
    "os": [{"name": "ubuntu", "version": "22.04"},
           {"name": "openeuler", "version": "24.03"}],
    "py_versions": ["3.10", "3.11", "3.12"]
  },
  "manylinux": {
    "chips": ["950", "910b", "310p", "A3"],
    "os": [{"name": "manylinux", "version": "2_34"}],
    "py_versions": ["3.10", "3.11", "3.12"]
  }
}
```

* The generator builds `chips x os x py` in the listed order. Adding or
  dropping a chip / OS / Python version is a **one-line config change**; no
  code change and no hand-editing of the arg files.
* The previous release only supplies entry ordering and formatting templates;
  the cartesian product always comes from this file.
* The difference against the previous release is reported (added / dropped
  combinations) but never blocks: the config is the intent.
* `arches` is **not** a matrix dimension. A tag builds both architectures; the
  availability policy (`tools/cann_release/release_profiles.json`) lists which
  archives must exist on OBS.
* Every generated tag is probed against OBS (toolkit, nnal, per-chip ops, both
  arches) before anything is written. A missing or unclear answer aborts.

## Formats the generator relies on

| Contract | Where | Failure mode |
|---|---|---|
| `        options:` followed by `          - <tag>` | the four build/push ymls | names the expected anchor and says to update the generator or workflow |
| `ALPHA_DICT = { ... }` ending with a `}`-only line | `tools/template.py` | `EXIT_DRIFT` with the expected shape |
| byte-reproducible JSON (`json.dump` indent 2 or 4 + optional EOF newline) | the four registration files | `EXIT_FORMAT`; the file is never reformatted silently |
| one `<!-- RELEASED:BEGIN/END -->` and one `<!-- LATEST:BEGIN/END -->` pair per OVERVIEW | `OVERVIEW.md`, `OVERVIEW.zh.md` | `EXIT_DRIFT` with instructions to re-add the markers or set `docs.mode=skip` |
| `### CANN <version>` heading | `supported_tags.md` | `EXIT_DRIFT` |

`tools/cann_release/tests/test_contracts.py` asserts all of the above against
the real files, so a format change fails on the PR instead of at release time.

## Documentation block

`OVERVIEW.md` / `OVERVIEW.zh.md` are prose documents with two fenced regions
the generator owns:

```
<!-- RELEASED:BEGIN --> ... <!-- RELEASED:END -->
<!-- LATEST:BEGIN -->  ... <!-- LATEST:END -->
```

Everything outside the markers can be rewritten freely. Inside, the generator
writes the released sentence, the LATEST heading/intro/table/note, in the
document's own wording and separators (`toolkit, ops, nnal` vs
`toolkit、ops、nnal`). The tag-naming example row and the quick-start
`CANN_TAG=` samples are deliberately **not** automated: they are documentation
samples (still valid tags), so they never block a release and can be edited by
hand.

`tools/cann_release/release_profiles.json` exposes `docs.mode`:

* `block` - rewrite the marker blocks (default),
* `skip` - leave the docs alone (useful while a documentation rewrite is in
  flight); the generator reports that it skipped them.

## Dry run

```
python3 tools/cann_release/generator.py <version> [--link-id <x.y.Tn>] --dry-run
```

Validates the version, builds the matrix, probes OBS and prints every tag plus
the files that would change - without touching the tree. Useful when reviewing
a matrix change or a new chip.

## Exit codes

| Code | Meaning |
|---|---|
| 0 | success |
| 2 | policy reject (unknown naming, beta without link-id, stable with link-id) |
| 3 | packages unavailable or probe inconclusive |
| 4 | version already covered |
| 5 | drift / inconsistent repo state (anchors, markers, matrix shape) |
| 6 | a registration file's formatting cannot be reproduced by `json.dump` |

## Tests

`pytest -q tools/cann_release/tests` (also run by
`.github/workflows/test_tools.yml` on PRs):

* `test_matrix.py` - cartesian product, ordering, tag conventions, diff report.
* `test_overview.py` - marker block replacement (en/zh), EOF preservation,
  `docs.mode=skip`, missing markers.
* `test_supported_tags.py` - section rendering, documentation ordering.
* `test_guards.py` - classification, link-id rules, exit codes, lossless JSON.
* `test_contracts.py` - the format contracts above, plus the English/Chinese
  documentation structure check.

## Verification performed

The declarative generator was replayed against the real `9.2.0-beta.2` release
commit (`a36525fb`, from parent `814c9cfa`): every registration file, the four
workflow ymls, `supported_tags.md`, `template.py` and the marker-fenced
OVERVIEW blocks came out **byte-identical**. The only differences were the
expected live inputs (the Python patch version moved on) and the two
doc-owned sample regions described above.

## Known limits

* Beta versions need the OBS `x.y.Tn` directory, which cannot be derived from
  the version string; the monitor's scan discovers it and prints it in the
  issue for copy-paste.
* The generator is Ubuntu-only (CI) and expects the repository layouts above;
  the contract tests make a layout change visible immediately.
* It validates packaging, never runtime behaviour on an NPU.
