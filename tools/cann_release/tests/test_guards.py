import json
import pathlib
import subprocess
import sys

import pytest

import generator as gen


def policy():
    return {
        "kinds": [
            {"kind": "beta", "pattern": r"^\d+\.\d+\.\d+-beta\.\d+$"},
            {"kind": "stable", "pattern": r"^\d+\.\d+\.\d+$"},
        ],
        "beta_requires_link_id": True,
        "link_id_pattern": r"^\d+\.\d+\.T\d+$",
    }


def test_classify_version_accepts_regular_names():
    p = policy()
    assert gen.classify_version("9.3.0", p) == "stable"
    assert gen.classify_version("9.3.0-beta.2", p) == "beta"


def test_classify_version_rejects_irregular_names():
    p = policy()
    for bad in ("9.3", "9.3.0-beta"):
        with pytest.raises(SystemExit) as exc:
            gen.classify_version(bad, p)
        assert exc.value.code == gen.EXIT_POLICY


def test_link_id_rules():
    p = policy()
    assert gen.resolve_link_id("beta", "9.3.T2", p) == "9.3.T2"
    for kind, link in (("beta", None), ("beta", "9.3.2"), ("stable", "9.3.T2")):
        with pytest.raises(SystemExit) as exc:
            gen.resolve_link_id(kind, link, p)
        assert exc.value.code == gen.EXIT_POLICY


def test_lossless_json_refuses_hand_formatting(tmp_path):
    path = tmp_path / "hand.json"
    path.write_text('{"a": 1}', encoding="utf-8")
    with pytest.raises(SystemExit) as exc:
        gen.load_json_lossless(str(path))
    assert exc.value.code == gen.EXIT_FORMAT


def test_covered_version_exits_4():
    repo = pathlib.Path(__file__).resolve().parents[3]
    entries = json.loads((repo / "build_cann_arg.json").read_text(encoding="utf-8"))["cann"]
    covered = entries[0]["cann_version"]
    result = subprocess.run(
        [sys.executable, "tools/cann_release/generator.py", covered],
        cwd=repo, capture_output=True, text=True)
    assert result.returncode == gen.EXIT_COVERED


def test_previous_release_prefers_the_stable_over_its_beta():
    arg = {"cann": [
        {"cann_version": "9.2.0", "tags": []},
        {"cann_version": "9.2.0-beta.2", "tags": []},
    ]}
    previous, entries = gen.previous_release(arg)
    assert previous == "9.2.0"
    assert entries == [arg["cann"][0]]
