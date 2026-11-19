#!/usr/bin/env python3
"""data/*.json を確かめてから site/data/ に置く。

- 形が崩れているファイルは置かない（前回の site/data/ のファイルがそのまま残る）。
- news.json は30日分に切り詰め、各事例が出典を持つことを確かめる。
- --check は置かずに確かめるだけ（PR の確認用）。終了コード 1 = 問題あり。
- --only gdelt|news で片方だけ置ける（REVIEW_MODE=pr のとき、ニュースは確認用ブランチ側でだけ置く）。
"""
from __future__ import annotations

import argparse
import html
import json
import re
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


# ---------- SEO（公開の直前に実行。コミットはしない） ----------

def _e(x) -> str:
    return html.escape(str(x if x is not None else ""), quote=True)


def head_tags(site_cfg: dict, asof: str) -> str:
    url, title, desc = site_cfg["url"], site_cfg["title"], site_cfg["description"]
    ld = {"@context": "https://schema.org", "@graph": [
        {"@type": "WebSite", "name": site_cfg["short_title"], "url": url, "inLanguage": "ja", "description": desc},
        {"@type": "WebApplication", "name": site_cfg["short_title"], "url": url, "applicationCategory": "EducationalApplication",
         "operatingSystem": "Web", "inLanguage": "ja", "isAccessibleForFree": True,
         "offers": {"@type": "Offer", "price": "0", "priceCurrency": "JPY"}, "dateModified": asof},
        {"@type": "Dataset", "name": "日本のニュースでの社会のプレッシャーの取り上げられ方（日別）", "url": url + "data/gdelt.json",
         "description": "GDELT の GKG をもとに、日本のニュースサイトの記事数と論調をテーマ別に日ごとに集計したもの。",
         "isBasedOn": "https://www.gdeltproject.org/", "creator": {"@type": "Organization", "name": site_cfg["short_title"]},
         "license": "https://www.gdeltproject.org/about.html#termsofuse", "dateModified": asof, "inLanguage": "ja"}]}
    return "\n".join([
        f"<title>{_e(title)}</title>",
        f'<meta name="description" content="{_e(desc)}">',
        f'<link rel="canonical" href="{_e(url)}">',
        '<meta name="robots" content="index,follow,max-image-preview:large">',
        '<meta property="og:type" content="website">',
        '<meta property="og:locale" content="ja_JP">',
        f'<meta property="og:site_name" content="{_e(site_cfg["short_title"])}">',
        f'<meta property="og:title" content="{_e(title)}">',
        f'<meta property="og:description" content="{_e(desc)}">',
        f'<meta property="og:url" content="{_e(url)}">',
        f'<meta property="og:image" content="{_e(url)}og.png">',
        '<meta property="og:image:width" content="1200">',
        '<meta property="og:image:height" content="630">',
        '<meta name="twitter:card" content="summary_large_image">',
        f'<script type="application/ld+json">{json.dumps(ld, ensure_ascii=False)}</script>'])


def static_body(site_cfg: dict, gd: dict | None, news: list | None) -> str:
    """JavaScript を動かさない検索エンジンや、読み込み中にも読める本文。"""
    out = ['<div class="static">', f'<h1>{_e(site_cfg["short_title"])}</h1>', f'<p>{_e(site_cfg["description"])}</p>']
    if gd and gd.get("items"):
        items = sorted(gd["items"].values(), key=lambda v: -((v.get("jp") or {}).get("n") or 0))
        out.append(f'<h2>{_e(gd.get("asof"))} のニュースでの取り上げられ方（日本のニュースサイト）</h2><ul>')
        out += [f'<li>{_e(v["name"])}：{(v.get("jp") or {}).get("n") or 0}件</li>' for v in items]
        out.append('</ul><p>データ：<a href="https://www.gdeltproject.org/">The GDELT Project</a></p>')
    if news and news[0].get("cases"):
        out.append(f'<h2>ニュースが広める「ふつう」の考え方（{_e(news[0].get("date"))}）</h2>')
        for c in news[0]["cases"]:
            f = (c.get("frames") or [{}])[0]
            src = (c.get("sources") or [{}])[0]
            out.append(f'<article><h3>{_e(f.get("name"))}</h3><p>{_e(c.get("kind"))}・{_e(c.get("title"))}</p>'
                       f'<p>広まりやすい考え：「{_e(f.get("norm"))}」</p>'
                       + (f'<p>元の記事：<a href="{_e(src.get("url"))}" rel="noopener">{_e(src.get("name"))}</a></p>' if src.get("url") else "")
                       + '</article>')
    out.append('<p>自分の立場でプレッシャーへのかわし方を考えるツールは、JavaScript を有効にすると使えます。</p></div>')
    return "\n".join(out)


def replace_block(text: str, name: str, body: str) -> str:
    start = re.search(rf"<!-- {name}:start[^>]*-->", text)
    end = text.find(f"<!-- {name}:end -->")
    if not start or end < 0:
        raise ValueError(f"index.html に {name} の印がありません")
    return text[: start.end()] + "\n" + body + "\n" + text[end:]


def seo(site_root: pathlib.Path) -> None:
    site_cfg = json.loads((ROOT / "config" / "site.json").read_text(encoding="utf-8"))
    load = lambda n: json.loads((site_root / "data" / n).read_text(encoding="utf-8")) if (site_root / "data" / n).exists() else None
    gd, news = load("gdelt.json"), load("news.json")
    asof = (gd or {}).get("asof") or ""
    index = site_root / "index.html"
    text = index.read_text(encoding="utf-8")
    text = replace_block(text, "seo", head_tags(site_cfg, asof))
    text = replace_block(text, "static", static_body(site_cfg, gd, news))
    index.write_text(text, encoding="utf-8")
    url = site_cfg["url"]
    (site_root / "sitemap.xml").write_text(
        '<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
        f"  <url><loc>{_e(url)}</loc>" + (f"<lastmod>{asof}</lastmod>" if asof else "")
        + "<changefreq>daily</changefreq></url>\n</urlset>\n",
        encoding="utf-8")
    (site_root / "robots.txt").write_text(f"User-agent: *\nAllow: /\nSitemap: {url}sitemap.xml\n", encoding="utf-8")
    print(f"SEO: head・静的な本文・sitemap.xml・robots.txt を書きました（{asof}）")


def main(argv=None) -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--only", choices=["gdelt", "news"])
    ap.add_argument("--seo", action="store_true", help="公開用に head・静的な本文・sitemap.xml・robots.txt を書く")
    ap.add_argument("--data", default=str(ROOT / "data"))
    ap.add_argument("--site", default=str(ROOT / "site" / "data"))
    a = ap.parse_args(argv)
    data, site = pathlib.Path(a.data), pathlib.Path(a.site)
    if a.seo:
        seo(site.parent)
        return 0
    jobs = [("gdelt.json", check_gdelt), ("news.json", check_news)]
    if a.only:
        jobs = [j for j in jobs if j[0].startswith(a.only)]
    ok = all([place(n, c, data, site, a.check) for n, c in jobs])
    return 0 if ok or not a.check else 1


if __name__ == "__main__":
    sys.exit(main())
