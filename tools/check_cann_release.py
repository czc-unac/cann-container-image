#!/usr/bin/env python3
"""Detect new CANN releases announced on the community bulletins page.

Run from the repository root:

    python3 tools/check_cann_release.py --check

Prints a JSON report whose ``new_versions`` lists stable/beta versions
announced on https://www.hiascend.com/productbulletins?tab=CANN that the
repository does not cover yet. Alpha versions are ignored entirely: they
are never built into images here.

Three guards keep the report quiet until a version is genuinely actionable:

* a version counts as covered once any known tag, publish path, workflow
  option or supported_tags.md heading carries it;
* historical, deliberately skipped versions stay quiet through two OR-ed
  guards (see candidate_versions): the version must sort above the newest
  covered version, or its bulletin must be published after the newest
  publish time among covered versions. The version-order guard is what
  catches betas announced ahead of the stable they precede (9.2.0-beta.1
  announced 2026/08/11 while 9.1.1, bulletin 2026/09/01, was the newest
  covered release), which a pure publish-time floor would have missed;
* the full package set (toolkit, nnal, per-chip ops for every current chip
  and both arches, probed over HTTP by tools/cann_availability.py) must
  exist on OBS. Announced-but-incomplete releases (e.g. 9.1.0-beta.2, whose
  ops archives for 910b/310p/A3/910 were never uploaded - only 950 was
  covered) land in ``announced_not_buildable`` in the JSON report and open
  no issue; that is a condition-of-CANN-not-satisfied state, not a
  maintainer decision to skip a release.

The report is consumed by .github/workflows/check_cann_release.yml, which
opens one notification issue per buildable version; release files are
generated from that issue by commenting "/release-cann <version> [link-id]",
which triggers tools/gen_cann_release.py via
.github/workflows/release_cann_agent.yml.

Adapted from Ascend/cann-container-image pull request #131 by wjunLu.
"""
import argparse
import json
import os
import re
import sys

import requests

from cann_availability import (AvailabilityUnknown, cann_url_prefix,
                               check_files, current_chips,
                               load_availability_policy, obs_base_url,
                               probe_settings, required_files, scan_beta,
                               version_sort_key)

BULLETIN_LIST_URL = ("https://www.hiascend.com/ascendgateway/ascendservice/"
                     "bulletins/front/list")
BULLETIN_REFERER = "https://www.hiascend.com/productbulletins?tab=CANN"
PROFILES_JSON = os.path.join("tools", "release_profiles.json")
TEMPLATE_PY = os.path.join("tools", "template.py")
ARG_CANN_JSON = "build_cann_arg.json"
ARG_MANYLINUX_JSON = "build_manylinux_arg.json"
PUBLISH_CANN_JSON = "cann_publish_version.json"
PUBLISH_MANYLINUX_JSON = "manylinux_publish_version.json"
SUPPORTED_TAGS_MD = "supported_tags.md"
WORKFLOW_FILES = [
    ".github/workflows/build_and_push_cann.yml",
    ".github/workflows/build_and_push_manylinux.yml",
    ".github/workflows/batch_build_and_push_cann.yml",
    ".github/workflows/batch_build_and_push_manylinux.yml",
]


class BulletinAPIError(RuntimeError):
    """The bulletin gateway answered unexpectedly."""


def classify_version(version):
    """Classify a bulletin version name as stable, beta or alpha."""
    if "alpha" in version:
        return "alpha"
    if "beta" in version:
        return "beta"
    return "stable"


def make_session():
    """Session carrying the Referer the bulletin gateway requires."""
    session = requests.Session()
    session.headers["Referer"] = BULLETIN_REFERER
    return session


def fetch_bulletin_versions(session):
    """Fetch all CANN community bulletins as {versionName: {id, publishTime}}."""
    versions = {}
    page = 1
    while True:
        resp = session.get(BULLETIN_LIST_URL, params={
            "productName": "CANN", "bulletinsType": 0, "versionType": 0,
            "lang": "zh", "pageNum": page, "pageSize": 50,
        }, timeout=30)
        if resp.status_code != 200:
            raise BulletinAPIError(
                f"bulletin list returned HTTP {resp.status_code}: {resp.text[:200]}")
        payload = resp.json()
        try:
            data = payload["data"]["list"]
            total = payload["data"]["totalCount"]
        except (KeyError, TypeError) as exc:
            raise BulletinAPIError(
                f"unexpected bulletin payload: {exc}: {str(payload)[:300]}")
        for item in data:
            versions[item["versionName"]] = {
                "id": item["id"], "publishTime": item["publishTime"]}
        if not data or len(versions) >= total:
            return versions
        page += 1


def _read_text(path):
    """Read a repo file, tolerating states where it does not exist yet."""
    if not os.path.exists(path):
        return ""
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def _load_json(path):
    if not os.path.exists(path):
        return {}
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def known_tokens():
    """Every version-ish token the repo already knows about."""
    tokens = set()
    for arg_path in (ARG_CANN_JSON, ARG_MANYLINUX_JSON):
        for group in _load_json(arg_path).values():
            for entry in group:
                tokens.update(entry["tags"])
    for publish_path in (PUBLISH_CANN_JSON, PUBLISH_MANYLINUX_JSON):
        for item in _load_json(publish_path).get("versions", []):
            tokens.add(item["path"].split("/", 1)[1])
    for yml_path in WORKFLOW_FILES:
        tokens.update(re.findall(r"^          - (\S+)$", _read_text(yml_path), re.M))
    tokens.update(re.findall(r"^### CANN (\S+)$", _read_text(SUPPORTED_TAGS_MD), re.M))
    return tokens


def is_known(version, tokens):
    return any(t == version or t.startswith(version + "-") for t in tokens)


def publish_floor(bulletins, tokens):
    """Newest publishTime among bulletin versions the repository covers."""
    times = [bulletins[v].get("publishTime") or "" for v in bulletins
             if is_known(v, tokens)]
    return max(times) if times else None


def newest_covered(bulletins, tokens):
    """Highest covered bulletin version by version order (not by date)."""
    covered = [v for v in bulletins if is_known(v, tokens)]
    return max(covered, key=version_sort_key) if covered else None


def candidate_versions(bulletins, tokens):
    """Uncovered stable/beta versions that are genuinely new to this repo.

    Two complementary guards, OR-ed, so intentionally skipped history stays
    quiet without hiding betas announced ahead of (or on the same day as) a
    stable release:

    * the version sorts above the newest covered version - this is what
      catches 9.2.0-beta.1 (bulletin 2026/08/11) while 9.1.1 (bulletin
      2026/09/01) is the newest covered release, and 9.2.0-beta.2 tie-ing
      the floor date; and
    * the bulletin was published strictly after the newest publish time
      among covered versions - this catches a lower-numbered release that
      was published later than a higher-numbered covered one.

    Deliberately skipped versions stay suppressed by both guards: 9.1.0-beta.2
    sorts below 9.1.1 and was published before it.
    """
    floor = publish_floor(bulletins, tokens)
    newest = newest_covered(bulletins, tokens)
    candidates = []
    for version in bulletins:
        if classify_version(version) not in ("stable", "beta"):
            continue
        if is_known(version, tokens):
            continue
        above_newest = (newest is None or
                        version_sort_key(version) > version_sort_key(newest))
        published_after_floor = (floor is not None and
                                 (bulletins[version].get("publishTime") or "") > floor)
        if above_newest or published_after_floor:
            candidates.append(version)
    return sorted(candidates, key=version_sort_key)


def buildability(version, kind, policy, base_url, chips):
    """Decide whether this version's full package set exists on OBS.

    Returns (buildable, oss_dir, reason, missing_sample). Raises
    AvailabilityUnknown when a probe could not be decided, so a flaky
    gateway never silently downgrades a real release to "not buildable".
    """
    files = required_files(policy, version, chips)
    missing_statuses = policy["missing_statuses"]
    if kind == "beta":
        buildable_dir, found, incomplete = scan_beta(
            base_url, version, files, policy, missing_statuses)
        if buildable_dir:
            return True, buildable_dir, "", []
        if incomplete:
            directory = sorted(incomplete)[0]
            sample = incomplete[directory]
            return (False, "",
                    f"package set incomplete under {directory} "
                    f"({len(sample)}/{len(files)} files missing)", sample[:4])
        return False, "", "no OBS directory found for this beta", []

    prefix = cann_url_prefix(base_url, version, kind)
    missing, unknown = check_files(prefix, files, missing_statuses,
                                   probe_settings(policy))
    if unknown:
        raise AvailabilityUnknown(
            f"probing {prefix} answered unclearly: {unknown[:3]}")
    if missing:
        return (False, "",
                f"package set incomplete ({len(missing)}/{len(files)} files "
                "missing)", missing[:4])
    return True, version, "", []


def cmd_check(session):
    bulletins = fetch_bulletin_versions(session)
    if not bulletins:
        # An empty bulletin universe means the API shape changed; reporting
        # "nothing new" on that would wrongly close open notifications.
        raise BulletinAPIError("bulletin list came back empty")
    tokens = known_tokens()
    candidates = candidate_versions(bulletins, tokens)
    policy = load_availability_policy(PROFILES_JSON)
    base_url = obs_base_url(TEMPLATE_PY)
    chips = current_chips(ARG_CANN_JSON, ARG_MANYLINUX_JSON)
    print(f"[check_cann_release] probing packages for {len(candidates)} "
          f"candidate(s); matrix chips: {chips}", file=sys.stderr)

    new_versions, not_buildable = [], []
    for version in candidates:
        kind = classify_version(version)
        publish_time = bulletins[version].get("publishTime") or ""
        buildable, oss_dir, reason, missing = buildability(
            version, kind, policy, base_url, chips)
        if buildable:
            new_versions.append({"version": version,
                                 "publishTime": publish_time,
                                 "oss_dir": oss_dir if kind == "beta" else ""})
        else:
            not_buildable.append({"version": version,
                                  "publishTime": publish_time,
                                  "reason": reason, "missing": missing})
            print(f"[check_cann_release] announced but not buildable: "
                  f"{version} - {reason}", file=sys.stderr)

    print(json.dumps({"new_versions": new_versions,
                      "announced_not_buildable": not_buildable},
                     indent=2, ensure_ascii=False))


def main():
    parser = argparse.ArgumentParser(description="CANN new-release monitor")
    parser.add_argument("--check", action="store_true",
                        help="report new bulletin versions as JSON")
    args = parser.parse_args()
    if not args.check:
        parser.error("pass --check")
    cmd_check(make_session())


if __name__ == "__main__":
    main()
