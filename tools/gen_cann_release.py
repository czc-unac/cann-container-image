#!/usr/bin/env python3
"""Generate every repository change needed to cover a new CANN release.

Run from the repository root:

    python3 tools/gen_cann_release.py <version> [--link-id <obs-dir>]

Example (beta):   python3 tools/gen_cann_release.py 9.2.0-beta.2 --link-id 9.2.T3
Example (stable): python3 tools/gen_cann_release.py 9.3.0

What it does, mirroring the release-cann-image skill deterministically:

1. Classifies <version> against tools/release_profiles.json. Anything not
   matching a known naming pattern, or any alpha, is rejected so the
   manual skill handles it instead.
2. Requires --link-id for beta releases (the OBS ``CANN x.y.Tn`` directory,
   which cannot be derived from the version string) and rejects it for
   stable releases, whose path is the version itself.
3. Probes the OBS bucket over HTTP to confirm this exact version's
   packages are really downloadable before generating anything.
4. Clones the previous release's matrix entries from build_cann_arg.json /
   build_manylinux_arg.json (single source of truth for chips/OS/Python,
   the cloned set is gated against tools/release_matrix.json), then:
   - inserts the ALPHA_DICT entry (beta only) in tools/template.py,
   - renders the new Dockerfiles through tools/template.py's own engine,
     scoped to the new version so existing files are never rewritten,
   - registers tags in cann_publish_version.json / manylinux_publish_version.json,
   - adds workflow_dispatch options to the four build/push workflow ymls,
   - retargets the LATEST sections of OVERVIEW.md / OVERVIEW.zh.md,
   - prepends a new section to supported_tags.md.
5. Self-checks every registration at the end.

Nothing is committed; the caller (release_cann_agent.yml or a human)
reviews the working tree, then commits and opens the PR.

Exit codes: 0 ok, 2 policy reject, 3 OBS packages unavailable,
4 version already covered, 5 drift or repo-state inconsistency,
6 file formatting not programmatically reproducible (human edit needed).
"""
import argparse
import copy
import importlib.util
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from check_cann_release import is_known, known_tokens, version_sort_key

PROFILES_JSON = os.path.join("tools", "release_profiles.json")
MATRIX_JSON = os.path.join("tools", "release_matrix.json")
TEMPLATE_PY = os.path.join("tools", "template.py")
ARG_CANN_JSON = "build_cann_arg.json"
ARG_MANYLINUX_JSON = "build_manylinux_arg.json"
PUBLISH_CANN_JSON = "cann_publish_version.json"
PUBLISH_MANYLINUX_JSON = "manylinux_publish_version.json"
SUPPORTED_TAGS_MD = "supported_tags.md"
OVERVIEW_FILES = ("OVERVIEW.md", "OVERVIEW.zh.md")
PUSH_CANN_YML = ".github/workflows/build_and_push_cann.yml"
PUSH_MANYLINUX_YML = ".github/workflows/build_and_push_manylinux.yml"
BATCH_CANN_YML = ".github/workflows/batch_build_and_push_cann.yml"
BATCH_MANYLINUX_YML = ".github/workflows/batch_build_and_push_manylinux.yml"

TAGS_APP_ID_FALLBACK = "17da20d1c2b6493cb38765adeba85884"
# Content cells for supported_tags.md rows ONLY. The OVERVIEW tables spell
# the same content differently - "toolkit, ops, nnal" in the English file,
# "toolkit、ops、nnal" in the Chinese one - and must never be derived from
# these constants: update_overview() clone-and-retargets the previous
# release's rows instead, which also preserves each file's header wording
# and separator-row shape.
SUPPORTED_TAGS_CONTENT = "toolkit/ops/nnal"
SUPPORTED_TAGS_CONTENT_DEVEL = ("toolkit/ops/nnal/os-tool/"
                                "Python-plugin/googletest")

EXIT_OK, EXIT_POLICY, EXIT_UNAVAILABLE, EXIT_COVERED, EXIT_DRIFT, EXIT_FORMAT = (
    0, 2, 3, 4, 5, 6)

OVERVIEW_HEADING = re.compile(r"^###.*\bCANN (\S+)$", re.M)


def abort(code, message):
    print(f"[gen_cann_release] ERROR: {message}", file=sys.stderr)
    sys.exit(code)


def read_text(path):
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def write_text(path, content, keep_trailing_newline=True):
    """Write LF text; keep_trailing_newline=False preserves no-EOF-newline."""
    if keep_trailing_newline and not content.endswith("\n"):
        content += "\n"
    if not keep_trailing_newline and content.endswith("\n"):
        content = content[:-1]
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(content)


def read_text_raw(path):
    """Text without universal-newline translation, to inspect EOF state."""
    with open(path, "r", encoding="utf-8", newline="") as f:
        return f.read()


def edit_text_lines(path, transform):
    """Split/transform/join lines, preserving whether the file ends in \\n."""
    raw = read_text_raw(path)
    had_trailing_newline = raw.endswith("\n")
    content = transform(raw.replace("\r\n", "\n"))
    write_text(path, content, keep_trailing_newline=had_trailing_newline)


def load_policy():
    try:
        return json.loads(read_text(PROFILES_JSON))
    except (OSError, ValueError) as exc:
        abort(EXIT_POLICY, f"cannot load {PROFILES_JSON}: {exc}")


def classify_version(version, policy):
    """Return the kind ('stable'/'beta') the version matches, else abort."""
    for bad in policy.get("reject_substrings", []):
        if bad in version.lower():
            abort(EXIT_POLICY, f"version '{version}' contains rejected "
                               f"substring '{bad}'; handle it with the "
                               "release-cann-image skill manually")
    for spec in policy.get("kinds", []):
        if re.match(spec["pattern"], version):
            return spec["kind"]
    abort(EXIT_POLICY, f"version '{version}' matches no naming pattern in "
                       f"{PROFILES_JSON}; extend the config or use the "
                       "release-cann-image skill manually")


def obs_base_url():
    match = re.search(r'^BASE_URL = "([^"]+)"', read_text(TEMPLATE_PY), re.M)
    if not match:
        abort(EXIT_DRIFT, f"could not parse BASE_URL from {TEMPLATE_PY}")
    return match.group(1)


def probe_packages(version, kind, link_id, policy):
    """HEAD the availability probe file; abort (exit 3) unless HTTP 200."""
    layout = policy["url_layout"][kind]
    url = layout.format(base=obs_base_url(), version=version,
                        link_id=link_id or "")
    url += "/" + policy["availability_probe_file"].format(version=version)
    try:
        resp = requests.head(url, timeout=30, allow_redirects=True)
    except requests.RequestException as exc:
        abort(EXIT_UNAVAILABLE, f"probe failed for {url}: {exc}")
    if resp.status_code != 200:
        abort(EXIT_UNAVAILABLE,
              f"CANN {version} packages not downloadable yet (HTTP "
              f"{resp.status_code} for {url}); the bulletin may precede "
              "the actual package upload - retry later")
    print(f"[gen_cann_release] OBS availability confirmed: {url}")


def load_json_lossless(path):
    """Parse JSON and prove it re-serializes byte-identically via json.dump.

    Returns (data, indent, trailing_newline). If the file carries formatting
    that json.dump cannot reproduce (hand edits), abort so nothing mangles.
    """
    raw = read_text(path)
    try:
        data = json.loads(raw)
    except ValueError as exc:
        abort(EXIT_DRIFT, f"{path} is not valid JSON: {exc}")
    for indent in (2, 4):
        for tail in ("\n", ""):
            if json.dumps(data, indent=indent, ensure_ascii=False) + tail == raw:
                return data, indent, bool(tail)
    abort(EXIT_FORMAT, f"{path} carries formatting that json.dump cannot "
                       "reproduce byte-identically; update it manually and "
                       "retry")


def dump_json_lossless(path, data, indent, trailing_newline):
    text = json.dumps(data, indent=indent, ensure_ascii=False)
    if trailing_newline:
        text += "\n"
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)


def previous_version(arg_cann):
    """Highest cann_version present in build_cann_arg.json."""
    versions = sorted({e["cann_version"] for e in arg_cann["cann"]},
                      key=version_sort_key)
    if not versions:
        abort(EXIT_DRIFT, f"{ARG_CANN_JSON} has no entries to clone a matrix from")
    return versions[-1]


def clone_matrix_entries(entries, prev, new):
    """Deep-copy the previous release's entries with the version swapped."""
    cloned = []
    for entry in entries:
        if entry["cann_version"] != prev:
            continue
        fresh = copy.deepcopy(entry)
        fresh["cann_version"] = new
        fresh["tags"] = [new + tag[len(prev):]
                         if tag.startswith(prev + "-") else tag
                         for tag in entry["tags"]]
        cloned.append(fresh)
    for fresh in cloned:
        if any(not tag.startswith(new + "-") for tag in fresh["tags"]):
            abort(EXIT_DRIFT, f"tag of {prev} entry does not carry the version "
                              f"prefix as expected: {fresh['tags']}")
    return cloned


def check_drift_gate(entries, baseline, label):
    """Cloned combos must lie inside the tools/release_matrix.json baseline."""
    def norm(token):
        return re.sub(r"[^a-z0-9]", "", token.lower())
    seen = {"chips": set(), "os": set(), "py_versions": set()}
    for entry in entries:
        seen["chips"].add(norm(entry["cann_chip"]))
        seen["os"].add(norm(f"{entry['os_name']}{entry['os_version']}"))
        seen["py_versions"].add(norm(entry["py_version"]))
    for key, values in sorted(seen.items()):
        allowed = {norm(v) for v in baseline[key]}
        extra = values - allowed
        if extra:
            abort(EXIT_DRIFT, f"{label}: cloned matrix introduces {key} "
                              f"{sorted(extra)} outside {MATRIX_JSON}; run "
                              "the release-cann-image skill manually and "
                              "update the baseline if intended")
        missing = allowed - values
        if missing:
            print(f"[gen_cann_release] NOTE: {label} drops {key} "
                  f"{sorted(missing)} relative to baseline (cloned as is)")


def patch_alpha_dict(version, link_id):
    """Insert a quoted entry before the closing brace of ALPHA_DICT."""
    text = read_text(TEMPLATE_PY)
    match = re.search(r"(ALPHA_DICT = \{.*?\n)\}", text, re.S)
    if not match:
        abort(EXIT_DRIFT, f"could not locate ALPHA_DICT in {TEMPLATE_PY}")
    body = match.group(1).rstrip()
    if f'"{version}":' in body:
        abort(EXIT_COVERED, f"{version} already present in ALPHA_DICT")
    if not body.endswith(","):
        body += ","
    body += f'\n    "{version}": "{link_id}"\n'
    write_text(TEMPLATE_PY, text[:match.start(1)] + body + text[match.end(1):])
    print(f'[gen_cann_release] ALPHA_DICT += "{version}": "{link_id}"')


_PY_URL_CACHE = {}


def cached_python_download_url(original):
    """template.get_python_download_url cached to one lookup per version.

    The original performs one HTTP GET per package item (a typical
    release has 30 cann + 12 manylinux entries); caching keeps that at
    three requests while leaving template.py's URL construction - and
    therefore the generated Dockerfile content - untouched.
    """
    def lookup(version):
        if version not in _PY_URL_CACHE:
            _PY_URL_CACHE[version] = original(version)
            print(f"[gen_cann_release] python{version} -> "
                  f"{_PY_URL_CACHE[version][2]}")
        return _PY_URL_CACHE[version]
    return lookup


def dockerfile_paths(cann_entries, manylinux_entries):
    paths = []
    for entry in cann_entries:
        chip = "a3" if entry["cann_chip"] == "A3" else entry["cann_chip"].lower()
        stem = (f"{entry['cann_version'].lower()}-{chip}-"
                f"{entry['os_name']}{entry['os_version']}-py{entry['py_version']}")
        paths.append(os.path.join("cann", stem, "Dockerfile"))
        paths.append(os.path.join("cann", stem + "-devel", "Dockerfile"))
    for entry in manylinux_entries:
        chip = "a3" if entry["cann_chip"] == "A3" else entry["cann_chip"].lower()
        stem = (f"{entry['cann_version'].lower()}-{chip}-"
                f"{entry['os_name']}_{entry['os_version']}-py{entry['py_version']}")
        paths.append(os.path.join("manylinux", stem, "Dockerfile"))
    return paths


def render_dockerfiles(cann_entries, manylinux_entries):
    """Run tools/template.py's engine for the new version's entries only."""
    spec = importlib.util.spec_from_file_location("cann_template", TEMPLATE_PY)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    mod.get_python_download_url = cached_python_download_url(
        mod.get_python_download_url)
    mod.render_and_save_cann_dockerfile({"cann": cann_entries},
                                        "ubuntu.Dockerfile.j2",
                                        "openeuler.Dockerfile.j2")
    mod.render_and_save_cann_devel_dockerfile({"cann": cann_entries},
                                              "ubuntu.devel.Dockerfile.j2",
                                              "openeuler.devel.Dockerfile.j2")
    mod.render_and_save_manylinux_dockerfile(
        {"manylinux": manylinux_entries}, "manylinux.Dockerfile.j2")


def prepend_publish(path, prefix, tags):
    data, indent, tail = load_json_lossless(path)
    existing = {item["path"] for item in data.get("versions", [])}
    for tag in reversed(tags):
        entry_path = f"{prefix}/{tag}"
        if entry_path in existing:
            continue
        data.setdefault("versions", []).insert(0, {"path": entry_path,
                                                   "tags": [tag]})
    dump_json_lossless(path, data, indent, tail)
    print(f"[gen_cann_release] {path}: registered {len(tags)} tags")


def insert_into_choice_list(path, new_lines):
    """Insert lines right after the first options: whose items are versions."""
    def transform(text):
        lines = text.split("\n")
        for index in range(len(lines) - 1):
            if lines[index] == "        options:" and \
                    re.match(r"^          - ", lines[index + 1]):
                lines[index + 1:index + 1] = new_lines
                print(f"[gen_cann_release] {path}: +{len(new_lines)} options")
                return "\n".join(lines)
        abort(EXIT_DRIFT,
              f"{path}: no workflow_dispatch options list found to extend")
    edit_text_lines(path, transform)


def tags_app_id():
    match = re.search(r"developer/ascendhub/detail/([0-9a-f]{32})\?",
                      read_text(SUPPORTED_TAGS_MD))
    return match.group(1) if match else TAGS_APP_ID_FALLBACK


def supported_tags_row(tag, content, app_id):
    hub = (f"https://www.hiascend.com/developer/ascendhub/detail/"
           f"{app_id}?version={tag}")
    dockerfile = ("https://github.com/Ascend/cann-container-image"
                  f"/blob/main/cann/{tag}/Dockerfile")
    return f"| [`{tag}`]({hub}) | [Dockerfile]({dockerfile}) | {content} |"


def doc_tag_sort_key(version):
    """Order tags the way docs do: chip ascending, ubuntu before other OS,
    python ascending - matching how maintainers sort supported_tags rows
    (which is NOT the build-arg order when e.g. a3 clones between 910b/950)."""
    def key(tag):
        rest = tag[len(version) + 1:]
        chip, tail = rest.split("-", 1)
        os_token, py = tail.rsplit("-py", 1)
        return (chip.lower(), 0 if os_token.startswith("ubuntu") else 1,
                tuple(int(part) for part in py.split(".")))
    return key


def prepend_supported_tags(version, tags_base):
    ordered = sorted(tags_base, key=doc_tag_sort_key(version))
    def transform(text):
        app_id = tags_app_id()
        section = [f"### CANN {version}", "",
                   "| Tag | Dockerfile | content |", "|---|---|---|"]
        section += [supported_tags_row(tag, SUPPORTED_TAGS_CONTENT, app_id)
                    for tag in ordered]
        section += [supported_tags_row(tag + "-devel",
                                       SUPPORTED_TAGS_CONTENT_DEVEL, app_id)
                    for tag in ordered]
        section += [""]
        lines = text.split("\n")
        for index, line in enumerate(lines):
            if line.startswith("### CANN "):
                lines[index:index] = section
                break
        else:
            abort(EXIT_DRIFT, f"{SUPPORTED_TAGS_MD}: no '### CANN' section found")
        print(f"[gen_cann_release] {SUPPORTED_TAGS_MD}: +{len(section) - 1} lines")
        return "\n".join(lines)
    edit_text_lines(SUPPORTED_TAGS_MD, transform)


def overview_latest_version(path):
    """The version OVERVIEW's LATEST heading currently advertises."""
    match = OVERVIEW_HEADING.search(read_text(path))
    if not match:
        abort(EXIT_DRIFT, f"{path}: no '### ... CANN <version>' heading found")
    return match.group(1)


def overview_replacement_pattern(old):
    # old must not match inside longer versions (9.1.10) or inside other
    # versions' qualifier tails (9.1.1-beta.3 when old were 9.1), while
    # tag suffixes like 9.1.1-950 or 9.1.1-a3 must still match.
    return re.compile(r"(?<![\w.\-])" + re.escape(old) +
                      r"(?!-beta)(?!-alpha)(?!-rc)(?![\d.])", re.I)


def update_overview(path, old, new):
    """Retarget the old latest version's references to the new version.

    Rules learned from release commits: the cann-version example row keeps
    its history and only gains the new version at the front (humans also
    normalized the following comma to ", ", reproduced here); elsewhere the
    old-latest token is replaced wherever it stands for the latest release.
    """
    pattern = overview_replacement_pattern(old)

    def transform(text):
        lines = text.replace("\r\n", "\n").split("\n")
        example_index = None
        for index, line in enumerate(lines):
            if "`cann-version`" in line or "`cann版本`" in line:
                if example_index is not None:
                    abort(EXIT_DRIFT, f"{path}: multiple cann-version example rows")
                example_index = index
                continue
            lines[index] = pattern.sub(new, line)
        if example_index is None:
            abort(EXIT_DRIFT, f"{path}: cann-version example row not found")
        row = lines[example_index]
        if f"`{new}`" in row:
            abort(EXIT_COVERED, f"{path}: {new} already in the example row")
        if f"`{old}`,`" in row:
            row = row.replace(f"`{old}`,`", f"`{new}`,`{old}`, `", 1)
        elif f"`{old}`" in row:
            row = row.replace(f"`{old}`", f"`{new}`,`{old}`", 1)
        else:
            abort(EXIT_DRIFT, f"{path}: example row does not reference {old}")
        lines[example_index] = row
        print(f"[gen_cann_release] {path}: LATEST {old} -> {new}")
        return "\n".join(lines)

    edit_text_lines(path, transform)


def verify_latest_tables(new, cann_entries):
    """OVERVIEW LATEST tables must carry exactly the cloned py3.12 combos."""
    combos = {(e["cann_chip"], e["os_name"]) for e in cann_entries
              if e["py_version"] == "3.12"}
    expected = len(combos) * 2
    for path in OVERVIEW_FILES:
        rows = [l for l in read_text(path).split("\n")
                if l.startswith(f"| [`{new}")]
        if len(rows) != expected:
            abort(EXIT_DRIFT, f"{path}: {len(rows)} rows tagged {new}, expected "
                              f"{expected} (plain+devel py3.12 combos cloned "
                              "from the previous release); the LATEST table "
                              "shape drifted from the matrix - handle this "
                              "release with the release-cann-image skill")


def self_check(version, cann_entries, manylinux_entries):
    if not is_known(version, known_tokens()):
        abort(EXIT_DRIFT, "post-generation self-check: the repositories "
                          "registration files still do not cover " + version)
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
    parser.add_argument("--skip-probe", action="store_true",
                        help="skip the OBS availability probe (testing only)")
    args = parser.parse_args()

    for required in (ARG_CANN_JSON, ARG_MANYLINUX_JSON, TEMPLATE_PY):
        if not os.path.exists(required):
            abort(EXIT_POLICY, f"{required} not found; run from the repository root")

    policy = load_policy()
    kind = classify_version(args.version, policy)

    link_id = args.link_id
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

    if is_known(args.version, known_tokens()):
        abort(EXIT_COVERED, f"the repository already covers {args.version}")

    arg_cann, cann_indent, cann_tail = load_json_lossless(ARG_CANN_JSON)
    arg_mlnx, mlnx_indent, mlnx_tail = load_json_lossless(ARG_MANYLINUX_JSON)
    prev = previous_version(arg_cann)
    print(f"[gen_cann_release] {args.version} classified as {kind}; "
          f"cloning matrix from {prev}")

    new_cann = clone_matrix_entries(arg_cann["cann"], prev, args.version)
    new_manylinux = clone_matrix_entries(arg_mlnx["manylinux"], prev, args.version)
    if not new_cann or not new_manylinux:
        abort(EXIT_DRIFT, f"previous version {prev} contributes no entries to "
                          "one of the arg files; repo state is inconsistent")

    matrix = json.loads(read_text(MATRIX_JSON))
    check_drift_gate(new_cann, matrix["cann"], "cann")
    check_drift_gate(new_manylinux, matrix["manylinux"], "manylinux")

    for path in OVERVIEW_FILES:
        latest = overview_latest_version(path)
        if latest != prev:
            abort(EXIT_DRIFT, f"{path} advertises LATEST {latest} but the "
                              f"build matrix tops out at {prev}; reconcile "
                              f"docs before generating {args.version}")

    if not args.skip_probe:
        probe_packages(args.version, kind, link_id, policy)

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
                            ["          - " + tag for tag in tags_base])
    insert_into_choice_list(PUSH_MANYLINUX_YML,
                            ["          - " + tag for tag in tags_mlnx])
    insert_into_choice_list(BATCH_CANN_YML,
                            ["          - " + args.version])
    insert_into_choice_list(BATCH_MANYLINUX_YML,
                            ["          - " + args.version])

    for path in OVERVIEW_FILES:
        update_overview(path, prev, args.version)
    verify_latest_tables(args.version, new_cann)

    prepend_supported_tags(args.version, tags_base)
    self_check(args.version, new_cann, new_manylinux)

    print("[gen_cann_release] SUMMARY " + json.dumps({
        "version": args.version,
        "kind": kind,
        "link_id": link_id,
        "cloned_from": prev,
        "cann_tags": len(tags_base),
        "manylinux_tags": len(tags_mlnx),
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
