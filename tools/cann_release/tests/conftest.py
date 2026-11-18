import sys
from pathlib import Path

import pytest

PACKAGE_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = PACKAGE_DIR.parents[1]

if str(PACKAGE_DIR) not in sys.path:
    sys.path.insert(0, str(PACKAGE_DIR))


@pytest.fixture()
def repo_root():
    """Path of the checked-out repository (for contract tests)."""
    return REPO_ROOT


@pytest.fixture()
def in_repo(monkeypatch):
    """Run a test with the repository root as the working directory."""
    monkeypatch.chdir(REPO_ROOT)
    return REPO_ROOT
