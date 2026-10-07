from pathlib import Path

import pytest

from findnews import db

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def conn(tmp_path):
    c = db.connect(tmp_path / "test.sqlite")
    db.init_db(c)
    db.ensure_municipalities(c)
    yield c
    c.close()


@pytest.fixture
def fixtures():
    return FIXTURES
