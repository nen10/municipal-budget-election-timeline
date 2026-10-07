"""コマンドライン: findnews db init / fetch <source> / manual load / detect run / status。"""

from __future__ import annotations

import json
from pathlib import Path

import click

from . import config, db, manual
from .detect import run as detect_run


def _conn(path):
    conn = db.connect(path)
    db.init_db(conn)
    return conn


@click.group()
@click.option("--db", "db_path", type=click.Path(), default=None, help=f"SQLite のパス(既定: {config.DEFAULT_DB})")
@click.pass_context
def main(ctx, db_path):
    """補助金圧力検出システム(要検証シグナルの抽出)。"""
    ctx.obj = {"db": db_path}


@main.group("db")
def db_group():
    """DB の操作。"""


@db_group.command("init")
@click.pass_context
def db_init(ctx):
    """スキーマを作成し、栃木県の市町マスタを投入する。"""
    conn = _conn(ctx.obj["db"])
    db.ensure_municipalities(conn)
    tables = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")]
    click.echo(f"initialized: {ctx.obj['db'] or config.DEFAULT_DB}")
    click.echo("tables: " + ", ".join(sorted(tables)))


@main.group("fetch")
def fetch_group():
    """データ取得(一覧 → data/raw 保存 → パース → DB 投入)。--offline で保存済みファイルのみ使う。"""


def _offline_opts(f):
    f = click.option("--offline", is_flag=True, help="ネットワークに出ず data/raw の既存ファイルだけをパース")(f)
    f = click.option("--force", is_flag=True, help="保存済みでも再ダウンロード")(f)
    return f


@fetch_group.command("soumu-card")
@click.option("--years", default=5, show_default=True, help="直近何年度分")
@_offline_opts
@click.pass_context
def f_card(ctx, years, offline, force):
    """総務省 市町村決算カード(栃木県)。"""
    from .fetch import soumu_card
    click.echo(json.dumps(soumu_card.run(_conn(ctx.obj["db"]), years, offline, force), ensure_ascii=False))


@fetch_group.command("soumu-tokko")
@click.option("--from-year", default=2020, show_default=True)
@click.option("--to-year", default=2025, show_default=True)
@_offline_opts
@click.pass_context
def f_tokko(ctx, from_year, to_year, offline, force):
    """総務省 特別交付税 報道発表(12 月分・3 月分)。"""
    from .fetch import soumu_tokko
    click.echo(json.dumps(soumu_tokko.run(_conn(ctx.obj["db"]), list(range(from_year, to_year + 1)), offline, force),
                          ensure_ascii=False, default=str))


@fetch_group.command("mlit-grants")
@_offline_opts
@click.pass_context
def f_mlit(ctx, offline, force):
    """国交省 社会資本整備総合交付金・防災・安全交付金・道路メンテナンス事業(当初配分、栃木県)。"""
    from .fetch import mlit_grants
    click.echo(json.dumps(mlit_grants.run(_conn(ctx.obj["db"]), None, offline, force), ensure_ascii=False))


@fetch_group.command("mlit-road")
@_offline_opts
@click.pass_context
def f_mlit_road(ctx, offline, force):
    """国交省 道路局 当初配分箇所表(栃木県、事業主体別)。PDF は mlit-grants と共通。"""
    from .fetch import mlit_road
    click.echo(json.dumps(mlit_road.run(_conn(ctx.obj["db"]), offline, force), ensure_ascii=False))


@fetch_group.command("soumu-jumin")
@_offline_opts
@click.pass_context
def f_jumin(ctx, offline, force):
    """総務省 住民基本台帳人口(市区町村別、最新年)。検証の参考人口列に使う。"""
    from .fetch import soumu_jumin
    click.echo(json.dumps(soumu_jumin.run(_conn(ctx.obj["db"]), offline, force), ensure_ascii=False))


@fetch_group.command("tochigi-election")
@_offline_opts
@click.pass_context
def f_election(ctx, offline, force):
    """栃木県選管 衆院選(2026-02, 2024-10, 2021-10)の開票区別得票と候補者届出状況公表票。"""
    from .fetch import tochigi_election
    conn = _conn(ctx.obj["db"])
    res = tochigi_election.run(conn, offline, force)
    manual.load_all(conn)  # 比例復活・議員 ID の反映
    click.echo(json.dumps(res, ensure_ascii=False))


@fetch_group.command("kokkai")
@click.option("--since", default="2015-01-01", show_default=True, help="キーワード全体検索の開始日")
@click.option("--max-records", default=300, show_default=True, help="1 クエリあたりの取得上限")
@click.option("--offline", is_flag=True)
@click.pass_context
def f_kokkai(ctx, since, max_records, offline):
    """国会会議録検索システム API(設計書 5.3 のキーワード)。"""
    from .fetch import kokkai
    click.echo(json.dumps(kokkai.run(_conn(ctx.obj["db"]), since, offline, None, max_records), ensure_ascii=False))


@fetch_group.command("all")
@_offline_opts
@click.pass_context
def f_all(ctx, offline, force):
    """全ソースを順に実行(手作業データを先に読み込む)。"""
    from .fetch import kokkai, mlit_grants, mlit_road, soumu_card, soumu_jumin, soumu_tokko, tochigi_election
    conn = _conn(ctx.obj["db"])
    click.echo(json.dumps({"manual": manual.load_all(conn)}, ensure_ascii=False))
    for name, fn in [("soumu_card", lambda: soumu_card.run(conn, 5, offline, force)),
                     ("soumu_tokko", lambda: soumu_tokko.run(conn, None, offline, force)),
                     ("mlit_grants", lambda: mlit_grants.run(conn, None, offline, force)),
                     ("mlit_road", lambda: mlit_road.run(conn, True, force)),
                     ("soumu_jumin", lambda: soumu_jumin.run(conn, offline, force)),
                     ("tochigi_election", lambda: tochigi_election.run(conn, offline, force)),
                     ("kokkai", lambda: kokkai.run(conn, offline=offline))]:
        try:
            click.echo(json.dumps({name: fn()}, ensure_ascii=False, default=str))
        except Exception as e:  # noqa: BLE001
            click.echo(json.dumps({name: f"ERROR {e!r}"}, ensure_ascii=False))
    manual.load_all(conn)  # 議員 ID の再リンク


@main.group("manual")
def manual_group():
    """手作業データ(data/manual)。"""


@manual_group.command("load")
@click.option("--dir", "manual_dir", type=click.Path(exists=True), default=None)
@click.pass_context
def manual_load(ctx, manual_dir):
    click.echo(json.dumps(manual.load_all(_conn(ctx.obj["db"]), Path(manual_dir) if manual_dir else None),
                          ensure_ascii=False))


@main.group("detect")
def detect_group():
    """検出。"""


@detect_group.command("run")
@click.option("--pref", default="09", show_default=True, help="都道府県コード 2 桁")
@click.option("--election", default=None, help="選挙 ID(例: shugiin_20260208。既定は最新)")
@click.option("--out", "out_dir", type=click.Path(), default=None, help="出力先(既定: data/processed/reports/<run_id>)")
@click.pass_context
def detect_cmd(ctx, pref, election, out_dir):
    """パネル構築・自治体自身の時系列における減少・発言一致・寄与内訳つきスコアを CSV と Markdown に出力。"""
    conn = _conn(ctx.obj["db"])
    res = detect_run.run(conn, pref, Path(out_dir) if out_dir else None, election)
    click.echo(json.dumps(res, ensure_ascii=False))


@main.group("verify")
def verify_group():
    """検証(DESIGN.md 第10節)。"""


@verify_group.command("tochigi")
@click.option("--out", "out_path", type=click.Path(), default=None,
              help="出力先(既定: data/processed/verification_tochigi.md)")
@click.pass_context
def verify_tochigi(ctx, out_path):
    """栃木県全 25 市町の補助金・交付金の時系列記録(各自治体を自分の過去とだけ比べる)。"""
    from . import verify
    out = Path(out_path) if out_path else config.PROCESSED_DIR / "verification_tochigi.md"
    res = verify.run(_conn(ctx.obj["db"]), out, "09")
    click.echo(json.dumps({"out": str(out), "municipalities": len(res.codes), "item_rows": len(res.item_rows),
                           "threshold": res.threshold}, ensure_ascii=False))


@main.command("matrix")
@click.option("--pref", default="09", show_default=True)
@click.option("--election", required=True, help="選挙 ID(例: shugiin_20260208, shugiin_20241027, shugiin_20211031)")
@click.option("--out", "out_dir", type=click.Path(), default=None, help="出力ディレクトリ(既定: data/processed)")
@click.pass_context
def matrix_cmd(ctx, pref, election, out_dir):
    """国政選挙との分離マトリックス(DESIGN.md 第11節)。"""
    from . import matrix
    if pref != "09":
        raise click.UsageError("第1フェーズは栃木県(09)のみ対応")
    click.echo(json.dumps(matrix.run(_conn(ctx.obj["db"]), election, pref, out_dir), ensure_ascii=False, default=str))


@main.command("status")
@click.pass_context
def status(ctx):
    """テーブル件数と取得ログ(最新の成否)。"""
    conn = _conn(ctx.obj["db"])
    for t in db.TABLES:
        click.echo(f"{t:22s} {conn.execute(f'SELECT COUNT(*) FROM {t}').fetchone()[0]}")
    click.echo("\n-- fetch_log (source, step ごとの最新) --")
    for r in conn.execute("""SELECT source, step, status, detail FROM fetch_log WHERE id IN
                             (SELECT MAX(id) FROM fetch_log GROUP BY source, step) ORDER BY source, step"""):
        click.echo(f"{r['source']:18s} {r['step']:9s} {r['status']:6s} {r['detail'][:150]}")


if __name__ == "__main__":
    main()
