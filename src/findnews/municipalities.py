"""全国地方公共団体コード(6 桁、検査数字つき)と自治体名の正規化。

栃木県(都道府県コード 09)の 25 市町を内蔵する。5 桁コードは総務省「全国地方公共団体コード」による。
検査数字は `check_digit()` で計算し、決算カードに記載された団体コードとテストで突合している
(tests/test_municipalities.py)。
"""

from __future__ import annotations

import re
import unicodedata

PREF_NAMES = {"09": "栃木県"}

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


def normalize_name(s: str) -> str:
    """空白(全角含む)と NFKC 差を除去した名称。PDF 抽出で「那 須 烏 山」となる場合に対応。"""
    s = unicodedata.normalize("NFKC", str(s))
    return re.sub(r"\s+", "", s)


def stem(name: str) -> str:
    """末尾の「市」「町」「村」を除いた語幹(特別交付税の都市分表は「市」を省略して載せる)。"""
    n = normalize_name(name)
    return n[:-1] if n and n[-1] in "市町村" else n


def lookup(name: str, pref_code: str = "09") -> str | None:
    """自治体名から 6 桁コードを返す。「宇都宮市第１」のような開票区名も先頭一致で解決する。"""
    n = normalize_name(name)
    if n in NAME_TO_CODE:
        return NAME_TO_CODE[n]
    # 開票区(例: 宇都宮市第1)
    for nm, c in NAME_TO_CODE.items():
        if n.startswith(nm) and c.startswith(pref_code):
            return c
    return None


def kind(name: str) -> str:
    return normalize_name(name)[-1]
