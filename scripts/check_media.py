#!/usr/bin/env python3
"""config/media.json の媒体ドメインが、実際に GDELT GKG に現れるかを確かめる。

前日（JST）の GKG を N 本に1本の割合で取り、SourceCommonName を数える。
- jp_domains / ref_domains の各ドメインの出現数（0 なら「未確認」）
- jp と判定された媒体の上位
- 日本語（srclc:jpn）なのに jp と判定されない媒体（jp_domains に足す候補）

使い方: python scripts/check_media.py [--date YYYY-MM-DD] [--sample 8]
"""
import argparse, collections, datetime as dt, json, pathlib, sys
from concurrent.futures import ThreadPoolExecutor

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import gkg  # noqa: E402


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default=(dt.datetime.now(gkg.JST).date() - dt.timedelta(days=1)).isoformat())
    ap.add_argument("--sample", type=int, default=8)
    a = ap.parse_args()
    media = json.loads((ROOT / "config" / "media.json").read_text(encoding="utf-8"))
    day = dt.date.fromisoformat(a.date)
    urls = gkg.file_urls(day, sample=a.sample)
    seen, jp_lang_other, jp_hosts = collections.Counter(), collections.Counter(), collections.Counter()
    with ThreadPoolExecutor(2) as ex:
        for i, z in enumerate(ex.map(gkg.fetch, urls)):
            if not z:
                continue
            for r in gkg.iter_rows(z):
                src = gkg.article_host(r)
                seen[src] += 1
                grp = gkg.classify_media(src, media)
                if grp == "jp":
                    jp_hosts[src] += 1
                elif gkg.source_lang(r[gkg.COL["TranslationInfo"]]) == "jpn":
                    jp_lang_other[src] += 1
            print(f"  {i + 1}/{len(urls)}", end="\r", flush=True)
    print()

    def count(d):
        return sum(v for h, v in seen.items() if gkg._matches(h, d))

    report = {}
    for key in ("jp_domains", "watch_domains", "ref_domains"):
        rows = [(d, count(d)) for d in media.get(key, [])]
        report[key] = dict(rows)
        print(f"== {key}")
        for d, n in rows:
            print(f"  {'OK ' if n else '-- '}{d:32s}{n}")
    print("== jp と判定した媒体の上位")
    for h, n in jp_hosts.most_common(60):
        print(f"  {h:40s}{n}")
    print("== 日本語の記事だが jp と判定されない媒体（jp_domains の候補。中身を確かめてから足す）")
    for h, n in jp_lang_other.most_common(30):
        print(f"  {h:40s}{n}")
    unseen = [d for k in report for d, n in report[k].items() if n == 0]
    if unseen:
        print(f"\n未確認（このサンプルでは0件）: {', '.join(unseen)}")


if __name__ == "__main__":
    main()
