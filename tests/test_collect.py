"""集計・候補の選定・trend の組み立て・GKG ファイルの読み取り。"""
import datetime as dt
import io
import json
import zipfile

import collect
import gkg
from conftest import load

CFG = load("pressures.json")
MEDIA = load("media.json")


def art(group="jp", url="https://www.asahi.com/a/1", title="", themes=(), tone=-1.0):
    return {"group": group, "host": gkg._host(url), "url": url, "title": title, "tone": tone,
            "themes": set(themes), "lang": "jpn", "date": "20261118000000"}


def test_aggregate_counts_and_tone():
    arts = [art(url="https://asahi.com/1", themes=["WB_695_POVERTY"], tone=-2.0),
            art(url="https://mainichi.jp/2", title="格差が拡大", tone=1.0),
            art(url="https://mainichi.jp/2", title="格差が拡大", tone=1.0),          # 重複は数えない
            art(url="https://mainichi.jp/3", title="無関係", tone=5.0),
            art(group="ref", url="https://bbc.com/x", title="Japan poverty", tone=-3.0)]
    res, jp = collect.aggregate(arts, CFG)
    assert res["items"]["inequality"]["jp"] == {"n": 2, "tone": -0.5}
    assert res["items"]["inequality"]["ref"] == {"n": 1, "tone": -3.0}
    assert res["items"]["hetero"]["jp"] == {"n": 0, "tone": None}
    assert res["totals"] == {"jp": 3, "ref": 1}
    assert len(jp) == 3


def test_pick_candidates_filters_and_dedups():
    jp = [art(url="https://a.jp/1", title="俳優Xが謝罪｜スポニチ", themes=["TAX_FNCACT_ACTOR"]),
          art(url="https://b.jp/1", title="俳優Xが謝罪｜北國新聞", themes=["TAX_FNCACT_ACTOR"]),   # 転載の重複
          art(url="https://c.jp/1", title="男を逮捕 容疑を否認", themes=["ARREST"]),
          art(url="https://d.jp/1", title="中学生が自殺 学校が会見", themes=["SOC_SUICIDE"]),      # 除外
          art(url="https://e.jp/1", title="", themes=["ARREST"]),                                 # 見出しなし
          art(url="https://f.jp/1", title="天気", themes=[])]
    out = collect.pick_candidates(jp, CFG)
    assert [x["url"] for x in out["芸能・ゴシップ"]] == ["https://a.jp/1"]
    assert [x["url"] for x in out["事件"]] == ["https://c.jp/1"]
    assert out["事件"][0]["themes"] == ["ARREST"]


def test_pick_candidates_limit():
    jp = [art(url=f"https://a.jp/{i}", title=f"{i}件目の別事件で男を逮捕", themes=["ARREST"]) for i in range(60)]
    assert len(collect.pick_candidates(jp, CFG, per_kind=40)["事件"]) == 40


def test_build_site_json_trend(tmp_path):
    for i, n in enumerate([3, None, 5]):
        d = dt.date(2026, 11, 16) + dt.timedelta(days=i)
        if n is None:
            continue
        items = {k: {"jp": {"n": n, "tone": -1.0}, "ref": {"n": 1, "tone": 0.5}} for k in CFG["pressures"]}
        (tmp_path / f"{d}.json").write_text(json.dumps({"items": items, "candidates": {}, "totals": {"jp": 9, "ref": 1}}))
    site = collect.build_site_json(dt.date(2026, 11, 18), CFG, "gkg-files", tmp_path)
    it = site["items"]["inequality"]
    assert site["trend_dates"][-1] == "2026-11-18" and len(site["trend_dates"]) == 7
    assert it["jp"]["trend"] == [None, None, None, None, 3, None, 5]
    assert it["jp"]["n"] == 5 and it["ref"]["n"] == 1
    assert site["source"].startswith("The GDELT Project")
    assert site["totals_trend"]["jp"] == [None, None, None, None, 9, None, 9]


def test_zero_warning():
    site = {"items": {k: {"jp": {"n": 0}} for k in "abcd"}}
    assert collect.zero_warning(site)
    site["items"]["a"]["jp"]["n"] = 1
    site["items"]["b"]["jp"]["n"] = 1
    assert collect.zero_warning(site) is None


def _gkg_line(src, url, themes="", locs="", tone="-2.5,1,3,4,5,0,100", title="見出し", tr=""):
    row = [""] * gkg.NCOL
    row[0], row[1], row[2], row[3], row[4] = "id", "20261118000000", "1", src, url
    row[8], row[10], row[15], row[25] = themes, locs, tone, tr
    row[26] = f"<PAGE_TITLE>{title}</PAGE_TITLE>"
    return "\t".join(row)


def test_articles_from_zip():
    lines = [_gkg_line("asahi.com", "https://www.asahi.com/a", "WB_695_POVERTY,3", tr="srclc:jpn;eng:GT-JPN 1.0"),
             _gkg_line("bbc.com", "https://www.bbc.com/news/1", locs="1#Japan#JA#JA##36#138#JA#5"),
             _gkg_line("bbc.com", "https://www.bbc.com/news/2", locs="1#France#FR#FR##46#2#FR#5"),  # 日本に触れない
             _gkg_line("example.com", "https://example.com/x")]
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("x.gkg.csv", "\n".join(lines) + "\n")
    got = list(collect.articles_from_zip(buf.getvalue(), MEDIA))
    assert [(a["group"], a["url"]) for a in got] == [("jp", "https://www.asahi.com/a"), ("ref", "https://www.bbc.com/news/1")]
    assert got[0]["themes"] == {"WB_695_POVERTY"} and got[0]["tone"] == -2.5 and got[0]["lang"] == "jpn"
