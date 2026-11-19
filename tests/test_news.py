"""出典の結びつけ・引用の規則・30日分の切り詰め・--dry-run。"""
import json

import news_frames as nf

POOL = [{"title": "俳優Xが会見で謝罪 ファンに説明", "url": "https://a.jp/1", "domain": "a.jp", "tone": -2.0},
        {"title": "男を逮捕 容疑を否認", "url": "https://b.jp/2", "domain": "b.jp", "tone": -5.0}]


def case(i, **kw):
    c = {"kind": "事件", "symbol": "人物A", "title": "t", "summary": "s", "source_index": i,
         "frames": [{"name": "n", "text": "t", "norm": "n", "places": ["職場"], "effect": "e", "js": {"f": "victim", "t": "x"}}]}
    c.update(kw)
    return c


def test_link_sources_drops_unlinked():
    got = nf.link_sources([case(1), case(7), case(None), case(-1)], POOL, "事件", "2026-11-18", "groq:m")
    assert len(got) == 1
    c = got[0]
    assert c["sources"] == [{"name": "b.jp（2026-11-18）", "url": "https://b.jp/2"}]
    assert c["review_required"] is True and c["generated_by"] == "groq:m"
    assert "source_index" not in c


def test_link_sources_max_three():
    assert len(nf.link_sources([case(0)] * 5, POOL, "芸能・ゴシップ", "d", "x")) == 3


def test_quote_rule():
    ok = case(0, summary="見出しは「会見で謝罪」と伝えた")
    assert nf.quote_problems(ok, POOL[0]["title"]) == []
    long = case(0, summary="「俳優Xが会見で謝罪 ファンに説明」")      # 15字以上
    assert nf.quote_problems(long, POOL[0]["title"])
    two = case(0, summary="「会見で謝罪」「ファンに説明」")             # 2か所
    assert nf.quote_problems(two, POOL[0]["title"])
    not_quote = case(0, summary="「心配」の言葉")                      # 見出しにない語は引用ではない
    assert nf.quote_problems(not_quote, POOL[0]["title"]) == []
    assert nf.link_sources([long], POOL, "事件", "d", "x") == []


def test_check_reports_bad_index():
    errs = nf.make_check(POOL)({"cases": [case(5)]})
    assert errs and "source_index" in errs[0]


def test_merge_days_keeps_30_newest_first():
    days = [{"date": f"2026-10-{d:02d}", "cases": []} for d in range(1, 32)]
    out = nf.merge_days(days, {"date": "2026-11-18", "cases": [1]})
    assert len(out) == 30
    assert out[0]["date"] == "2026-11-18" and out[-1]["date"] == "2026-10-03"
    again = nf.merge_days(out, {"date": "2026-11-18", "cases": [2]})
    assert len(again) == 30 and again[0]["cases"] == [2]


def test_dry_run_does_not_call_llm(tmp_path, monkeypatch, capsys):
    arts = tmp_path / "articles.json"
    arts.write_text(json.dumps({"date": "2026-11-18", "kinds": {"事件": POOL[1:], "芸能・ゴシップ": POOL[:1]}},
                               ensure_ascii=False), encoding="utf-8")

    def boom(*a, **k):
        raise AssertionError("LLM が呼ばれた")

    monkeypatch.setattr(nf.llm, "generate", boom)
    out = tmp_path / "news.json"
    assert nf.main(["--dry-run", "--articles", str(arts), "--out", str(out)]) == 0
    text = capsys.readouterr().out
    assert "[0] 男を逮捕 容疑を否認 — b.jp" in text and "LLM は呼んでいません" in text
    assert not out.exists()


def test_main_writes_with_mock(tmp_path, monkeypatch):
    arts = tmp_path / "articles.json"
    arts.write_text(json.dumps({"date": "2026-11-18", "kinds": {"事件": POOL[1:]}}, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(nf.llm, "generate", lambda *a, **k: ({"cases": [case(0)]}, "cerebras:gpt-oss-120b"))
    out = tmp_path / "news.json"
    assert nf.main(["--articles", str(arts), "--out", str(out)]) == 0
    days = json.loads(out.read_text(encoding="utf-8"))
    assert days[0]["date"] == "2026-11-18" and days[0]["generated_by"] == "cerebras:gpt-oss-120b"
    assert days[0]["cases"][0]["sources"][0]["url"] == "https://b.jp/2"
    assert days[0]["cases"][0]["review_required"] is True


def test_no_candidates_keeps_previous(tmp_path):
    out = tmp_path / "news.json"
    out.write_text("[]", encoding="utf-8")
    assert nf.main(["--articles", str(tmp_path / "missing.json"), "--out", str(out)]) == 2
    assert out.read_text(encoding="utf-8") == "[]"
