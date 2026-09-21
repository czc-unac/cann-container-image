#!/usr/bin/env python3
"""Package-availability probes for CANN release artifacts on Ascend OBS.

Shared by tools/check_cann_release.py (daily monitor) and
tools/gen_cann_release.py (release generator) so both sides agree on what
"the packages for this version exist" means: a version is buildable only
when, for every chip of the repository's current matrix and both arches,
the toolkit, nnal and per-chip ops archives are downloadable. Toolkits and
nnals follow TEMPLATE_PY's URL layout; file names mirror exactly what the
generated Dockerfiles fetch (including the uppercase A3 chip spelling).

Status semantics (probed with anonymous HEAD requests):

* 200            -> present
* 403/404        -> missing (OBS answers 403 for keys of public buckets)
* anything else,
  or a timeout   -> unknown: retried with exponential backoff (see the
                    "probe" policy in tools/release_profiles.json) and only
                    then reported. Callers must treat unknown as an error,
                    never as "missing", so a transient OBS hiccup can not
                    silently suppress a real release - the retries are what
                    keep that strictness practical on CI runners.

Beta scans proceed in batches and stop at the first complete directory, so
finding 9.2.T1/T3 costs a couple of batches rather than a 600-candidate
sweep against the bucket.

Announced-but-incomplete releases (e.g. CANN 9.1.0-beta.2, whose ops
archives for 910b/310p/A3/910 were never uploaded) are the reason this
module exists: the monitor must not open issues for them, and the
generator must refuse to build them.
"""
import json
import os
import random
import re
import time
from concurrent.futures import ThreadPoolExecutor

import requests

# Fallback probe policy; release_profiles.json "availability"."probe"
# overrides these. Deliberately conservative: OBS resets connections when
# hammered from CI runners, and unknown answers cost retries.
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
    """BASE_URL as declared by tools/template.py."""
    with open(template_path, "r", encoding="utf-8") as f:
        match = re.search(r'^BASE_URL = "([^"]+)"', f.read(), re.M)
    if not match:
        raise RuntimeError(f"could not parse BASE_URL from {template_path}")
    return match.group(1)


def load_availability_policy(profiles_path):
    """The 'availability' section of tools/release_profiles.json."""
    with open(profiles_path, "r", encoding="utf-8") as f:
        policy = json.load(f)
    try:
        return policy["availability"]
    except KeyError:
        raise RuntimeError(f"{profiles_path} has no 'availability' section")


def current_chips(arg_cann_path, arg_manylinux_path):
    """Chip series the newest releases in the arg files actually cover.

    Union across both files' newest version blocks; order is the sorted
    de-duplicated set so probes are deterministic.
    """
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
    """OBS directory prefix, matching tools/template.py's layout."""
    directory = link_id if kind == "beta" else version
    return f"{base_url}/CANN/CANN%20{directory}"


def beta_dir_candidates(version, scan_max):
    """['9.1.T1', ...] for '9.1.0-beta.2'; empty for non-beta names."""
    match = re.match(r"^(\d+\.\d+)\.\d+-beta\.", version)
    if not match:
        return []
    return [f"{match.group(1)}.T{n}" for n in range(1, scan_max + 1)]


def _probe(url, missing_statuses, timeout, retries, backoff_seconds):
    """(state, detail) for one HEAD request, retried while unknown.

    200 and the missing statuses are authoritative and returned at once;
    timeouts, connection resets and other statuses are retried with
    exponential backoff plus jitter. Only an answer that stays unknown
    across every attempt is reported as UNKNOWN to the caller.
    """
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
    """Locate the OBS directory of a beta version and test completeness.

    Candidates are scanned in batches of ``workers`` and the search stops
    the moment a directory turns out complete, so a beta whose directory
    sorts early (the common case: 9.2.T1, 9.2.T3) costs two batches
    instead of a whole-range sweep. Returns (buildable_dir,
    discovered_dirs, incomplete{dir: missing list}).

    Raises AvailabilityUnknown when the toolkit was never found and at
    least one candidate answered unclearly, so a flaky gateway cannot turn
    into a silent "not buildable". The scan also aborts as soon as
    ``unknown_budget`` candidates answer unclearly: with retries a fully
    unreachable gateway would otherwise walk all 600 candidates and burn
    the runner's time budget.
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
