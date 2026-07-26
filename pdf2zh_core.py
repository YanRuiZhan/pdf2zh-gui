# -*- coding: utf-8 -*-
"""Non-GUI core for pdf2zh-gui: services, HTTP, prefs, prompts, runtime patches.

Kept free of tkinter imports so it can be unit-tested headlessly.
"""
import base64
import json
import logging
import os
import re
import time
from pathlib import Path
from string import Template
from urllib.parse import urlsplit

__version__ = "1.1.0"

os.environ.setdefault("PYTHONUTF8", "1")
os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")

CONFIG_PATH = Path.home() / ".config" / "PDFMathTranslate" / "config.json"
GUI_SERVICES_PATH = CONFIG_PATH.with_name("gui_services.json")
GUI_PREFS_PATH = CONFIG_PATH.with_name("gui_prefs.json")
DEFAULT_GUI_PREFS_PATH = Path(__file__).with_name("default_gui_prefs.json")

REPO_OWNER = "YanRuiZhan"
REPO_NAME = "pdf2zh-gui"
REPO_URL = f"https://github.com/{REPO_OWNER}/{REPO_NAME}"

LANGS = {
    "English": "en",
    "简体中文": "zh",
    "繁體中文": "zh-TW",
    "日本語": "ja",
    "한국어": "ko",
    "Français": "fr",
    "Deutsch": "de",
    "Español": "es",
    "Русский": "ru",
}
OUTPUT_MODES = {
    "双语 + 中文": "both",
    "仅双语": "dual",
    "仅中文": "mono",
}
SERVICE_PLACEHOLDER = "请添加服务"
QA_HISTORY_LIMIT = 10

DEFAULT_NOTES = "保留所有英文人名、地名不翻译"

# $lang_in/$lang_out/$text are substituted by pdf2zh; $notes by the GUI
PROMPT_WITH_NOTES = Template(
    "You are a professional, authentic machine translation engine. "
    "Only Output the translated text, do not include any other text."
    "\n\nTranslation notes (follow strictly):\n$notes\n\n"
    "Translate the following markdown source text to $lang_out. "
    "Keep the formula notation {v*} unchanged. "
    "Output translation directly without any additional text."
    "\n\nSource Text: $text\n\nTranslated Text:"
)


def build_prompt_template(notes: str):
    """-> Template for pdf2zh, or None to use its stock prompt."""
    notes = (notes or "").strip()
    if not notes:
        return None
    lines = "\n".join(
        f"- {ln.strip()}" for ln in notes.splitlines() if ln.strip()
    )
    return Template(PROMPT_WITH_NOTES.safe_substitute({"notes": lines}))


# (env_key, label, required, secret, default)
SERVICE_SCHEMAS = {
    "openailiked": [
        ("OPENAILIKED_BASE_URL", "Base URL", True, False, "https://"),
        ("OPENAILIKED_API_KEY", "API Key", False, True, ""),
        ("OPENAILIKED_MODEL", "模型名称", True, False, ""),
    ],
    "anthropic": [
        ("OPENAILIKED_BASE_URL", "Base URL", True, False, "https://api.anthropic.com/v1/"),
        ("OPENAILIKED_API_KEY", "API Key", True, True, ""),
        ("OPENAILIKED_MODEL", "模型名称", True, False, "claude-sonnet-5"),
    ],
    "claudeliked": [
        ("OPENAILIKED_BASE_URL", "Base URL", True, False, "https://"),
        ("OPENAILIKED_API_KEY", "API Key", False, True, ""),
        ("OPENAILIKED_MODEL", "模型名称", True, False, "claude-sonnet-5"),
    ],
    "openai": [
        ("OPENAI_BASE_URL", "Base URL", False, False, "https://api.openai.com/v1"),
        ("OPENAI_API_KEY", "API Key", True, True, ""),
        ("OPENAI_MODEL", "模型名称", False, False, "gpt-4o-mini"),
    ],
    "deepseek": [
        ("DEEPSEEK_API_KEY", "API Key", True, True, ""),
        ("DEEPSEEK_MODEL", "模型名称", False, False, "deepseek-chat"),
    ],
    "gemini": [
        ("GEMINI_API_KEY", "API Key", True, True, ""),
        ("GEMINI_MODEL", "模型名称", False, False, "gemini-2.0-flash"),
    ],
    "zhipu": [
        ("ZHIPU_API_KEY", "API Key", True, True, ""),
        ("ZHIPU_MODEL", "模型名称", False, False, "glm-4-flash"),
    ],
    "silicon": [
        ("SILICON_API_KEY", "API Key", True, True, ""),
        ("SILICON_MODEL", "模型名称", False, False, "Qwen/Qwen2.5-7B-Instruct"),
    ],
    "grok": [
        ("GROK_API_KEY", "API Key", True, True, ""),
        ("GROK_MODEL", "模型名称", False, False, "grok-3"),
    ],
    "groq": [
        ("GROQ_API_KEY", "API Key", True, True, ""),
        ("GROQ_MODEL", "模型名称", False, False, "llama-3.3-70b-versatile"),
    ],
    "ollama": [
        ("OLLAMA_HOST", "服务地址", False, False, "http://127.0.0.1:11434"),
        ("OLLAMA_MODEL", "模型名称", True, False, "gemma2"),
    ],
    "azure-openai": [
        ("AZURE_OPENAI_BASE_URL", "Base URL", True, False, "https://xxx.openai.azure.com"),
        ("AZURE_OPENAI_API_KEY", "API Key", True, True, ""),
        ("AZURE_OPENAI_MODEL", "部署/模型名", False, False, "gpt-4o-mini"),
        ("AZURE_OPENAI_API_VERSION", "API 版本", False, False, "2024-06-01"),
    ],
}
SERVICE_TYPE_LABELS = {
    "openai": "OpenAI 官方",
    "anthropic": "Claude 官方",
    "openailiked": "OpenAI 兼容",
    "claudeliked": "Claude 兼容",
    "deepseek": "DeepSeek",
    "gemini": "Google Gemini",
    "zhipu": "智谱 GLM",
    "silicon": "硅基流动 SiliconFlow",
    "grok": "xAI Grok",
    "groq": "Groq",
    "ollama": "Ollama 本地",
    "azure-openai": "Azure OpenAI",
}
TYPE_LABEL_TO_KEY = {v: k for k, v in SERVICE_TYPE_LABELS.items()}

# GUI type -> pdf2zh service name (anthropic rides the OpenAI-compatible channel)
SERVICE_BACKEND = {"anthropic": "openailiked", "claudeliked": "openailiked"}

# fixed REST bases for providers whose translator hardcodes the URL
PROVIDER_FIXED_BASE = {
    "deepseek": "https://api.deepseek.com/v1",
    "gemini": "https://generativelanguage.googleapis.com/v1beta/openai/",
    "zhipu": "https://open.bigmodel.cn/api/paas/v4",
    "silicon": "https://api.siliconflow.cn/v1",
    "grok": "https://api.x.ai/v1",
    "groq": "https://api.groq.com/openai/v1",
}

# Services where we smart-complete the base URL (openailiked-style)
_BASE_SUFFIX_HINTS = {"openailiked": "/v1", "anthropic": "/v1", "claudeliked": "/v1"}


def _normalize_base_url(raw: str, stype: str = "openailiked") -> str:
    """Smart-complete partial URLs to the correct chat-completion root.

    Handles:
      "https://api.anthropic.com"       → "https://api.anthropic.com/v1"
      "https://api.anthropic.com/"      → "https://api.anthropic.com/v1"
      "https://api.anthropic.com/v1"    → "https://api.anthropic.com/v1"
      "http://localhost:8000"           → "http://localhost:8000/v1"
    """
    url = (raw or "").strip().rstrip("/")
    if not url:
        return (raw or "").strip()
    if stype in _BASE_SUFFIX_HINTS:
        suffix = _BASE_SUFFIX_HINTS[stype]
        # Only inspect the URL path. Domains such as api.longcat.chat should
        # not count as an existing /api segment.
        path = urlsplit(url).path.rstrip("/")
        for pat in ("/v1", "/v2", "/api", "/v1beta"):
            if path == pat or path.startswith(pat + "/") or path.endswith(pat):
                return url
        return url + suffix
    if stype in PROVIDER_FIXED_BASE and "/" not in url.split("://")[-1]:
        return PROVIDER_FIXED_BASE[stype]
    return url


def _api_target(stype: str, envs: dict):
    """-> (base_url, headers) for direct REST calls (test / list models / chat)."""
    envs = envs or {}

    def need(key, label):
        v = (envs.get(key) or "").strip()
        if not v:
            raise ValueError(f"请先填写 {label}")
        return v

    if stype == "ollama":
        base = (envs.get("OLLAMA_HOST") or "http://127.0.0.1:11434").strip()
        return base.rstrip("/"), {}
    if stype == "azure-openai":
        return (
            need("AZURE_OPENAI_BASE_URL", "Base URL").rstrip("/"),
            {"api-key": need("AZURE_OPENAI_API_KEY", "API Key")},
        )
    if stype in ("openailiked", "anthropic", "claudeliked"):
        raw = need("OPENAILIKED_BASE_URL", "Base URL")
        base = _normalize_base_url(raw, stype).rstrip("/")
        key = (envs.get("OPENAILIKED_API_KEY") or "").strip()
        headers = {"Authorization": f"Bearer {key}"} if key else {}
        if stype in ("anthropic", "claudeliked") or is_anthropic_style(stype, base):
            if not key:
                raise ValueError("请先填写 API Key")
            headers["x-api-key"] = key
            headers["anthropic-version"] = "2023-06-01"
        return base, headers
    base = (
        PROVIDER_FIXED_BASE.get(stype)
        or (envs.get("OPENAI_BASE_URL") or "").strip()
        or "https://api.openai.com/v1"
    ).rstrip("/")
    key = need(f"{stype.upper().replace('-', '_')}_API_KEY", "API Key")
    return base, {"Authorization": f"Bearer {key}"}


# ---------------------------------------------------------------- proxy ----
PROXY_MODES = {"跟随系统": "system", "直接连接": "direct", "自定义": "custom"}
PROXY_MODE_LABELS = {v: k for k, v in PROXY_MODES.items()}
_PROXY_ENV_KEYS = (
    "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY",
    "http_proxy", "https_proxy", "all_proxy",
)
_ORIGINAL_PROXY_ENV = {k: os.environ.get(k) for k in _PROXY_ENV_KEYS}
_proxy_settings = {"mode": "system", "url": ""}


def set_proxy_settings(mode: str, url: str = "", *, apply_env: bool = True):
    """Configure how every outbound request reaches the network.

    system  - honour HTTP(S)_PROXY / system settings (default)
    direct  - bypass any proxy
    custom  - force the given proxy URL, also for pdf2zh's own SDK calls
    """
    mode = mode if mode in ("system", "direct", "custom") else "system"
    url = (url or "").strip()
    if mode == "custom" and not url:
        mode = "system"
    _proxy_settings["mode"] = mode
    _proxy_settings["url"] = url
    if apply_env:
        apply_proxy_env()


def get_proxy_settings() -> dict:
    return dict(_proxy_settings)


def resolve_proxy():
    """-> (proxy_url_or_None, trust_env) for httpx."""
    mode = _proxy_settings["mode"]
    if mode == "direct":
        return None, False
    if mode == "custom" and _proxy_settings["url"]:
        return _proxy_settings["url"], False
    return None, True


def apply_proxy_env():
    """Mirror the proxy choice into os.environ so pdf2zh/openai follow it too."""
    mode = _proxy_settings["mode"]
    if mode == "custom" and _proxy_settings["url"]:
        for key in _PROXY_ENV_KEYS:
            os.environ[key] = _proxy_settings["url"]
    elif mode == "direct":
        for key in _PROXY_ENV_KEYS:
            os.environ.pop(key, None)
    else:
        for key, value in _ORIGINAL_PROXY_ENV.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def _http(timeout: int):
    """httpx Client honouring the configured proxy mode."""
    import httpx

    proxy, trust_env = resolve_proxy()
    kwargs = dict(timeout=timeout, follow_redirects=True, trust_env=trust_env)
    if proxy:
        try:
            return httpx.Client(proxy=proxy, **kwargs)
        except TypeError:  # httpx < 0.26 spells it "proxies"
            return httpx.Client(proxies=proxy, **kwargs)
        except Exception:
            pass
    return httpx.Client(**kwargs)


def _network_hint(timeout: int) -> str:
    return (
        f"请求超过 {timeout} 秒未响应，请检查：\n"
        f"① 网络能否访问该服务地址\n"
        f"② API Key 是否正确\n"
        f"③ 需要代理时，在「翻译设置 → 网络代理」中配置"
    )


# ----------------------------------------------------------- chat layer ----
DEFAULT_MAX_TOKENS = 4096
_RETRY_STATUS = {408, 409, 425, 429, 500, 502, 503, 504}
_translate_max_tokens = DEFAULT_MAX_TOKENS


def set_translate_max_tokens(value):
    """Upper bound for a single translated chunk on the Anthropic path."""
    global _translate_max_tokens
    try:
        _translate_max_tokens = max(256, min(32000, int(value)))
    except (TypeError, ValueError):
        _translate_max_tokens = DEFAULT_MAX_TOKENS


def get_translate_max_tokens() -> int:
    return _translate_max_tokens


def is_anthropic_style(stype: str, base: str) -> bool:
    """True when the endpoint speaks Anthropic's /messages protocol."""
    base = base or ""
    return (
        stype in ("anthropic", "claudeliked")
        or "/anthropic" in base
        or "anthropic.com" in base
    )


def extract_message(data) -> str:
    """Pull assistant text out of an OpenAI / Anthropic / Ollama response."""
    if not isinstance(data, dict):
        return ""
    if "choices" in data:
        choice = (data.get("choices") or [{}])[0] or {}
        message = choice.get("message") or {}
        return str(message.get("content") or choice.get("text") or "").strip()
    parts = data.get("content")
    if isinstance(parts, str):
        return parts.strip()
    if isinstance(parts, list):
        return "".join(
            p.get("text", "") if isinstance(p, dict) else str(p) for p in parts
        ).strip()
    message = data.get("message")
    if isinstance(message, dict):  # ollama
        return str(message.get("content") or "").strip()
    return ""


def _sleep_backoff(attempt: int):
    time.sleep(min(8.0, 1.2 * (2 ** attempt)))


def chat(
    stype: str,
    envs: dict,
    model: str,
    messages: list,
    *,
    system: str | None = None,
    max_tokens: int = 1024,
    temperature: float = 0,
    timeout: int = 30,
    retries: int = 2,
) -> str:
    """One entry point for every direct LLM call (test / lookup / Q&A).

    Retries transient failures with exponential backoff and speaks whichever
    protocol the endpoint expects.
    """
    import httpx

    if not model:
        raise ValueError("请先填写模型名称")
    base, headers = _api_target(stype, envs)
    envs = envs or {}
    last_error: Exception | None = None

    for attempt in range(max(0, retries) + 1):
        try:
            with _http(timeout) as client:
                if stype == "ollama":
                    body = list(messages)
                    if system:
                        body = [{"role": "system", "content": system}] + body
                    resp = client.post(
                        f"{base}/api/chat",
                        json={
                            "model": model,
                            "stream": False,
                            "messages": body,
                            "options": {
                                "temperature": temperature,
                                "num_predict": max_tokens,
                            },
                        },
                    )
                elif stype == "azure-openai":
                    ver = (envs.get("AZURE_OPENAI_API_VERSION") or "2024-06-01").strip()
                    body = list(messages)
                    if system:
                        body = [{"role": "system", "content": system}] + body
                    resp = client.post(
                        f"{base}/openai/deployments/{model}/chat/completions"
                        f"?api-version={ver}",
                        headers=headers,
                        json={
                            "model": model, "messages": body,
                            "max_tokens": max_tokens, "temperature": temperature,
                        },
                    )
                elif is_anthropic_style(stype, base):
                    payload = {
                        "model": model, "messages": list(messages),
                        "max_tokens": max_tokens, "temperature": temperature,
                    }
                    if system:
                        payload["system"] = system
                    resp = client.post(f"{base}/messages", headers=headers, json=payload)
                    if resp.status_code in (404, 405):
                        # gateway only exposes the OpenAI-shaped route
                        body = list(messages)
                        if system:
                            body = [{"role": "system", "content": system}] + body
                        resp = client.post(
                            f"{base}/chat/completions", headers=headers,
                            json={
                                "model": model, "messages": body,
                                "max_tokens": max_tokens, "temperature": temperature,
                            },
                        )
                else:
                    body = list(messages)
                    if system:
                        body = [{"role": "system", "content": system}] + body
                    resp = client.post(
                        f"{base}/chat/completions", headers=headers,
                        json={
                            "model": model, "messages": body,
                            "max_tokens": max_tokens, "temperature": temperature,
                        },
                    )

                if resp.status_code in _RETRY_STATUS and attempt < retries:
                    last_error = ValueError(
                        f"HTTP {resp.status_code}：{resp.text[:160]}"
                    )
                    _sleep_backoff(attempt)
                    continue
                if resp.status_code >= 400:
                    raise ValueError(f"HTTP {resp.status_code}：{resp.text[:160]}")
                return extract_message(resp.json())
        except httpx.TimeoutException as exc:
            last_error = TimeoutError(_network_hint(timeout))
            if attempt >= retries:
                raise last_error from exc
            _sleep_backoff(attempt)
        except httpx.TransportError as exc:
            last_error = ConnectionError(
                f"网络连接失败：{exc}\n\n"
                f"需要代理时，请在「翻译设置 → 网络代理」中配置。"
            )
            if attempt >= retries:
                raise last_error from exc
            _sleep_backoff(attempt)

    raise last_error or RuntimeError("请求失败")


def fetch_models(stype: str, envs: dict) -> list[str]:
    if stype == "azure-openai":
        raise ValueError("Azure 使用部署名，请到 Azure 门户查看")
    import httpx

    base, headers = _api_target(stype, envs)
    try:
        with _http(12) as c:
            if stype == "ollama":
                r = c.get(f"{base}/api/tags")
                r.raise_for_status()
                return sorted(m["name"] for m in r.json().get("models", []))
            # Anthropic-native endpoints also answer GET <base>/models
            r = c.get(f"{base}/models", headers=headers)
            if r.status_code >= 400:
                raise ValueError(f"HTTP {r.status_code}：{r.text[:160]}")
            data = r.json()
            rows = data.get("data") or data.get("models") or []
            ids = [m.get("id", "") for m in rows if isinstance(m, dict) and m.get("id")]
            ids = [i[7:] if i.startswith("models/") else i for i in ids]
            if not ids:
                raise ValueError("接口未返回任何模型")
            return sorted(set(ids))
    except httpx.TimeoutException:
        raise TimeoutError(_network_hint(12))
    except httpx.TransportError as exc:
        raise ConnectionError(
            f"网络连接失败：{exc}\n\n需要代理时，请在「翻译设置 → 网络代理」中配置。"
        )


def test_service(stype: str, envs: dict, model: str, timeout: int = 12) -> str:
    """Minimal 1-token round-trip used by the 「测试连接」 button."""
    import httpx

    if stype == "ollama":
        base, _ = _api_target(stype, envs)
        try:
            with _http(timeout) as c:
                r = c.get(f"{base}/api/tags")
                r.raise_for_status()
                names = [m["name"] for m in r.json().get("models", [])]
        except httpx.TimeoutException:
            raise TimeoutError(_network_hint(timeout))
        if model and model not in names and f"{model}:latest" not in names:
            raise ValueError(
                f"服务已连通，但本地没有模型 {model}"
                f"（已装：{', '.join(names[:6]) or '无'}）"
            )
        return "Ollama 连接正常" + (f"，模型 {model} 可用" if model else "")

    chat(
        stype, envs, model,
        [{"role": "user", "content": "hi"}],
        max_tokens=16, timeout=timeout, retries=1,
    )
    return f"连接成功，{model} 响应正常"


def quick_translate(stype: str, envs: dict, model: str, text: str,
                    timeout: int = 25, *, target_language: str = "简体中文") -> str:
    """One-shot dictionary-style lookup via the selected AI service."""
    target_language = (target_language or "").strip() or "简体中文"
    ask = (
        "你是准确、简洁的多语言词典助手。解释下面的单词或短语：\n"
        f"- 目标语言是{target_language}；释义、说明和例句翻译都必须使用该语言\n"
        "- 查询词不是目标语言时，给出目标语言对应表达\n"
        "- 查询词已是目标语言时，用目标语言解释，并按需补充常见英文表达\n"
        "- 如有通行音标，给出音标和词性；最多列出 3 个常用义项\n"
        "- 若是专业术语，补充一句领域内含义\n"
        "直接输出 Markdown 结果，不要寒暄，保持简洁。\n\n"
        f"查询：{text}"
    )
    return chat(
        stype, envs, model, [{"role": "user", "content": ask}],
        max_tokens=700, temperature=0, timeout=timeout,
    )


QA_SYSTEM_PROMPT = (
    "你是文献阅读时使用的快问快答助手。回答用户临时提出的小问题，"
    "重点是准确、简洁、直接，不要寒暄。若问题和论文、学术概念、"
    "英文表达或技术术语有关，优先给出面向阅读理解的解释。"
    "不确定时明确说明不确定，不要编造。"
)


def quick_ask(stype: str, envs: dict, model: str, question: str,
              history: list, timeout: int = 35) -> str:
    """Short literature-reading Q&A via the selected AI service."""
    question = (question or "").strip()
    if not question:
        raise ValueError("请输入要提问的内容")
    turns = []
    for item in (history or [])[-QA_HISTORY_LIMIT:]:
        prev_q = str(item.get("question", "")).strip()
        prev_a = str(item.get("answer", "")).strip()
        if not prev_q or not prev_a:
            continue
        turns.append({"role": "user", "content": prev_q[:1200]})
        turns.append({"role": "assistant", "content": prev_a[:1600]})
    turns.append({"role": "user", "content": question})
    return chat(
        stype, envs, model, turns, system=QA_SYSTEM_PROMPT,
        max_tokens=900, temperature=0.2, timeout=timeout,
    )


# ------------------------------------------------- secrets at rest (DPAPI) --
_ENC_PREFIX = "enc:v1:"
_ENTROPY = b"pdf2zh-gui/service-secrets"


def _dpapi(func_name: str, raw: bytes):
    """Call CryptProtectData / CryptUnprotectData; None when unavailable."""
    if os.name != "nt":
        return None
    try:
        import ctypes
        from ctypes import wintypes

        class _Blob(ctypes.Structure):
            _fields_ = [
                ("cbData", wintypes.DWORD),
                ("pbData", ctypes.POINTER(ctypes.c_char)),
            ]

        buf_in = ctypes.create_string_buffer(raw, len(raw))
        buf_ent = ctypes.create_string_buffer(_ENTROPY, len(_ENTROPY))
        blob_in = _Blob(len(raw), ctypes.cast(buf_in, ctypes.POINTER(ctypes.c_char)))
        blob_ent = _Blob(
            len(_ENTROPY), ctypes.cast(buf_ent, ctypes.POINTER(ctypes.c_char))
        )
        blob_out = _Blob()
        fn = getattr(ctypes.windll.crypt32, func_name)
        ok = fn(
            ctypes.byref(blob_in), None, ctypes.byref(blob_ent),
            None, None, 0, ctypes.byref(blob_out),
        )
        if not ok:
            return None
        try:
            return ctypes.string_at(blob_out.pbData, blob_out.cbData)
        finally:
            ctypes.windll.kernel32.LocalFree(blob_out.pbData)
    except Exception:
        return None


def encrypt_secret(text: str) -> str:
    """DPAPI-protect a secret; returns the plaintext if that is not possible."""
    if not text or text.startswith(_ENC_PREFIX):
        return text
    blob = _dpapi("CryptProtectData", text.encode("utf-8"))
    if not blob:
        return text
    return _ENC_PREFIX + base64.b64encode(blob).decode("ascii")


def decrypt_secret(text: str) -> str:
    if not text or not text.startswith(_ENC_PREFIX):
        return text
    try:
        blob = base64.b64decode(text[len(_ENC_PREFIX):].encode("ascii"))
    except Exception:
        return ""
    raw = _dpapi("CryptUnprotectData", blob)
    if raw is None:
        return ""
    return raw.decode("utf-8", "replace")


def _secret_keys(stype: str) -> set:
    schema = SERVICE_SCHEMAS.get(stype, [])
    return {key for key, _label, _req, secret, _default in schema if secret}


# -------------------------------------------------------------- storage ----
def load_prefs() -> dict:
    prefs = {}
    try:
        prefs.update(json.loads(DEFAULT_GUI_PREFS_PATH.read_text(encoding="utf-8")))
    except Exception:
        pass
    try:
        prefs.update(json.loads(GUI_PREFS_PATH.read_text(encoding="utf-8")))
    except Exception:
        pass
    return prefs


def save_prefs(prefs: dict):
    try:
        GUI_PREFS_PATH.parent.mkdir(parents=True, exist_ok=True)
        GUI_PREFS_PATH.write_text(
            json.dumps(prefs, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    except Exception:
        pass


def load_config() -> dict:
    try:
        return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def load_profiles() -> list:
    """User-defined AI services: decrypt secrets, normalize stored base URLs."""
    try:
        data = json.loads(GUI_SERVICES_PATH.read_text(encoding="utf-8"))
    except Exception:
        return []
    result = []
    for p in data if isinstance(data, list) else []:
        if not (p.get("display") and p.get("type") in SERVICE_SCHEMAS):
            continue
        stype = p["type"]
        envs = dict(p.get("envs") or {})
        secrets = _secret_keys(stype)
        for k, v in list(envs.items()):
            if not isinstance(v, str) or not v:
                continue
            if k in secrets or k.endswith("API_KEY"):
                envs[k] = decrypt_secret(v)
            elif k.endswith("BASE_URL") or k.endswith("HOST"):
                envs[k] = _normalize_base_url(v, stype)
        p["envs"] = envs
        result.append(p)
    return result


def save_profiles(profiles: list):
    """Persist services with API keys DPAPI-protected (user-scoped)."""
    payload = []
    for p in profiles:
        stype = p.get("type", "")
        secrets = _secret_keys(stype)
        envs = {}
        for k, v in (p.get("envs") or {}).items():
            if isinstance(v, str) and v and (k in secrets or k.endswith("API_KEY")):
                envs[k] = encrypt_secret(v)
            else:
                envs[k] = v
        payload.append({**p, "envs": envs})
    GUI_SERVICES_PATH.parent.mkdir(parents=True, exist_ok=True)
    GUI_SERVICES_PATH.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    try:  # best effort: keep the file readable only by the current user
        if os.name == "nt":
            os.chmod(GUI_SERVICES_PATH, 0o600)
    except Exception:
        pass


def parse_pages(text: str):
    """'1,3,5-8' (1-based) -> [0,2,4,5,6,7]; empty/全部 -> None."""
    text = (text or "").strip()
    if not text or text in ("全部", "all", "ALL"):
        return None
    pages = []
    for part in text.split(","):
        part = part.strip()
        if not part:
            continue
        m = re.fullmatch(r"(\d+)\s*-\s*(\d+)", part)
        if m:
            a, b = int(m.group(1)), int(m.group(2))
            if a < 1 or b < a:
                raise ValueError(part)
            pages.extend(range(a - 1, b))
        elif part.isdigit() and int(part) >= 1:
            pages.append(int(part) - 1)
        else:
            raise ValueError(part)
    return sorted(set(pages))


def select_outputs(output_mode: str) -> tuple:
    """Which artifacts to keep for a given 输出文件 choice."""
    return {
        "both": ("dual", "mono"),
        "dual": ("dual",),
        "mono": ("mono",),
    }.get(output_mode, ("dual", "mono"))


# ------------------------------------------------------- runtime patches ----
def patch_pdf2zh_runtime():
    """Runtime compatibility patches kept local to this desktop GUI."""
    try:
        import requests
        from pdf2zh import pdfinterp, translator
    except Exception:
        return

    cls = getattr(translator, "OpenAITranslator", None)
    if cls is not None and not getattr(cls, "_pdf2zh_gui_messages_patch", False):
        orig_init = cls.__init__
        orig_do_translate = cls.do_translate

        def patched_init(
            self, lang_in, lang_out, model, base_url=None, api_key=None,
            envs=None, prompt=None, ignore_cache=False,
        ):
            orig_init(
                self, lang_in, lang_out, model, base_url=base_url,
                api_key=api_key, envs=envs, prompt=prompt,
                ignore_cache=ignore_cache,
            )
            raw_base = (
                base_url
                or getattr(self, "_base_url", "")
                or getattr(self, "envs", {}).get("OPENAI_BASE_URL")
                or getattr(self, "envs", {}).get("OPENAILIKED_BASE_URL")
                or ""
            )
            raw_key = (
                api_key
                or getattr(self, "_api_key", "")
                or getattr(self, "envs", {}).get("OPENAI_API_KEY")
                or getattr(self, "envs", {}).get("OPENAILIKED_API_KEY")
                or ""
            )
            self._pdf2zh_gui_base_url = str(raw_base).rstrip("/")
            self._pdf2zh_gui_api_key = str(raw_key)
            self._pdf2zh_gui_use_messages = bool(
                self._pdf2zh_gui_base_url
                and (
                    "/anthropic" in self._pdf2zh_gui_base_url
                    or "anthropic.com" in self._pdf2zh_gui_base_url
                )
            )

        def native_messages_translate(self, text):
            key = getattr(self, "_pdf2zh_gui_api_key", "")
            base = getattr(self, "_pdf2zh_gui_base_url", "").rstrip("/")
            payload = {
                "model": self.model,
                "max_tokens": get_translate_max_tokens(),
                "temperature": getattr(self, "options", {}).get("temperature", 0),
                "messages": self.prompt(text, getattr(self, "prompttext", None)),
            }
            headers = {
                "Authorization": f"Bearer {key}",
                "x-api-key": key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            }
            last_error = None
            for attempt in range(3):
                try:
                    resp = requests.post(
                        f"{base}/messages", headers=headers, json=payload,
                        timeout=(15, 180),
                    )
                except requests.RequestException as exc:
                    last_error = ValueError(f"网络请求失败：{exc}")
                    if attempt == 2:
                        raise last_error from exc
                    _sleep_backoff(attempt)
                    continue
                if resp.status_code in _RETRY_STATUS and attempt < 2:
                    _sleep_backoff(attempt)
                    continue
                if resp.status_code >= 400:
                    raise ValueError(
                        f"HTTP {resp.status_code} from Anthropic-compatible service: "
                        f"{resp.text[:500]}"
                    )
                data = resp.json()
                content = extract_message(data)
                if not content:
                    raise ValueError("Empty response from Anthropic-compatible service")
                regex = getattr(self, "think_filter_regex", None)
                return regex.sub("", content).strip() if regex else content
            raise last_error or ValueError("Anthropic-compatible service unavailable")

        def patched_do_translate(self, text):
            if getattr(self, "_pdf2zh_gui_use_messages", False):
                return native_messages_translate(self, text)
            return orig_do_translate(self, text)

        cls.__init__ = patched_init
        cls.do_translate = patched_do_translate
        cls._pdf2zh_gui_messages_patch = True

    interp = getattr(pdfinterp, "PDFPageInterpreterEx", None)
    if interp is not None and not getattr(interp, "_pdf2zh_gui_scs_patch", False):
        orig_interp_init = interp.__init__

        def patched_interp_init(self, *args, **kwargs):
            orig_interp_init(self, *args, **kwargs)
            if not hasattr(self, "scs"):
                self.scs = None

        interp.__init__ = patched_interp_init
        interp._pdf2zh_gui_scs_patch = True


# ---------------------------------------------------------- self update ----
def github_latest_commit(branch: str = "main", timeout: int = 12) -> dict:
    """-> {'sha': ..., 'message': ...} for the tip of the branch on GitHub."""
    with _http(timeout) as c:
        r = c.get(
            f"https://api.github.com/repos/{REPO_OWNER}/{REPO_NAME}/commits/{branch}",
            headers={"Accept": "application/vnd.github+json"},
        )
        if r.status_code >= 400:
            raise ValueError(f"HTTP {r.status_code}：{r.text[:160]}")
        data = r.json()
        return {
            "sha": data.get("sha", ""),
            "message": ((data.get("commit") or {}).get("message") or "").splitlines()[0]
            if (data.get("commit") or {}).get("message") else "",
        }


_SKIP_ON_UPDATE = {
    ".git", "__pycache__", "gui_services.json", "gui_prefs.json", "config.json",
}


def download_and_extract(branch: str, destination: Path, timeout: int = 180) -> int:
    """Zip fallback for installs without git. -> number of files replaced."""
    import io
    import shutil
    import zipfile

    url = f"{REPO_URL}/archive/refs/heads/{branch}.zip"
    with _http(timeout) as c:
        r = c.get(url)
        if r.status_code >= 400:
            raise ValueError(f"下载失败 HTTP {r.status_code}")
        payload = r.content
    destination = Path(destination)
    replaced = 0
    with zipfile.ZipFile(io.BytesIO(payload)) as zf:
        names = zf.namelist()
        root = names[0].split("/")[0] if names else ""
        for name in names:
            if name.endswith("/"):
                continue
            rel = name[len(root) + 1:] if root and name.startswith(root + "/") else name
            if not rel:
                continue
            parts = Path(rel).parts
            if any(part in _SKIP_ON_UPDATE for part in parts):
                continue
            target = destination / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(name) as src, open(target, "wb") as dst:
                shutil.copyfileobj(src, dst)
            replaced += 1
    return replaced


logging.getLogger(__name__).addHandler(logging.NullHandler())
