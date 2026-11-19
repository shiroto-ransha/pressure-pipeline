"""媒体の判定・V2Tone/V2Themes/V2Locations の解析・日付の範囲。"""
import datetime as dt

import pytest

import gkg
from conftest import load

MEDIA = load("media.json")


@pytest.mark.parametrize("src,expected", [
    ("asahi.com", "jp"),
    ("https://www.asahi.com/articles/ASV1.html", "jp"),
    ("mainichi.jp", "jp"),                      # jp_suffix
    ("medical.mainichi.jp", "jp"),
    ("news.livedoor.com", "jp"),
    ("blog.livedoor.com", None),                # livedoor.com 全体は日本の報道ではない
    ("https://japan.cnet.com/article/1/", "jp"),
    ("https://www.cnet.com/news/x", None),      # 英語版 CNET は対象外
    ("373news.com", "jp"),
    ("bbc.co.uk", "ref"),
    ("https://www.bbc.com/news/world-asia-1", "ref"),
    ("edition.nytimes.com", "ref"),
    ("reuters.com", "ref"),
    ("ameblo.jp", None),                        # ブログは除外
    ("kazokuchannel.doorblog.jp", None),
    ("www.mhlw.go.jp", None),                   # 官公庁は除外
    ("u-tokyo.ac.jp", None),
    ("japan.focustaiwan.tw", None),             # 日本語版でも日本の媒体ではない
    ("notasahi.com", None),                     # 部分一致で誤判定しない
    ("", None),
])
def test_classify_media(src, expected):
    assert gkg.classify_media(src, MEDIA) == expected


def test_ref_checked_before_exclusion():
    media = {"ref_domains": ["bbc.co.uk"], "exclude_domains": ["co.uk"], "jp_suffix": [".jp"]}
    assert gkg.classify_media("bbc.co.uk", media) == "ref"


def test_parse_tone():
    assert gkg.parse_tone("-1.76,2.1,3.86,5.96,20.1,0.3,525") == -1.76
    assert gkg.parse_tone("3") == 3.0
    assert gkg.parse_tone("") is None
    assert gkg.parse_tone("abc,1") is None


def test_parse_themes_strips_offsets_and_dedups():
    s = "TAX_ETHNICITY_AMERICAN,8;EDUCATION,576;EDUCATION,1806;WB_695_POVERTY,12"
    assert gkg.parse_themes(s) == {"TAX_ETHNICITY_AMERICAN", "EDUCATION", "WB_695_POVERTY"}
    assert gkg.parse_themes("") == set()


def test_has_country_uses_fips_field_only():
    loc = "5#Ehime, Ehime, Japan#JA#JA05#227007#33.6#132.8#-227007#10;1#United States#US#US##39.8#-98.5#US#30"
    assert gkg.has_country(loc, "JA")
    assert not gkg.has_country("1#Jamaica#JM#JM##18#-77#JM#5", "JA")
    assert not gkg.has_country("", "JA")


def test_page_title_unescapes_entities():
    ex = "<PAGE_AUTHORS>x</PAGE_AUTHORS><PAGE_TITLE>ASCII.jp&#xFF1A;&#x53EF;&#x611B;</PAGE_TITLE>"
    assert gkg.page_title(ex) == "ASCII.jp：可愛"
    assert gkg.page_title("") == ""


def test_source_lang():
    assert gkg.source_lang("srclc:jpn;eng:GT-JPN 1.0") == "jpn"
    assert gkg.source_lang("") == "eng"


def test_jst_day_slots_cover_utc_range():
    s = gkg.jst_day_slots(dt.date(2026, 11, 18))
    assert len(s) == 96
    assert s[0] == "20261117150000" and s[-1] == "20261118144500"


def test_file_urls_both_feeds_and_sample():
    urls = gkg.file_urls(dt.date(2026, 11, 18), sample=4)
    assert len(urls) == 48
    assert urls[0].endswith("20261117150000.gkg.csv.zip")
    assert urls[24].endswith("20261117150000.translation.gkg.csv.zip")
    assert all(u.startswith("https://") for u in urls)
