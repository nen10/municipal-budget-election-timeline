"""全国地方公共団体コード(6 桁、検査数字つき)と自治体名の正規化。

栃木県(都道府県コード 09)の 25 市町を内蔵する。5 桁コードは総務省「全国地方公共団体コード」による。
検査数字は `check_digit()` で計算し、決算カードに記載された団体コードとテストで突合している
(tests/test_municipalities.py)。
"""

from __future__ import annotations

import re
import unicodedata

from .prefs import PREFS as PREF_NAMES  # noqa: E402  (互換のため残す)

# (5 桁コード, 名称)
_TOCHIGI_5 = [
    ("09201", "宇都宮市"), ("09202", "足利市"), ("09203", "栃木市"), ("09204", "佐野市"),
    ("09205", "鹿沼市"), ("09206", "日光市"), ("09208", "小山市"), ("09209", "真岡市"),
    ("09210", "大田原市"), ("09211", "矢板市"), ("09213", "那須塩原市"), ("09214", "さくら市"),
    ("09215", "那須烏山市"), ("09216", "下野市"), ("09301", "上三川町"), ("09342", "益子町"),
    ("09343", "茂木町"), ("09344", "市貝町"), ("09345", "芳賀町"), ("09361", "壬生町"),
    ("09364", "野木町"), ("09384", "塩谷町"), ("09386", "高根沢町"), ("09407", "那須町"),
    ("09411", "那珂川町"),
]


def check_digit(code5: str) -> str:
    """全国地方公共団体コードの検査数字(総務省方式)。"""
    if not re.fullmatch(r"\d{5}", code5):
        raise ValueError(code5)
    s = sum(int(d) * w for d, w in zip(code5, (6, 5, 4, 3, 2)))
    return str((11 - s % 11) % 10)


def code6(code5: str) -> str:
    return code5 + check_digit(code5)


TOCHIGI: dict[str, str] = {code6(c): n for c, n in _TOCHIGI_5}  # code6 -> name
NAME_TO_CODE: dict[str, str] = {n: c for c, n in TOCHIGI.items()}

# 都道府県ごとの市区町村マスタ(code -> name)。栃木県は内蔵、他県は DB の municipalities
# (総務省 住民基本台帳人口の市区町村別ファイルから投入)を load_masters() で読み込む。
_MASTERS: dict[str, dict[str, str]] = {"09": dict(TOCHIGI)}


class MasterNotAvailable(LookupError):
    """その都道府県の市区町村マスタがない(未対応)。"""


def set_master(pref_code: str, mapping: dict[str, str]) -> None:
    if mapping:
        _MASTERS[pref_code] = dict(mapping)


def load_masters(conn) -> list[str]:
    """DB の municipalities から全都道府県のマスタを読み込む。読み込んだ都道府県コードを返す。"""
    got: dict[str, dict[str, str]] = {}
    for r in conn.execute("SELECT code, name, pref_code FROM municipalities"):
        got.setdefault(r[2], {})[r[0]] = r[1]
    for p, m in got.items():
        if p != "09" or len(m) >= len(TOCHIGI):
            set_master(p, m)
    return sorted(got)


def master(pref_code: str) -> dict[str, str]:
    if pref_code not in _MASTERS:
        raise MasterNotAvailable(f"都道府県 {pref_code} の市区町村マスタがない(fetch --source soumu_jumin で取得)")
    return _MASTERS[pref_code]


def has_master(pref_code: str) -> bool:
    return pref_code in _MASTERS


def normalize_name(s: str) -> str:
    """空白(全角含む)と NFKC 差を除去した名称。PDF 抽出で「那 須 烏 山」となる場合に対応。"""
    s = unicodedata.normalize("NFKC", str(s))
    return re.sub(r"\s+", "", s)


def stem(name: str) -> str:
    """末尾の「市」「町」「村」を除いた語幹(特別交付税の都市分表は「市」を省略して載せる)。"""
    n = normalize_name(name)
    return n[:-1] if n and n[-1] in "市町村" else n


def lookup(name: str, pref_code: str = "09") -> str | None:
    """自治体名から 6 桁コードを返す。「宇都宮市第１」のような開票区名、「芳賀郡益子町」のような郡名つきも解決する。"""
    n = normalize_name(name)
    m = master(pref_code)
    rev = {normalize_name(v): k for k, v in m.items()}
    if n in rev:
        return rev[n]
    g = re.match(r"^.+?郡(.+[町村])$", n)
    if g and g.group(1) in rev:
        return rev[g.group(1)]
    # 開票区(例: 宇都宮市第1)。長い名称から先に照合する
    for nm in sorted(rev, key=len, reverse=True):
        if n.startswith(nm):
            return rev[nm]
    return None


def kind(name: str) -> str:
    return normalize_name(name)[-1]
