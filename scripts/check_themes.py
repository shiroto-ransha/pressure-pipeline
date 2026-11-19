#!/usr/bin/env python3
"""config/pressures.json の gkg_themes が、GDELT 公式のテーマ一覧に実在するかを確かめる。

公式一覧: https://data.gdeltproject.org/api/v2/guides/LOOKUP-GKGTHEMES.TXT
（"テーマ名<TAB>出現数" の形。--lookup でローカルのファイルも使える）

存在しないテーマがあれば一覧を出して終了コード 1 を返す。推測でテーマ名を書かないための確認。
使い方: python scripts/check_themes.py [--lookup path]
"""
import argparse, json, pathlib, sys, urllib.request

ROOT = pathlib.Path(__file__).resolve().parent.parent
URL = "https://data.gdeltproject.org/api/v2/guides/LOOKUP-GKGTHEMES.TXT"


def load_lookup(path=None) -> dict:
    if path:
        text = pathlib.Path(path).read_text(encoding="utf-8", errors="replace")
    else:
        with urllib.request.urlopen(URL, timeout=120) as r:
            text = r.read().decode("utf-8", "replace")
    out = {}
    for line in text.splitlines():
        parts = line.strip().split("\t")
        if parts and parts[0]:
            out[parts[0]] = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else 0
    return out


def configured_themes(cfg: dict) -> list[tuple[str, str]]:
    """(場所, テーマ名) の一覧。"""
    out = []
    for group in ("pressures", "candidates", "news_categories"):
        for key, rule in cfg.get(group, {}).items():
            out += [(f"{group}.{key}", t) for t in rule.get("gkg_themes", [])]
    out += [("news_exclude", t) for t in cfg.get("news_exclude", {}).get("gkg_themes", [])]
    return out


def missing(cfg: dict, lookup: dict) -> list[tuple[str, str]]:
    return [(where, t) for where, t in configured_themes(cfg) if t not in lookup]


def main():
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("--lookup")
    a = ap.parse_args()
    cfg = json.loads((ROOT / "config" / "pressures.json").read_text(encoding="utf-8"))
    lookup = load_lookup(a.lookup)
    print(f"公式一覧: {len(lookup)} テーマ")
    for where, t in configured_themes(cfg):
        print(f"  {'OK ' if t in lookup else 'NG '}{where:32s}{t:48s}{lookup.get(t, '')}")
    bad = missing(cfg, lookup)
    if bad:
        print(f"\n実在しないテーマが {len(bad)} 件あります。", file=sys.stderr)
        sys.exit(1)
    print("\nすべて実在します。")


if __name__ == "__main__":
    main()
