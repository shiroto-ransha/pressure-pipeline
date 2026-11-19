"""build.py: 壊れたデータは置かず、前回のファイルを残す。"""
import json

import build


def write(p, obj):
    p.write_text(json.dumps(obj, ensure_ascii=False), encoding="utf-8")


GD = {"asof": "2026-11-18", "source": "The GDELT Project (https://www.gdeltproject.org/)",
      "items": {"inequality": {"name": "格差", "jp": {"n": 3, "tone": -1.0, "trend": [3]}, "ref": None}}}
CASE = {"kind": "事件", "symbol": "人物A", "title": "t", "summary": "s",
        "frames": [{"name": "n", "text": "t", "norm": "n", "places": ["職場"], "effect": "e", "js": {"f": "victim", "t": "x"}}],
        "sources": [{"name": "a.jp", "url": "https://a.jp/1"}], "review_required": True}


def test_places_valid_files(tmp_path):
    data, site = tmp_path / "data", tmp_path / "site"
    data.mkdir()
    write(data / "gdelt.json", GD)
    write(data / "news.json", [{"date": "2026-11-18", "cases": [CASE]}])
    assert build.main(["--data", str(data), "--site", str(site)]) == 0
    assert json.loads((site / "gdelt.json").read_text(encoding="utf-8"))["asof"] == "2026-11-18"


def test_keeps_previous_on_broken(tmp_path):
    data, site = tmp_path / "data", tmp_path / "site"
    data.mkdir()
    site.mkdir()
    (site / "news.json").write_text("PREVIOUS", encoding="utf-8")
    bad = dict(CASE, sources=[])
    write(data / "news.json", [{"date": "2026-11-18", "cases": [bad]}])
    write(data / "gdelt.json", {"items": {}})
    assert build.main(["--data", str(data), "--site", str(site), "--check"]) == 1
    build.main(["--data", str(data), "--site", str(site)])
    assert (site / "news.json").read_text(encoding="utf-8") == "PREVIOUS"
    assert not (site / "gdelt.json").exists()


def test_news_truncated_to_30(tmp_path):
    data, site = tmp_path / "data", tmp_path / "site"
    data.mkdir()
    days = [{"date": f"2026-10-{d:02d}", "cases": []} for d in range(31, 0, -1)]
    write(data / "news.json", days)
    assert build.main(["--data", str(data), "--site", str(site), "--only", "news"]) == 0
    assert len(json.loads((site / "news.json").read_text(encoding="utf-8"))) == 30


def test_seo_writes_head_static_sitemap(tmp_path, monkeypatch):
    import shutil
    site = tmp_path / "site"
    (site / "data").mkdir(parents=True)
    shutil.copy(build.ROOT / "site" / "index.html", site / "index.html")
    write(site / "data" / "gdelt.json", GD)
    write(site / "data" / "news.json", [{"date": "2026-11-18", "cases": [CASE]}])
    assert build.main(["--seo", "--site", str(site / "data")]) == 0
    text = (site / "index.html").read_text(encoding="utf-8")
    assert '<meta name="description"' in text and text.count("<title>") == 1
    assert "<li>格差：3件</li>" in text and "<h3>n</h3>" in text
    assert "<lastmod>2026-11-18</lastmod>" in (site / "sitemap.xml").read_text(encoding="utf-8")
    assert "Sitemap: https://" in (site / "robots.txt").read_text(encoding="utf-8")
    build.main(["--seo", "--site", str(site / "data")])          # 2回目でも重複しない
    assert (site / "index.html").read_text(encoding="utf-8").count("<title>") == 1


def test_static_body_escapes_html():
    out = build.static_body({"short_title": "t", "description": "d"}, None,
                            [{"date": "d", "cases": [dict(CASE, title="<script>x</script>")]}])
    assert "<script>x" not in out and "&lt;script&gt;" in out
