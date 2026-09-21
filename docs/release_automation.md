# Automated CANN release monitoring and draft release PR generation

## Summary

This adds a deterministic, human-gated automation layer around CANN image
releases. It watches the CANN community bulletins, and when a new stable or
beta version is genuinely buildable it opens one notification issue; a
maintainer can then reply `/release-cann <version> [x.y.Tn]` to have the
complete release change-set generated, committed and pushed as a **draft**
PR. Image building and publishing remain exactly as today: manual
`workflow_dispatch`.

Nothing here changes existing pipelines. All scripts are additive and the
existing `workflow_dispatch` release flow is untouched.

## What it adds

| Area | Files | Purpose |
|---|---|---|
| Monitor | `tools/check_cann_release.py`, `.github/workflows/check_cann_release.yml` | Daily bulletin check, one notification issue per buildable version |
| Package gate | `tools/cann_availability.py` | Shared OBS availability probe used by both sides |
| Generator | `tools/gen_cann_release.py` | Deterministic release change-set generator |
| Policy | `tools/release_profiles.json`, `tools/release_matrix.json` | Naming/link-id policy and matrix drift baseline |
| Trigger | `.github/workflows/release_cann_agent.yml` | `/release-cann` comment → generation → gates → branch → draft PR |
| Docs | `.claude/skills/release-cann-image/SKILL.md` | Skill now leads with the generator; manual steps kept as fallback |
| Misc | `requirements.txt` (`requests`), `.gitignore` (`__pycache__`, `*.pyc`) | `template.py` already imported `requests` undeclared; the generator imports tool modules, so its bytecode must not enter commits |

## Architecture at a glance

```
                hiascend.com bulletins API                     OBS bucket
                        |                                          ^
                        v                                          | HEAD probes
  +-----------------------------------+                          |
  | check-cann-release.yml (daily)    |                          |
  |   tools/check_cann_release.py ----+--> tools/cann_availability.py
  |   - covered?  (known_tokens)      |                          |
  |   - fresh vs newest/floor guard   |                          |
  |   - full package set present? ----+--------------------------+
  +-----------------------------------+
        | new_versions            | announced_not_buildable
        v                         v
  one issue per version      run summary only (no issue)
        |
        | maintainer comments: /release-cann <version> [x.y.Tn]
        v
  +-----------------------------------+
  | release-cann-agent.yml            |
  |   parse+validate trigger          |
  |   tools/gen_cann_release.py ------+--> tools/cann_availability.py (exact dir)
  |   gate: path allowlist            |
  |   gate: coverage re-check         |
  |   commit -s / push / draft PR     |
  +-----------------------------------+
        |
        v
  draft PR "Add CANN <version> images"   (merge + workflow_dispatch build stay manual)
```

Two workflows, three Python modules, two JSON policy files. There is no
model, no inference service and no third-party write access in the path:
every decision is a rule in code or in the policy files, and the generator
only reads (the OBS bucket and `repo.huaweicloud.com` for the current Python
patch release) before writing to this repository.

## Workflow: `check-cann-release.yml`

```yaml
on:
  schedule: [{cron: '23 1 * * *'}]   # daily, 01:23 UTC == 09:23 Beijing
  workflow_dispatch:
permissions: {contents: read, issues: write}
concurrency: {group: cann-release-check, cancel-in-progress: false}
```

| Step | What it does | Why it is shaped this way |
|---|---|---|
| `actions/checkout` + `setup-python 3.11` + `pip install -r requirements.txt` | standard environment | `requirements.txt` pins `jinja2`, `requests` |
| **Check for new CANN releases** | `python3 tools/check_cann_release.py --check > /tmp/report.json`, echoes it | the script never talks to GitHub; all issue work lives in later steps, so the detection logic is testable locally |
| **Open one notification issue per version** | loops over `new_versions` (`@tsv`: version, publishTime, oss_dir), builds the body, creates the issue only if an exact-titled open one is absent | one thread per version keeps trigger comments, reports and PR links together; see "issue lifecycle" below |
| **Close notifications whose versions are covered** | lists open issues matching the title prefix, parses the version from the title, closes those no longer in `new_versions` with a comment | per-version close granularity; a version that becomes covered closes its own issue only |
| **Report announced-but-incomplete versions** | renders `announced_not_buildable` into `$GITHUB_STEP_SUMMARY` | visibility without issue noise: CANN-side incomplete is not actionable |
| **Email the maintainer on failure** | `dawidd6/action-send-mail@v3`, gated on `vars.MAIL_NOTIFY_TO`, `continue-on-error: true` | crash notification should not require the repo owner to be watching Actions; unconfigured forks simply skip it |

### Issue lifecycle and dedup

* Title: `New CANN release detected: <version>`.
* Dedup uses `gh issue list --search "\"$title\" in:title"` **plus** an exact
  match in the built-in jq:
  `--json number,title -q --arg t "$title" '[.[] | select(.title == $t) | .number][0] // empty'`.
  The `in:title` search is token-prefix based, so without the exact-match
  guard a query for `9.3.0` can prefix-hit an issue for `9.3.0-beta.1`.
* The body is static per version (version, bulletin publish time, usage hint,
  auto-detected OBS directory), so an existing open issue is **left alone**
  instead of being re-edited every run; the only changing field would be the
  run URL, which is not worth the churn.
* Version names come from an external API, so they enter titles/search strings
  only if they match `^[0-9][0-9A-Za-z.-]*$` (otherwise logged and skipped).
* Auto-close comment: "CANN `<version>` is now covered by the repository."

## Workflow: `release-cann-agent.yml`

```yaml
on:
  issue_comment: {types: [created]}
  workflow_dispatch:
    inputs: {version, link_id}
permissions: {contents: write, issues: write, pull-requests: write}
concurrency: {group: release-cann-agent, cancel-in-progress: false}
env: {PYTHONDONTWRITEBYTECODE: 1}
```

Job-level guard (`if`) — all of the following must hold for an
`issue_comment` run:

| Condition | Reason |
|---|---|
| `github.event.issue.pull_request == null` | comments on PRs must never trigger it |
| `author_association == 'OWNER'` and `comment.user.login == github.repository_owner` | the generation path writes to the repo and opens PRs; only the owner may invoke it |
| `startsWith(issue.title, 'New CANN release detected: ')` | only monitor-created issues are trigger surfaces |
| `startsWith(comment.body, '/release-cann ')` | explicit command, no accidental matches |

`workflow_dispatch` bypasses the guard for rehearsals on a branch.

### Step-by-step

1. **Parse `/release-cann` trigger.** Splits the comment, cross-checks that
   the commented version equals the version in the issue title, then applies
   two regexes: `^[0-9]+\.[0-9]+\.[0-9]+(-beta\.[0-9]+)?$` for the version and
   `^[0-9]+\.[0-9]+\.T[0-9]+$` for beta link-ids. Beta requires the link-id
   (the OBS directory cannot be derived from the version string); stable
   releases must not carry one. Validated values leave the step as
   `$GITHUB_OUTPUT`s, never as raw interpolation.
2. **Generate the release change-set.** `python3 tools/gen_cann_release.py
   <version> [--link-id <id>]`, output teed to the log.
3. **Gate A — only release-owned paths may change.** Unions tracked
   modifications and untracked files, then fails if any path is outside:
   `tools/template.py`, `build_(cann|manylinux)_arg.json`,
   `*_publish_version.json`, `OVERVIEW.{md,zh.md}`, `supported_tags.md`,
   `.github/workflows/(batch_)?build_and_push_(cann|manylinux).yml`, `cann/`,
   `manylinux/`. Also runs `git diff --check` for whitespace errors.
4. **Gate B — the repository now covers the version.** Re-imports
   `tools/check_cann_release.py` and asserts `is_known(version, known_tokens())`.
   A generation that silently failed to register the version cannot reach the
   push.
5. **Commit, push branch, open draft PR.** `git checkout -B
   release-cann-<version>`, `git add -A`, `git commit -s -m "Add CANN
   <version> images"` with a body naming the generator and the triggering run.
   Identity comes from `vars.RELEASE_GIT_NAME` / `vars.RELEASE_GIT_EMAIL`
   (must be the CLA-signed email). Branch is force-pushed (re-runs are
   idempotent), then:
   * target = `vars.RELEASE_PR_TARGET_REPO` or this repository;
   * `gh pr create --draft --head <owner>:<branch>`, using
     `secrets.RELEASE_PR_TOKEN` when set;
   * if PR creation fails (e.g. cross-repo without a PAT), the run still
     succeeds and reports a `compare?...&draft=1` link instead.
6. **Report back to the notification issue** (`if: always()`): success posts
   the PR link plus "build/publish remain manual"; failure points at the run
   log and the manual skill flow.

Note: `issue_comment` and `schedule` triggers only fire from the repository's
**default branch**, so both workflows activate after this lands on `main`;
until then they can be exercised with `workflow_dispatch` on the branch.

## Module: `tools/cann_availability.py`

The shared definition of "the packages for this version exist". Used by the
monitor (which must not report unpackaged announcements) and by the generator
(which must not build Dockerfiles whose downloads 404).

### Status semantics

| Probe result | Classification | Consequence |
|---|---|---|
| HTTP 200 | `present` | file counted as available |
| HTTP 403 / 404 (configurable) | `missing` | contributes to `missing` |
| anything else (5xx) or timeout/reset | `unknown` | retried with exponential backoff + jitter; only if every attempt stays unknown does the caller **raise** — never counted as missing |

The unknown rule is the core safety property: a flaky gateway must not turn
into a silently suppressed release, nor into a silently rejected generation.
The retries are what make that strictness practical on CI runners, which
reset or time out on OBS under load (observed in the first fork rehearsal:
2 of 14 probes flaked on a single run).

### Probe policy

Configured under `availability.probe` in `tools/release_profiles.json`:

| Setting | Default | Why |
|---|---|---|
| `timeout_seconds` | 30 | single HEAD budget; CI runners need more than the local default |
| `workers` | 4 | deliberately low: OBS resets connections when hammered |
| `retries` | 4 | extra attempts for unknown answers only (backoff 0.5s, 1s, 2s, 4s + jitter) |
| `backoff_seconds` | 0.5 | base for the exponential backoff |
| `unknown_budget` | 8 | abort a beta scan once this many candidate directories answer unclearly, so an unreachable gateway cannot retry its way through all 600 candidates and burn the runner's time budget |

Beta scans run in batches of `workers` and **stop at the first complete
directory**, so the common case (the directory sorts early: `9.2.T1`,
`9.2.T3`) costs one batch plus the 14-file completeness check instead of a
600-candidate sweep.

### Required file set

`required_files(policy, version, chips)` expands `file_templates` from
`release_profiles.json`:

* templates without `{chip}` → once per arch (`arch` loop),
* templates with `{chip}` → once per chip per arch.

With the current policy (2 common templates, 1 ops template, 5 chips, 2 arches)
that is `2*2 + 5*2 = 14` files. Names are exactly what the generated
Dockerfiles `curl`: `Ascend-cann-toolkit_<v>_linux-<arch>.run`,
`Ascend-cann-nnal_<v>_linux-<arch>.run`,
`Ascend-cann-<chip>-ops_<v>_linux-<arch>.run` — with `template.py`'s raw chip
spelling, so `A3` stays uppercase in the file name.

### Function reference

| Function | Design |
|---|---|
| `version_sort_key(version)` | token-wise key so `9.1.0 < 9.1.0-beta.1 < 9.1.1 < 9.2.0-beta.1`; shared by both tools |
| `obs_base_url(template_path)` | parses `BASE_URL` out of `tools/template.py` so the bucket has one source of truth |
| `load_availability_policy(profiles_path)` | returns the `availability` section of the policy file |
| `current_chips(arg_cann, arg_manylinux)` | union of chips in the newest version block of both arg files (newest per `version_sort_key`); the monitor's matrix source |
| `required_files(policy, version, chips)` | builds the archive list described above |
| `cann_url_prefix(base_url, version, kind, link_id)` | beta → `CANN%20<link_id>`; stable → `CANN%20<version>`; mirrors `template.py` |
| `beta_dir_candidates(version, scan_max)` | `9.1.0-beta.2` → `['9.1.T1', …, '9.1.T600']` (bound from policy) |
| `_probe(url, missing_statuses, timeout, retries, backoff)` | one HEAD request, retried with backoff while unknown; never raises, returns `(state, detail)` |
| `check_files(prefix, files, missing, settings)` | parallel HEADs under the probe policy, returns `(missing, unknown)` |
| `scan_beta(base_url, version, files, policy, missing_statuses)` | see algorithm below |

### `scan_beta` algorithm (beta versions have no derivable directory)

1. Walk candidate `x.y.Tn` directories in batches of `workers` (4), probing
   only the toolkit file. The walk stops as soon as a complete directory is
   found, so the usual case costs one or two batches rather than a full sweep.
2. Within a batch, directories answering 200 are "found"; unclear answers are
   counted against `unknown_budget` (8) — reaching it aborts the scan with
   `AvailabilityUnknown`, which keeps an unreachable gateway from retrying its
   way through all 600 candidates.
3. For each found directory, probe the **full** 14-file set.
   * all present → return that directory as buildable (fast path);
   * any unknown → raise `AvailabilityUnknown` (after the probe retries);
   * otherwise record the directory's missing files and keep scanning.
4. If nothing was found and some candidates answered unknown → raise. So a
   gateway outage cannot be mistaken for "no directory exists".
5. Otherwise the version is genuinely not buildable (e.g. `9.1.0-beta.2`,
   whose ops archives for 910b/310p/A3/910 were never uploaded: 8/14 missing).

## Module: `tools/check_cann_release.py`

```
bulletins ──► candidates ──► buildability ──► new_versions (actionable)
             (coverage +      (cann_          └ announced_not_buildable
              freshness)       availability)     (log/summary only)
```

Freshness is decided by two OR-ed guards so intentionally skipped history
stays quiet without hiding betas announced ahead of the stable they precede:
the version sorts above the newest covered version, or its bulletin is newer
than the publish floor. The publish floor alone would have missed both
`9.2.0-beta.1` (2026/08/11) and `9.2.0-beta.2` (2026/09/01, tied with the
`9.1.1` floor), which is exactly what the trial on a pre-#130 repository state
demonstrated.

### Function reference

| Function | Design |
|---|---|
| `classify_version` | `alpha` / `beta` / `stable` by substring; alpha is ignored everywhere |
| `make_session` | `requests.Session` carrying the `Referer` the bulletin gateway requires |
| `fetch_bulletin_versions` | paginates `bulletins/front/list` (page size 50) into `{versionName: {id, publishTime}}`; a non-200 or a changed payload shape raises `BulletinAPIError` with a payload snippet |
| `_read_text` / `_load_json` | tolerate files that do not exist yet (empty string / `{}`) |
| `known_tokens` | everything the repo already covers: all tags in both arg files, publish entry paths (`cann/<tag>` → `<tag>`), `- <tag>` option lines in the four build/push ymls, `### CANN <v>` headings in `supported_tags.md` |
| `is_known` | covered if a token equals the version or starts with `<version>-` |
| `publish_floor` | newest `publishTime` among covered bulletin versions; one of the two freshness guards |
| `newest_covered` | highest covered bulletin version ordered by version (not by date); the other freshness guard |
| `candidate_versions` | OR-ed guards: report when the version sorts above the newest covered version **or** its bulletin beats the publish floor; returns the sorted candidate list |
| `buildability` | stable → probe `CANN%20<version>` directly; beta → `scan_beta`; returns `(buildable, oss_dir, reason, missing_sample)`; propagates `AvailabilityUnknown` |
| `cmd_check` | orchestrates: fetch → empty check → tokens → `candidate_versions` → per-candidate buildability → JSON report; logs probe progress and not-buildable reasons to stderr |

### Report schema (stdout, consumed by the workflow)

```jsonc
{
  "new_versions": [
    {"version": "9.3.0", "publishTime": "2026/09/16", "oss_dir": ""}
  ],
  "announced_not_buildable": [
    {"version": "9.1.0-beta.2", "publishTime": "2026/07/09",
     "reason": "package set incomplete under 9.1.T7 (8/14 files missing)",
     "missing": ["Ascend-cann-310p-ops_...", "..."]}
  ]
}
```

Only `new_versions` opens issues. An empty bulletin list is an **error**, not
"nothing new" — otherwise a changed API shape would silently close open
notifications.

## Module: `tools/gen_cann_release.py`

`python3 tools/gen_cann_release.py <version> [--link-id <x.y.Tn>] [--skip-probe]`

### Pipeline (strict order, all writes after all reads that can fail)

1. **Policy** — `classify_version(version, policy)` rejects alphas and
   unmatched naming; beta requires a link-id matching `link_id_pattern`,
   stable rejects one.
2. **Coverage guard** — `is_known(version, known_tokens())` → abort `EXIT_COVERED`.
3. **Load arg files losslessly** (`load_json_lossless`) and compute
   `previous_version` (highest `cann_version`).
4. **Clone the matrix** (`clone_matrix_entries`) from the previous release's
   entries in `build_cann_arg.json` / `build_manylinux_arg.json` — the matrix
   is *derived*, never re-invented — and assert every cloned tag carries the
   new prefix.
5. **Drift gate** (`check_drift_gate`) — normalized chips/OS/Python must be a
   subset of `tools/release_matrix.json`; additions abort `EXIT_DRIFT`, drops
   are noted.
6. **Docs consistency** — each `OVERVIEW*` LATEST heading must equal
   `previous_version`.
7. **Availability probe** (`probe_packages` on the exact target directory with
   the cloned chips) → abort `EXIT_UNAVAILABLE` on missing or unknown, with the
   missing sample and a pointer to the manual skill path.
8. **`ALPHA_DICT` patch** (beta only) — inserts `"<version>": "<link-id>"` into
   `tools/template.py` before the closing brace.
9. **Write the new arg entries** at the head of both arrays; **render
   Dockerfiles** through `template.py`'s own engine, scoped to the new version
   (`render_dockerfiles`), with `get_python_download_url` wrapped by
   `cached_python_download_url` (one HTTP lookup per Python minor version
   instead of one per package item).
10. **Register** the tags: publish JSONs (`prepend_publish`), the four
    workflow ymls (`insert_into_choice_list`), `supported_tags.md`
    (`prepend_supported_tags`), `OVERVIEW.md`/`OVERVIEW.zh.md`
    (`update_overview` + `verify_latest_tables`).
11. **Self-check** — `is_known(version, …)` plus existence of every generated
    Dockerfile; prints a `SUMMARY` JSON.

### Function reference

| Function | Design |
|---|---|
| `_json_lossless` pair | before writing, prove `json.dumps(data, indent=2 or 4) + tail` reproduces the file **byte-for-byte**; otherwise abort `EXIT_FORMAT` rather than reformatting someone's hand-edited file |
| `write_text` / `read_text_raw` / `edit_text_lines` | text edits preserve whether the file ended with a newline; line-level transforms are applied on LF-normalized content |
| `clone_matrix_entries` | deep-copies the previous release's entries, swaps `cann_version` and the tag prefix; tags are the ground truth for spacing, so nothing is re-derived by hand |
| `check_drift_gate` | compares `re.sub('[^a-z0-9]','',lower)` forms of chips/OS/py, so `A3`/`a3` and `manylinux_2_34`/`manylinux2_34` cannot cause false trips |
| `patch_alpha_dict` | regex-inserts the beta mapping; refuses if the version is already present |
| `render_dockerfiles` | loads `tools/template.py` via `importlib`, replaces its Python-URL lookup with the caching wrapper, and calls its three renderers with **only** the new entries, so unrelated Dockerfiles are never rewritten |
| `prepend_publish` | inserts `{"path": "<dir>/<tag>", "tags": [tag]}` entries at the head, skipping paths that already exist (idempotent re-runs) |
| `insert_into_choice_list` | inserts option lines after the first `options:` block whose items are version tags |
| `tags_app_id` | learns the AscendHub AppID from the first existing `supported_tags.md` link; the constant is only a last-resort fallback |
| `doc_tag_sort_key` | documentation ordering: chip ascending, `ubuntu` before other OS values, Python version ascending — deliberately different from the build-arg order |
| `prepend_supported_tags` | builds the new `### CANN <v>` section (plain rows, then `-devel` rows, both slash-separated content cells) and inserts it before the first heading |
| `update_overview` | token-level retargeting: every standalone occurrence of the old LATEST version becomes the new one (lookarounds protect `-beta`/`-alpha`/`-rc` tails and longer numbers); the `cann-version` example row is special-cased to `"<new>","<old>", …` so history is kept; aborts if the heading, example row, or uniqueness expectations do not hold |
| `verify_latest_tables` | after the edit, each OVERVIEW must contain exactly `2 × py3.12 combos` new-version rows (plain + devel); otherwise the table drifted from the matrix and a human should take over |
| `self_check` | final coverage + generated-file existence assertions |

### Byte-preservation guarantees

* JSON files are only rewritten through the lossless pair; anything else is
  refused (`EXIT_FORMAT`).
* Text files keep their EOF-newline state.
* Dockerfiles are produced by `template.py` itself, so they cannot drift from
  the engine maintainers already use.

### Exit codes

| Code | Meaning | Typical cause |
|---|---|---|
| 0 | success | change-set written, `SUMMARY` printed |
| 2 | policy reject | alpha, unknown naming scheme (e.g. `8.3.rc2`), beta without link-id, stable with link-id |
| 3 | packages unavailable | missing or unknown probe results under the target OBS directory |
| 4 | already covered | version already registered (safe re-run) |
| 5 | drift / inconsistent state | matrix outside baseline, arg files without the previous release, OVERVIEW LATEST out of sync |
| 6 | formatting not reproducible | a target file carries formatting `json.dump` cannot reproduce |

## Configuration file schemas

`tools/release_profiles.json`

```jsonc
{
  "reject_substrings": ["alpha"],
  "kinds": [                                   // first match wins
    {"kind": "beta",   "pattern": "^\\d+\\.\\d+\\.\\d+-beta\\.\\d+$"},
    {"kind": "stable", "pattern": "^\\d+\\.\\d+\\.\\d+$"}
  ],
  "url_layout": {"stable": "{base}/CANN/CANN%20{version}",
                 "beta":   "{base}/CANN/CANN%20{link_id}"},
  "availability": {
    "arches": ["aarch64", "x86_64"],
    "file_templates": [
      "Ascend-cann-toolkit_{version}_linux-{arch}.run",
      "Ascend-cann-nnal_{version}_linux-{arch}.run",
      "Ascend-cann-{chip}-ops_{version}_linux-{arch}.run"
    ],
    "beta_scan_max": 600,
    "missing_statuses": [403, 404],
    "probe": {
      "timeout_seconds": 30,
      "workers": 4,
      "retries": 4,
      "backoff_seconds": 0.5,
      "unknown_budget": 8
    }
  },
  "beta_requires_link_id": true,
  "link_id_pattern": "^\\d+\\.\\d+\\.T\\d+$"
}
```

`tools/release_matrix.json`

```jsonc
{
  "cann":      {"chips": ["310p", "910", "910b", "950", "A3"],
                "os": ["ubuntu22.04", "openeuler24.03"],
                "py_versions": ["3.10", "3.11", "3.12"]},
  "manylinux": {"chips": ["310p", "910b", "950", "A3"],
                "os": ["manylinux_2_34"],
                "py_versions": ["3.10", "3.11", "3.12"]}
}
```

Both files are consumed as data only: extending a naming scheme, a chip set or
an arch list is a config change, never a code change.

## Why these choices

* **No AI in the loop.** Release files are mechanical transformations of the
  previous release; determinism is worth more than flexibility here, and the
  manual `release-cann-image` skill remains for irregular cases.
* **Previous release = single source of truth.** Cloning beats re-specifying
  the matrix: chip/OS/Python changes in the last release carry forward
  automatically, and the only guard needed is the drift gate.
* **Fail loud on ambiguity.** Unknown probe results, empty bulletin payloads
  and unreproducible formatting all abort; none of them degrade into a silent
  "nothing to do".
* **Human gate at the trigger.** Detection is automatic; releasing is a
  deliberate `/release-cann` comment by the owner, and the output is always a
  draft PR.

## Verification

- **Golden replay**: from `814c9cfa` (Publish CANN 9.1.1),
  `gen_cann_release.py 9.2.0-beta.2 --link-id 9.2.T3` reproduces **every file**
  of the real release commit `a36525fb` byte-for-byte (78 files: Dockerfiles,
  build args, publish JSONs, four workflows, OVERVIEW ×2, supported_tags,
  `template.py`).
- **Availability probes against live OBS**: `9.2.T3`, `9.1.1`, `9.1.T6`
  complete (14/14); `9.1.T7` reports 8/14 missing; `9.1.0-beta.2` is
  classified `announced_not_buildable`.
- **Monitor on current `main`**: `new_versions: []`,
  `announced_not_buildable: []`.
- **Failure paths**: already-covered version → exit 4, unmatched naming (e.g.
  `8.3.rc2`) → exit 2, non-existent packages → exit 3, all before any write.
- **Issue routing**: replayed against a `gh` stub — create/skip/close and the
  not-buildable summary all behave as intended, including the
  `9.3.0` vs `9.3.0-beta.1` prefix trap.
- All workflow `run` blocks pass `bash -n`; both YAML files parse.

## Permissions and configuration footprint

| Item | Required for | Notes |
|---|---|---|
| `issues: write` | notification issues | monitor workflow |
| `contents: write`, `pull-requests: write` | branch + draft PR | agent workflow, runs in this repository |
| `vars.RELEASE_GIT_NAME` / `RELEASE_GIT_EMAIL` | commit identity | must be the CLA-signed email, otherwise `ascend-cla/no` blocks the PR |
| `vars.RELEASE_PR_TARGET_REPO` | where the draft PR is opened | falls back to this repository; cross-repo needs the token below |
| `secrets.RELEASE_PR_TOKEN` | cross-repo PR creation | fine-grained PAT with `pull requests: write`; without it the run degrades to "branch pushed + compare link" |
| `vars.MAIL_NOTIFY_TO`, `secrets.MAIL_SERVER/PORT/USERNAME/PASSWORD` | crash email | if unset the step is skipped; `continue-on-error` keeps mail failures from masking the real one |

Two things reviewers should weigh explicitly:

1. **Merging enables a daily cron in this repository** that may open issues.
   Issue creation here is currently restricted, so this is a policy decision,
   not just a CI change. The check can be limited to forks/manual dispatch if
   preferred.
2. **One new third-party action**, `dawidd6/action-send-mail@v3`, only for
   failure email and only when configured. Happy to pin it to a commit SHA, or
   to drop the email path entirely.

## Rollout

1. Merge into this repository (or a fork) — the comment trigger and cron only
   activate from the default branch.
2. Set the variables/secrets above; `RELEASE_PR_TARGET_REPO` can stay unset for
   a same-repo rehearsal.
3. Try it with `workflow_dispatch` on `release_cann_agent.yml`
   (`version` + optional `link_id`).
4. Build/publish afterwards is the existing manual `workflow_dispatch`
   batch flow, unchanged.

## Open questions for maintainers

1. Should the daily monitor run in the upstream repository, in forks only, or
   behind an opt-in variable?
2. Pin `dawidd6/action-send-mail` to a commit SHA, or replace the crash
   notification with another channel?
3. For auto-generated release PRs: target this repository, or require
   `RELEASE_PR_TARGET_REPO`/`RELEASE_PR_TOKEN` to point upstream?
