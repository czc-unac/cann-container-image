#!/usr/bin/env python3
"""Detect CANN versions announced on the community bulletins that we miss.

Run from the repository root:

    python3 tools/cann_release/monitor.py --check

Prints a JSON report with ``new_versions`` (uncovered, buildable stable/beta
versions) and ``announced_not_buildable`` (announced, but OBS lacks part of
the package set).

A version is reported only when it is uncovered, fresh (it sorts above the
newest covered version, or was published after the newest covered publish
time) and buildable (full OBS package set on both arches).

Adapted from Ascend/cann-container-image pull request #131 by wjunLu.
"""
import argparse
import json
import re
import sys

import requests

from availability import (AvailabilityUnknown, cann_url_prefix, check_files,
                          current_chips, load_availability_policy, obs_base_url,
                          probe_settings, required_files, scan_beta,
                          version_sort_key, TEMPLATE_PY)
from registry import (ARG_CANN_JSON, ARG_MANYLINUX_JSON, is_known,
                      known_tokens)

BULLETIN_LIST_URL = ("https://www.hiascend.com/ascendgateway/ascendservice/"
                     "bulletins/front/list")
BULLETIN_REFERER = "https://www.hiascend.com/productbulletins?tab=CANN"
PROFILES_JSON = "tools/cann_release/release_profiles.json"


class BulletinAPIError(RuntimeError):
    """The bulletin gateway answered unexpectedly."""


def classify_version(version):
    """Classify a bulletin version name as stable or beta."""
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

    Both guards are OR-ed: the version sorts above the newest covered one, or
    its bulletin was published after the newest covered publish time. The
    first catches betas announced ahead of the stable they precede; the
    second catches a lower-numbered release published later. Skipped history
    stays quiet under both.
    """
    floor = publish_floor(bulletins, tokens)
    newest = newest_covered(bulletins, tokens)
    if tokens and newest is None:
        # No covered version in the payload means freshness cannot be judged;
        # "report everything" would resurface skipped history, so refuse.
        raise BulletinAPIError(
            "the bulletin list contains none of the versions this repository "
            "covers; the API payload looks incomplete, refusing to judge "
            "freshness")
    candidates = []
    for version in bulletins:
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
    AvailabilityUnknown when a probe could not be decided.
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
        # An empty universe means the API shape changed; reporting "nothing
        # new" on it would wrongly close open notifications.
        raise BulletinAPIError("bulletin list came back empty")
    tokens = known_tokens()
    candidates = candidate_versions(bulletins, tokens)
    policy = load_availability_policy(PROFILES_JSON)
    base_url = obs_base_url(TEMPLATE_PY)
    chips = current_chips(ARG_CANN_JSON, ARG_MANYLINUX_JSON)
    print(f"[cann-release] probing packages for {len(candidates)} candidate(s); "
          f"matrix chips: {chips}", file=sys.stderr)

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
            print(f"[cann-release] announced but not buildable: {version} - {reason}",
                  file=sys.stderr)

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
