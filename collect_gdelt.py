#!/usr/bin/env python3
"""GDELT DOC 2.0 API から、圧力ごとの報道量と論調（トーン）を集計する。

- 日本の媒体（sourcecountry:japan）を本集計、
  BBC・ロイター・Bloomberg・NYTimes の日本関連記事を参考値として取得する。
- GDELT は「5秒に1回まで」の制限があるため、間隔を空けて順に問い合わせ、
  429 のときは待ってから再試行する。
- 出力: data/gdelt.json
  データの利用時は "The GDELT Project" の名前とサイトへのリンクを表示すること。
"""
import json, time, datetime, pathlib, urllib.parse, urllib.request, urllib.error

ROOT = pathlib.Path(__file__).resolve().parent
API = "https://api.gdeltproject.org/api/v2/doc/doc"
INTERVAL = 12      # 秒。公式の下限は5秒だが、混雑時は429が続くため余裕を持たせる
RETRIES = 4


def tonechart(query: str, window: str) -> dict:
    url = API + "?" + urllib.parse.urlencode(
        {"query": query, "mode": "tonechart", "timespan": window, "format": "json"})
    for attempt in range(RETRIES):
        time.sleep(INTERVAL if attempt == 0 else 30 * attempt)
        try:
            with urllib.request.urlopen(url, timeout=60) as r:
                body = r.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as e:
            if e.code == 429:
                continue
            return {"error": f"HTTP {e.code}"}
        except Exception as e:  # ネットワーク障害など
            return {"error": str(e)[:80]}
        if not body.strip().startswith("{"):
            return {"error": body.strip()[:80]}   # 検索式の書式エラーなど
        bins = json.loads(body).get("tonechart", [])
        n = sum(b["count"] for b in bins)
        tone = round(sum(b["bin"] * b["count"] for b in bins) / n, 2) if n else None
        return {"n": n, "tone": tone}
    return {"error": "rate limited (429)"}


def main():
    cfg = json.loads((ROOT / "config" / "pressures.json").read_text(encoding="utf-8"))
    out = {"asof": datetime.date.today().isoformat(), "window": cfg["window"],
           "source": "The GDELT Project (https://www.gdeltproject.org/)",
           "items": {}, "candidates": {}}
    for group in ("pressures", "candidates"):
        for key, p in cfg[group].items():
            jp = tonechart(f'{p["query"]} {cfg["japan_filter"]}', cfg["window"])
            ref = tonechart(f'{p["query"]} {cfg["reference_filter"]}', cfg["window"])
            out["items" if group == "pressures" else "candidates"][key] = {
                "name": p["name"], "query": p["query"], "jp": jp, "ref": ref}
            print(key, jp, ref, flush=True)
    dst = ROOT / "data" / "gdelt.json"
    dst.parent.mkdir(exist_ok=True)
    dst.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()
