"""都道府県コード(2 桁)と名称。"""

from __future__ import annotations

_NAMES = ["北海道", "青森県", "岩手県", "宮城県", "秋田県", "山形県", "福島県", "茨城県", "栃木県", "群馬県",
          "埼玉県", "千葉県", "東京都", "神奈川県", "新潟県", "富山県", "石川県", "福井県", "山梨県", "長野県",
          "岐阜県", "静岡県", "愛知県", "三重県", "滋賀県", "京都府", "大阪府", "兵庫県", "奈良県", "和歌山県",
          "鳥取県", "島根県", "岡山県", "広島県", "山口県", "徳島県", "香川県", "愛媛県", "高知県", "福岡県",
          "佐賀県", "長崎県", "熊本県", "大分県", "宮崎県", "鹿児島県", "沖縄県"]

PREFS: dict[str, str] = {f"{i + 1:02d}": n for i, n in enumerate(_NAMES)}
# 出力ファイル名に使う英字名(既存の出力名との互換のため。未登録の県は都道府県コードを使う)
SLUGS = {"09": "tochigi"}


def name(code: str) -> str:
    if code not in PREFS:
        raise ValueError(f"都道府県コードが不正: {code}")
    return PREFS[code]


def short(code: str) -> str:
    """「栃木県」→「栃木」、「北海道」はそのまま。国交省の都道府県リンクや特別交付税表の縦書きラベルに使う。"""
    n = name(code)
    return n if n == "北海道" else n[:-1]


def slug(code: str) -> str:
    return SLUGS.get(code, code)
