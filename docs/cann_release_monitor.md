# CANN release monitor

[English](cann_release_monitor.md) | [简体中文](cann_release_monitor_zh.md)

`tools/cann_release/monitor.py` watches the CANN community bulletins and reports
versions that this repository does not cover yet. A daily workflow turns each
report into one notification issue so a maintainer can act on it. The monitor
never writes to the repository and never builds anything.

## What counts as a new release

A version is reported only when all three checks pass.

1. **Not covered yet.** A version counts as covered when any known tag,
   publish path, workflow option line or `supported_tags.md` heading carries
   *that exact version segment*. `9.2.0-beta.2-910b-...` covers
   `9.2.0-beta.2` but never the later `9.2.0` stable - otherwise a repository
   that shipped only betas would silently swallow the stable announcement.
2. **Fresh.** Two OR-ed guards keep deliberately skipped history quiet without
   hiding releases announced ahead of the stable they precede:
   * the version sorts above the newest covered version, and/or
   * the bulletin was published after the newest publish time among covered
     versions.
   The first guard is what catches `9.2.0-beta.1` (bulletin 2026/08/11) while
   `9.1.1` (bulletin 2026/09/01) is the newest covered release; the second
   catches a lower-numbered release published later.
3. **Buildable.** The full OBS package set must exist: toolkit, nnal and the
   per-chip ops archive for every chip of the current matrix on both arches
   (`tools/cann_release/availability.py`). Announced-but-incomplete releases
   (for example `9.1.0-beta.2`, whose ops archives for 910b/310p/A3/910 were
   never uploaded) are listed in the run summary and open no issue.

## Report and issue lifecycle

`--check` prints JSON to stdout:

```jsonc
{
  "new_versions": [
    {"version": "9.3.0", "publishTime": "2026/09/16", "oss_dir": ""}
  ],
  "announced_not_buildable": [
    {"version": "9.1.0-beta.2", "publishTime": "2026/07/09",
     "reason": "package set incomplete under 9.1.T7 (8/14 files missing)",
     "missing": ["Ascend-cann-310p-ops_9.1.0-beta.2_linux-aarch64.run"]}
  ]
}
```

* Only `new_versions` creates issues; `announced_not_buildable` goes to the
  workflow run summary.
* Issue title: `New CANN release detected: <version>`.
* Dedup requires an exact title match in addition to the token-based search
  (`in:title` alone would let `9.3.0` prefix-match `9.3.0-beta.1`).
* The body is static per version (version, bulletin publish time, usage hint
  and the auto-detected OBS directory for betas), so an existing open issue is
  left untouched instead of being re-edited every day.
* An issue closes automatically once its version becomes covered.
* An empty bulletin response is an error, never "nothing new" - otherwise an
  API change would close open notifications.

## Availability probing

`tools/cann_release/availability.py` classifies anonymous HEAD answers as:

| Answer | Meaning |
|---|---|
| 200 | present |
| 403 / 404 (configurable) | missing |
| anything else, timeout, reset | unknown - retried with exponential backoff, and only if every attempt stays unknown does the run fail |

Unknown must never be read as "missing": a flaky gateway would otherwise
silently suppress a real release. The probe policy (timeout, workers, retries,
backoff, `unknown_budget`) lives in `tools/cann_release/release_profiles.json`.

Beta versions have no derivable OBS directory (`CANN x.y.Tn`), so the scan
walks candidate directories in batches of `workers`, stops at the first
complete one, and aborts once `unknown_budget` candidates answer unclearly -
an unreachable gateway fails in minutes instead of retrying through 600
candidates.

## Workflow

`.github/workflows/check_cann_release.yml`

* Triggers: daily cron `23 1 * * *` (01:23 UTC) and `workflow_dispatch`.
* Permissions: `contents: read`, `issues: write`.
* Serialized by a `cann-release-check` concurrency group.
* Issues are the notification channel: GitHub notifies watchers of this
  repository when a version is reported. Announced-but-incomplete versions
  never open issues; they are listed in the run summary.

The repository must have **Issues enabled**; otherwise issue creation fails and
the run reports it.

## Running locally

```bash
pip install -r requirements.txt
python3 tools/cann_release/monitor.py --check    # prints the JSON report
pytest -q tools/cann_release/tests               # unit + contract tests
```

The tests stub all HTTP calls, so they run offline in seconds.

## Known limits

* The bulletin list endpoint is an internal gateway (`Referer` required,
  undocumented payload). A shape change raises `BulletinAPIError` and fails
  the run - deliberately loud, but it needs a code update.
* Package availability is judged by HEAD requests against the public OBS
  bucket; the `x.y.Tn` scan assumes the historical directory layout and a
  bound of `beta_scan_max` (600).
* The monitor validates *packaging*, not runtime behaviour on an NPU.
