#!/usr/bin/env python3
"""Turn one CANN version into the complete release change-set.

Run from the repository root:

    python3 tools/cann_release/generator.py <version> [--link-id x.y.Tn] [--dry-run]

Pipeline: classify the version -> validate the beta link-id -> build the
declared matrix -> probe OBS -> write the arg/publish registries, Dockerfiles,
workflow option lists, OVERVIEW marker blocks and the supported_tags section
-> self-check. Nothing is committed.

Exit codes: 0 ok, 2 policy reject, 3 packages unavailable, 4 already covered,
5 drift or inconsistent state, 6 JSON formatting not reproducible.
"""
import argparse
import json
import os
import re
import sys

from availability import (TEMPLATE_PY, cann_url_prefix, check_files,
                          obs_base_url, probe_settings, required_files)
from docs import prepend_supported_tags, update_overview
from matrix import build_matrix_entries, load_matrix, report_matrix_diff
from registry import (ARG_CANN_JSON, ARG_MANYLINUX_JSON, PUBLISH_CANN_JSON,
                      PUBLISH_MANYLINUX_JSON, SUPPORTED_TAGS_MD, is_known,
                      known_tokens)
from repo_files import (BATCH_CANN_YML, BATCH_MANYLINUX_YML, EXIT_COVERED,
                        EXIT_DRIFT, EXIT_FORMAT, EXIT_POLICY, EXIT_UNAVAILABLE,
                        OPTION_PREFIX, PUSH_CANN_YML, PUSH_MANYLINUX_YML,
                        abort, dockerfile_paths, dump_json_lossless,
                        insert_into_choice_list, load_json_lossless,
                        patch_alpha_dict, prepend_publish, previous_release,
                        read_text, render_dockerfiles)

PROFILES_JSON = "tools/cann_release/release_profiles.json"
OVERVIEW_FILES = ("OVERVIEW.md", "OVERVIEW.zh.md")


def load_policy():
    try:
        return json.loads(read_text(PROFILES_JSON))
    except (OSError, ValueError) as exc:
        abort(EXIT_POLICY, f"cannot load {PROFILES_JSON}: {exc}")


def classify_version(version, policy):
    """Return the kind ('stable'/'beta') the version matches, else abort."""
    for spec in policy.get("kinds", []):
        if re.match(spec["pattern"], version):
            return spec["kind"]
    abort(EXIT_POLICY, f"version '{version}' matches no naming pattern in "
                       f"{PROFILES_JSON}; extend the config or use the "
                       "release-cann-image skill manually")


def resolve_link_id(kind, link_id, policy):
    """Validate the beta-only OBS directory argument."""
    if kind == "beta":
        if policy.get("beta_requires_link_id", True) and not link_id:
            abort(EXIT_POLICY, "beta releases need the OBS directory id: "
                               "--link-id x.y.Tn (visible in the bulletin's "
                               "download links); pass it as the second word "
                               "of /release-cann")
        if link_id and not re.match(policy.get("link_id_pattern", ""), link_id):
            abort(EXIT_POLICY, f"link-id '{link_id}' does not match "
                               "release_profiles.json link_id_pattern")
    elif link_id:
        abort(EXIT_POLICY, "stable releases derive their OBS path from the "
                           "version itself; --link-id must not be given")
    return link_id


def probe_packages(version, kind, link_id, policy, chips):
    """Every archive a complete build needs must exist on OBS; else exit 3."""
    base_url = obs_base_url(TEMPLATE_PY)
    availability = policy["availability"]
    files = required_files(availability, version, chips)
    prefix = cann_url_prefix(base_url, version, kind, link_id)
    missing, unknown = check_files(prefix, files,
                                   availability["missing_statuses"],
                                   probe_settings(availability))
    if unknown:
        abort(EXIT_UNAVAILABLE, f"OBS probe inconclusive for {prefix}: "
                                f"{unknown[:3]}; retry later")
    if missing:
        abort(EXIT_UNAVAILABLE,
              f"CANN {version} is announced but its package set is incomplete "
              f"({len(missing)}/{len(files)} files missing under {prefix}), "
              f"e.g. {missing[:4]}. The bulletin may still be uploading, or "
              "the release intentionally covers only part of the platform "
              "matrix (e.g. 950 only) - in that case build it with the "
              "release-cann-image skill manually")
    print(f"[cann-release] OBS availability confirmed "
          f"({len(files)} files): {prefix}")


def self_check(version, cann_entries, manylinux_entries):
    if not is_known(version, known_tokens()):
        abort(EXIT_DRIFT, "post-generation self-check: the registration files "
                          "still do not cover " + version)
    missing = [p for p in dockerfile_paths(cann_entries, manylinux_entries)
               if not os.path.exists(p)]
    if missing:
        abort(EXIT_DRIFT, f"missing generated Dockerfiles: {missing[:3]}")


def main():
    parser = argparse.ArgumentParser(
        description="Generate all changes covering a new CANN release")
    parser.add_argument("version", help="CANN version, e.g. 9.3.0-beta.1")
    parser.add_argument("--link-id",
                        help="OBS CANN x.y.Tn directory; required for beta")
    parser.add_argument("--dry-run", action="store_true",
                        help="validate and print the planned change-set only")
    args = parser.parse_args()

    for required in (ARG_CANN_JSON, ARG_MANYLINUX_JSON, TEMPLATE_PY):
        if not os.path.exists(required):
            abort(EXIT_POLICY, f"{required} not found; run from the repository root")

    policy = load_policy()
    kind = classify_version(args.version, policy)
    link_id = resolve_link_id(kind, args.link_id, policy)

    if is_known(args.version, known_tokens()):
        abort(EXIT_COVERED, f"the repository already covers {args.version}")

    arg_cann, cann_indent, cann_tail = load_json_lossless(ARG_CANN_JSON)
    arg_mlnx, mlnx_indent, mlnx_tail = load_json_lossless(ARG_MANYLINUX_JSON)
    previous, previous_cann = previous_release(arg_cann)
    previous_mlnx = [e for e in arg_mlnx["manylinux"]
                     if e["cann_version"] == previous]
    print(f"[cann-release] {args.version} classified as {kind}; "
          f"previous release is {previous}")

    matrix = load_matrix()
    new_cann = build_matrix_entries("cann", matrix["cann"], args.version)
    new_manylinux = build_matrix_entries("manylinux", matrix["manylinux"],
                                         args.version)
    report_matrix_diff(previous_cann, new_cann, "cann")
    report_matrix_diff(previous_mlnx, new_manylinux, "manylinux")
    print(f"[cann-release] declared matrix: {len(new_cann)} cann + "
          f"{len(new_manylinux)} manylinux tags")

    chips = sorted({e["cann_chip"] for e in new_cann + new_manylinux})
    probe_packages(args.version, kind, link_id, policy, chips)

    if args.dry_run:
        print("[cann-release] DRY RUN - nothing written")
        for entry in new_cann:
            print(f"  cann      {entry['tags'][0]}")
        for entry in new_manylinux:
            print(f"  manylinux {entry['tags'][0]}")
        print(f"  files: {ARG_CANN_JSON}, {ARG_MANYLINUX_JSON}, "
              f"{PUBLISH_CANN_JSON}, {PUBLISH_MANYLINUX_JSON}, "
              f"{SUPPORTED_TAGS_MD}, {', '.join(OVERVIEW_FILES)}, "
              f"4 workflow ymls, {len(new_cann) * 2 + len(new_manylinux)} "
              "Dockerfiles"
              + (f", {TEMPLATE_PY} (ALPHA_DICT)" if kind == "beta" else ""))
        return

    if kind == "beta":
        patch_alpha_dict(args.version, link_id)

    arg_cann["cann"] = new_cann + arg_cann["cann"]
    dump_json_lossless(ARG_CANN_JSON, arg_cann, cann_indent, cann_tail)
    arg_mlnx["manylinux"] = new_manylinux + arg_mlnx["manylinux"]
    dump_json_lossless(ARG_MANYLINUX_JSON, arg_mlnx, mlnx_indent, mlnx_tail)

    render_dockerfiles(new_cann, new_manylinux)

    tags_base = [e["tags"][0] for e in new_cann]
    tags_mlnx = [e["tags"][0] for e in new_manylinux]
    prepend_publish(PUBLISH_CANN_JSON, "cann", tags_base)
    prepend_publish(PUBLISH_MANYLINUX_JSON, "manylinux", tags_mlnx)

    insert_into_choice_list(PUSH_CANN_YML,
                            [OPTION_PREFIX + tag for tag in tags_base])
    insert_into_choice_list(PUSH_MANYLINUX_YML,
                            [OPTION_PREFIX + tag for tag in tags_mlnx])
    insert_into_choice_list(BATCH_CANN_YML,
                            [OPTION_PREFIX + args.version])
    insert_into_choice_list(BATCH_MANYLINUX_YML,
                            [OPTION_PREFIX + args.version])

    docs_mode = policy.get("docs", {}).get("mode", "block")
    if docs_mode not in ("block", "skip"):
        abort(EXIT_POLICY, f"{PROFILES_JSON}: docs.mode must be 'block' or "
                           f"'skip', got {docs_mode!r}")
    for path in OVERVIEW_FILES:
        update_overview(path, args.version, new_cann, docs_mode)

    prepend_supported_tags(args.version, tags_base)
    self_check(args.version, new_cann, new_manylinux)

    print("[cann-release] SUMMARY " + json.dumps({
        "version": args.version,
        "kind": kind,
        "link_id": link_id,
        "previous": previous,
        "cann_tags": len(tags_base),
        "manylinux_tags": len(tags_mlnx),
        "docs_mode": docs_mode,
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
