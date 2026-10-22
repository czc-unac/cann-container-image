#!/usr/bin/env python3
"""The documentation regions a release generates.

OVERVIEW.md / OVERVIEW.zh.md are prose documents; the generator owns only the
regions fenced by the RELEASED and LATEST markers and never touches anything
outside them. Wording, table layout and separators are per language.
supported_tags.md gets a new section prepended before the newest version.
"""
import re

from registry import SUPPORTED_TAGS_MD
from repo_files import (abort, EXIT_DRIFT, edit_text_lines, read_text,
                        read_text_raw, write_text)

SUPPORTED_TAGS_ANCHOR = "### CANN "
TAGS_APP_ID_FALLBACK = "17da20d1c2b6493cb38765adeba85884"
SUPPORTED_TAGS_CONTENT = "toolkit/ops/nnal"
SUPPORTED_TAGS_CONTENT_DEVEL = ("toolkit/ops/nnal/os-tool/"
                                "Python-plugin/googletest")

OVERVIEW_MARKERS = {
    "OVERVIEW.md": {
        "released": ("<!-- RELEASED:BEGIN -->", "<!-- RELEASED:END -->"),
        "latest": ("<!-- LATEST:BEGIN -->", "<!-- LATEST:END -->"),
    },
}
OVERVIEW_MARKERS["OVERVIEW.zh.md"] = OVERVIEW_MARKERS["OVERVIEW.md"]

OVERVIEW_TEXT = {
    "OVERVIEW.md": {
        "released": ("CANN {version} has been released. Please visit the "
                     "[Mirror Download](https://quay.io/repository/ascend/cann"
                     "?tab=tags) page to obtain it."),
        "heading": "### LATEST CANN {version}",
        "intro": ("For tags associated with historical versions, please refer "
                  "to [Supported Tags](https://github.com/Ascend/"
                  "cann-container-image/tree/main/supported_tags.md)."),
        "table": ["| Tag | Dockerfile | content |", "|---|---|---|"],
        "content_plain": "toolkit, ops, nnal",
        "content_devel": ("toolkit, ops, nnal, os-tool, Python-plugin, "
                          "googletest"),
        "note": ("> Note: On aarch64, the `{version}-950-*` images additionally "
                 "include URMA (Unified RoCE Message Access) for RoCE-based "
                 "distributed communication; x86_64 images do not include it."),
    },
    "OVERVIEW.zh.md": {
        "released": ("CANN 最新版本 {version} 已经发布，请前往 "
                     "[镜像下载](https://www.hiascend.com/developer/ascendhub/"
                     "detail/{app_id}?version={version}) 获取。"),
        "heading": "### 最新版本 CANN {version}",
        "intro": ("如下所示是CANN最新发布的 {version} 版本的所有镜像，历史版本所有的 Tag "
                  "请参考 [Supported Tags](https://github.com/Ascend/"
                  "cann-container-image/tree/main/supported_tags.md)"),
        "table": ["| Tag | Dockerfile | 镜像内容 |", "|---|---|----|"],
        "content_plain": "toolkit、ops、nnal",
        "content_devel": "toolkit、ops、nnal、os-tool、Python-plugin、googletest",
        "note": ("> 注：在 aarch64 架构下，`{version}-950-*` 镜像额外包含 URMA"
                 "（Unified RoCE Message Access，统一 RoCE 消息访问）用于 RoCE"
                 " 分布式通信；x86_64 镜像不包含。"),
    },
}


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
    """Documentation order: chip ascending, ubuntu first, then python."""
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
        section = [f"{SUPPORTED_TAGS_ANCHOR}{version}", "",
                   "| Tag | Dockerfile | content |", "|---|---|---|"]
        section += [supported_tags_row(tag, SUPPORTED_TAGS_CONTENT, app_id)
                    for tag in ordered]
        section += [supported_tags_row(tag + "-devel",
                                       SUPPORTED_TAGS_CONTENT_DEVEL, app_id)
                    for tag in ordered]
        section += [""]
        lines = text.split("\n")
        for index, line in enumerate(lines):
            if line.startswith(SUPPORTED_TAGS_ANCHOR):
                lines[index:index] = section
                break
        else:
            abort(EXIT_DRIFT, f"{SUPPORTED_TAGS_MD}: expected a "
                              f"'{SUPPORTED_TAGS_ANCHOR}<version>' heading to "
                              "insert the new section before")
        print(f"[cann-release] {SUPPORTED_TAGS_MD}: +{len(section) - 1} lines")
        return "\n".join(lines)
    edit_text_lines(SUPPORTED_TAGS_MD, transform)


def replace_marker_block(text, path, marker_name, body):
    begin, end = OVERVIEW_MARKERS[path][marker_name]
    if text.count(begin) != 1 or text.count(end) != 1:
        abort(EXIT_DRIFT,
              f"{path}: expected exactly one '{begin}' and one '{end}' marker "
              f"around the {marker_name} block; re-add the markers (the "
              "generator only rewrites what is fenced) or set "
              "docs.mode=skip in the policy while the docs are reworked")
    pattern = re.compile(re.escape(begin) + r"(.*?)" + re.escape(end), re.S)
    return pattern.sub(lambda _match: f"{begin}\n{body}\n{end}", text)


def latest_py_version(entries):
    return max((e["py_version"] for e in entries),
               key=lambda py: tuple(int(part) for part in py.split(".")))


def update_overview(path, version, cann_entries, docs_mode):
    if docs_mode == "skip":
        print(f"[cann-release] {path}: skipped (docs.mode=skip)")
        return
    raw = read_text_raw(path)
    had_trailing_newline = raw.endswith("\n")
    text = raw.replace("\r\n", "\n")
    config = OVERVIEW_TEXT[path]
    app_id = tags_app_id()
    newest_py = latest_py_version(cann_entries)
    base_tags = sorted(
        (e["tags"][0] for e in cann_entries if e["py_version"] == newest_py),
        key=doc_tag_sort_key(version))
    rows = [supported_tags_row(tag, config["content_plain"], app_id)
            for tag in base_tags]
    rows += [supported_tags_row(tag + "-devel", config["content_devel"], app_id)
             for tag in base_tags]

    latest_lines = [config["heading"].format(version=version), "",
                    config["intro"].format(version=version), ""]
    latest_lines += config["table"] + rows
    if any(e["cann_chip"] == "950" for e in cann_entries):
        latest_lines += ["", config["note"].format(version=version)]

    text = replace_marker_block(text, path, "latest", "\n".join(latest_lines))
    text = replace_marker_block(
        text, path, "released",
        config["released"].format(version=version, app_id=app_id))
    write_text(path, text, keep_trailing_newline=had_trailing_newline)
    print(f"[cann-release] {path}: LATEST block -> {version} "
          f"({len(rows)} rows)")
