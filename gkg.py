"""GDELT GKG 2.x の生ファイルを読むための小さな部品集（標準ライブラリだけ）。

collect.py と scripts/ から使う。ここにある関数は副作用がなく、tests/ でテストする。

GKG 2.1 の列（タブ区切り、27列）:
  0 GKGRECORDID  1 DATE  2 SourceCollectionIdentifier  3 SourceCommonName
  4 DocumentIdentifier  5 Counts  6 V2Counts  7 Themes  8 V2Themes
  9 Locations  10 V2Locations  11 Persons  12 V2Persons  13 Organizations
  14 V2Organizations  15 V2Tone  16 Dates  17 GCAM  18 SharingImage
  19 RelatedImages  20 SocialImageEmbeds  21 SocialVideoEmbeds  22 Quotations
  23 AllNames  24 Amounts  25 TranslationInfo  26 Extras
"""
from __future__ import annotations

import datetime as dt
import html
import io
import re
import time
import urllib.error
import urllib.request
import zipfile
from typing import Iterable, Iterator

BASE = "https://data.gdeltproject.org/gdeltv2/"
COL = {"DATE": 1, "SourceCommonName": 3, "DocumentIdentifier": 4, "V2Themes": 8,
       "V2Locations": 10, "V2Tone": 15, "TranslationInfo": 25, "Extras": 26}
NCOL = 27
JST = dt.timezone(dt.timedelta(hours=9))
UA = "pressure-pipeline/1.0 (+https://www.gdeltproject.org/ data consumer)"


# ---------- 媒体の判定 ----------

def _host(name: str) -> str:
    name = (name or "").strip().lower().rstrip(".")
    if "://" in name:
        name = name.split("://", 1)[1]
    name = name.split("/", 1)[0].split(":", 1)[0]
    return name[4:] if name.startswith("www.") else name


def _matches(host: str, domain: str) -> bool:
    domain = _host(domain)
    return host == domain or host.endswith("." + domain)


def classify_media(source: str, media: dict) -> str | None:
    """SourceCommonName（または URL）を 'jp' / 'ref' / None に振り分ける。

    - ref_domains（BBC など）を先に見る。bbc.co.uk は .uk なので jp と衝突しないが、順序を固定しておく。
    - exclude_domains は jp 判定から外す（例: .jp で終わるが日本の媒体ではないもの）。
    - jp_domains（.com など .jp 以外の日本の媒体）か、jp_suffix（既定 .jp）で終わるものを jp とする。
    """
    host = _host(source)
    if not host:
        return None
    if any(_matches(host, d) for d in media.get("ref_domains", [])):
        return "ref"
    if any(_matches(host, d) for d in media.get("exclude_domains", [])):
        return None
    if any(_matches(host, d) for d in media.get("jp_domains", [])):
        return "jp"
    if any(host.endswith(s.lower()) for s in media.get("jp_suffix", [".jp"])):
        return "jp"
    return None


# ---------- 列の解析 ----------

def parse_tone(v2tone: str) -> float | None:
    """V2Tone（"tone,pos,neg,polarity,activity,selfgroup,wordcount"）の第1要素。"""
    head = (v2tone or "").split(",", 1)[0].strip()
    try:
        return float(head)
    except ValueError:
        return None


def parse_themes(v2themes: str) -> set[str]:
    """V2Themes（"THEME,offset;THEME,offset"）からテーマ名の集合を作る。"""
    out = set()
    for part in (v2themes or "").split(";"):
        name = part.rsplit(",", 1)[0].strip() if "," in part else part.strip()
        if name:
            out.add(name)
    return out


def has_country(v2locations: str, fips: str = "JA") -> bool:
    """V2Locations の国コード（3番目の要素、FIPS）に fips があるか。"""
    for loc in (v2locations or "").split(";"):
        f = loc.split("#")
        if len(f) > 2 and f[2] == fips:
            return True
    return False


_TITLE = re.compile(r"<PAGE_TITLE>(.*?)</PAGE_TITLE>", re.S)


def page_title(extras: str) -> str:
    m = _TITLE.search(extras or "")
    return html.unescape(m.group(1)).strip() if m else ""


def source_lang(translation_info: str) -> str:
    """TranslationInfo（"srclc:jpn;eng:GT-JPN 1.0"）から元の言語。空なら英語。"""
    m = re.search(r"srclc:([a-z]{3})", translation_info or "")
    return m.group(1) if m else "eng"


# ---------- 圧力・カテゴリとの対応 ----------

def match_rule(rule: dict, themes: set[str], title: str, url: str) -> bool:
    """rule = {"gkg_themes": [...], "keywords": [...]} にテーマかキーワードで当たるか。"""
    if themes and any(t in themes for t in rule.get("gkg_themes", [])):
        return True
    hay = f"{title}\n{url}".lower()
    return any(k.lower() in hay for k in rule.get("keywords", []) if k)


def match_pressures(rules: dict, themes: set[str], title: str, url: str) -> list[str]:
    return [k for k, r in rules.items() if match_rule(r, themes, title, url)]


# ---------- ファイルの列挙と取得 ----------

def jst_day_slots(day: dt.date) -> list[str]:
    """JST の day 0:00〜24:00 に当たる、15分ごとの UTC タイムスタンプ（96個）。"""
    start = dt.datetime(day.year, day.month, day.day, tzinfo=JST).astimezone(dt.timezone.utc)
    return [(start + dt.timedelta(minutes=15 * i)).strftime("%Y%m%d%H%M%S") for i in range(96)]


def file_urls(day: dt.date, feeds: Iterable[str] = ("eng", "translation"), sample: int = 1) -> list[str]:
    """前日分の GKG ファイルの URL。feed: eng → *.gkg.csv.zip、translation → *.translation.gkg.csv.zip。

    masterfilelist.txt（数百MB）を毎回読む代わりに、15分刻みの命名規則から URL を組み立てる。
    存在しない（404）ものは fetch 時に飛ばす。
    """
    slots = jst_day_slots(day)[:: max(1, sample)]
    urls = []
    for feed in feeds:
        suffix = ".gkg.csv.zip" if feed == "eng" else ".translation.gkg.csv.zip"
        urls += [BASE + s + suffix for s in slots]
    return urls


def fetch(url: str, retries: int = 3, timeout: int = 120) -> bytes | None:
    """GKG の zip を取る。404 は None（未公開・欠番）。それ以外はリトライ。"""
    url = url.replace("http://data.gdeltproject.org", "https://data.gdeltproject.org")
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.read()
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return None
            err = e
        except Exception as e:  # ネットワーク障害など
            err = e
        time.sleep(5 * (attempt + 1))
    print(f"  ! 取得失敗: {url} ({err})", flush=True)
    return None


def iter_rows(zbytes: bytes) -> Iterator[list[str]]:
    """zip のバイト列から行を読み、必要な列だけを残したリストを返す。

    行全体を split すると GCAM などで重いので、まず SourceCommonName だけを見る用途には
    iter_raw を使う。
    """
    for raw in iter_raw(zbytes):
        r = raw.split("\t")
        if len(r) >= NCOL:
            yield r


def iter_raw(zbytes: bytes) -> Iterator[str]:
    with zipfile.ZipFile(io.BytesIO(zbytes)) as z:
        for name in z.namelist():
            with z.open(name) as f:
                for line in io.TextIOWrapper(f, encoding="utf-8", errors="replace", newline="\n"):
                    yield line.rstrip("\r\n")


def article_host(row: list[str]) -> str:
    """記事 URL のホスト名。URL が空なら SourceCommonName。"""
    url = row[COL["DocumentIdentifier"]]
    return _host(url) if url.startswith("http") else _host(row[COL["SourceCommonName"]])


def source_of(raw: str) -> str:
    """行から SourceCommonName だけを安く取り出す。"""
    parts = raw.split("\t", 4)
    return parts[3] if len(parts) > 4 else ""
