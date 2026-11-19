"""無料枠の LLM を順に試すクライアント（標準ライブラリだけ）。

- Gemini はネイティブ API（responseSchema で JSON を強制）。
- それ以外（Groq / Cerebras / Cloudflare Workers AI / OpenRouter）は OpenAI 互換の Chat Completions。
- 上から順に試し、429・5xx・タイムアウト・通信障害・スキーマ不一致なら次へ回す。
  スキーマ不一致は、同じプロバイダーに1回だけ「スキーマに合わせて直して」と再依頼してから次へ。
- キーが未設定のプロバイダーは飛ばす。順番は LLM_ORDER（例: "gemini,groq"）で変える。
- モデル名はコードに固定しない（config/llm.json の default_model を、*_MODEL 環境変数で上書き）。
  起動時に各社のモデル一覧 API で存在を確かめ、なければ警告して次へ回す。

送るのは公開ニュースの見出し・URL・媒体名だけにすること（推定ツールの回答などは送らない）。
"""
from __future__ import annotations

import json
import os
import pathlib
import socket
import sys
import urllib.error
import urllib.parse
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parent
CONFIG = ROOT / "config" / "llm.json"


class ProviderError(Exception):
    """このプロバイダーでは続けられない（次へ回す）。"""


class AllProvidersFailed(Exception):
    pass


# ---------- 通信（テストで差し替える） ----------

def http(method: str, url: str, headers: dict, body: dict | None = None, timeout: int = 180) -> dict:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method, headers={"Content-Type": "application/json", **headers})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "replace")[:300] if e.fp else ""
        raise ProviderError(f"HTTP {e.code}: {detail}") from e
    except (urllib.error.URLError, socket.timeout, TimeoutError, ConnectionError) as e:
        raise ProviderError(f"通信エラー: {e}") from e
    except json.JSONDecodeError as e:
        raise ProviderError(f"応答が JSON ではありません: {e}") from e


# ---------- スキーマ ----------

def to_json_schema(s: dict) -> dict:
    """Gemini 形式（type が大文字）のスキーマを、標準の JSON Schema（小文字）に変換する。"""
    out = {}
    for k, v in s.items():
        if k == "type":
            out[k] = v.lower()
        elif k == "properties":
            out[k] = {p: to_json_schema(x) for p, x in v.items()}
        elif k == "items":
            out[k] = to_json_schema(v)
        else:
            out[k] = v
    return out


def validate(obj, schema: dict, path: str = "$") -> list[str]:
    """このプロジェクトで使う範囲（object/array/string/integer/number/boolean、required、enum）の検証。

    schema は Gemini 形式でも標準形式でもよい。エラーの一覧を返す（空なら合格）。
    """
    errs: list[str] = []
    t = (schema.get("type") or "").lower()
    checks = {"object": lambda x: isinstance(x, dict), "array": lambda x: isinstance(x, list),
              "string": lambda x: isinstance(x, str), "boolean": lambda x: isinstance(x, bool),
              "integer": lambda x: isinstance(x, int) and not isinstance(x, bool),
              "number": lambda x: isinstance(x, (int, float)) and not isinstance(x, bool)}
    if t and t in checks and not checks[t](obj):
        return [f"{path}: {t} ではありません"]
    if "enum" in schema and obj not in schema["enum"]:
        errs.append(f"{path}: {obj!r} は許可された値ではありません")
    if t == "object":
        for r in schema.get("required", []):
            if r not in obj:
                errs.append(f"{path}.{r}: 必須の項目がありません")
        for k, sub in schema.get("properties", {}).items():
            if k in obj:
                errs += validate(obj[k], sub, f"{path}.{k}")
    if t == "array" and "items" in schema:
        for i, x in enumerate(obj):
            errs += validate(x, schema["items"], f"{path}[{i}]")
    return errs


def parse_json_text(text: str):
    text = (text or "").strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1] if "\n" in text else text
        text = text.rsplit("```", 1)[0]
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        if "{" in text and "}" in text:
            return json.loads(text[text.index("{"): text.rindex("}") + 1])
        raise


# ---------- プロバイダー ----------

def load_config() -> dict:
    return json.loads(CONFIG.read_text(encoding="utf-8"))


def _fill(template: str, env: dict, model: str = "") -> str:
    vals = {k: env.get(k, "") for k in ("CF_ACCOUNT_ID",)}
    vals["model_tail"] = urllib.parse.quote(model.rsplit("/", 1)[-1])
    return template.format(**vals)


class Provider:
    def __init__(self, name: str, spec: dict, env: dict, timeout: int, temperature: float):
        self.name, self.spec, self.env = name, spec, env
        self.kind = spec["kind"]
        self.key = env.get(spec["key_env"], "")
        self.model = env.get(spec["model_env"]) or spec.get("default_model", "")
        self.base = _fill(env.get(spec.get("base_env", ""), "") or spec["base_url"], env).rstrip("/")
        self.timeout, self.temperature = timeout, temperature

    @property
    def label(self) -> str:
        return f"{self.name}:{self.model}"

    def available(self) -> str | None:
        """使えない理由（使えるなら None）。"""
        if not self.key:
            return f"{self.spec['key_env']} が未設定"
        missing = [e for e in self.spec.get("requires_env", []) if not self.env.get(e)]
        if missing:
            return f"{', '.join(missing)} が未設定"
        if not self.model:
            return f"{self.spec['model_env']} が未設定"
        return None

    def _headers(self) -> dict:
        if self.kind == "gemini":
            return {"x-goog-api-key": self.key}
        h = {"Authorization": f"Bearer {self.key}"}
        if self.name == "openrouter":
            h["X-Title"] = "pressure-pipeline"
        return h

    def model_exists(self) -> bool | None:
        """モデル一覧 API で確かめる。True/False、確かめられなければ None。"""
        try:
            if self.kind == "gemini":
                names, token = set(), ""
                for _ in range(10):
                    q = "?pageSize=1000" + (f"&pageToken={token}" if token else "")
                    res = http("GET", f"{self.base}/models{q}", self._headers(), timeout=30)
                    names |= {m.get("name", "").removeprefix("models/") for m in res.get("models", [])}
                    token = res.get("nextPageToken")
                    if not token:
                        break
                return self.model.removeprefix("models/") in names
            if "models_url" in self.spec:   # Cloudflare
                res = http("GET", _fill(self.spec["models_url"], self.env, self.model), self._headers(), timeout=30)
                return any(m.get("name") == self.model for m in res.get("result", []))
            res = http("GET", f"{self.base}/models", self._headers(), timeout=30)
            return any(m.get("id") == self.model for m in res.get("data", []))
        except ProviderError as e:
            print(f"  [{self.name}] モデル一覧を取得できません（{e}）。確認せずに続けます。", file=sys.stderr)
            return None

    def complete(self, system: str, messages: list[dict], schema: dict) -> str:
        """JSON のテキストを返す。messages は [{"role": "user"|"assistant", "content": str}]。"""
        if self.kind == "gemini":
            body = {"contents": [{"role": "model" if m["role"] == "assistant" else "user", "parts": [{"text": m["content"]}]}
                                 for m in messages],
                    "systemInstruction": {"parts": [{"text": system}]},
                    "generationConfig": {"responseMimeType": "application/json", "responseSchema": schema,
                                         "temperature": self.temperature}}
            res = http("POST", f"{self.base}/models/{self.model}:generateContent", self._headers(), body, self.timeout)
            try:
                return res["candidates"][0]["content"]["parts"][0]["text"]
            except (KeyError, IndexError) as e:
                raise ProviderError(f"応答に本文がありません: {str(res)[:200]}") from e
        sys_text = system + "\n\n出力は次の JSON Schema に従う JSON オブジェクトだけにすること（説明文やコードブロックは付けない）:\n" + \
            json.dumps(to_json_schema(schema), ensure_ascii=False)
        body = {"model": self.model, "temperature": self.temperature, "response_format": {"type": "json_object"},
                "messages": [{"role": "system", "content": sys_text}] + messages}
        res = http("POST", f"{self.base}/chat/completions", self._headers(), body, self.timeout)
        try:
            return res["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as e:
            raise ProviderError(f"応答に本文がありません: {str(res)[:200]}") from e


def providers(order: list[str] | None = None, env: dict | None = None, cfg: dict | None = None) -> list[Provider]:
    cfg = cfg or load_config()
    env = dict(os.environ) if env is None else env
    names = order or [x.strip() for x in env.get("LLM_ORDER", "").split(",") if x.strip()] or cfg["order"]
    out = []
    for n in names:
        if n not in cfg["providers"]:
            print(f"  [{n}] 不明なプロバイダーです。飛ばします。", file=sys.stderr)
            continue
        out.append(Provider(n, cfg["providers"][n], env, int(cfg.get("timeout_sec", 180)), float(cfg.get("temperature", 0.4))))
    return out


def generate(system: str, prompt: str, schema: dict, *, force: str | None = None, check=None,
             env: dict | None = None, cfg: dict | None = None, verify_models: bool = True) -> tuple[dict, str]:
    """順に試して、スキーマに合う JSON（dict）と、生成したプロバイダーのラベルを返す。

    check(obj) -> list[str] を渡すと、スキーマ以外の検査（不合格なら再依頼の対象）も行う。
    """
    chain = providers([force] if force else None, env=env, cfg=cfg)
    log = []
    for p in chain:
        why = p.available()
        if why:
            log.append(f"{p.name}: 飛ばす（{why}）")
            continue
        if verify_models and p.model_exists() is False:
            msg = f"{p.name}: モデル {p.model} が一覧にありません。{p.spec['model_env']} を見直してください。"
            print(f"  警告: {msg}", file=sys.stderr)
            log.append(msg)
            continue
        messages = [{"role": "user", "content": prompt}]
        for attempt in range(2):   # 初回＋スキーマ修正の再依頼1回
            try:
                text = p.complete(system, messages, schema)
            except ProviderError as e:
                log.append(f"{p.label}: {e}")
                print(f"  [{p.label}] 失敗: {e}。次へ回します。", file=sys.stderr)
                break
            try:
                obj = parse_json_text(text)
                errs = validate(obj, schema) + (check(obj) if check else [])
            except (json.JSONDecodeError, ValueError) as e:
                obj, errs = None, [f"JSON として読めません: {e}"]
            if not errs:
                return obj, p.label
            log.append(f"{p.label}: スキーマ不一致（{'; '.join(errs[:3])}）")
            print(f"  [{p.label}] スキーマ不一致: {errs[:3]}", file=sys.stderr)
            if attempt == 0:
                messages = messages + [{"role": "assistant", "content": text[:20000]},
                                       {"role": "user", "content": "出力が指定のスキーマに合っていません。次の問題を直し、"
                                        "スキーマに合わせた JSON だけを出し直してください:\n- " + "\n- ".join(errs[:20])}]
    raise AllProvidersFailed("すべてのプロバイダーで失敗しました:\n  " + "\n  ".join(log))
