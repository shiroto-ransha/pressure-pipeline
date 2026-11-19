"""圧力 → テーマ・キーワードの対応と、テーマ実在の確認。"""
import check_themes
import gkg
from conftest import load

CFG = load("pressures.json")


def test_every_pressure_has_rule_and_name():
    for group in ("pressures", "candidates"):
        for k, r in CFG[group].items():
            assert r["name"], k
            assert r.get("gkg_themes") or r.get("keywords"), f"{k} にテーマもキーワードもありません"


def test_theme_match():
    rules = CFG["pressures"]
    assert gkg.match_pressures(rules, {"WB_695_POVERTY"}, "", "") == ["inequality"]
    assert "hetero" in gkg.match_pressures(rules, {"LGBT"}, "", "")
    assert gkg.match_pressures(rules, {"EDUCATION"}, "", "") == []


def test_keyword_match_title_and_url():
    rules = CFG["pressures"]
    assert "isolation" in gkg.match_pressures(rules, set(), "孤独死が増加、自治体が見守り", "")
    assert "service" in gkg.match_pressures(rules, set(), "", "https://example.jp/news/kasuhara-2026")
    assert "marital" in gkg.match_pressures(rules, set(), "Japan's birth rate hits record low", "")


def test_missing_themes_detected():
    cfg = {"pressures": {"a": {"gkg_themes": ["REAL", "MADE_UP"]}}, "news_exclude": {"gkg_themes": ["ALSO_FAKE"]}}
    lookup = {"REAL": 10}
    assert check_themes.missing(cfg, lookup) == [("pressures.a", "MADE_UP"), ("news_exclude", "ALSO_FAKE")]


def test_lookup_parser(tmp_path):
    p = tmp_path / "lookup.txt"
    p.write_text("TAX_FNCACT\t999\nWB_695_POVERTY\t42626100\n\n", encoding="utf-8")
    assert check_themes.load_lookup(str(p)) == {"TAX_FNCACT": 999, "WB_695_POVERTY": 42626100}
