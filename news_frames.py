#!/usr/bin/env python3
"""その日の日本の報道から事例を選び、「世間の反応が公共空間に供給する枠組み」を
LLM で下書きする。

- 記事の候補は GDELT の記事一覧（見出し・URL・媒体のメタデータ）から取る。
  記事本文は取得しない（著作権・利用規約への配慮）。
- LLM は LLM_PROVIDER で切り替える: gemini（無料枠あり）| anthropic
    GEMINI_API_KEY, GEMINI_MODEL（既定: gemini-2.5-flash。AI Studio で使えるモデル名に合わせる）
    ANTHROPIC_API_KEY, ANTHROPIC_MODEL
- 出力は「下書き」として review_required=true を付ける。公開前に人が確認する運用を前提にする。
- 出力: data/news.json（新しい日付を先頭に追加し、30日分を残す）
"""
import json, os, time, datetime, pathlib, urllib.parse, urllib.request

ROOT = pathlib.Path(__file__).resolve().parent
GDELT = "https://api.gdeltproject.org/api/v2/doc/doc"

PRESSURE_KEYS = ["objectify", "role_f", "masc", "hetero", "ethnic", "class", "able", "looks", "young", "old",
                 "precarious", "marital", "isolation", "harass", "ideo_minority", "grievance", "echo",
                 "money_rank", "scarcity", "inequality", "relative_future", "fame"]
JS_KEYS = ["victim", "observer", "beneficiary", "perpetrator"]
PLACES = ["電車・駅", "路上・公園", "職場", "飲食店・飲み会", "店舗・接客", "役所・病院・窓口", "学校・PTA", "冠婚葬祭・地域行事", "SNS・ネット"]

RULES = f"""あなたは、報道に対する「世間の反応（報道の見出しや表現を含む）」が、公共空間にどんな暗黙の枠組み（どう振る舞ってよいか・どう見てよいかという規範）を供給しているかを解説する編集者です。

厳守すること:
- 渡された見出しとメタデータに書かれていることだけを事実として扱う。書かれていない事実・発言・反応を作らない。
- 実名・グループ名・番組名は書かない。人物は「人物A〔性別・年代・職業カテゴリ〕」のように記号化する（不明な要素は省く）。
- 容疑者・被告は推定無罪として扱い、断定しない。被害者の人物像や行為の手口には立ち入らない。
- 次のものは選ばない: 自殺・自傷、未成年が当事者のもの、性犯罪の被害の詳細、一般人（芸能人・公人以外）が中心のもの。
- 当事者の内心や対処方略は扱わない。扱うのは枠組みだけ。良い・悪いの評価を入れない。政治的な話題はどの立場にも同じ基準で書く。
- 見出しの文言を長く写さない（引用するなら15字未満を1か所まで）。
- 各枠組みには、受け手のどの「正当性への過敏さ」（victim=被害者, observer=観察者, beneficiary=受益者, perpetrator=加害者）を刺激するかを書く。
- 圧力キーは次から選ぶ: {", ".join(PRESSURE_KEYS)}
- 場所は次から選ぶ: {", ".join(PLACES)}
"""

SCHEMA = {
    "type": "OBJECT",
    "properties": {"cases": {"type": "ARRAY", "items": {
        "type": "OBJECT",
        "properties": {
            "kind": {"type": "STRING", "enum": ["芸能・ゴシップ", "事件"]},
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


def gdelt_articles(query: str, n: int = 40) -> list:
    url = GDELT + "?" + urllib.parse.urlencode(
        {"query": query, "mode": "ArtList", "maxrecords": n, "timespan": "1d", "sort": "HybridRel", "format": "json"})
    for attempt in range(4):
        time.sleep(12 if attempt == 0 else 30 * attempt)
        try:
            with urllib.request.urlopen(url, timeout=60) as r:
                body = r.read().decode("utf-8", "replace")
            if body.strip().startswith("{"):
                return [{"title": a.get("title"), "url": a.get("url"), "domain": a.get("domain"),
                         "date": a.get("seendate")} for a in json.loads(body).get("articles", [])]
        except Exception:
            pass
    return []


def call_gemini(prompt: str) -> dict:
    model = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash")
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
    body = {"contents": [{"role": "user", "parts": [{"text": prompt}]}],
            "systemInstruction": {"parts": [{"text": RULES}]},
            "generationConfig": {"responseMimeType": "application/json", "responseSchema": SCHEMA, "temperature": 0.4}}
    req = urllib.request.Request(url, data=json.dumps(body).encode(), method="POST",
                                 headers={"Content-Type": "application/json", "x-goog-api-key": os.environ["GEMINI_API_KEY"]})
    with urllib.request.urlopen(req, timeout=180) as r:
        res = json.loads(r.read())
    return json.loads(res["candidates"][0]["content"]["parts"][0]["text"])


def call_anthropic(prompt: str) -> dict:
    body = {"model": os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-5-5"), "max_tokens": 8000, "system": RULES,
            "messages": [{"role": "user", "content": prompt + "\n\n次のJSONスキーマに従うJSONだけを出力してください:\n" + json.dumps(SCHEMA, ensure_ascii=False)}]}
    req = urllib.request.Request("https://api.anthropic.com/v1/messages", data=json.dumps(body).encode(), method="POST",
                                 headers={"content-type": "application/json", "x-api-key": os.environ["ANTHROPIC_API_KEY"],
                                          "anthropic-version": "2023-06-01"})
    with urllib.request.urlopen(req, timeout=180) as r:
        text = json.loads(r.read())["content"][0]["text"]
    return json.loads(text[text.index("{"): text.rindex("}") + 1])


def main():
    cfg = json.loads((ROOT / "config" / "pressures.json").read_text(encoding="utf-8"))
    pool = []
    for kind, q in cfg["news_queries"].items():
        for a in gdelt_articles(q):
            a["kind_hint"] = kind
            pool.append(a)
    if not pool:
        print("候補記事がありません。更新しません。")
        return
    listing = "\n".join(f'[{i}] ({a["kind_hint"]}) {a["title"]} — {a["domain"]}' for i, a in enumerate(pool))
    prompt = ("次は今日の日本の媒体の記事見出しの一覧です。条件に合うものを、芸能・ゴシップから最大3件、事件から最大3件選び、"
              "1件につき枠組みを2つずつ書いてください。source_index には一覧の番号を入れてください。\n\n" + listing)
    provider = os.environ.get("LLM_PROVIDER", "gemini")
    result = call_gemini(prompt) if provider == "gemini" else call_anthropic(prompt)

    today = datetime.date.today().isoformat()
    cases = []
    for c in result.get("cases", []):
        i = c.pop("source_index", None)
        if i is None or not (0 <= i < len(pool)):
            continue   # 出典をたどれない事例は捨てる
        a = pool[i]
        c["sources"] = [{"name": f'{a["domain"]}（{today}）', "url": a["url"]}]
        c["review_required"] = True
        cases.append(c)

    dst = ROOT / "data" / "news.json"
    days = json.loads(dst.read_text(encoding="utf-8")) if dst.exists() else []
    days = [d for d in days if d["date"] != today]
    days.insert(0, {"date": today, "updated": datetime.datetime.now().strftime("%Y-%m-%d %H:%M"),
                    "generated_by": provider, "cases": cases})
    dst.parent.mkdir(exist_ok=True)
    dst.write_text(json.dumps(days[:30], ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"{len(cases)}件を追加しました（{provider}）")


if __name__ == "__main__":
    main()
