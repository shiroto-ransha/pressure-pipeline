#!/usr/bin/env python3
"""前日の日本の報道から事例を選び、「世間の反応が公共空間に供給する枠組み」を LLM で下書きする。

- 候補記事は collect.py が GKG から作る data/articles.json（見出し・URL・媒体・論調・テーマ）。
  記事本文は取得しない（著作権・利用規約への配慮）。
- LLM は llm.py の無料枠フォールバック連鎖（Gemini → Groq → Cerebras → Cloudflare → OpenRouter）。
  呼び出しはカテゴリごとに1回（計2回）＋スキーマ修正の再依頼まで。
- 出典は source_index で候補記事に結びつけ、結びつかない事例は捨てる。
- 出力の全件に review_required: true と generated_by を付ける（公開前に人が確認する前提）。
- 出力: data/news.json（新しい日付が先頭。30日分を残す）

使い方:
  python news_frames.py               生成して data/news.json を更新
  python news_frames.py --dry-run     LLM を呼ばずに、プロンプトと候補一覧を表示
  python news_frames.py --provider groq   特定のプロバイダーだけを使う
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import pathlib
import re
import sys

import llm
import review_gate

ROOT = pathlib.Path(__file__).resolve().parent
DATA = ROOT / "data"
KEEP_DAYS = 30
MAX_CASES = 3
QUOTE_MAX = 15   # 引用は15字未満
JST = dt.timezone(dt.timedelta(hours=9))

PRESSURE_KEYS = ["objectify", "role_f", "masc", "hetero", "ethnic", "class", "family", "able", "looks", "young", "old",
                 "precarious", "marital", "isolation", "harass", "service", "ideo_minority", "grievance", "echo",
                 "money_rank", "scarcity", "inequality", "relative_future", "fame"]
JS_KEYS = ["victim", "observer", "beneficiary", "perpetrator"]
PLACES = ["電車・駅", "路上・公園", "職場", "飲食店・飲み会", "店舗・接客", "役所・病院・窓口", "学校・PTA", "冠婚葬祭・地域行事", "SNS・ネット"]
KINDS = ["芸能・ゴシップ", "事件"]

RULES = f"""あなたは、報道に対する「世間の反応（報道の見出しや表現を含む）」が、公共空間にどんな暗黙の枠組み（どう振る舞ってよいか・どう見てよいかという規範）を供給しているかを解説する編集者です。

厳守すること:
- 高校生にも分かる、やさしい日本語で書く。一文は短くする。専門用語（規範、内面化、無徴化、言説、正当化、方略など）は使わず、日常の言葉に言いかえる。
- name（枠組みの名前）は20字以内の、ひと目で意味が分かる言い方にする。norm は「〜してよい」「〜するべきだ」のような、ふだんの言葉の一文にする。
- 渡された見出しとメタデータに書かれていることだけを事実として扱う。書かれていない事実・発言・反応を作らない。
- 実名・グループ名・番組名は書かない。「〇〇市長」「〇〇社長」のように、地名や会社名と役職で個人が分かる書き方もしない。symbol は必ず「人物A〔性別・年代・職業カテゴリ〕」の形にする（事件名や出来事の名前は書かない）。人物は「人物A〔性別・年代・職業カテゴリ〕」のように記号化する（不明な要素は省く）。
- 容疑者・被告は推定無罪として扱い、断定しない。被害者の人物像や行為の手口には立ち入らない。
- 次のものは選ばない: 自殺・自傷、未成年が当事者のもの、性犯罪の被害の詳細、一般人（芸能人・公人以外）が中心のもの。
- 解説するのは「世間の反応（報道を含む）が公共空間に供給する枠組み」だけ。当事者の内心や対処方略は扱わない。良い・悪いの評価を入れない。政治的な話題はどの立場にも同じ基準で書く。
- 見出しの文言を長く写さない。引用するなら「」で囲み、15字未満を1か所まで。
- 各枠組みには、規範（norm）、働く場所（places）、同じ立場の人への作用（effect）、刺激される正当性への過敏さ（js.f: victim=被害者, observer=観察者, beneficiary=受益者, perpetrator=加害者、js.t: その説明）を付ける。
- source_index には、根拠にした候補記事の番号を必ず入れる。候補にない出来事は書かない。
- 圧力キー（pressures）は次から選ぶ: {", ".join(PRESSURE_KEYS)}
- 場所（places）は次から選ぶ: {", ".join(PLACES)}
"""

SCHEMA = {
    "type": "OBJECT",
    "properties": {"cases": {"type": "ARRAY", "items": {
        "type": "OBJECT",
        "properties": {
            "kind": {"type": "STRING", "enum": KINDS},
            "symbol": {"type": "STRING"}, "title": {"type": "STRING"}, "summary": {"type": "STRING"},
            "source_index": {"type": "INTEGER"},
            "frames": {"type": "ARRAY", "items": {"type": "OBJECT", "properties": {
                "name": {"type": "STRING"}, "text": {"type": "STRING"}, "norm": {"type": "STRING"},
                "places": {"type": "ARRAY", "items": {"type": "STRING", "enum": PLACES}},
                "effect": {"type": "STRING"}, "pressures": {"type": "ARRAY", "items": {"type": "STRING", "enum": PRESSURE_KEYS}},
                "js": {"type": "OBJECT", "properties": {"f": {"type": "STRING", "enum": JS_KEYS}, "t": {"type": "STRING"}},
                       "required": ["f", "t"]}},
                "required": ["name", "text", "norm", "places", "effect", "js"]}}},
        "required": ["kind", "symbol", "title", "summary", "source_index", "frames"]}}},
    "required": ["cases"]}


# ---------- 候補とプロンプト ----------

def load_candidates(path: pathlib.Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def listing(pool: list[dict]) -> str:
    """LLM に渡す候補一覧。公開ニュースの見出し・媒体・論調だけ（本文・利用者の入力は送らない）。"""
    lines = []
    for i, a in enumerate(pool):
        tone = f"{a['tone']:+.1f}" if isinstance(a.get("tone"), (int, float)) else "–"
        lines.append(f"[{i}] {a['title']} — {a['domain']}（論調 {tone}）")
    return "\n".join(lines)


def build_prompt(kind: str, pool: list[dict], date: str) -> str:
    return (f"次は {date}（日本時間）の日本の媒体の記事見出しの一覧です（カテゴリ: {kind}）。"
            f"規則に合うものを最大{MAX_CASES}件選び、1件につき枠組みを2つずつ書いてください。"
            f"kind は「{kind}」、source_index には一覧の番号を入れてください。規則に合うものがなければ cases を空にしてください。\n\n"
            + listing(pool))


# ---------- 検査 ----------

QUOTE = re.compile(r"「([^「」]+)」")


def quote_problems(case: dict, source_title: str) -> list[str]:
    """見出しからの引用（「」内で見出しにそのまま現れる文字列）が、15字未満・1か所までかを調べる。"""
    texts = [case.get("title", ""), case.get("summary", "")]
    for f in case.get("frames", []):
        texts += [f.get("name", ""), f.get("text", ""), f.get("norm", ""), f.get("effect", ""), (f.get("js") or {}).get("t", "")]
    quoted = {q for t in texts for q in QUOTE.findall(t or "") if q and q in source_title}
    errs = [f"引用「{q}」が{QUOTE_MAX}字以上です" for q in quoted if len(q) >= QUOTE_MAX]
    if len(quoted) > 1:
        errs.append(f"見出しからの引用が{len(quoted)}か所あります（1か所まで）")
    return errs


def make_check(pool: list[dict]):
    """llm.generate に渡す追加検査（不合格なら再依頼の対象になる）。"""
    def check(obj: dict) -> list[str]:
        errs = []
        for n, c in enumerate(obj.get("cases", [])):
            i = c.get("source_index")
            if not isinstance(i, int) or not 0 <= i < len(pool):
                errs.append(f"cases[{n}].source_index={i!r} は候補一覧にありません")
                continue
            errs += [f"cases[{n}]: {e}" for e in quote_problems(c, pool[i]["title"])]
            # 公開前の自動チェック（review_gate）と同じ規則。出典はあとで付けるので、ここでは仮に付けて調べる
            probe = dict(c, sources=[{"url": pool[i]["url"]}])
            errs += [f"cases[{n}]: {e}" for e in review_gate.case_problems(probe, pool[i]["title"])]
            hard = review_gate.jargon_in(c)
            if hard:
                errs.append(f"cases[{n}]: 硬い言葉（{', '.join(hard)}）を、高校生にも分かる言葉に言いかえる")
        return errs
    return check


def link_sources(cases: list[dict], pool: list[dict], kind: str, date: str, generated_by: str) -> list[dict]:
    """source_index を出典に置き換える。結びつかない事例・引用の規則に反する事例は捨てる。"""
    out = []
    for c in cases[:MAX_CASES]:
        c = dict(c)
        i = c.pop("source_index", None)
        if not isinstance(i, int) or not 0 <= i < len(pool):
            print(f"  出典をたどれない事例を捨てました: {c.get('title')}", file=sys.stderr)
            continue
        a = pool[i]
        probs = quote_problems(c, a["title"]) + review_gate.case_problems(dict(c, sources=[{"url": a["url"]}]), a["title"])
        if probs:
            print(f"  規則に反する事例を捨てました: {c.get('title')}（{probs}）", file=sys.stderr)
            continue
        c["kind"] = kind
        c["sources"] = [{"name": f"{a['domain']}（{date}）", "url": a["url"]}]
        c["review_required"] = True
        c["generated_by"] = generated_by
        out.append(c)
    return out


def merge_days(days: list[dict], entry: dict, keep: int = KEEP_DAYS) -> list[dict]:
    """同じ日付を置き換えて先頭に入れ、日付の新しい順に keep 日分だけ残す。"""
    days = [d for d in days if d.get("date") != entry["date"]] + [entry]
    days.sort(key=lambda d: d.get("date", ""), reverse=True)
    return days[:keep]


# ---------- 実行 ----------

def main(argv=None) -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description="ニュースの枠組みの下書き")
    ap.add_argument("--dry-run", action="store_true", help="LLM を呼ばずにプロンプトと候補を表示する")
    ap.add_argument("--provider", help="このプロバイダーだけを使う（gemini, groq, cerebras, cloudflare, openrouter）")
    ap.add_argument("--articles", default=str(DATA / "articles.json"))
    ap.add_argument("--out", default=str(DATA / "news.json"))
    ap.add_argument("--no-verify-models", action="store_true", help="モデル一覧 API での確認を省く")
    a = ap.parse_args(argv)

    cand = load_candidates(pathlib.Path(a.articles))
    date = cand.get("date") or (dt.datetime.now(JST).date() - dt.timedelta(days=1)).isoformat()
    kinds = cand.get("kinds") or {}
    if not any(kinds.get(k) for k in KINDS):
        print("候補記事がありません（collect.py が失敗した可能性）。news.json は更新しません。", file=sys.stderr)
        return 2

    if a.dry_run:
        print("=== システム指示 ===\n" + RULES)
        for kind in KINDS:
            pool = kinds.get(kind) or []
            print(f"=== {kind}: 候補 {len(pool)} 件 ===")
            for i, x in enumerate(pool):
                print(f"  [{i}] {x['domain']}  {x['url']}")
            print(f"--- プロンプト（{kind}）---\n{build_prompt(kind, pool, date)}\n")
        print("（--dry-run のため LLM は呼んでいません）")
        return 0

    cases, used, failures = [], [], []
    for kind in KINDS:
        pool = kinds.get(kind) or []
        if not pool:
            continue
        try:
            obj, label = llm.generate(RULES, build_prompt(kind, pool, date), SCHEMA, force=a.provider,
                                      check=make_check(pool), verify_models=not a.no_verify_models)
        except llm.AllProvidersFailed as e:
            print(f"{kind}: {e}", file=sys.stderr)
            failures.append(kind)
            continue
        got = link_sources(obj.get("cases", []), pool, kind, date, label)
        print(f"{kind}: {len(got)} 件（{label}）")
        cases += got
        used.append(label)

    if not cases:
        print("事例を1件も作れませんでした。news.json は更新しません。", file=sys.stderr)
        return 3
    out = pathlib.Path(a.out)
    days = json.loads(out.read_text(encoding="utf-8")) if out.exists() else []
    entry = {"date": date, "updated": dt.datetime.now(JST).strftime("%Y-%m-%d %H:%M"),
             "generated_by": ", ".join(dict.fromkeys(used)), "review_required": True, "cases": cases}
    if failures:
        entry["incomplete"] = failures
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(merge_days(days, entry), ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"{len(cases)} 件を {out} に書きました（{entry['generated_by']}）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
