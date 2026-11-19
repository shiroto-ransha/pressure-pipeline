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


# ---------- 表示する項目の入れかえ（1 in 1 out） ----------

def test_select_display_one_in_one_out():
    scores = {"a": 10, "b": 8, "c": 2, "x": 9, "y": 7}
    keys, swap = collect.select_display(["a", "b", "c"], scores, 3)
    assert swap == {"in": "x", "out": "c"}                 # 外でいちばん多い x が、中でいちばん少ない c と入れかわる
    assert sorted(keys) == ["a", "b", "x"]                  # y も c より多いが、1日に1つだけ


def test_select_display_no_swap_when_not_greater():
    keys, swap = collect.select_display(["a", "b"], {"a": 5, "b": 3, "x": 3}, 2)
    assert swap is None and keys == ["a", "b"]              # 同数では入れかえない


def test_select_display_fills_and_trims():
    keys, swap = collect.select_display([], {"a": 1, "b": 5, "c": 3}, 2)
    assert keys == ["b", "c"] and swap is None
    keys, _ = collect.select_display(["a", "b", "c", "gone"], {"a": 1, "b": 5, "c": 3}, 2)
    assert len(keys) == 2 and "gone" not in keys


def test_display_state_once_per_day(tmp_path):
    cfg = {"pressures": {"a": {}, "b": {}}, "display_count": 2}
    path = tmp_path / "display.json"
    d = dt.date(2026, 11, 18)
    scores = {"a": 5, "b": 1, "x": 4, "y": 3}
    keys, swap = collect.update_display_state(path, d, scores, cfg)
    assert swap == {"in": "x", "out": "b"}
    again, swap2 = collect.update_display_state(path, d, scores, cfg)   # 同じ日にもう一度実行しても
    assert again == keys and swap2 == swap                              # 入れかえは1回分のまま
    nxt, swap3 = collect.update_display_state(path, d + dt.timedelta(days=1), scores, cfg)
    assert swap3 == {"in": "y", "out": "x"} or swap3 is None or swap3["out"] in keys
    old, swap4 = collect.update_display_state(path, d - dt.timedelta(days=3), scores, cfg)
    assert swap4 is None                                                # 過去の日の集計し直しでは変えない
    state = json.loads(path.read_text(encoding="utf-8"))
    assert state["history"][0]["date"] >= "2026-11-18"


def test_site_json_marks_new_item(tmp_path):
    cfg = {"pressures": {"a": {"name": "A"}, "b": {"name": "B"}}, "candidates": {"x": {"name": "X"}},
           "display_count": 2, "trend_days": 2}
    for day, n in (("2026-11-17", {"a": 5, "b": 1, "x": 3}), ("2026-11-18", {"a": 5, "b": 1, "x": 3})):
        h = {"items": {k: {"jp": {"n": n[k], "tone": 0.0}, "ref": {"n": 0, "tone": None}} for k in ("a", "b")},
             "candidates": {"x": {"jp": {"n": n["x"], "tone": 0.0}, "ref": {"n": 0, "tone": None}}}}
        (tmp_path / f"{day}.json").write_text(json.dumps(h))
    site = collect.build_site_json(dt.date(2026, 11, 18), cfg, "gkg-files", tmp_path, tmp_path / "display.json")
    assert list(site["items"]) == ["a", "x"] and site["items"]["x"]["is_new"] is True
    assert list(site["candidates"]) == ["b"]
    assert site["swap"]["in_name"] == "X" and site["swap"]["out_name"] == "B"
