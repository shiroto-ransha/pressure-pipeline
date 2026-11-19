"""LLM のフォールバック連鎖（モックで 429 を返して次へ回る）とスキーマ検証。"""
import json

import pytest

import llm
import news_frames

CFG = llm.load_config()
GOOD = {"cases": []}


class Fake:
    """llm.http の代わり。URL に含まれる語で応答を決める。"""

    def __init__(self, routes):
        self.routes, self.calls = routes, []

    def __call__(self, method, url, headers, body=None, timeout=0):
        self.calls.append((method, url))
        for key, resp in self.routes.items():
            if key in url:
                r = resp.pop(0) if isinstance(resp, list) else resp
                if isinstance(r, Exception):
                    raise r
                return r
        raise AssertionError(f"想定外の呼び出し: {url}")


def chat(obj):
    return {"choices": [{"message": {"content": json.dumps(obj, ensure_ascii=False)}}]}


def gem(obj):
    return {"candidates": [{"content": {"parts": [{"text": json.dumps(obj)}]}}]}


def test_429_then_next(monkeypatch):
    """429 なら次のプロバイダーへ回る。キーのないプロバイダーは飛ばす。"""
    fake = Fake({"api.groq.com/openai/v1/chat": llm.ProviderError("HTTP 429: rate limited"),
                 "api.cerebras.ai/v1/chat": chat(GOOD)})
    monkeypatch.setattr(llm, "http", fake)
    env = {"GROQ_API_KEY": "k1", "CEREBRAS_API_KEY": "k2"}   # gemini などは未設定 → 飛ばす
    obj, label = llm.generate("sys", "prompt", news_frames.SCHEMA, env=env, cfg=CFG, verify_models=False)
    assert obj == GOOD and label.startswith("cerebras:")
    assert [u for _, u in fake.calls] == ["https://api.groq.com/openai/v1/chat/completions",
                                         "https://api.cerebras.ai/v1/chat/completions"]


def test_single_key_works(monkeypatch):
    fake = Fake({"openrouter.ai/api/v1/chat": chat(GOOD)})
    monkeypatch.setattr(llm, "http", fake)
    obj, label = llm.generate("s", "p", news_frames.SCHEMA, env={"OPENROUTER_API_KEY": "k"}, cfg=CFG, verify_models=False)
    assert label.startswith("openrouter:") and len(fake.calls) == 1


def test_single_key_429_fails_cleanly(monkeypatch):
    monkeypatch.setattr(llm, "http", Fake({"chat": llm.ProviderError("HTTP 429")}))
    with pytest.raises(llm.AllProvidersFailed, match="429"):
        llm.generate("s", "p", news_frames.SCHEMA, env={"GROQ_API_KEY": "k"}, cfg=CFG, verify_models=False)


def test_transient_retry_same_model(monkeypatch):
    monkeypatch.setattr(llm, "_sleep", lambda s: None)
    fake = Fake({"api.groq.com": [llm.ProviderError("HTTP 503: high demand"), chat(GOOD)]})
    monkeypatch.setattr(llm, "http", fake)
    _, label = llm.generate("s", "p", news_frames.SCHEMA, env={"GROQ_API_KEY": "a"}, cfg=CFG, verify_models=False)
    assert label.startswith("groq:") and len(fake.calls) == 2


def test_429_not_retried(monkeypatch):
    monkeypatch.setattr(llm, "_sleep", lambda s: None)
    fake = Fake({"api.groq.com": [llm.ProviderError("HTTP 429"), chat(GOOD)]})
    monkeypatch.setattr(llm, "http", fake)
    with pytest.raises(llm.AllProvidersFailed):
        llm.generate("s", "p", news_frames.SCHEMA, env={"GROQ_API_KEY": "a"}, cfg=CFG, verify_models=False)
    assert len(fake.calls) == 1


def test_model_list_falls_back_within_provider(monkeypatch):
    monkeypatch.setattr(llm, "_sleep", lambda s: None)
    fake = Fake({"/models/m1:": llm.ProviderError("HTTP 503"), "/models/m2:": gem(GOOD)})
    monkeypatch.setattr(llm, "http", fake)
    _, label = llm.generate("s", "p", news_frames.SCHEMA, env={"GEMINI_API_KEY": "g", "GEMINI_MODEL": "m1, m2"},
                            cfg=CFG, verify_models=False)
    assert label == "gemini:m2"
    assert [u.rsplit("/", 1)[1] for _, u in fake.calls] == ["m1:generateContent"] * 2 + ["m2:generateContent"]


def test_timeout_and_5xx_move_on(monkeypatch):
    monkeypatch.setattr(llm, "_sleep", lambda s: None)
    fake = Fake({"generativelanguage": llm.ProviderError("通信エラー: timed out"),
                 "api.groq.com": llm.ProviderError("HTTP 503"),
                 "api.cerebras.ai": chat(GOOD)})
    monkeypatch.setattr(llm, "http", fake)
    env = {"GEMINI_API_KEY": "g", "GROQ_API_KEY": "a", "CEREBRAS_API_KEY": "b"}
    _, label = llm.generate("s", "p", news_frames.SCHEMA, env=env, cfg=CFG, verify_models=False)
    assert label.startswith("cerebras:")


def test_no_keys():
    with pytest.raises(llm.AllProvidersFailed, match="未設定"):
        llm.generate("s", "p", news_frames.SCHEMA, env={}, cfg=CFG, verify_models=False)


def test_schema_repair_once_then_next(monkeypatch):
    bad = {"cases": [{"kind": "事件"}]}
    fake = Fake({"api.groq.com": [chat(bad), chat(bad)], "api.cerebras.ai": chat(GOOD)})
    monkeypatch.setattr(llm, "http", fake)
    obj, label = llm.generate("s", "p", news_frames.SCHEMA, env={"GROQ_API_KEY": "a", "CEREBRAS_API_KEY": "b"},
                              cfg=CFG, verify_models=False)
    assert label.startswith("cerebras:")
    assert sum("groq" in u for _, u in fake.calls) == 2      # 初回＋修正の再依頼1回


def test_schema_repair_succeeds(monkeypatch):
    fake = Fake({"api.groq.com": [chat({"nope": 1}), chat(GOOD)]})
    monkeypatch.setattr(llm, "http", fake)
    obj, label = llm.generate("s", "p", news_frames.SCHEMA, env={"GROQ_API_KEY": "a"}, cfg=CFG, verify_models=False)
    assert obj == GOOD and label.startswith("groq:")


def test_gemini_native_and_order_env(monkeypatch):
    fake = Fake({"generativelanguage.googleapis.com": gem(GOOD)})
    monkeypatch.setattr(llm, "http", fake)
    env = {"GEMINI_API_KEY": "g", "GEMINI_MODEL": "gemini-next-flash", "LLM_ORDER": "gemini"}
    obj, label = llm.generate("s", "p", news_frames.SCHEMA, env=env, cfg=CFG, verify_models=False)
    assert label == "gemini:gemini-next-flash"
    assert fake.calls[0][1].endswith("/models/gemini-next-flash:generateContent")


def test_gemini_generation_config_merged(monkeypatch):
    seen = {}

    def fake(method, url, headers, body=None, timeout=0):
        seen["body"], seen["timeout"] = body, timeout
        return gem(GOOD)

    monkeypatch.setattr(llm, "http", fake)
    llm.generate("s", "p", news_frames.SCHEMA, env={"GEMINI_API_KEY": "g"}, cfg=CFG, verify_models=False)
    gc = seen["body"]["generationConfig"]
    assert gc["thinkingConfig"] == {"thinkingLevel": "low"} and gc["responseMimeType"] == "application/json"
    assert seen["timeout"] == CFG["timeout_sec"]


def test_missing_model_warns_and_skips(monkeypatch):
    fake = Fake({"api.groq.com/openai/v1/models": {"data": [{"id": "other-model"}]},
                 "api.cerebras.ai/v1/models": {"data": [{"id": "gpt-oss-120b"}]},
                 "api.cerebras.ai/v1/chat": chat(GOOD)})
    monkeypatch.setattr(llm, "http", fake)
    obj, label = llm.generate("s", "p", news_frames.SCHEMA, env={"GROQ_API_KEY": "a", "CEREBRAS_API_KEY": "b"}, cfg=CFG)
    assert label == "cerebras:gpt-oss-120b"


def test_force_provider(monkeypatch):
    fake = Fake({"api.cerebras.ai": chat(GOOD)})
    monkeypatch.setattr(llm, "http", fake)
    _, label = llm.generate("s", "p", news_frames.SCHEMA, force="cerebras",
                            env={"GROQ_API_KEY": "a", "CEREBRAS_API_KEY": "b"}, cfg=CFG, verify_models=False)
    assert label.startswith("cerebras:") and all("groq" not in u for _, u in fake.calls)


def test_cloudflare_url_uses_account(monkeypatch):
    fake = Fake({"/accounts/ACC/ai/v1/chat/completions": chat(GOOD)})
    monkeypatch.setattr(llm, "http", fake)
    _, label = llm.generate("s", "p", news_frames.SCHEMA, force="cloudflare",
                            env={"CF_API_TOKEN": "t", "CF_ACCOUNT_ID": "ACC"}, cfg=CFG, verify_models=False)
    assert label.startswith("cloudflare:")


def test_validate_schema():
    case = {"kind": "事件", "symbol": "人物A", "title": "t", "summary": "s", "source_index": 0,
            "frames": [{"name": "n", "text": "t", "norm": "n", "places": ["職場"], "effect": "e",
                        "js": {"f": "observer", "t": "x"}}]}
    assert llm.validate({"cases": [case]}, news_frames.SCHEMA) == []
    bad = json.loads(json.dumps(case))
    bad["frames"][0]["places"] = ["月面"]
    bad["frames"][0]["js"]["f"] = "judge"
    bad["source_index"] = "0"
    errs = llm.validate({"cases": [bad]}, news_frames.SCHEMA)
    assert len(errs) == 3
    assert llm.validate([], news_frames.SCHEMA) == ["$: object ではありません"]


def test_to_json_schema_lowercases():
    s = llm.to_json_schema(news_frames.SCHEMA)
    assert s["type"] == "object" and s["properties"]["cases"]["items"]["properties"]["source_index"]["type"] == "integer"


def test_parse_json_text_code_fence():
    assert llm.parse_json_text('```json\n{"cases": []}\n```') == {"cases": []}
    assert llm.parse_json_text('Here: {"cases": []} done') == {"cases": []}
