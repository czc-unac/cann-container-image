#!/usr/bin/env python3
"""The repository's release registry: what versions it already covers.

Coverage is derived from every place a shipped version is recorded: arg-file
tags, publish paths, workflow_dispatch option lines and supported_tags.md
headings. A version is covered only by its exact version segment - the beta
tag 9.2.0-beta.2-... must not mark the later 9.2.0 stable as covered.
"""
import json
import os
import re

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

QUALIFIER_PREFIXES = ("beta",)


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
    """Is this exact version covered by any known tag/path/heading?

    A tag covers a version only when the tag's version segment equals it:
    ``9.2.0-beta.2-910b-...`` covers ``9.2.0-beta.2`` but not ``9.2.0``.
    """
    for token in tokens:
        if token == version:
            return True
        if token.startswith(version + "-"):
            remainder = token[len(version) + 1:]
            if not remainder.startswith(QUALIFIER_PREFIXES):
                return True
    return False
