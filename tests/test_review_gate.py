"""review_gate: 自動マージ前のチェック。"""
import json

import news_frames as nf
import review_gate as rg


def case(**kw):
    c = {"kind": "事件", "symbol": "人物A〔男性・40代・会社員〕", "title": "男性が逮捕された事案", "summary": "男性容疑者が逮捕された。",
         "frames": [{"name": "見張り合う道路", "text": "t", "norm": "他人の運転を記録してよい", "places": ["路上・公園"], "effect": "e",
                     "js": {"f": "observer", "t": "x"}}],
         "sources": [{"name": "a.jp", "url": "https://a.jp/1"}], "review_required": True, "generated_by": "gemini:m"}
    c.update(kw)
    return c


def test_clean_case_passes():
    assert rg.case_problems(case(), "男を逮捕 容疑を否認", exclude=[]) == []


def test_symbol_must_be_person_symbol():
    assert any("記号" in p for p in rg.case_problems(case(symbol="医師派遣汚職事件"), exclude=[]))


def test_real_name_patterns():
    assert rg.name_like("山田太郎さんが会見した") == ["山田太郎さん"]
    assert rg.name_like("阿波市長の修理費") == ["阿波市長"]
    assert rg.name_like("男性容疑者と女性さん、人物Aさん") == []
    assert any("実名" in p for p in rg.case_problems(case(summary="鈴木容疑者が否認"), exclude=[]))


def test_headline_proper_nouns():
    probs = rg.case_problems(case(summary="キンコンのメンバーが謝罪"), "キンコン西野が謝罪 - スポニチ", exclude=[])
    assert any("キンコン" in p for p in probs)
    assert rg.case_problems(case(summary="ニュースで話題に"), "スポニチ ニュース", exclude=[]) == []


def test_exclusion_and_sources():
    assert any("除外" in p for p in rg.case_problems(case(summary="中学生が関わった"), exclude=["中学生"]))
    assert any("出典" in p for p in rg.case_problems(case(sources=[]), exclude=[]))


def test_gate_drops_marks_and_keeps_reviewed(tmp_path):
    days = [{"date": "2026-11-21", "cases": [case(), case(symbol="事件の名前"), case(review_required=False, symbol="旧")]}]
    out, dropped, warns = rg.gate(days)
    cases = out[0]["cases"]
    assert len(cases) == 2 and len(dropped) == 1
    assert cases[0]["auto_checked"] is True and "auto_checked" not in cases[1]   # 人が確認済みのものは触らない
    again, dropped2, _ = rg.gate(out)                                            # 2回目は何もしない
    assert dropped2 == [] and len(again[0]["cases"]) == 2


def test_jargon_warns_only():
    out, dropped, warns = rg.gate([{"date": "d", "cases": [case(summary="規範が可視化される")]}])
    assert dropped == [] and len(warns) == 1


def test_main_writes_report(tmp_path):
    f = tmp_path / "news.json"
    f.write_text(json.dumps([{"date": "d", "cases": [case(), case(symbol="X")]}], ensure_ascii=False), encoding="utf-8")
    rep = tmp_path / "r.md"
    assert rg.main([str(f), "--report", str(rep)]) == 0
    assert "外した事例: 1 件" in rep.read_text(encoding="utf-8")
    assert len(json.loads(f.read_text(encoding="utf-8"))[0]["cases"]) == 1


def test_generation_check_asks_for_fixes():
    pool = [{"title": "鈴木一郎が謝罪 キンコン", "url": "https://a.jp/1", "domain": "a.jp", "tone": 0}]
    bad = case(symbol="謝罪騒動", summary="規範が揺らぐ", source_index=0)
    errs = nf.make_check(pool)({"cases": [bad]})
    assert any("記号" in e for e in errs) and any("硬い言葉" in e for e in errs)
