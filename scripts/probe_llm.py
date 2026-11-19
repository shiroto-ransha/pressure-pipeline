#!/usr/bin/env python3
"""LLM の応答が遅い・返らないときの診断。条件を変えた短い呼び出しを順に試し、所要時間を表示する。

送るのは固定の短い文と、data/articles.json の見出し数件だけ（推定ツールの入力は送らない）。
使い方: python scripts/probe_llm.py [--provider gemini] [--timeout 120]
"""
import argparse, json, pathlib, sys, time

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import llm  # noqa: E402
import news_frames  # noqa: E402


def main():
    for s in (sys.stdout, sys.stderr):
        s.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("--provider", default="gemini")
    ap.add_argument("--timeout", type=int, default=120)
    a = ap.parse_args()
    p = llm.providers([a.provider])[0]
    p.timeout = a.timeout
    print(f"provider={p.label} available={p.available() or 'yes'}")
    if p.available():
        return 1
    t = time.time()
    print(f"model_exists={p.model_exists()} ({time.time() - t:.1f}s)")
    if p.kind == "gemini":
        res = llm.http("GET", f"{p.base}/models?pageSize=1000", p._headers(), timeout=30)
        names = [m["name"].removeprefix("models/") for m in res.get("models", [])
                 if "generateContent" in m.get("supportedGenerationMethods", []) and "flash" in m["name"]]
        print("flash models:", ", ".join(names))

    arts = json.loads((ROOT / "data" / "articles.json").read_text(encoding="utf-8"))
    pool = (arts["kinds"].get("事件") or [])[:5]
    small = news_frames.build_prompt("事件", pool, arts["date"])
    full_pool = arts["kinds"].get("事件") or []
    full = news_frames.build_prompt("事件", full_pool, arts["date"])
    tiny_schema = {"type": "OBJECT", "properties": {"ok": {"type": "BOOLEAN"}}, "required": ["ok"]}

    base_gc = p.spec.get("generation_config", {})
    trials = [
        ("tiny / 設定なし (1)", "JSON で ok=true を返してください。", tiny_schema, {}),
        ("tiny / 設定なし (2)", "JSON で ok=true を返してください。", tiny_schema, {}),
        ("tiny / 設定あり", "JSON で ok=true を返してください。", tiny_schema, base_gc),
        ("候補5件 / 設定なし", small, news_frames.SCHEMA, {}),
        (f"候補{len(full_pool)}件 / 設定なし", full, news_frames.SCHEMA, {}),
    ]
    for name, prompt, schema, gc in trials:
        p.spec = dict(p.spec, generation_config=gc)
        t = time.time()
        try:
            text = p.complete(news_frames.RULES, [{"role": "user", "content": prompt}], schema)
            obj = llm.parse_json_text(text)
            errs = llm.validate(obj, schema)
            print(f"OK   {time.time() - t:6.1f}s  {name}  文字数={len(text)}  スキーマ不一致={len(errs)}")
            if schema is news_frames.SCHEMA:
                print("     cases:", len(obj.get("cases", [])))
        except Exception as e:  # 診断なので全部表示して続ける
            print(f"FAIL {time.time() - t:6.1f}s  {name}  {str(e)[:600]!r}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
