import pytest
import requests

import monitor


def page_payload(version="9.3.0-beta.1"):
    return {"data": {
        "versionName": f"CANN {version}",
        "publishTime": "2026/11/13",
        "packageList": [
            {"downloadUrl": "https://ascend-repo.obs.cn-east-2.myhuaweicloud.com"
                            "/CANN/CANN 9.3.T2/Ascend-cann-310p-ops_9.3.0-beta.1"
                            "_linux-aarch64.run"},
            {"downloadUrl": "https://ascend-repo.obs.cn-east-2.myhuaweicloud.com"
                            "/CANN/CANN%209.3.T2/Ascend-cann-nnal_9.3.0-beta.1"
                            "_linux-x86_64.run"},
            {"downloadUrl": "https://ascend-cann-open.obs.cn-north-4.myhuaweicloud.com"
                            "/CANN/CANN%209.3.0-beta.1/Ascend-cann_9.3.0-beta.1"
                            "_linux-aarch64.run"},
        ],
    }}


class FakeResponse:
    def __init__(self, payload, status=200):
        self.status_code = status
        self._payload = payload

    def json(self):
        return self._payload


class FakeSession:
    def __init__(self, response=None, error=None):
        self.response = response
        self.error = error

    def get(self, *args, **kwargs):
        if self.error:
            raise self.error
        return self.response


def test_download_page_latest_is_normalized_and_carries_the_obs_dir():
    page = monitor.fetch_download_page_latest(
        FakeSession(FakeResponse(page_payload())))
    assert page["version"] == "9.3.0-beta.1"
    assert page["publishTime"] == "2026/11/13"
    assert page["oss_dir"] == "9.3.T2"


def test_extract_obs_dir_accepts_encoded_and_plain_spaces():
    assert monitor.extract_obs_dir([
        {"downloadUrl": ".../CANN%209.3.T2/a.run"},
        {"downloadUrl": ".../CANN 9.3.T2/b.run"},
    ]) == "9.3.T2"


def test_extract_obs_dir_refuses_ambiguous_directories():
    assert monitor.extract_obs_dir([
        {"downloadUrl": ".../CANN%209.3.T1/a.run"},
        {"downloadUrl": ".../CANN%209.3.T2/b.run"},
    ]) == ""


def test_download_page_failure_is_not_fatal():
    page = monitor.fetch_download_page_latest(
        FakeSession(error=requests.ConnectionError("boom")))
    assert page is None


def test_merge_adds_new_versions_and_keeps_bulletin_times():
    bulletins = {"9.2.1": {"id": 1, "publishTime": "2026/10/28"}}
    hints = monitor.merge_download_page(bulletins, {
        "version": "9.3.0-beta.1", "publishTime": "2026/11/13",
        "oss_dir": "9.3.T2",
    })
    assert "9.3.0-beta.1" in bulletins
    assert hints == {"9.3.0-beta.1": "9.3.T2"}

    bulletins = {"9.3.0-beta.1": {"id": 2, "publishTime": "2026/11/13"}}
    monitor.merge_download_page(bulletins, {
        "version": "9.3.0-beta.1", "publishTime": "2026/11/14",
        "oss_dir": "9.3.T2",
    })
    assert bulletins["9.3.0-beta.1"]["publishTime"] == "2026/11/13"


def test_buildability_probes_the_hinted_directory(monkeypatch):
    captured = {}

    def fake_check(prefix, files, missing_statuses, settings=None):
        captured["prefix"] = prefix
        return [], []

    monkeypatch.setattr(monitor, "check_files", fake_check)
    policy = {
        "arches": ["aarch64", "x86_64"],
        "file_templates": [
            "Ascend-cann-toolkit_{version}_linux-{arch}.run",
            "Ascend-cann-{chip}-ops_{version}_linux-{arch}.run",
        ],
        "missing_statuses": [403, 404],
        "probe": {"timeout_seconds": 1, "workers": 2, "retries": 0,
                  "backoff_seconds": 0, "unknown_budget": 4},
    }
    result = monitor.buildability("9.3.0-beta.1", "beta", policy,
                                  "http://base", ["310p"], "9.3.T2")
    assert result == (True, "9.3.T2", "", [])
    assert captured["prefix"].endswith("CANN%209.3.T2")
