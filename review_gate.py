#!/usr/bin/env python3
"""ニュースの下書きの自動チェック（REVIEW_MODE=automerge で、PR をマージする前に通す）。

人の確認の代わりに、機械で判定できる規則だけを確かめる。規則に反する事例は外し、残りをマージする。
外した事例と理由は PR へのコメント（--report に書く Markdown）に残す。

外す（drop）:
  - 出典（sources の URL）がない
  - 人物が記号になっていない（symbol が「人物」で始まらない）
  - 実名らしい書き方（「〇〇さん」「〇〇氏」「〇〇容疑者」「〇〇被告」の〇〇が、記号や一般的な言葉ではない）
  - 見出しにあるカタカナ・英字の固有名（グループ名・番組名など）をそのまま使っている（見出しが分かるときだけ）
  - 除外するテーマ（config/pressures.json の news_exclude のキーワード：自殺・未成年・性犯罪など）
知らせるだけ（warn）:
  - 硬い言葉（JARGON）。下書きを作る段階で AI に直しを頼んでいる（news_frames.make_check）。

限界：実名の判定は文字の並びによる推測で、漢字だけの氏名を必ず見つけられるわけではない。
そのため公開後も「AIの下書き（自動チェック済み・人の確認前）」と表示し、問題に気づいたら news.json から消す。

使い方: python review_gate.py data/news.json [--report report.md] [--check-only]
"""
from __future__ import annotations

import argparse
import json
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parent

# 高校生向けの言葉づかいにするために避ける言葉（AI への直しの依頼と、チェックの報告に使う）
JARGON = ["規範", "内面化", "無徴化", "言説", "正当化", "方略", "可視化", "構造的", "権力構造", "断罪", "惹起", "醸成",
          "帰責", "表象", "規律", "言及", "顕在化", "常態化", "周縁化", "他者化", "主体", "客体", "包摂", "排除の論理"]

HONORIFIC = re.compile(r"([一-龥々]{2,4}|[ァ-ヶー]{2,10}|[A-Za-zＡ-Ｚａ-ｚ]{2,12})(さん|氏|容疑者|被告人|被告|選手|監督|議員|知事|市長)")
# 「〇〇さん」などの〇〇として許す一般的な言葉（記号・続柄・職業など）
GENERIC = {"男性", "女性", "男子", "女子", "人物", "当事者", "本人", "相手", "知人", "友人", "同僚", "上司", "部下", "元交際", "交際",
           "夫", "妻", "父親", "母親", "会社員", "無職", "自営業", "外国籍", "外国人", "日本人", "元職員", "職員", "教員", "教師",
           "元教授", "教授", "医師", "店員", "社員", "芸人", "俳優", "女優", "歌手", "タレント", "アイドル", "前", "元", "同",
           "容疑", "被告", "現職", "前職", "元市", "同市", "同県", "地元", "大学", "自治体", "市", "県", "町", "村",
           "人物群", "メンバー", "グループ", "コンビ", "ユーザー", "ファン", "スタッフ"}
# 見出しにあってもよいカタカナ・英字（媒体名や一般語）
COMMON_WORDS = {"ニュース", "スポニチ", "デイリー", "オリコン", "ネット", "コメント", "インスタ", "インスタグラム", "ツイッター",
                "ドラマ", "テレビ", "グループ", "メンバー", "アイドル", "タレント", "ファン", "ライブ", "イベント", "ポスト",
                "ハロウィン", "ダイエット", "メンタル", "ハラスメント", "パワハラ", "セクハラ", "カスハラ", "スマホ", "アプリ",
                "サービス", "トラブル", "ドライブレコーダー", "クレジットカード", "コンビニ", "スーパー", "ホテル", "タクシー",
                "SNS", "NHK", "TBS", "ORICON", "NEWS", "WEB", "YouTube", "TikTok", "LINE", "AI", "DV", "LGBT", "LGBTQ"}


def load_exclude() -> list[str]:
    cfg = json.loads((ROOT / "config" / "pressures.json").read_text(encoding="utf-8"))
    return [k for k in cfg.get("news_exclude", {}).get("keywords", []) if k and k.strip() == k and len(k) >= 2]


def case_texts(case: dict) -> list[str]:
    out = [case.get("symbol", ""), case.get("title", ""), case.get("summary", "")]
    for f in case.get("frames", []):
        out += [f.get("name", ""), f.get("text", ""), f.get("norm", ""), f.get("effect", ""), (f.get("js") or {}).get("t", "")]
    return [t for t in out if t]


def name_like(text: str) -> list[str]:
    hits = []
    for m in HONORIFIC.finditer(text):
        word = m.group(1)
        if word in GENERIC or word.startswith("人物") or any(word.endswith(g) for g in ("男性", "女性", "人物")):
            continue
        hits.append(m.group(0))
    return hits


def proper_nouns(headline: str) -> set[str]:
    """見出しのカタカナ（3字以上）・英字（3字以上）の語。媒体名・一般語は除く。"""
    words = set(re.findall(r"[ァ-ヶー]{3,}", headline)) | set(re.findall(r"[A-Za-zＡ-Ｚａ-ｚ][A-Za-zＡ-Ｚａ-ｚ0-9&\.]{2,}", headline))
    return {w for w in words if w not in COMMON_WORDS and w.upper() not in COMMON_WORDS}


def case_problems(case: dict, headline: str | None = None, exclude: list[str] | None = None) -> list[str]:
    """外す理由の一覧（空なら合格）。"""
    probs = []
    src = case.get("sources") or []
    if not src or not all(str(s.get("url", "")).startswith("http") for s in src):
        probs.append("出典がない")
    if not str(case.get("symbol", "")).startswith("人物"):
        probs.append(f"人物が記号になっていない（{case.get('symbol')!r}）")
    texts = case_texts(case)
    names = sorted({h for t in texts for h in name_like(t)})
    if names:
        probs.append(f"実名らしい書き方: {', '.join(names[:5])}")
    if headline:
        used = sorted({w for w in proper_nouns(headline) if any(w in t for t in texts)})
        if used:
            probs.append(f"見出しの固有名をそのまま使っている: {', '.join(used[:5])}")
    ex = exclude if exclude is not None else load_exclude()
    blob = "\n".join(texts).lower()
    hit = sorted({k for k in ex if k.lower() in blob})
    if hit:
        probs.append(f"除外するテーマの言葉: {', '.join(hit[:5])}")
    return probs


def jargon_in(case: dict) -> list[str]:
    blob = "\n".join(case_texts(case))
    return [w for w in JARGON if w in blob]


def gate(days: list[dict]) -> tuple[list[dict], list[str], list[str]]:
    """確認待ち（review_required が true で、まだ自動チェックしていない）事例を確かめる。外した事例を除いた days と報告を返す。"""
    exclude = load_exclude()
    dropped, warns = [], []
    for day in days:
        keep = []
        for c in day.get("cases", []):
            if c.get("review_required") is True and not c.get("auto_checked"):
                probs = case_problems(c, exclude=exclude)
                if probs:
                    dropped.append(f"{day.get('date')}「{c.get('title', '')[:40]}」: {'／'.join(probs)}")
                    continue
                c["auto_checked"] = True
                j = jargon_in(c)
                if j:
                    warns.append(f"{day.get('date')}「{c.get('title', '')[:40]}」: 硬い言葉 {', '.join(j)}")
            keep.append(c)
        day["cases"] = keep
    return days, dropped, warns


def report_md(dropped: list[str], warns: list[str], kept: int) -> str:
    lines = ["## 自動チェックの結果", "", f"- 公開する事例（自動チェック済み・人の確認前）: {kept} 件"]
    lines.append(f"- 外した事例: {len(dropped)} 件")
    lines += [f"  - {d}" for d in dropped]
    if warns:
        lines.append(f"- 硬い言葉が残っている事例（公開はする）: {len(warns)} 件")
        lines += [f"  - {w}" for w in warns]
    lines += ["", "実名の判定は文字の並びによる推測です。問題に気づいたら、main の `data/news.json` からその事例を消してください。"]
    return "\n".join(lines) + "\n"


def main(argv=None) -> int:
    for s in (sys.stdout, sys.stderr):
        if hasattr(s, "reconfigure"):
            s.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("news", nargs="?", default=str(ROOT / "data" / "news.json"))
    ap.add_argument("--report", help="結果を Markdown で書く先")
    ap.add_argument("--check-only", action="store_true", help="news.json を書きかえない")
    a = ap.parse_args(argv)
    path = pathlib.Path(a.news)
    days = json.loads(path.read_text(encoding="utf-8"))
    days, dropped, warns = gate(days)
    kept = sum(1 for d in days for c in d.get("cases", []) if c.get("auto_checked"))
    md = report_md(dropped, warns, kept)
    print(md)
    if a.report:
        pathlib.Path(a.report).write_text(md, encoding="utf-8")
    if not a.check_only:
        path.write_text(json.dumps(days, ensure_ascii=False, indent=1), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
