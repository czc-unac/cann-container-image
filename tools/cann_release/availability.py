#!/usr/bin/env python3
"""Package-availability probes for CANN release artifacts on Ascend OBS.

A version is buildable only when the toolkit, nnal and per-chip ops archives
exist for every chip of the current matrix on both arches. File names mirror
what the generated Dockerfiles fetch (including the uppercase A3 spelling).

Anonymous HEAD answers: 200 present, 403/404 missing, anything else unknown.
Unknown is retried with backoff and then reported as an error - never as
"missing", so a flaky gateway cannot suppress a real release.
"""
import json
import os
import random
import re
import time
from concurrent.futures import ThreadPoolExecutor

import requests

TEMPLATE_PY = os.path.join("tools", "template.py")

# Fallback probe policy; release_profiles.json "availability"."probe" wins.
DEFAULT_PROBE = {
    "timeout_seconds": 30,
    "workers": 4,
    "retries": 4,
    "backoff_seconds": 0.5,
    "unknown_budget": 8,
}

PRESENT = "present"
MISSING = "missing"
UNKNOWN = "unknown"


class AvailabilityUnknown(RuntimeError):
    """At least one probe could not be decided (timeout, 5xx, ...)."""


def probe_settings(policy):
    """Probe policy from the availability section, with safe defaults."""
    settings = dict(DEFAULT_PROBE)
    settings.update(policy.get("probe", {}))
    return settings


def version_sort_key(version):
    """Sort key so 9.1.0 < 9.1.0-beta.1 < 9.1.1 < 9.2.0-beta.1."""
    parts = []
    for token in re.split(r"[.\-]", version):
        if token.isdigit():
            parts.append((0, int(token), ""))
        else:
            parts.append((1, 0, token))
    return parts


def obs_base_url(template_path):
    """BASE_URL as declared by template.py."""
    with open(template_path, "r", encoding="utf-8") as f:
        match = re.search(r'^BASE_URL = "([^"]+)"', f.read(), re.M)
    if not match:
        raise RuntimeError(f"could not parse BASE_URL from {template_path}")
    return match.group(1)


def load_availability_policy(profiles_path):
    """The 'availability' section of release_profiles.json."""
    with open(profiles_path, "r", encoding="utf-8") as f:
        policy = json.load(f)
    try:
        return policy["availability"]
    except KeyError:
        raise RuntimeError(f"{profiles_path} has no 'availability' section")


def current_chips(arg_cann_path, arg_manylinux_path):
    """Chips covered by the newest version block of both arg files."""
    chips = set()
    for path in (arg_cann_path, arg_manylinux_path):
        if not os.path.exists(path):
            continue
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        for group in data.values():
            if not group:
                continue
            newest = max((entry["cann_version"] for entry in group),
                         key=version_sort_key)
            chips.update(entry["cann_chip"] for entry in group
                         if entry["cann_version"] == newest)
    return sorted(chips)


def required_files(policy, version, chips):
    """Every archive a complete build of this version needs."""
    files = []
    for template in policy["file_templates"]:
        if "{chip}" in template:
            for chip in chips:
                for arch in policy["arches"]:
                    files.append(template.format(version=version, chip=chip,
                                                 arch=arch))
        else:
            for arch in policy["arches"]:
                files.append(template.format(version=version, arch=arch))
    return files


def cann_url_prefix(base_url, version, kind, link_id=None):
    """OBS directory prefix, matching template.py's layout."""
    directory = link_id if kind == "beta" else version
    return f"{base_url}/CANN/CANN%20{directory}"


def beta_dir_candidates(version, scan_max):
    """['9.1.T1', ...] for '9.1.0-beta.2'; empty for non-beta names."""
    match = re.match(r"^(\d+\.\d+)\.\d+-beta\.", version)
    if not match:
        return []
    return [f"{match.group(1)}.T{n}" for n in range(1, scan_max + 1)]


def _probe(url, missing_statuses, timeout, retries, backoff_seconds):
    """(state, detail) for one HEAD request, retried while unknown."""
    detail = ""
    for attempt in range(retries + 1):
        try:
            resp = requests.head(url, timeout=timeout, allow_redirects=True)
        except requests.RequestException as exc:
            detail = str(exc)
        else:
            if resp.status_code == 200:
                return PRESENT, ""
            if resp.status_code in missing_statuses:
                return MISSING, f"HTTP {resp.status_code}"
            detail = f"HTTP {resp.status_code}"
        if attempt < retries:
            time.sleep(backoff_seconds * (2 ** attempt)
                       + random.uniform(0, backoff_seconds))
    return UNKNOWN, detail


def check_files(prefix, files, missing_statuses, settings=None):
    """Probe prefix/<file> for every file; returns (missing, unknown)."""
    settings = settings or dict(DEFAULT_PROBE)

    def one(name):
        state, detail = _probe(f"{prefix}/{name}", missing_statuses,
                               settings["timeout_seconds"],
                               settings["retries"],
                               settings["backoff_seconds"])
        return name, state, detail

    with ThreadPoolExecutor(max_workers=settings["workers"]) as pool:
        results = list(pool.map(one, files))
    missing = [name for name, state, _ in results if state == MISSING]
    unknown = [(name, detail) for name, state, detail in results
               if state == UNKNOWN]
    return missing, unknown


def scan_beta(base_url, version, files, policy, missing_statuses):
    """Locate a beta's OBS directory and test completeness.

    Candidates are scanned in batches and the search stops at the first
    complete directory. Raises AvailabilityUnknown when probes stay unclear,
    so a flaky gateway never turns into a silent "not buildable";
    unknown_budget bounds the damage when OBS itself is unreachable.
    """
    settings = probe_settings(policy)
    toolkit = next(name for name in files
                   if name.startswith("Ascend-cann-toolkit_"))
    candidates = beta_dir_candidates(version, policy["beta_scan_max"])

    def probe_toolkit(candidate):
        state, _ = _probe(f"{base_url}/CANN/CANN%20{candidate}/{toolkit}",
                          missing_statuses, settings["timeout_seconds"],
                          settings["retries"], settings["backoff_seconds"])
        return candidate, state

    batch = settings["workers"]
    found = []
    incomplete = {}
    unknown = []
    for start in range(0, len(candidates), batch):
        chunk = candidates[start:start + batch]
        with ThreadPoolExecutor(max_workers=batch) as pool:
            hits = list(pool.map(probe_toolkit, chunk))
        found.extend(c for c, state in hits if state == PRESENT)
        unknown.extend(c for c, state in hits if state == UNKNOWN)
        if len(unknown) >= settings["unknown_budget"]:
            raise AvailabilityUnknown(
                f"{len(unknown)} candidate directories did not answer while "
                f"scanning {version}; the OBS gateway looks unreachable "
                f"(first: {unknown[:3]})")
        for candidate in [c for c, state in hits if state == PRESENT]:
            prefix = f"{base_url}/CANN/CANN%20{candidate}"
            missing, unk = check_files(prefix, files, missing_statuses,
                                       settings)
            if unk:
                raise AvailabilityUnknown(
                    f"probing {prefix} answered unclearly: {unk[:3]}")
            if not missing:
                return candidate, found, incomplete
            incomplete[candidate] = missing

    if not found and unknown:
        raise AvailabilityUnknown(
            f"{len(unknown)} candidate directories did not answer, "
            f"e.g. {unknown[:3]}")
    return None, found, incomplete
