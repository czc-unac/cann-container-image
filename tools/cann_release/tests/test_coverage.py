import json

import registry

WORKFLOWS = (
    "build_and_push_cann.yml",
    "build_and_push_manylinux.yml",
    "batch_build_and_push_cann.yml",
    "batch_build_and_push_manylinux.yml",
)


def write_json(path, payload):
    path.write_text(json.dumps(payload), encoding="utf-8")


def make_repo(tmp_path):
    write_json(tmp_path / "build_cann_arg.json", {"cann": [{
        "cann_version": "9.3.0", "cann_chip": "910b", "os_name": "ubuntu",
        "os_version": "22.04", "py_version": "3.12",
        "tags": ["9.3.0-910b-ubuntu22.04-py3.12"],
    }]})
    write_json(tmp_path / "build_manylinux_arg.json", {"manylinux": [{
        "cann_version": "9.3.0", "cann_chip": "950", "os_name": "manylinux",
        "os_version": "2_34", "py_version": "3.12",
        "tags": ["9.3.0-950-manylinux_2_34-py3.12"],
    }]})
    write_json(tmp_path / "cann_publish_version.json", {"versions": [{
        "path": "cann/9.2.0-910b-openeuler24.03-py3.12",
        "tags": ["9.2.0-910b-openeuler24.03-py3.12"],
    }]})
    write_json(tmp_path / "manylinux_publish_version.json", {"versions": [{
        "path": "manylinux/9.2.0-a3-manylinux_2_34-py3.12",
        "tags": ["9.2.0-a3-manylinux_2_34-py3.12"],
    }]})
    workflows = tmp_path / ".github" / "workflows"
    workflows.mkdir(parents=True)
    for name in WORKFLOWS:
        (workflows / name).write_text(
            "        options:\n"
            "          - 9.1.1-910b-ubuntu22.04-py3.12\n",
            encoding="utf-8")
    (tmp_path / "supported_tags.md").write_text(
        "## Supported tags\n\n### CANN 9.2.0\n", encoding="utf-8")
    return tmp_path


def test_known_tokens_reads_every_registration_source(tmp_path, monkeypatch):
    monkeypatch.chdir(make_repo(tmp_path))
    tokens = registry.known_tokens()
    assert "9.3.0-910b-ubuntu22.04-py3.12" in tokens          # arg json
    assert "9.3.0-950-manylinux_2_34-py3.12" in tokens        # arg json
    assert "9.2.0-910b-openeuler24.03-py3.12" in tokens       # publish path
    assert "9.2.0-a3-manylinux_2_34-py3.12" in tokens         # publish path
    assert "9.1.1-910b-ubuntu22.04-py3.12" in tokens          # workflow option
    assert "9.2.0" in tokens                                  # supported_tags


def test_known_tokens_tolerates_missing_files(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert registry.known_tokens() == set()


def test_is_known_matches_the_exact_version_segment():
    tokens = {"9.2.0-beta.2-910b-ubuntu22.04-py3.12",
              "9.1.1-910b-ubuntu22.04-py3.12"}
    assert registry.is_known("9.2.0-beta.2", tokens)
    assert registry.is_known("9.1.1", tokens)
    # a beta tag must not cover the stable release that follows it
    assert not registry.is_known("9.2.0", tokens)
    assert not registry.is_known("9.2.0-beta.3", tokens)


def test_is_known_accepts_headings():
    tokens = {"9.2.0"}
    assert registry.is_known("9.2.0", tokens)
    assert not registry.is_known("9.2.1", tokens)
