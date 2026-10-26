#!/usr/bin/env python3
"""Read and rewrite the repository files a release change-set touches.

JSON registries must re-serialize through json.dump byte-identically; the
generator refuses instead of silently reformatting them. Workflow option
lists and the template ALPHA_DICT are edited through named anchors, so a
format change fails with instructions rather than a KeyError.
"""
import importlib.util
import json
import os
import re
import sys

from availability import TEMPLATE_PY, version_sort_key
from registry import (ARG_CANN_JSON, ARG_MANYLINUX_JSON, PUBLISH_CANN_JSON,
                      PUBLISH_MANYLINUX_JSON, WORKFLOW_FILES)

EXIT_OK, EXIT_POLICY, EXIT_UNAVAILABLE, EXIT_COVERED, EXIT_DRIFT, EXIT_FORMAT = (
    0, 2, 3, 4, 5, 6)

OPTIONS_ANCHOR = "        options:"
OPTION_PREFIX = "          - "
ALPHA_DICT_PATTERN = re.compile(r"(ALPHA_DICT = \{.*?\n)\}", re.S)

(PUSH_CANN_YML, PUSH_MANYLINUX_YML,
 BATCH_CANN_YML, BATCH_MANYLINUX_YML) = WORKFLOW_FILES


def abort(code, message):
    print(f"[cann-release] ERROR: {message}", file=sys.stderr)
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
    with open(path, "r", encoding="utf-8", newline="") as f:
        return f.read()


def edit_text_lines(path, transform):
    """Split/transform/join lines, preserving whether the file ends in \\n."""
    raw = read_text_raw(path)
    had_trailing_newline = raw.endswith("\n")
    content = transform(raw.replace("\r\n", "\n"))
    write_text(path, content, keep_trailing_newline=had_trailing_newline)


def load_json_lossless(path):
    """Parse JSON and prove it re-serializes byte-identically via json.dump."""
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


def previous_release(arg_cann):
    """Highest cann_version in build_cann_arg.json plus its entries."""
    versions = sorted({e["cann_version"] for e in arg_cann["cann"]},
                      key=version_sort_key)
    if not versions:
        abort(EXIT_DRIFT, f"{ARG_CANN_JSON} has no entries to compare against")
    previous = versions[-1]
    return previous, [e for e in arg_cann["cann"]
                      if e["cann_version"] == previous]


def patch_alpha_dict(version, link_id):
    """Insert a quoted entry before the closing brace of ALPHA_DICT."""
    text = read_text(TEMPLATE_PY)
    match = ALPHA_DICT_PATTERN.search(text)
    if not match:
        abort(EXIT_DRIFT, f"{TEMPLATE_PY}: could not find an ALPHA_DICT block "
                          "ending with a line containing only '}'")
    body = match.group(1).rstrip()
    if f'"{version}":' in body:
        abort(EXIT_COVERED, f"{version} already present in ALPHA_DICT")
    if not body.endswith(","):
        body += ","
    body += f'\n    "{version}": "{link_id}"\n'
    write_text(TEMPLATE_PY,
               text[:match.start(1)] + body + text[match.end(1):])
    print(f'[cann-release] ALPHA_DICT += "{version}": "{link_id}"')


_PY_URL_CACHE = {}


def cached_python_download_url(original):
    """template.get_python_download_url cached to one lookup per version."""
    def lookup(version):
        if version not in _PY_URL_CACHE:
            _PY_URL_CACHE[version] = original(version)
            print(f"[cann-release] python{version} -> "
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
    """Run template.py's engine for the new version's entries only."""
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
    print(f"[cann-release] {path}: registered {len(tags)} tags")


def insert_into_choice_list(path, new_lines):
    """Insert lines right after the first options: whose items are versions."""
    def transform(text):
        lines = text.split("\n")
        for index in range(len(lines) - 1):
            if lines[index] == OPTIONS_ANCHOR and \
                    lines[index + 1].startswith(OPTION_PREFIX):
                lines[index + 1:index + 1] = new_lines
                print(f"[cann-release] {path}: +{len(new_lines)} options")
                return "\n".join(lines)
        abort(EXIT_DRIFT,
              f"{path}: expected a '{OPTIONS_ANCHOR}' line followed by "
              f"'{OPTION_PREFIX}<tag>' entries; the workflow_dispatch option "
              "list changed shape - update the generator or the workflow")
    edit_text_lines(path, transform)
