#!/usr/bin/env python3
"""data/*.json を確かめてから site/data/ に置く。

- 形が崩れているファイルは置かない（前回の site/data/ のファイルがそのまま残る）。
- news.json は30日分に切り詰め、各事例が出典を持つことを確かめる。
- --check は置かずに確かめるだけ（PR の確認用）。終了コード 1 = 問題あり。
- --only gdelt|news で片方だけ置ける（REVIEW_MODE=pr のとき、ニュースは確認用ブランチ側でだけ置く）。
"""
from __future__ import annotations

import argparse
import json
import pathlib
import shutil
import sys

import llm
import news_frames

ROOT = pathlib.Path(__file__).resolve().parent


def check_gdelt(obj) -> list[str]:
    errs = []
    if not isinstance(obj, dict):
        return ["gdelt.json: オブジェクトではありません"]
    for k in ("asof", "items", "source"):
        if k not in obj:
            errs.append(f"gdelt.json: {k} がありません")
    if "GDELT" not in str(obj.get("source", "")):
        errs.append("gdelt.json: 出典（The GDELT Project）がありません")
    for key, it in (obj.get("items") or {}).items():
        if not isinstance(it, dict) or "name" not in it:
            errs.append(f"gdelt.json: items.{key} の形が違います")
            continue
        for grp in ("jp", "ref"):
            v = it.get(grp)
            if v is not None and not (isinstance(v, dict) and isinstance(v.get("n"), int)):
                errs.append(f"gdelt.json: items.{key}.{grp} の形が違います")
    return errs


CASE_SCHEMA = news_frames.SCHEMA["properties"]["cases"]["items"]


def check_news(obj) -> list[str]:
    if not isinstance(obj, list):
        return ["news.json: 配列ではありません"]
    errs = []
    if len(obj) > news_frames.KEEP_DAYS:
        errs.append(f"news.json: {len(obj)} 日分あります（{news_frames.KEEP_DAYS} 日まで）")
    dates = [d.get("date", "") for d in obj if isinstance(d, dict)]
    if dates != sorted(dates, reverse=True):
        errs.append("news.json: 日付の新しい順になっていません")
    for di, day in enumerate(obj):
        if not isinstance(day, dict) or not isinstance(day.get("cases"), list):
            errs.append(f"news.json[{di}]: cases がありません")
            continue
        for ci, c in enumerate(day["cases"]):
            schema = dict(CASE_SCHEMA, required=[r for r in CASE_SCHEMA["required"] if r != "source_index"])
            errs += [f"news.json[{di}].cases[{ci}]{e[1:]}" for e in llm.validate(c, schema)]
            src = c.get("sources") if isinstance(c, dict) else None
            if not src or not all(isinstance(s, dict) and str(s.get("url", "")).startswith("http") for s in src):
                errs.append(f"news.json[{di}].cases[{ci}]: 出典（sources）がありません")
    return errs


def place(name: str, checker, data: pathlib.Path, site: pathlib.Path, dry: bool) -> bool:
    src = data / name
    try:
        obj = json.loads(src.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError) as e:
        print(f"{name}: 読めません（{e}）。site/data/ の前回のファイルを残します。", file=sys.stderr)
        return False
    if name == "news.json" and isinstance(obj, list):
        obj = obj[: news_frames.KEEP_DAYS]
    errs = checker(obj)
    if errs:
        print(f"{name}: 問題があります。site/data/ の前回のファイルを残します。\n  " + "\n  ".join(errs[:20]), file=sys.stderr)
        return False
    if not dry:
        site.mkdir(parents=True, exist_ok=True)
        (site / name).write_text(json.dumps(obj, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"{name}: site/data/ に置きました")
    else:
        print(f"{name}: OK")
    return True


def main(argv=None) -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--only", choices=["gdelt", "news"])
    ap.add_argument("--data", default=str(ROOT / "data"))
    ap.add_argument("--site", default=str(ROOT / "site" / "data"))
    a = ap.parse_args(argv)
    data, site = pathlib.Path(a.data), pathlib.Path(a.site)
    jobs = [("gdelt.json", check_gdelt), ("news.json", check_news)]
    if a.only:
        jobs = [j for j in jobs if j[0].startswith(a.only)]
    ok = all([place(n, c, data, site, a.check) for n, c in jobs])
    return 0 if ok or not a.check else 1


if __name__ == "__main__":
    sys.exit(main())
