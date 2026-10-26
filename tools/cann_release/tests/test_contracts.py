import json
import re

WORKFLOW_FILES = (
    ".github/workflows/build_and_push_cann.yml",
    ".github/workflows/build_and_push_manylinux.yml",
    ".github/workflows/batch_build_and_push_cann.yml",
    ".github/workflows/batch_build_and_push_manylinux.yml",
)
ARG_FILES = ("build_cann_arg.json", "build_manylinux_arg.json")
PUBLISH_FILES = ("cann_publish_version.json", "manylinux_publish_version.json")
DOC_PAIRS = ("cann_release_monitor",)


def read(repo_root, name):
    return (repo_root / name).read_text(encoding="utf-8")


def test_workflow_option_lines_stay_readable(repo_root):
    """The monitor derives covered tags from these exact option lines."""
    for name in WORKFLOW_FILES:
        text = read(repo_root, name)
        assert re.search(r"^          - \S+$", text, re.M), \
            f"{name}: no '          - <tag>' option lines found"


def test_supported_tags_headings_stay_readable(repo_root):
    text = read(repo_root, "supported_tags.md")
    assert re.search(r"^### CANN \S+$", text, re.M), \
        "supported_tags.md: no '### CANN <version>' headings found"


def test_registration_json_files_parse(repo_root):
    for name in ARG_FILES + PUBLISH_FILES:
        payload = json.loads(read(repo_root, name))
        assert payload, f"{name} is empty"


def test_newest_matrix_entries_have_the_fields_the_monitor_reads(repo_root):
    for name, group in (("build_cann_arg.json", "cann"),
                        ("build_manylinux_arg.json", "manylinux")):
        entries = json.loads(read(repo_root, name))[group]
        newest = entries[0]["cann_version"]
        for entry in entries:
            if entry["cann_version"] != newest:
                continue
            for field in ("cann_version", "cann_chip", "os_name",
                          "os_version", "py_version", "tags"):
                assert field in entry, f"{name}: newest entry lacks {field}"
            assert entry["tags"], f"{name}: newest entry has no tags"


def test_template_base_url_stays_parseable(repo_root):
    text = read(repo_root, "tools/template.py")
    assert re.search(r'^BASE_URL = "https?://', text, re.M), \
        "tools/template.py: BASE_URL declaration not found"


def test_availability_policy_has_the_expected_shape(repo_root):
    policy = json.loads(read(repo_root, "tools/cann_release/release_profiles.json"))
    availability = policy["availability"]
    for key in ("arches", "file_templates", "beta_scan_max",
                "missing_statuses", "probe"):
        assert key in availability, f"availability lacks {key}"
    for key in ("timeout_seconds", "workers", "retries", "backoff_seconds",
                "unknown_budget"):
        assert key in availability["probe"], f"probe lacks {key}"
    templates = " ".join(availability["file_templates"])
    assert "{version}" in templates and "{arch}" in templates
    assert "{chip}" in templates, "no per-chip ops template configured"


def heading_levels(path):
    """Heading depth per line, ignoring fenced code blocks."""
    levels, fenced = [], False
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith("```"):
            fenced = not fenced
            continue
        if not fenced and line.startswith("#"):
            levels.append(len(line) - len(line.lstrip("#")))
    return levels


def test_docs_exist_in_both_languages_with_the_same_structure(repo_root):
    for base in DOC_PAIRS:
        en = repo_root / "docs" / f"{base}.md"
        zh = repo_root / "docs" / f"{base}_zh.md"
        assert en.exists(), f"missing {en.name}"
        assert zh.exists(), f"missing {zh.name}"
        assert heading_levels(en) == heading_levels(zh), \
            f"{base}: English and Chinese docs drifted structurally"
        en_text, zh_text = en.read_text(encoding="utf-8"), zh.read_text(encoding="utf-8")
        assert f"({base}_zh.md)" in en_text, f"{en.name}: no Chinese link"
        assert f"({base}.md)" in zh_text, f"{zh.name}: no English link"
