import pytest

import docs
from repo_files import EXIT_DRIFT

APP_ID = "0123456789abcdef0123456789abcdef"


def test_prepends_a_section_with_plain_and_devel_rows(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "supported_tags.md").write_text(
        "## Supported tags\n\nA list.\n\n"
        f"| [`x`](https://www.hiascend.com/developer/ascendhub/detail/"
        f"{APP_ID}?version=x) | x | x |\n\n### CANN 9.2.0\n",
        encoding="utf-8")

    docs.prepend_supported_tags("9.3.0", [
        "9.3.0-950-ubuntu22.04-py3.12",
        "9.3.0-310p-ubuntu22.04-py3.12",
    ])
    text = (tmp_path / "supported_tags.md").read_text(encoding="utf-8")

    assert text.index("### CANN 9.3.0") < text.index("### CANN 9.2.0")
    assert APP_ID in text
    assert "toolkit/ops/nnal" in text
    assert "toolkit/ops/nnal/os-tool/Python-plugin/googletest" in text
    # documentation order: chip ascending
    assert text.index("9.3.0-310p-ubuntu22.04-py3.12`]") < \
        text.index("9.3.0-950-ubuntu22.04-py3.12`]")


def test_aborts_when_no_heading_exists(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "supported_tags.md").write_text("no headings\n", encoding="utf-8")
    with pytest.raises(SystemExit) as exc:
        docs.prepend_supported_tags("9.3.0", ["9.3.0-910b-ubuntu22.04-py3.12"])
    assert exc.value.code == EXIT_DRIFT
