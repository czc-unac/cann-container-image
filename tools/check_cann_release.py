#!/usr/bin/env python3
"""Detect new CANN releases announced on the community bulletins page.

Run from the repository root:

    python3 tools/check_cann_release.py --check

Prints a JSON report whose ``new_versions`` lists stable/beta versions
announced on https://www.hiascend.com/productbulletins?tab=CANN that the
repository does not cover yet. Alpha versions are ignored entirely: they
are never built into images here.

Two guards keep the report quiet until a version is genuinely actionable:

* a version counts as covered once any known tag, publish path, workflow
  option or supported_tags.md heading carries it;
* a version is only reported if its bulletin was published later than the
  newest publish time among versions the repository already covers, so
  historical versions maintainers deliberately skipped (e.g. 9.1.0-beta.2)
  do not resurface every day.

The report is consumed by .github/workflows/check_cann_release.yml, which
opens a notification issue; release files are generated from that issue by
commenting "/release-cann <version> [link-id]", which triggers
tools/gen_cann_release.py via .github/workflows/release_cann_agent.yml.

Adapted from Ascend/cann-container-image pull request #131 by wjunLu.
"""
import argparse
import json
import os
import re

import requests

BULLETIN_LIST_URL = ("https://www.hiascend.com/ascendgateway/ascendservice/"
                     "bulletins/front/list")
BULLETIN_REFERER = "https://www.hiascend.com/productbulletins?tab=CANN"
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


def version_sort_key(version):
    """Sort key so 9.1.0 < 9.1.0-beta.1 < 9.1.1 < 9.2.0-beta.1."""
    parts = []
    for token in re.split(r"[.\-]", version):
        if token.isdigit():
            parts.append((0, int(token), ""))
        else:
            parts.append((1, 0, token))
    return parts


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


def cmd_check(session):
    bulletins = fetch_bulletin_versions(session)
    if not bulletins:
        # An empty bulletin universe means the API shape changed; reporting
        # "nothing new" on that would wrongly close open notifications.
        raise BulletinAPIError("bulletin list came back empty")
    tokens = known_tokens()
    floor = publish_floor(bulletins, tokens)
    new_versions = sorted(
        (v for v in bulletins
         if classify_version(v) in ("stable", "beta")
         and not is_known(v, tokens)
         and (floor is None or (bulletins[v].get("publishTime") or "") > floor)),
        key=version_sort_key)
    report = {
        "new_versions": [
            {"version": v, "publishTime": bulletins[v].get("publishTime") or ""}
            for v in new_versions],
    }
    print(json.dumps(report, indent=2, ensure_ascii=False))


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
