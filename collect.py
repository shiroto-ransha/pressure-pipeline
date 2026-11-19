#!/usr/bin/env python3
"""圧力ごとの報道量（n）と論調（tone）を、GDELT から日別に集計する。

経路（環境変数 GDELT_BACKEND か --backend で選ぶ）:
  gkg-files（既定） GKG 2.x の15分ごとの生ファイル。キー不要・無料。英語系と翻訳系（日本語の記事はこちら）の両方を読む。
  bigquery         BigQuery の gdelt-bq.gdeltv2.gkg_partitioned（サンドボックスの無料枠）。google-cloud-bigquery が必要。
  doc              DOC 2.0 API（最後の手段。429 が出やすいので12秒間隔）。候補記事は作らない。

出力:
  data/history/YYYY-MM-DD.json  その日の集計（trend の材料）
  data/gdelt.json               サイト用（直近 trend_days 日の推移つき）
  data/articles.json            ニュースの候補記事（news_frames.py が読む。見出し・URL・媒体・論調・テーマだけ）

使い方: python collect.py [--date YYYY-MM-DD] [--sample N] [--backend gkg-files] [--workers 2]
  --date は JST の日付。既定は「昨日」。
データを表示するときは The GDELT Project の名前とリンクを必ず示すこと（利用条件）。
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import pathlib
import sys
import re
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Iterable, Iterator

import gkg

ROOT = pathlib.Path(__file__).resolve().parent
DATA = ROOT / "data"
SOURCE = "The GDELT Project (https://www.gdeltproject.org/)"
CANDIDATES_PER_KIND = 40


def load_json(path: pathlib.Path, default=None):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def write_json(path: pathlib.Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(path)


# ---------- 記事の取り出し（GKG ファイル） ----------

def article_from_row(row: list[str], group: str) -> dict:
    c = gkg.COL
    return {"group": group, "host": gkg.article_host(row), "url": row[c["DocumentIdentifier"]],
            "title": gkg.page_title(row[c["Extras"]]), "tone": gkg.parse_tone(row[c["V2Tone"]]),
            "themes": gkg.parse_themes(row[c["V2Themes"]]), "lang": gkg.source_lang(row[c["TranslationInfo"]]),
            "date": row[c["DATE"]]}


def articles_from_zip(zbytes: bytes, media: dict) -> Iterator[dict]:
    """1本の GKG ファイルから、jp / ref の記事だけを取り出す。

    まず URL だけを安く切り出して媒体を判定し、対象の行だけを全列に分ける。
    """
    fips = media.get("ref_country_fips", "JA")
    for raw in gkg.iter_raw(zbytes):
        head = raw.split("\t", 5)
        if len(head) < 6:
            continue
        url = head[4]
        group = gkg.classify_media(url if url.startswith("http") else head[3], media)
        if not group:
            continue
        row = raw.split("\t")
        if len(row) < gkg.NCOL:
            continue
        if group == "ref" and not gkg.has_country(row[gkg.COL["V2Locations"]], fips):
            continue
        yield article_from_row(row, group)


def gkg_files_articles(day: dt.date, media: dict, sample: int, workers: int, stats: dict) -> Iterator[dict]:
    urls = gkg.file_urls(day, sample=sample)
    stats.update(files_expected=len(urls), files_ok=0)
    t0 = time.time()
    with ThreadPoolExecutor(max(1, min(2, workers))) as ex:   # 並列は2本まで
        for i, z in enumerate(ex.map(gkg.fetch, urls), 1):
            if z:
                stats["files_ok"] += 1
                yield from articles_from_zip(z, media)
            if i % 12 == 0 or i == len(urls):
                print(f"  {i}/{len(urls)} 本（取得 {stats['files_ok']}、{time.time() - t0:.0f}秒）", flush=True)


# ---------- BigQuery（任意） ----------

def bigquery_articles(day: dt.date, media: dict, stats: dict) -> Iterator[dict]:
    """gdelt-bq.gdeltv2.gkg_partitioned から、JST の1日分を必要な列だけ読む。

    パーティション（UTC の日）で2日分に絞り、DATE で JST の24時間に切る。
    スキャン量は選んだ列 × 2パーティション分（目安で数GB/日。実行前に dry run で確認して表示する）。
    認証は Application Default Credentials（Workload Identity、または GOOGLE_APPLICATION_CREDENTIALS）。
    """
    from google.cloud import bigquery  # 任意の依存。requirements-bigquery.txt

    slots = gkg.jst_day_slots(day)
    start, end = int(slots[0]), int((dt.datetime.strptime(slots[-1], "%Y%m%d%H%M%S") + dt.timedelta(minutes=15)).strftime("%Y%m%d%H%M%S"))
    d0 = dt.datetime.strptime(slots[0], "%Y%m%d%H%M%S").date()
    sql = """
SELECT DATE, SourceCommonName, DocumentIdentifier, V2Themes, V2Tone, V2Locations, TranslationInfo, Extras
FROM `gdelt-bq.gdeltv2.gkg_partitioned`
WHERE _PARTITIONTIME >= TIMESTAMP(@d0) AND _PARTITIONTIME < TIMESTAMP(DATE_ADD(@d0, INTERVAL 2 DAY))
  AND DATE >= @start AND DATE < @end
  AND (REGEXP_CONTAINS(DocumentIdentifier, r'^https?://[^/]+\\.jp(/|$)') OR REGEXP_CONTAINS(DocumentIdentifier, @domains))
"""
    doms = media.get("jp_domains", []) + media.get("ref_domains", [])
    pattern = r"^https?://([^/]*\.)?(" + "|".join(d.replace(".", r"\.") for d in doms) + r")(/|$)"
    params = [bigquery.ScalarQueryParameter("d0", "DATE", d0.isoformat()),
              bigquery.ScalarQueryParameter("start", "INT64", start),
              bigquery.ScalarQueryParameter("end", "INT64", end),
              bigquery.ScalarQueryParameter("domains", "STRING", pattern)]
    client = bigquery.Client(project=os.environ.get("BQ_PROJECT") or None)
    dry = client.query(sql, job_config=bigquery.QueryJobConfig(query_parameters=params, dry_run=True, use_query_cache=False))
    gb = dry.total_bytes_processed / 1e9
    print(f"  BigQuery のスキャン見込み: {gb:.2f} GB", flush=True)
    limit = float(os.environ.get("BQ_MAX_GB", "20"))
    if gb > limit:
        raise RuntimeError(f"スキャン見込みが BQ_MAX_GB={limit} を超えるので中止しました")
    job = client.query(sql, job_config=bigquery.QueryJobConfig(query_parameters=params))
    stats.update(files_expected=1, files_ok=0, scanned_gb=round(gb, 2))
    fips = media.get("ref_country_fips", "JA")
    for r in job.result():
        stats["files_ok"] = 1
        row = [""] * gkg.NCOL
        for name, idx in gkg.COL.items():
            row[idx] = str(r.get(name) or "")
        group = gkg.classify_media(row[gkg.COL["DocumentIdentifier"]] or row[gkg.COL["SourceCommonName"]], media)
        if not group or (group == "ref" and not gkg.has_country(row[gkg.COL["V2Locations"]], fips)):
            continue
        yield article_from_row(row, group)


# ---------- 集計 ----------

def aggregate(articles: Iterable[dict], cfg: dict) -> tuple[dict, list[dict]]:
    """記事の流れから、圧力ごと・媒体群ごとの n と tone を作る。jp の記事は候補用に返す。"""
    groups = {"items": cfg["pressures"], "candidates": cfg.get("candidates", {})}
    acc = {g: {k: {"jp": [0, 0.0, 0], "ref": [0, 0.0, 0]} for k in rules} for g, rules in groups.items()}
    seen, jp_articles = set(), []
    totals = {"jp": 0, "ref": 0}
    for a in articles:
        key = a["url"] or (a["host"], a["title"])
        if key in seen:
            continue
        seen.add(key)
        totals[a["group"]] += 1
        for g, rules in groups.items():
            for k in gkg.match_pressures(rules, a["themes"], a["title"], a["url"]):
                n_tone = acc[g][k][a["group"]]
                n_tone[0] += 1
                if a["tone"] is not None:
                    n_tone[1] += a["tone"]
                    n_tone[2] += 1
        if a["group"] == "jp":
            jp_articles.append(a)
    out = {}
    for g, keys in acc.items():
        out[g] = {k: {grp: {"n": v[0], "tone": round(v[1] / v[2], 2) if v[2] else None}
                      for grp, v in by.items()} for k, by in keys.items()}
    out["totals"] = totals
    return out, jp_articles


def title_key(title: str) -> str:
    """配信記事（共同通信・ORICON など）が地方紙に転載されたときの重複を見分けるためのキー。"""
    t = re.split(r"[｜|]| - ", title, 1)[0]
    return re.sub(r"[\s　「」『』（）()]", "", t)[:28]


def pick_candidates(jp_articles: list[dict], cfg: dict, per_kind: int = CANDIDATES_PER_KIND) -> dict:
    """ニュースの候補記事を、カテゴリごとに上位 per_kind 件選ぶ（見出しのない記事・除外語に当たる記事は外す）。"""
    excl = cfg.get("news_exclude", {})
    cats = cfg.get("news_categories", {})
    keep_themes = set()
    for rule in list(cats.values()) + list(cfg["pressures"].values()) + list(cfg.get("candidates", {}).values()):
        keep_themes.update(rule.get("gkg_themes", []))
    out = {}
    for kind, rule in cats.items():
        scored, titles = [], set()
        for a in jp_articles:
            if not a["title"] or title_key(a["title"]) in titles:
                continue
            if gkg.match_rule(excl, a["themes"], a["title"], a["url"]):
                continue
            th = sum(t in a["themes"] for t in rule.get("gkg_themes", []))
            kw = sum(k.lower() in a["title"].lower() for k in rule.get("keywords", []))
            if th + kw == 0:
                continue
            titles.add(title_key(a["title"]))
            scored.append((kw * 2 + th, abs(a["tone"] or 0), a))
        scored.sort(key=lambda x: (-x[0], -x[1]))
        out[kind] = [{"title": a["title"], "url": a["url"], "domain": a["host"], "tone": a["tone"], "date": a["date"],
                      "lang": a["lang"], "themes": sorted(t for t in a["themes"] if t in keep_themes)}
                     for _, _, a in scored[:per_kind]]
    return out


def build_site_json(day: dt.date, cfg: dict, backend: str, history_dir: pathlib.Path) -> dict:
    """data/history/ の直近 trend_days 日から、サイト用の gdelt.json を組み立てる。"""
    ndays = int(cfg.get("trend_days", 7))
    days = [(day - dt.timedelta(days=i)).isoformat() for i in range(ndays - 1, -1, -1)]
    hist = {d: load_json(history_dir / f"{d}.json") for d in days}
    today = hist[day.isoformat()] or {}
    out = {"asof": day.isoformat(), "window": "1d", "backend": backend, "source": SOURCE,
           "trend_dates": days, "items": {}, "candidates": {}}
    for g, rules in (("items", cfg["pressures"]), ("candidates", cfg.get("candidates", {}))):
        for k, rule in rules.items():
            entry = {"name": rule["name"]}
            for grp in ("jp", "ref"):
                cur = ((today.get(g) or {}).get(k) or {}).get(grp)
                trend = [((((hist[d] or {}).get(g) or {}).get(k) or {}).get(grp) or {}).get("n") if hist[d] else None
                         for d in days]
                entry[grp] = {"n": cur["n"], "tone": cur["tone"], "trend": trend} if cur else None
            out[g][k] = entry
    if today.get("totals"):
        out["totals"] = today["totals"]
    # 媒体群ごとの記事総数の推移（「記事全体に占める割合」の比較に使う）
    out["totals_trend"] = {grp: [((hist[d] or {}).get("totals") or {}).get(grp) for d in days] for grp in ("jp", "ref")}
    return out


def zero_warning(site: dict) -> str | None:
    items = site["items"]
    zero = [k for k, v in items.items() if not v.get("jp") or not v["jp"]["n"]]
    if len(zero) * 2 > len(items):
        return f"警告: 日本の媒体の件数が0の圧力が半数を超えています（{len(zero)}/{len(items)}: {', '.join(zero)}）。キーワードかテーマを見直してください。"
    return None


# ---------- DOC API（最後の手段） ----------

def doc_backend(day: dt.date, cfg: dict) -> dict:
    import collect_gdelt  # 旧来の DOC API 方式（12秒間隔＋429 時の待機）
    res = {"items": {}, "candidates": {}}
    for g, rules in (("items", cfg["pressures"]), ("candidates", cfg.get("candidates", {}))):
        for k, p in rules.items():
            jp = collect_gdelt.tonechart(f'{p["query"]} {cfg["japan_filter"]}', "1d")
            ref = collect_gdelt.tonechart(f'{p["query"]} {cfg["reference_filter"]}', "1d")
            res[g][k] = {"jp": jp if "n" in jp else None, "ref": ref if "n" in ref else None}
            print(" ", k, jp, ref, flush=True)
    return res


# ---------- 実行 ----------

def main(argv=None) -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--date", help="JST の日付 YYYY-MM-DD（既定: 昨日）")
    ap.add_argument("--sample", type=int, default=int(os.environ.get("GKG_SAMPLE", "1")), help="N 本に1本だけ読む（既定 1 = 全本）")
    ap.add_argument("--backend", default=os.environ.get("GDELT_BACKEND", "gkg-files"), choices=["gkg-files", "bigquery", "doc"])
    ap.add_argument("--workers", type=int, default=2)
    ap.add_argument("--data", default=str(DATA))
    a = ap.parse_args(argv)
    data = pathlib.Path(a.data)
    day = dt.date.fromisoformat(a.date) if a.date else dt.datetime.now(gkg.JST).date() - dt.timedelta(days=1)
    cfg = load_json(ROOT / "config" / "pressures.json")
    media = load_json(ROOT / "config" / "media.json")
    print(f"GDELT {a.backend}: {day}（JST）", flush=True)

    stats: dict = {}
    if a.backend == "doc":
        res, jp_articles = doc_backend(day, cfg), []
        stats.update(files_expected=0, files_ok=0)
    else:
        src = bigquery_articles(day, media, stats) if a.backend == "bigquery" else \
            gkg_files_articles(day, media, a.sample, a.workers, stats)
        res, jp_articles = aggregate(src, cfg)
        if not stats.get("files_ok"):
            print("GKG を1本も取得できませんでした。前回のデータを残して終了します。", file=sys.stderr)
            return 2

    hist = {"date": day.isoformat(), "backend": a.backend, "sample": a.sample,
            "generated": dt.datetime.now(gkg.JST).strftime("%Y-%m-%d %H:%M"), **stats, **res}
    write_json(data / "history" / f"{day.isoformat()}.json", hist)
    site = build_site_json(day, cfg, a.backend, data / "history")
    write_json(data / "gdelt.json", site)
    if a.backend != "doc":
        write_json(data / "articles.json", {"date": day.isoformat(), "source": SOURCE,
                                            "kinds": pick_candidates(jp_articles, cfg)})
    t = res.get("totals", {})
    print(f"記事: 日本の媒体 {t.get('jp', '?')} 件、参考媒体（日本関連）{t.get('ref', '?')} 件", flush=True)
    for k, v in site["items"].items():
        print(f"  {k:12s} jp={v['jp']}  ref={(v['ref'] or {}).get('n')}")
    warn = zero_warning(site)
    if warn:
        print(warn, file=sys.stderr)
        if os.environ.get("GITHUB_ACTIONS"):
            print(f"::warning::{warn}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
