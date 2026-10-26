import pytest

import matrix
from repo_files import EXIT_DRIFT

MATRIX = {
    "chips": ["310p", "A3"],
    "os": [{"name": "ubuntu", "version": "22.04"},
           {"name": "openeuler", "version": "24.03"}],
    "py_versions": ["3.10", "3.12"],
}


def test_builds_the_cartesian_product_in_arg_file_order():
    tags = [e["tags"][0] for e in matrix.build_matrix_entries("cann", MATRIX, "9.3.0")]
    assert tags == [
        "9.3.0-310p-ubuntu22.04-py3.10",
        "9.3.0-310p-ubuntu22.04-py3.12",
        "9.3.0-310p-openeuler24.03-py3.10",
        "9.3.0-310p-openeuler24.03-py3.12",
        "9.3.0-a3-ubuntu22.04-py3.10",
        "9.3.0-a3-ubuntu22.04-py3.12",
        "9.3.0-a3-openeuler24.03-py3.10",
        "9.3.0-a3-openeuler24.03-py3.12",
    ]


def test_entries_carry_the_fields_the_arg_files_use():
    entry = matrix.build_matrix_entries("cann", MATRIX, "9.3.0")[0]
    assert entry == {
        "cann_version": "9.3.0",
        "cann_chip": "310p",
        "os_name": "ubuntu",
        "os_version": "22.04",
        "py_version": "3.10",
        "tags": ["9.3.0-310p-ubuntu22.04-py3.10"],
    }


def test_manylinux_joins_os_name_and_version_with_an_underscore():
    entries = matrix.build_matrix_entries("manylinux", {
        "chips": ["910b"], "os": [{"name": "manylinux", "version": "2_34"}],
        "py_versions": ["3.12"]}, "9.3.0")
    assert entries[0]["tags"] == ["9.3.0-910b-manylinux_2_34-py3.12"]


def test_empty_sections_are_rejected():
    with pytest.raises(SystemExit) as exc:
        matrix.build_matrix_entries("cann", {"chips": [], "os": [],
                                             "py_versions": ["3.12"]}, "9.3.0")
    assert exc.value.code == EXIT_DRIFT


def test_os_entries_must_be_structured():
    with pytest.raises(SystemExit) as exc:
        matrix.build_matrix_entries("cann", {"chips": ["910b"],
                                             "os": ["ubuntu22.04"],
                                             "py_versions": ["3.12"]}, "9.3.0")
    assert exc.value.code == EXIT_DRIFT


def test_matrix_diff_reports_additions_and_removals(capsys):
    before = matrix.build_matrix_entries("cann", MATRIX, "9.2.0")
    after = matrix.build_matrix_entries("cann", {
        "chips": ["310p", "960"],
        "os": [{"name": "ubuntu", "version": "22.04"}],
        "py_versions": ["3.12"]}, "9.3.0")
    matrix.report_matrix_diff(before, after, "cann")
    out = capsys.readouterr().out
    assert "adds" in out and "drops" in out
