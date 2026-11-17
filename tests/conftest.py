import pytest

from jobscanner import sites


@pytest.fixture(autouse=True)
def no_workday_pause(monkeypatch):
    monkeypatch.setattr(sites, "WORKDAY_PAUSE", 0)
