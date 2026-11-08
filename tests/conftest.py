import pytest
from finn.db import init


@pytest.fixture(autouse=True)
def database(tmp_path, monkeypatch):
    monkeypatch.setenv('FINN_DB', str(tmp_path / 'test.db'))
    monkeypatch.delenv('LLM_API_KEY', raising=False)
    init()
