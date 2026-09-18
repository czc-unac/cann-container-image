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
  or a timeout   -> unknown: the probe could not decide. Callers must treat
                    unknown as an error, never as "missing", so a transient
                    OBS hiccup can not silently suppress a real release.

Announced-but-incomplete releases (e.g. CANN 9.1.0-beta.2, whose ops
archives for 910b/310p/A3/910 were never uploaded) are the reason this
module exists: the monitor must not open issues for them, and the
generator must refuse to build them.
"""
import json
import os
import re
from concurrent.futures import ThreadPoolExecutor

import requests

DEFAULT_TIMEOUT = 20
DEFAULT_WORKERS = 12

PRESENT = "present"
MISSING = "missing"
UNKNOWN = "unknown"


class AvailabilityUnknown(RuntimeError):
    """At least one probe could not be decided (timeout, 5xx, ...)."""


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


def _probe(url, missing_statuses, timeout):
    """(state, detail) for one HEAD request; never raises."""
    try:
        resp = requests.head(url, timeout=timeout, allow_redirects=True)
    except requests.RequestException as exc:
        return UNKNOWN, str(exc)
    if resp.status_code == 200:
        return PRESENT, ""
    if resp.status_code in missing_statuses:
        return MISSING, f"HTTP {resp.status_code}"
    return UNKNOWN, f"HTTP {resp.status_code}"


def check_files(prefix, files, missing_statuses,
                timeout=DEFAULT_TIMEOUT, workers=DEFAULT_WORKERS):
    """Probe prefix/<file> for every file; returns (missing, unknown)."""
    def one(name):
        state, detail = _probe(f"{prefix}/{name}", missing_statuses, timeout)
        return name, state, detail

    with ThreadPoolExecutor(max_workers=workers) as pool:
        results = list(pool.map(one, files))
    missing = [name for name, state, _ in results if state == MISSING]
    unknown = [(name, detail) for name, state, detail in results
               if state == UNKNOWN]
    return missing, unknown


def scan_beta(base_url, version, files, policy, missing_statuses):
    """Locate the OBS directory of a beta version and test completeness.

    Returns (buildable_dir, discovered_dirs, incomplete{dir: missing list}).
    Raises AvailabilityUnknown when the toolkit was never found and at
    least one candidate directory answered unclearly, so a flaky gateway
    cannot turn into a silent "not buildable".
    """
    toolkit = next(name for name in files
                   if name.startswith("Ascend-cann-toolkit_"))
    candidates = beta_dir_candidates(version, policy["beta_scan_max"])

    def probe_toolkit(candidate):
        state, _ = _probe(f"{base_url}/CANN/CANN%20{candidate}/{toolkit}",
                          missing_statuses, DEFAULT_TIMEOUT)
        return candidate, state

    with ThreadPoolExecutor(max_workers=DEFAULT_WORKERS) as pool:
        hits = list(pool.map(probe_toolkit, candidates))
    found = [candidate for candidate, state in hits if state == PRESENT]
    unknown = [candidate for candidate, state in hits if state == UNKNOWN]

    buildable = None
    incomplete = {}
    for candidate in found:
        prefix = f"{base_url}/CANN/CANN%20{candidate}"
        missing, unk = check_files(prefix, files, missing_statuses)
        if unk:
            raise AvailabilityUnknown(
                f"probing {prefix} answered unclearly: {unk[:3]}")
        if not missing:
            buildable = candidate
            break
        incomplete[candidate] = missing

    if buildable is None and not found and unknown:
        raise AvailabilityUnknown(
            f"{len(unknown)} candidate directories did not answer, "
            f"e.g. {unknown[:3]}")
    return buildable, found, incomplete
