"""コマンドライン(DESIGN.md 14.3)。

  findnews db init
  findnews fetch    --pref <2桁> --years 2021-2026 [--source <id> ...] [--offline] [--force]
  findnews events   import data/manual/events.csv
  findnews events   generate --pref <2桁>
  findnews timeline --pref <2桁> [--muni <6桁>] [--indicator <id>]
  findnews matrix   --pref <2桁> --election <選挙ID>
  findnews verify   --pref <2桁>          (別名: findnews verify tochigi)
  findnews detect run --pref <2桁> [--election <選挙ID>]
  findnews sources list [--pref <2桁>]
  findnews manual load / status
"""

from __future__ import annotations

import json
from pathlib import Path

import click

from . import config, db, manual
from .municipalities import load_masters


def _conn(path):
    conn = db.connect(path)
    db.init_db(conn)
    load_masters(conn)
    return conn


def _years(spec: str | None) -> list[int]:
    if not spec:
        return []
    out = []
    for part in spec.split(","):
        if "-" in part:
            a, b = part.split("-")
            out += list(range(int(a), int(b) + 1))
        else:
            out.append(int(part))
    return out


def _echo(obj):
    click.echo(json.dumps(obj, ensure_ascii=False, default=str))


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


@main.command("fetch")
@click.option("--pref", default="09", show_default=True, help="都道府県コード 2 桁")
@click.option("--years", default="2020-2026", show_default=True, help="対象年度(例: 2021-2026、2024,2025)")
@click.option("--source", "sources", multiple=True,
              help="ソース ID(複数可。soumu_jumin, soumu_card, soumu_tokko, mlit_grants, mlit_road, election, kokkai)。省略時は全部")
@click.option("--offline", is_flag=True, help="ネットワークに出ず data/raw の既存ファイルだけをパース")
@click.option("--force", is_flag=True, help="保存済みでも再ダウンロード")
@click.pass_context
def fetch_cmd(ctx, pref, years, sources, offline, force):
    """データ取得(一覧 → data/raw 保存 → パース → DB と observations に投入)。未対応のソースは「未対応」と出力する。"""
    from . import pipeline
    _echo(pipeline.run(_conn(ctx.obj["db"]), pref, _years(years), list(sources) or None, offline, force))


@main.group("events")
def events_group():
    """政局イベント(DESIGN.md 13.3)。"""


@events_group.command("import")
@click.argument("path", type=click.Path(exists=True), default=str(config.MANUAL_DIR / "events.csv"))
@click.pass_context
def events_import(ctx, path):
    """手作業のイベント CSV を取り込む(出典 URL のない行は登録しない)。"""
    from . import events
    _echo(events.import_csv(_conn(ctx.obj["db"]), path))


@events_group.command("generate")
@click.option("--pref", default="09", show_default=True)
@click.pass_context
def events_generate(ctx, pref):
    """選挙投票日・役職就任・配分公表日・交付決定日のイベントを自動登録する。"""
    from . import events
    _echo(events.generate(_conn(ctx.obj["db"]), pref))


@main.command("timeline")
@click.option("--pref", default="09", show_default=True)
@click.option("--muni", "munis", multiple=True, help="自治体コード 6 桁(複数可。省略時は全市区町村)")
@click.option("--indicator", "indicators", multiple=True, help="指標 ID(複数可。`findnews sources list` 参照)")
@click.option("--out", "out_dir", type=click.Path(), default=None, help="出力ディレクトリ(既定: data/processed)")
@click.pass_context
def timeline_cmd(ctx, pref, munis, indicators, out_dir):
    """差分時系列と政局イベントの対応表(timeline_<pref>.csv、timeline_<pref>/<自治体>.md、events_<pref>.md)。"""
    from . import timeline
    from .municipalities import MasterNotAvailable
    try:
        _echo(timeline.run(_conn(ctx.obj["db"]), pref, list(munis) or None, list(indicators) or None,
                           Path(out_dir) if out_dir else None))
    except MasterNotAvailable as e:
        _echo({"status": "未対応", "reason": str(e)})


@main.group("verify", invoke_without_command=True)
@click.option("--pref", default="09", show_default=True)
@click.option("--out", "out_path", type=click.Path(), default=None, help="出力先(既定: data/processed/verification_<slug>.md)")
@click.pass_context
def verify_group(ctx, pref, out_path):
    """検証(DESIGN.md 第10節): 都道府県内の全市区町村の時系列記録。"""
    if ctx.invoked_subcommand is None:
        _verify(ctx, pref, out_path)


def _verify(ctx, pref, out_path):
    from . import verify
    from .prefs import slug
    out = Path(out_path) if out_path else config.PROCESSED_DIR / f"verification_{slug(pref)}.md"
    res = verify.run(_conn(ctx.obj["db"]), out, pref)
    _echo({"out": str(out), "municipalities": len(res.codes), "item_rows": len(res.item_rows), "threshold": res.threshold})


@verify_group.command("tochigi")
@click.option("--out", "out_path", type=click.Path(), default=None)
@click.pass_context
def verify_tochigi(ctx, out_path):
    """`verify --pref 09` の別名。"""
    _verify(ctx, "09", out_path)


@main.command("matrix")
@click.option("--pref", default="09", show_default=True)
@click.option("--election", required=True, help="選挙 ID(例: shugiin_20260208, shugiin_20241027, shugiin_20211031)")
@click.option("--out", "out_dir", type=click.Path(), default=None, help="出力ディレクトリ(既定: data/processed)")
@click.pass_context
def matrix_cmd(ctx, pref, election, out_dir):
    """国政選挙との分離マトリックス(DESIGN.md 第11節)。"""
    from . import matrix
    from .sources import election_source
    conn = _conn(ctx.obj["db"])
    if not conn.execute("SELECT 1 FROM elections WHERE pref_code=? AND election_id LIKE ?", (pref, election + "_%")).fetchone():
        _echo({"status": "未対応", "reason": f"都道府県 {pref} の選挙 {election} の得票データがない"
               + ("" if election_source(pref) else "(この都道府県の選管アダプタが未実装)")})
        return
    _echo(matrix.run(conn, election, pref, out_dir))


@main.group("detect")
def detect_group():
    """検出。"""


@detect_group.command("run")
@click.option("--pref", default="09", show_default=True, help="都道府県コード 2 桁")
@click.option("--election", default=None, help="選挙 ID(例: shugiin_20260208。既定は最新)")
@click.option("--out", "out_dir", type=click.Path(), default=None, help="出力先(既定: data/processed/reports/<run_id>)")
@click.pass_context
def detect_cmd(ctx, pref, election, out_dir):
    """パネル構築・自治体自身の差分・発言一致・寄与内訳つきスコアを CSV と Markdown に出力。"""
    from .detect import run as detect_run
    _echo(detect_run.run(_conn(ctx.obj["db"]), pref, Path(out_dir) if out_dir else None, election))


@main.group("sources")
def sources_group():
    """データソース(アダプタ)のレジストリ。"""


@sources_group.command("list")
@click.option("--pref", default="09", show_default=True, help="対応状況を表示する都道府県")
@click.pass_context
def sources_list(ctx, pref):
    """登録済みのアダプタと、指定した都道府県での対応状況。"""
    from .sources import INDICATORS, status_for_pref
    _conn(ctx.obj["db"])
    for r in status_for_pref(pref):
        click.echo(f"{r['source_id']:18s} {r['kind']:10s} {r['coverage']:10s} {r['status']}  {r['label']}"
                   + (f"  [{r['indicators']}]" if r["indicators"] else "") + (f"  注: {r['note']}" if r["note"] else ""))
    click.echo("\n指標 ID:")
    for k, (no, label, ministry) in INDICATORS.items():
        click.echo(f"  {k:18s} 指標 {no:3s} {label}" + (f"(所管 {ministry})" if ministry else ""))


@main.group("manual")
def manual_group():
    """手作業データ(data/manual)。"""


@manual_group.command("load")
@click.option("--dir", "manual_dir", type=click.Path(exists=True), default=None)
@click.pass_context
def manual_load(ctx, manual_dir):
    _echo(manual.load_all(_conn(ctx.obj["db"]), Path(manual_dir) if manual_dir else None))


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
        click.echo(f"{r['source']:18s} {r['step']:12s} {r['status']:6s} {r['detail'][:150]}")


if __name__ == "__main__":
    main()
