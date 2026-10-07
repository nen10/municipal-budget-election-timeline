import re

from findnews import db
from findnews.municipalities import TOCHIGI, check_digit, lookup


def test_init_creates_all_tables(conn):
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    for t in db.TABLES:
        assert t in tables


def test_every_table_has_provenance_columns(conn):
    for t in db.TABLES:
        cols = {r[1] for r in conn.execute(f"PRAGMA table_info({t})")}
        assert {"source_url", "retrieved_at"} <= cols, t


def test_init_is_idempotent(conn):
    db.init_db(conn)
    db.ensure_municipalities(conn)
    assert conn.execute("SELECT COUNT(*) FROM municipalities").fetchone()[0] == 25


def test_municipality_codes_are_6digit_with_check_digit(conn):
    for (code,) in conn.execute("SELECT code FROM municipalities"):
        assert re.fullmatch(r"09\d{4}", code)
        assert check_digit(code[:5]) == code[5]


def test_known_codes():
    assert TOCHIGI["092151"] == "那須烏山市"
    assert TOCHIGI["094111"] == "那珂川町"
    assert check_digit("09201") == "1"  # 宇都宮市 092011
    assert lookup("宇都宮市第１") == "092011"
    assert lookup("那 須 烏 山 市") == "092151"
    assert lookup("那須町") == "094072"
