import pytest

import docs
import matrix
from repo_files import EXIT_DRIFT

APP_ID = "0123456789abcdef0123456789abcdef"


def prepare(tmp_path, path, with_trailing_newline=True):
    (tmp_path / "supported_tags.md").write_text(
        f"| [`x`](https://www.hiascend.com/developer/ascendhub/detail/"
        f"{APP_ID}?version=x) | x | x |\n", encoding="utf-8")
    doc = ("intro kept\n"
           "<!-- RELEASED:BEGIN -->\nold\n<!-- RELEASED:END -->\n"
           "middle kept\n"
           "<!-- LATEST:BEGIN -->\nold\n<!-- LATEST:END -->\n"
           "tail kept")
    if with_trailing_newline:
        doc += "\n"
    (tmp_path / path).write_text(doc, encoding="utf-8", newline="")
    return tmp_path / path


def entries():
    return matrix.build_matrix_entries("cann", {
        "chips": ["310p", "A3"],
        "os": [{"name": "ubuntu", "version": "22.04"},
               {"name": "openeuler", "version": "24.03"}],
        "py_versions": ["3.10", "3.12"]}, "9.3.0")


def test_replaces_only_the_marked_blocks(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    path = prepare(tmp_path, "OVERVIEW.md")
    docs.update_overview("OVERVIEW.md", "9.3.0", entries(), "block")
    text = path.read_text(encoding="utf-8")

    for kept in ("intro kept", "middle kept", "tail kept"):
        assert kept in text
    assert text.count("<!-- RELEASED:BEGIN -->") == 1
    assert text.count("<!-- LATEST:BEGIN -->") == 1
    assert "CANN 9.3.0 has been released" in text
    assert "### LATEST CANN 9.3.0" in text
    # newest python only: 2 chips * 2 OS = 4 base rows + 4 devel rows
    assert text.count("| [`9.3.0-") == 8
    assert "toolkit, ops, nnal" in text
    assert "toolkit, ops, nnal, os-tool, Python-plugin, googletest" in text
    # documentation order: 310p before A3, ubuntu before openeuler
    assert text.index("9.3.0-310p-ubuntu22.04-py3.12`]") < \
        text.index("9.3.0-310p-openeuler24.03-py3.12`]") < \
        text.index("9.3.0-a3-openeuler24.03-py3.12`]")


def test_preserves_a_missing_eof_newline(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    path = prepare(tmp_path, "OVERVIEW.md", with_trailing_newline=False)
    docs.update_overview("OVERVIEW.md", "9.3.0", entries(), "block")
    assert not path.read_text(encoding="utf-8").endswith("\n")


def test_chinese_doc_uses_ideographic_separators(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    path = prepare(tmp_path, "OVERVIEW.zh.md")
    with_950 = matrix.build_matrix_entries("cann", {
        "chips": ["310p", "950"],
        "os": [{"name": "ubuntu", "version": "22.04"}],
        "py_versions": ["3.12"]}, "9.3.0")
    docs.update_overview("OVERVIEW.zh.md", "9.3.0", with_950, "block")
    text = path.read_text(encoding="utf-8")
    assert "最新版本 CANN 9.3.0" in text
    assert "toolkit、ops、nnal" in text
    assert "URMA" in text


def test_missing_markers_abort_with_instructions(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "supported_tags.md").write_text("no app id\n", encoding="utf-8")
    (tmp_path / "OVERVIEW.md").write_text("no markers here\n", encoding="utf-8")
    with pytest.raises(SystemExit) as exc:
        docs.update_overview("OVERVIEW.md", "9.3.0", entries(), "block")
    assert exc.value.code == EXIT_DRIFT


def test_skip_mode_leaves_the_file_untouched(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    path = prepare(tmp_path, "OVERVIEW.md")
    before = path.read_text(encoding="utf-8")
    docs.update_overview("OVERVIEW.md", "9.3.0", entries(), "skip")
    assert path.read_text(encoding="utf-8") == before
