import json

import pytest

import availability as ca


def policy(**overrides):
    base = {
        "arches": ["aarch64", "x86_64"],
        "file_templates": [
            "Ascend-cann-toolkit_{version}_linux-{arch}.run",
            "Ascend-cann-nnal_{version}_linux-{arch}.run",
            "Ascend-cann-{chip}-ops_{version}_linux-{arch}.run",
        ],
        "beta_scan_max": 8,
        "missing_statuses": [403, 404],
        "probe": {
            "timeout_seconds": 1,
            "workers": 2,
            "retries": 2,
            "backoff_seconds": 0.0,
            "unknown_budget": 4,
        },
    }
    base.update(overrides)
    return base


class Resp:
    def __init__(self, code):
        self.status_code = code


def fake_head(sequence):
    calls = {"n": 0}

    def head(url, timeout=None, allow_redirects=None):
        calls["n"] += 1
        item = sequence[min(calls["n"] - 1, len(sequence) - 1)]
        if isinstance(item, Exception):
            raise item
        return Resp(item)

    return head, calls


def test_required_files_expands_chips_and_arches():
    files = ca.required_files(policy(), "9.3.0", ["310p", "A3"])
    # 2 common templates * 2 arches + 2 chips * 2 arches
    assert len(files) == 8
    assert "Ascend-cann-toolkit_9.3.0_linux-aarch64.run" in files
    assert "Ascend-cann-A3-ops_9.3.0_linux-x86_64.run" in files
    assert "Ascend-cann-310p-ops_9.3.0_linux-aarch64.run" in files


def test_probe_retries_unknown_answers(monkeypatch):
    head, calls = fake_head([ca.requests.Timeout("t"), 200])
    monkeypatch.setattr(ca.requests, "head", head)
    state, _ = ca._probe("http://x", {403, 404}, 1, 2, 0.0)
    assert state == ca.PRESENT
    assert calls["n"] == 2


def test_probe_never_retries_missing(monkeypatch):
    head, calls = fake_head([403])
    monkeypatch.setattr(ca.requests, "head", head)
    state, _ = ca._probe("http://x", {403, 404}, 1, 2, 0.0)
    assert state == ca.MISSING
    assert calls["n"] == 1


def test_probe_reports_unknown_after_every_attempt(monkeypatch):
    head, calls = fake_head([ca.requests.Timeout("t")])
    monkeypatch.setattr(ca.requests, "head", head)
    state, _ = ca._probe("http://x", {403, 404}, 1, 2, 0.0)
    assert state == ca.UNKNOWN
    assert calls["n"] == 3  # first attempt plus two retries


def test_check_files_separates_missing_from_unknown(monkeypatch):
    def head(url, timeout=None, allow_redirects=None):
        if url.endswith("/a"):
            return Resp(200)
        if url.endswith("/b"):
            return Resp(404)
        raise ca.requests.ConnectionError("boom")

    monkeypatch.setattr(ca.requests, "head", head)
    missing, unknown = ca.check_files("http://x", ["a", "b", "c"], {403, 404},
                                      policy()["probe"])
    assert missing == ["b"]
    assert [name for name, _ in unknown] == ["c"]


def test_scan_beta_batches_and_stops_at_the_first_complete_directory(monkeypatch):
    settings = policy()
    calls = {"toolkit": 0, "files": 0}

    def fake_probe(url, *args, **kwargs):
        if "/Ascend-cann-toolkit_" in url:
            calls["toolkit"] += 1
            present = "/CANN%209.3.T1/" in url
            return (ca.PRESENT if present else ca.MISSING), ""
        calls["files"] += 1
        return ca.PRESENT, ""

    monkeypatch.setattr(ca, "_probe", fake_probe)
    files = ca.required_files(settings, "9.3.0", ["310p", "A3"])
    buildable, found, incomplete = ca.scan_beta(
        "http://x", "9.3.0-beta.1", files, settings, settings["missing_statuses"])
    assert buildable == "9.3.T1"
    assert found == ["9.3.T1"]
    assert incomplete == {}
    # one batch of candidates plus the completeness check for the hit
    assert calls["toolkit"] + calls["files"] == (
        settings["probe"]["workers"] + len(files))


def test_scan_beta_aborts_when_the_gateway_is_unreachable(monkeypatch):
    settings = policy()
    calls = {"n": 0}

    def fake_probe(url, *args, **kwargs):
        calls["n"] += 1
        return ca.UNKNOWN, "timeout"

    monkeypatch.setattr(ca, "_probe", fake_probe)
    files = ca.required_files(settings, "9.3.0", ["310p", "A3"])
    with pytest.raises(ca.AvailabilityUnknown):
        ca.scan_beta("http://x", "9.3.0-beta.1", files, settings,
                     settings["missing_statuses"])
    assert calls["n"] <= settings["probe"]["unknown_budget"]


def test_version_sort_key_puts_betas_below_their_stable():
    versions = ["9.2.0", "9.2.0-beta.10", "9.1.9", "9.2.0-beta.2"]
    assert sorted(versions, key=ca.version_sort_key) == [
        "9.1.9", "9.2.0-beta.2", "9.2.0-beta.10", "9.2.0"]


def test_current_chips_uses_the_newest_stable_block(tmp_path):
    arg_cann = tmp_path / "build_cann_arg.json"
    arg_cann.write_text(json.dumps({"cann": [
        {"cann_version": "9.2.0", "cann_chip": "950"},
        {"cann_version": "9.2.0-beta.2", "cann_chip": "310p"},
    ]}), encoding="utf-8")
    assert ca.current_chips(str(arg_cann),
                            str(tmp_path / "missing.json")) == ["950"]
