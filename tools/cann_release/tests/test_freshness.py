import pytest

import monitor


def bulletins(**times):
    return {version: {"id": version, "publishTime": published}
            for version, published in times.items()}


def test_betas_announced_ahead_of_the_covered_stable_are_reported():
    """The publish-time floor alone would hide 9.2.0-beta.1/beta.2.

    Real history: 9.1.1 (bulletin 2026/09/01) was covered, while beta.1 was
    announced 2026/08/11 and beta.2 on 2026/09/01 itself.
    """
    data = bulletins(**{
        "9.1.1": "2026/09/01",
        "9.2.0-beta.1": "2026/08/11",
        "9.2.0-beta.2": "2026/09/01",
        "9.1.0-beta.2": "2026/07/09",
    })
    tokens = {"9.1.1-910b-ubuntu22.04-py3.12"}
    assert monitor.candidate_versions(data, tokens) == [
        "9.2.0-beta.1", "9.2.0-beta.2"]


def test_lower_numbered_release_published_later_is_reported():
    data = bulletins(**{"9.2.0-beta.1": "2026/08/11", "9.1.2": "2026/09/20"})
    tokens = {"9.2.0-beta.1-910b-ubuntu22.04-py3.12"}
    assert monitor.candidate_versions(data, tokens) == ["9.1.2"]


def test_deliberately_skipped_history_stays_quiet():
    data = bulletins(**{"9.1.0-beta.2": "2026/07/09", "9.1.1": "2026/09/01"})
    tokens = {"9.1.1-910b-ubuntu22.04-py3.12", "9.2.0-950-openeuler24.03-py3.12"}
    assert monitor.candidate_versions(data, tokens) == []


def test_incomplete_bulletin_payload_is_refused():
    """No covered version in the payload means freshness cannot be judged."""
    data = bulletins(**{"9.1.0-beta.2": "2026/07/09"})
    tokens = {"9.1.1-910b-ubuntu22.04-py3.12"}
    with pytest.raises(monitor.BulletinAPIError):
        monitor.candidate_versions(data, tokens)


def test_new_stable_is_not_swallowed_by_its_beta_tags():
    data = bulletins(**{"9.2.0": "2026/10/01", "9.2.0-beta.2": "2026/09/01"})
    tokens = {"9.2.0-beta.2-910b-ubuntu22.04-py3.12"}
    assert monitor.candidate_versions(data, tokens) == ["9.2.0"]


def test_without_any_covered_version_every_stable_and_beta_is_reported():
    data = bulletins(**{
        "9.1.0": "2026/07/01",
        "9.2.0-beta.1": "2026/08/11",
    })
    assert monitor.candidate_versions(data, set()) == ["9.1.0", "9.2.0-beta.1"]


def test_helpers_expose_floor_and_newest_covered():
    data = bulletins(**{"9.1.1": "2026/09/01", "9.2.0-beta.1": "2026/08/11"})
    tokens = {"9.1.1-910b-ubuntu22.04-py3.12"}
    assert monitor.publish_floor(data, tokens) == "2026/09/01"
    assert monitor.newest_covered(data, tokens) == "9.1.1"
    assert monitor.publish_floor(data, set()) is None
    assert monitor.newest_covered(data, set()) is None


def test_newest_covered_prefers_the_stable_over_its_beta():
    data = bulletins(**{"9.2.0-beta.2": "2026/09/01", "9.2.0": "2026/10/17"})
    tokens = {"9.2.0-beta.2-910b-ubuntu22.04-py3.12",
              "9.2.0-910b-ubuntu22.04-py3.12"}
    assert monitor.newest_covered(data, tokens) == "9.2.0"


def test_stable_published_on_the_same_day_as_the_newest_covered_version():
    """Only the version-order guard catches this; the floor needs a strict >."""
    data = bulletins(**{"9.2.0-beta.2": "2026/09/01", "9.2.0": "2026/09/01"})
    tokens = {"9.2.0-beta.2-910b-ubuntu22.04-py3.12"}
    assert monitor.candidate_versions(data, tokens) == ["9.2.0"]
