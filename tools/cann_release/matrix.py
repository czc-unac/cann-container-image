#!/usr/bin/env python3
"""The declared release matrix: chips x OS x Python, per family.

The cartesian product comes from release_matrix.json - that is the intent.
The previous release only supplies entry ordering and formatting, and the
difference against it is reported for review, never blocked.
"""
import json

from repo_files import abort, EXIT_DRIFT, EXIT_POLICY, read_text

MATRIX_JSON = "tools/cann_release/release_matrix.json"


def load_matrix():
    try:
        return json.loads(read_text(MATRIX_JSON))
    except (OSError, ValueError) as exc:
        abort(EXIT_POLICY, f"cannot load {MATRIX_JSON}: {exc}")


def tag_for(family, version, chip, os_name, os_version, py_version):
    """Tag convention per family: ubuntu22.04 vs manylinux_2_34."""
    separator = "" if family == "cann" else "_"
    return (f"{version}-{chip.lower()}-{os_name}{separator}{os_version}"
            f"-py{py_version}")


def build_matrix_entries(family, config, version):
    """Cartesian product of the declared matrix, in arg-file order."""
    for key in ("chips", "os", "py_versions"):
        if not config.get(key):
            abort(EXIT_DRIFT, f"{MATRIX_JSON}: {family}.{key} must be a "
                              "non-empty list")
    entries = []
    for chip in config["chips"]:
        for os_spec in config["os"]:
            try:
                os_name, os_version = os_spec["name"], os_spec["version"]
            except (TypeError, KeyError):
                abort(EXIT_DRIFT, f"{MATRIX_JSON}: {family}.os entries must be "
                                  "objects with 'name' and 'version'")
            for py_version in config["py_versions"]:
                entries.append({
                    "cann_version": version,
                    "cann_chip": chip,
                    "os_name": os_name,
                    "os_version": os_version,
                    "py_version": py_version,
                    "tags": [tag_for(family, version, chip, os_name,
                                     os_version, py_version)],
                })
    return entries


def matrix_keys(entries):
    return {(e["cann_chip"], e["os_name"], e["os_version"], e["py_version"])
            for e in entries}


def report_matrix_diff(previous_entries, new_entries, label):
    """Report (never block) how the matrix differs from the last release."""
    before, after = matrix_keys(previous_entries), matrix_keys(new_entries)
    added, removed = after - before, before - after
    if added:
        print(f"[cann-release] NOTE: {label} adds {len(added)} "
              f"combination(s) relative to the previous release")
    if removed:
        print(f"[cann-release] NOTE: {label} drops {len(removed)} "
              f"combination(s) relative to the previous release")
