"""データ取得モジュール(ソースごとに 1 モジュール)。

各モジュールは次の 4 段階を分離して実装する:
  1. list_sources()  … 取得対象 URL の一覧を作る(ネットワークアクセスあり)
  2. download()      … data/raw/<source>/ に保存(manifest.jsonl に URL・取得日時・SHA-256)
  3. parse()         … 保存済みファイルをパース(ネットワーク不要、findnews.parse.* を使用)
  4. load()          … DB へ投入
`run()` はこれらを順に呼ぶ。`offline=True` なら 1・2 を飛ばし、data/raw の既存ファイルだけを使う。
"""

SOURCES = ["soumu_card", "soumu_tokko", "mlit_grants", "tochigi_election", "kokkai"]
