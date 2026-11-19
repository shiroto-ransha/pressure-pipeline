#!/usr/bin/env python3
"""SNS で共有したときの画像 site/og.png（1200×630）を作る。デザインA の配色と書体。

使い方: python scripts/make_og.py   （playwright と chromium が必要）
"""
import pathlib

from playwright.sync_api import sync_playwright

ROOT = pathlib.Path(__file__).resolve().parent.parent
HTML = """<!doctype html><html lang="ja"><head><meta charset="utf-8">
<link href="https://fonts.googleapis.com/css2?family=Noto+Serif+JP:wght@700&family=Noto+Sans+JP:wght@400;700&display=swap" rel="stylesheet">
<style>
body{margin:0;width:1200px;height:630px;background:#F3EFE6;color:#161513;font-family:'Noto Sans JP',sans-serif;box-sizing:border-box;
 display:flex;flex-direction:column;justify-content:space-between;padding:64px 72px;border:14px solid #161513}
.k{font-size:26px;letter-spacing:.2em}
h1{margin:0;font-family:'Noto Serif JP',serif;font-size:96px;line-height:1.15}
p{margin:0;font-size:34px;line-height:1.5;color:#45423C}
.bar{display:flex;gap:10px;align-items:flex-end;height:80px}
.bar i{display:block;width:44px;background:#CFC8B8}.bar i.a{background:#B5341A}
.row{display:flex;justify-content:space-between;align-items:flex-end}
</style></head><body>
<div class="k">社会からのプレッシャーと、そのかわし方</div>
<div><h1>圧力と、その応じ方。</h1><p>毎朝のニュースで見る、社会のプレッシャー。</p></div>
<div class="row"><div class="bar"><i style="height:40%"></i><i style="height:65%"></i><i class="a" style="height:100%"></i><i style="height:55%"></i><i style="height:30%"></i><i style="height:75%"></i><i style="height:45%"></i></div>
<div class="k" style="font-size:22px">無料・登録なし</div></div>
</body></html>"""


def main():
    with sync_playwright() as p:
        b = p.chromium.launch()
        page = b.new_page(viewport={"width": 1200, "height": 630})
        page.set_content(HTML, wait_until="networkidle")
        page.screenshot(path=str(ROOT / "site" / "og.png"))
        b.close()
    print("site/og.png を書きました")


if __name__ == "__main__":
    main()
