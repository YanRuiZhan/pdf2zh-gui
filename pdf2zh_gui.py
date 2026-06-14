# -*- coding: utf-8 -*-
"""pdf2zh desktop GUI - translate PDFs without a server. Anthropic-flavored UI."""
import json
import logging
import os
import queue
import re
import sys
import threading
import traceback
from pathlib import Path
from string import Template
from urllib.parse import urlsplit

os.environ.setdefault("PYTHONUTF8", "1")
os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")

# pythonw.exe has no std streams; tqdm/onnxruntime would crash on None
if sys.stdout is None:
    sys.stdout = open(os.devnull, "w", encoding="utf-8")
if sys.stderr is None:
    sys.stderr = open(os.devnull, "w", encoding="utf-8")

import tkinter.font as tkfont
import tkinter as tk
import customtkinter as ctk
from tkinter import filedialog
from tkinterdnd2 import DND_FILES, TkinterDnD

TITLE_ICON_PATH = Path(__file__).with_name("star.ico")
TITLE_ICON_PNG_PATH = Path(__file__).with_name("star.png")
APP_ICON_PATH = Path(__file__).with_name("pdf_translate_icon_full.ico")
CONFIG_PATH = Path.home() / ".config" / "PDFMathTranslate" / "config.json"

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
KEYLESS_SERVICES = ("google", "bing")
GEOMETRY_PREF_VERSION = 4
QA_HISTORY_LIMIT = 10
QA_RULE_CHAR = "─"

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
    notes = notes.strip()
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
        ("OPENAILIKED_MODEL", "模型名称", True, False, "claude-sonnet-4-6"),
    ],
    "claudeliked": [
        ("OPENAILIKED_BASE_URL", "Base URL", True, False, "https://"),
        ("OPENAILIKED_API_KEY", "API Key", False, True, ""),
        ("OPENAILIKED_MODEL", "模型名称", True, False, "claude-3-5-sonnet-20241022"),
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
        ("GEMINI_MODEL", "模型名称", False, False, "gemini-1.5-flash"),
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
        ("GROK_MODEL", "模型名称", False, False, "grok-2-1212"),
    ],
    "groq": [
        ("GROQ_API_KEY", "API Key", True, True, ""),
        ("GROQ_MODEL", "模型名称", False, False, "llama-3-3-70b-versatile"),
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
    url = raw.strip().rstrip("/")
    if not url:
        return raw.strip()
    if stype in _BASE_SUFFIX_HINTS:
        suffix = _BASE_SUFFIX_HINTS[stype]
        # Only inspect the URL path. Domains such as api.longcat.chat should
        # not count as an existing /api segment.
        path = urlsplit(url).path.rstrip("/")
        for pat in ("/v1", "/v2", "/api", "/v1beta"):
            if path == pat or path.startswith(pat + "/") or path.endswith(pat):
                return url
        return url + suffix
    if stype in PROVIDER_FIXED_BASE and "/" not in url.split("://")[1]:
        return PROVIDER_FIXED_BASE[stype]
    return url


def _api_target(stype: str, envs: dict):
    """-> (base_url, headers) for direct REST calls (test / list models)."""
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
        if stype in ("anthropic", "claudeliked") or "anthropic.com" in base or "/anthropic" in base:
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


_PROXY = "http://127.0.0.1:10808"  # socks5h too; use http for Windows compat


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
                "max_tokens": 4096,
                "temperature": getattr(self, "options", {}).get("temperature", 0),
                "messages": self.prompt(text, getattr(self, "prompttext", None)),
            }
            headers = {
                "Authorization": f"Bearer {key}",
                "x-api-key": key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            }
            resp = requests.post(
                f"{base}/messages", headers=headers, json=payload,
                timeout=(15, 120),
            )
            try:
                resp.raise_for_status()
            except requests.HTTPError as exc:
                raise ValueError(
                    f"HTTP {resp.status_code} from Anthropic-compatible service: "
                    f"{resp.text[:500]}"
                ) from exc
            data = resp.json()
            parts = data.get("content") or []
            if isinstance(parts, str):
                content = parts.strip()
            else:
                content = "".join(
                    p.get("text", "") if isinstance(p, dict) else str(p)
                    for p in parts
                ).strip()
            if not content:
                raise ValueError("Empty response from Anthropic-compatible service")
            regex = getattr(self, "think_filter_regex", None)
            return regex.sub("", content).strip() if regex else content

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


def _http(timeout: int):
    """httpx Client with proxy fallback (new httpx uses proxy=, old uses proxies=)."""
    import httpx

    try:
        return httpx.Client(timeout=timeout, follow_redirects=True,
                            proxy=_PROXY)
    except TypeError:
        return httpx.Client(timeout=timeout, follow_redirects=True,
                            proxies=_PROXY)
    except Exception:
        return httpx.Client(timeout=timeout, follow_redirects=True)


def fetch_models(stype: str, envs: dict) -> list[str]:
    if stype == "azure-openai":
        raise ValueError("Azure 使用部署名，请到 Azure 门户查看")
    import httpx
    from httpx import TimeoutException
    base, headers = _api_target(stype, envs)
    try:
        with _http(10) as c:
            if stype == "ollama":
                r = c.get(f"{base}/api/tags")
                r.raise_for_status()
                return sorted(m["name"] for m in r.json().get("models", []))
            # Anthropic-native endpoints use GET <base>/models (no results key)
            r = c.get(f"{base}/models", headers=headers)
            if r.status_code >= 400:
                raise ValueError(f"HTTP {r.status_code}：{r.text[:160]}")
            data = r.json()
            ids = [m.get("id", "") for m in data.get("data", []) if m.get("id")]
            ids = [i[7:] if i.startswith("models/") else i for i in ids]
            if not ids:
                raise ValueError("接口未返回任何模型")
            return sorted(set(ids))
    except TimeoutException:
        raise TimeoutError(
            f"超过 10 秒未响应，请检查：\n"
            f"① 网络代理（本机建议开 10808）\n"
            f"② API Key 是否正确\n"
            f"③ Base URL 是否可访问"
        )


def test_service(  # type: ignore[no-redef]
    stype: str, envs: dict, model: str, timeout: int = 10
) -> str:
    """Minimal 1-token chat round-trip with user-friendly timeout message."""
    import httpx
    from httpx import TimeoutException as _TimeoutException

    base, headers = _api_target(stype, envs)

    try:
        with _http(timeout) as c:
            if stype == "ollama":
                r = c.get(f"{base}/api/tags")
                r.raise_for_status()
                names = [m["name"] for m in r.json().get("models", [])]
                if model and model not in names and f"{model}:latest" not in names:
                    raise ValueError(
                        f"服务已连通，但本地没有模型 {model}"
                        f"（已装：{', '.join(names[:6]) or '无'}）"
                    )
                return "Ollama 连接正常" + (f"，模型 {model} 可用" if model else "")
            if not model:
                raise ValueError("请先填写模型名称")
            payload = {
                "model": model,
                "messages": [{"role": "user", "content": "hi"}],
                "max_tokens": 1,
            }
            if stype == "azure-openai":
                ver = (envs.get("AZURE_OPENAI_API_VERSION") or "2024-06-01").strip()
                url = f"{base}/openai/deployments/{model}/chat/completions?api-version={ver}"
                r = c.post(url, headers=headers, json=payload)
            elif stype in ("anthropic", "claudeliked") or "/anthropic" in base or "anthropic.com" in base:
                # LongCat / custom Claude-like gateways may speak Anthropic-native
                # POST <base>/messages rather than /chat/completions
                if "/anthropic" in base or "anthropic.com" in base:
                    r = c.post(f"{base}/messages", headers=headers, json=payload)
                else:
                    try:
                        r = c.post(f"{base}/chat/completions", headers=headers, json=payload)
                        if r.status_code in (404, 405):
                            raise Exception  # fall through to /messages
                    except Exception:
                        r = c.post(f"{base}/messages", headers=headers, json=payload)
            else:
                r = c.post(f"{base}/chat/completions", headers=headers, json=payload)
            if r.status_code >= 400:
                raise ValueError(f"HTTP {r.status_code}：{r.text[:160]}")
            return f"连接成功，{model} 响应正常"
    except _TimeoutException:
        raise TimeoutError(
            f"超过 {timeout} 秒未响应，请检查：\n"
            f"① 网络代理（本机建议开 10808）\n"
            f"② API Key 是否正确\n"
            f"③ Base URL 是否可访问"
        )

GUI_SERVICES_PATH = CONFIG_PATH.with_name("gui_services.json")
GUI_PREFS_PATH = CONFIG_PATH.with_name("gui_prefs.json")
DEFAULT_GUI_PREFS_PATH = Path(__file__).with_name("default_gui_prefs.json")


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


def quick_translate(stype: str, envs: dict, model: str, text: str,
                    timeout: int = 25) -> str:
    """One-shot dictionary-style lookup via the selected AI service."""
    if not model:
        raise ValueError("请先填写模型名称")
    base, headers = _api_target(stype, envs)
    ask = (
        "你是英汉双向词典助手。解释下面的单词或短语：\n"
        "- 英文输入：给出音标、词性和中文释义，最多列 3 个常用义项\n"
        "- 中文输入：给出对应的英文表达和例句\n"
        "- 若是专业术语，补充一句领域内含义\n"
        "直接输出结果，不要寒暄，控制在 5 行以内。\n\n"
        f"查询：{text}"
    )
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": ask}],
        "max_tokens": 500,
        "temperature": 0,
    }
    with _http(timeout) as c:
        if stype == "ollama":
            r = c.post(f"{base}/api/chat",
                       json={"model": model, "stream": False,
                             "messages": payload["messages"]})
            if r.status_code >= 400:
                raise ValueError(f"HTTP {r.status_code}：{r.text[:160]}")
            return (r.json().get("message", {}).get("content") or "").strip()
        if stype == "azure-openai":
            ver = (envs.get("AZURE_OPENAI_API_VERSION") or "2024-06-01").strip()
            url = f"{base}/openai/deployments/{model}/chat/completions?api-version={ver}"
            r = c.post(url, headers=headers, json=payload)
        elif stype in ("anthropic", "claudeliked") or "/anthropic" in base or "anthropic.com" in base:
            r = c.post(f"{base}/messages", headers=headers, json=payload)
        else:
            r = c.post(f"{base}/chat/completions", headers=headers, json=payload)
        if r.status_code >= 400:
            raise ValueError(f"HTTP {r.status_code}：{r.text[:160]}")
        data = r.json()
        if "choices" in data:  # OpenAI flavor
            return (data["choices"][0]["message"]["content"] or "").strip()
        parts = data.get("content") or []  # Anthropic flavor
        if isinstance(parts, str):
            return parts.strip()
        return "".join(
            p.get("text", "") if isinstance(p, dict) else str(p) for p in parts
        ).strip()


def quick_ask(stype: str, envs: dict, model: str, question: str,
              history: list[dict], timeout: int = 35) -> str:
    """Short literature-reading Q&A via the selected AI service."""
    if not model:
        raise ValueError("请先填写模型名称")
    question = question.strip()
    if not question:
        raise ValueError("请输入要提问的内容")
    base, headers = _api_target(stype, envs)
    system_prompt = (
        "你是文献阅读时使用的快问快答助手。回答用户临时提出的小问题，"
        "重点是准确、简洁、直接，不要寒暄。若问题和论文、学术概念、"
        "英文表达或技术术语有关，优先给出面向阅读理解的解释。"
        "不确定时明确说明不确定，不要编造。"
    )
    turns = []
    for item in (history or [])[-QA_HISTORY_LIMIT:]:
        prev_q = str(item.get("question", "")).strip()
        prev_a = str(item.get("answer", "")).strip()
        if not prev_q or not prev_a:
            continue
        turns.append({"role": "user", "content": prev_q[:1200]})
        turns.append({"role": "assistant", "content": prev_a[:1600]})
    turns.append({"role": "user", "content": question})

    common = {"model": model, "max_tokens": 900, "temperature": 0.2}
    with _http(timeout) as c:
        if stype == "ollama":
            messages = [{"role": "system", "content": system_prompt}] + turns
            r = c.post(
                f"{base}/api/chat",
                json={
                    "model": model,
                    "stream": False,
                    "messages": messages,
                    "options": {"temperature": 0.2, "num_predict": 900},
                },
            )
            if r.status_code >= 400:
                raise ValueError(f"HTTP {r.status_code}：{r.text[:160]}")
            return (r.json().get("message", {}).get("content") or "").strip()
        if stype == "azure-openai":
            ver = (envs.get("AZURE_OPENAI_API_VERSION") or "2024-06-01").strip()
            url = f"{base}/openai/deployments/{model}/chat/completions?api-version={ver}"
            payload = {
                **common,
                "messages": [{"role": "system", "content": system_prompt}] + turns,
            }
            r = c.post(url, headers=headers, json=payload)
        elif stype in ("anthropic", "claudeliked") or "/anthropic" in base or "anthropic.com" in base:
            payload = {**common, "system": system_prompt, "messages": turns}
            r = c.post(f"{base}/messages", headers=headers, json=payload)
        else:
            payload = {
                **common,
                "messages": [{"role": "system", "content": system_prompt}] + turns,
            }
            r = c.post(f"{base}/chat/completions", headers=headers, json=payload)
        if r.status_code >= 400:
            raise ValueError(f"HTTP {r.status_code}：{r.text[:160]}")
        data = r.json()
        if "choices" in data:
            return (data["choices"][0]["message"]["content"] or "").strip()
        parts = data.get("content") or []
        if isinstance(parts, str):
            return parts.strip()
        return "".join(
            p.get("text", "") if isinstance(p, dict) else str(p) for p in parts
        ).strip()

# ---- Anthropic palette ----
IVORY = "#F0EEE6"      # window background
PAPER = "#FAF9F5"      # cards
WHITE = "#FFFFFF"      # inputs / lists
INK = "#1F1E1D"        # primary text
SLATE = "#57564F"      # secondary text
FAINT = "#9B998F"      # hints / disabled
LINE = "#DCD8CC"       # hairline borders
CLAY = "#D97757"       # Claude orange
CLAY_DK = "#BA5D3F"    # orange hover
TINT = "#F3E8E0"       # clay-tinted hover
GHOST_H = "#ECE8DC"    # neutral ghost hover
BAR_BG = "#E5E1D3"     # progress trough

COLOR_WAIT = FAINT
COLOR_RUN = CLAY
COLOR_OK = "#6E8F5E"
COLOR_FAIL = "#A8432F"


def event_has_control(event) -> bool:
    return bool(getattr(event, "state", 0) & 0x0004)


def clamp_window_geometry(geometry: str, screen_w: int, screen_h: int) -> str | None:
    m = re.fullmatch(r"(\d+)x(\d+)([+-]\d+)([+-]\d+)", geometry)
    if not m:
        return None
    width, height = int(m.group(1)), int(m.group(2))
    x, y = int(m.group(3)), int(m.group(4))
    width = min(max(width, 620), max(620, screen_w - 80))
    height = min(max(height, 430), max(430, screen_h - 120))
    x = min(max(x, 0), max(0, screen_w - width))
    y = min(max(y, 0), max(0, screen_h - height))
    return f"{width}x{height}+{x}+{y}"


def is_legacy_startup_min_geometry(geometry: str, prefs: dict) -> bool:
    if prefs.get("geometry_pref_version") == GEOMETRY_PREF_VERSION:
        return False
    m = re.fullmatch(r"(\d+)x(\d+)[+-]\d+[+-]\d+", geometry)
    if not m:
        return False
    width, height = int(m.group(1)), int(m.group(2))
    return (width <= 640 and height <= 520) or height <= 580


def set_window_icon(window, set_default=False):
    if TITLE_ICON_PNG_PATH.exists():
        try:
            photo = tk.PhotoImage(file=str(TITLE_ICON_PNG_PATH))
            refs = getattr(window, "_title_icon_refs", [])
            refs.append(photo)
            window._title_icon_refs = refs
            window.iconphoto(set_default, photo)
        except Exception:
            pass
    icon_path = TITLE_ICON_PATH if TITLE_ICON_PATH.exists() else APP_ICON_PATH
    if not icon_path.exists():
        return
    try:
        window.iconbitmap(str(icon_path))
    except Exception:
        pass
    if set_default:
        try:
            window.iconbitmap(default=str(icon_path))
        except Exception:
            pass


def load_config() -> dict:
    try:
        return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def load_profiles() -> list[dict]:
    """User-defined AI services, with URL normalization for stored records."""
    try:
        data = json.loads(GUI_SERVICES_PATH.read_text(encoding="utf-8"))
        result = []
        for p in data:
            if not (p.get("display") and p.get("type") in SERVICE_SCHEMAS):
                continue
            stype = p["type"]
            envs = p.get("envs", {})
            # auto-fix any base-url fields that were saved before smart-normalize
            for k, v in list(envs.items()):
                if v and (k.endswith("BASE_URL") or k.endswith("HOST")):
                    envs[k] = _normalize_base_url(v, stype)
            result.append(p)
        return result
    except Exception:
        return []


def save_profiles(profiles: list[dict]):
    GUI_SERVICES_PATH.parent.mkdir(parents=True, exist_ok=True)
    GUI_SERVICES_PATH.write_text(
        json.dumps(profiles, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def parse_pages(text: str):
    """'1,3,5-8' (1-based) -> [0,2,4,5,6,7]; empty/全部 -> None."""
    text = text.strip()
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


class QueueLogHandler(logging.Handler):
    def __init__(self, q: queue.Queue):
        super().__init__(level=logging.INFO)
        self.q = q

    def emit(self, record):
        try:
            self.q.put(("log", record.getMessage()))
        except Exception:
            pass


class FastScrollFrame(ctk.CTkScrollableFrame):
    """CTkScrollableFrame: 3x wheel speed, scrollbar inset inside the border."""

    def _border_spacing(self):
        return self._apply_widget_scaling(
            self._parent_frame.cget("corner_radius")
            + self._parent_frame.cget("border_width")
        )

    def _create_grid(self):
        super()._create_grid()
        if self._orientation == "vertical":
            # stock layout flushes the scrollbar against the right edge,
            # painting over the rounded border; inset it like the canvas is
            pad = self._apply_widget_scaling(
                self._parent_frame.cget("corner_radius")
                + self._parent_frame.cget("border_width")
                + 4
            )
            self._scrollbar.grid_configure(padx=(0, pad), pady=(2, 2))

    def set_scrollbar_visible(self, visible: bool):
        if self._orientation != "vertical":
            return
        border_spacing = self._border_spacing()
        if visible:
            self._parent_canvas.grid_configure(
                row=1, column=0, sticky="nsew",
                padx=(border_spacing, 0), pady=border_spacing,
            )
            self._scrollbar.grid()
        else:
            self._scrollbar.grid_remove()
            self._parent_canvas.grid_configure(
                row=1, column=0, sticky="nsew",
                padx=border_spacing, pady=border_spacing,
            )
            self._parent_frame.grid_columnconfigure(1, weight=0, minsize=0)

    def _mouse_wheel_all(self, event):
        if event_has_control(event):
            return
        if sys.platform.startswith("win") and self.check_if_master_is_canvas(event.widget):
            if self._shift_pressed:
                if self._parent_canvas.xview() != (0.0, 1.0):
                    self._parent_canvas.xview("scroll", -int(event.delta / 2), "units")
            else:
                if self._parent_canvas.yview() != (0.0, 1.0):
                    self._parent_canvas.yview("scroll", -int(event.delta / 2), "units")
        else:
            super()._mouse_wheel_all(event)


class ScrollDropdown:
    """Small scrollable popup used by PrettyOptionMenu for long model lists."""

    def __init__(self, owner, values, command, font=None, visible_rows=6):
        self.owner = owner
        self.values = list(values)
        self.command = command
        self.font = font
        self.visible_rows = visible_rows
        self._popup = None
        self._scroll = None
        self._outside_bind = None
        self._top_config_bind = None
        self._owner_config_bind = None
        self._popup_size = (0, 0)

    def configure(self, values=None):
        if values is not None:
            self.values = list(values)

    def close(self):
        if self._popup is not None:
            try:
                self._popup.destroy()
            except Exception:
                pass
            self._popup = None
            self._scroll = None
        if self._outside_bind is not None:
            try:
                self.owner.winfo_toplevel().unbind("<Button-1>", self._outside_bind)
            except Exception:
                pass
            self._outside_bind = None
        if self._top_config_bind is not None:
            try:
                self.owner.winfo_toplevel().unbind("<Configure>", self._top_config_bind)
            except Exception:
                pass
            self._top_config_bind = None
        if self._owner_config_bind is not None:
            try:
                self.owner.unbind("<Configure>", self._owner_config_bind)
            except Exception:
                pass
            self._owner_config_bind = None

    def toggle(self, x, y, width):
        if self._popup is not None:
            self.close()
            return
        self.open(x, y, width)

    def _px(self, value):
        try:
            return max(1, int(round(self.owner._apply_widget_scaling(value))))
        except Exception:
            return max(1, int(round(value)))

    def _popup_position(self, width, height):
        top = self.owner.winfo_toplevel()
        try:
            top.update_idletasks()
            self.owner.update_idletasks()
            top_x = top.winfo_rootx()
            top_y = top.winfo_rooty()
            top_w = top.winfo_width()
            top_h = top.winfo_height()
            owner_x = self.owner.winfo_rootx() - top_x
            owner_y = self.owner.winfo_rooty() - top_y
        except Exception:
            top_w, top_h = 10_000, 10_000
            owner_x, owner_y = 0, 0
        below_y = owner_y + self.owner.winfo_height()
        above_y = owner_y - height
        if below_y + height <= top_h - 8:
            popup_y = below_y
        else:
            popup_y = below_y if len(self.values) > 6 else max(8, above_y)
        popup_x = min(max(8, owner_x), max(8, top_w - width - 8))
        if popup_x <= 8 and owner_x > 40:
            popup_x = owner_x
        if popup_y <= 8 and below_y > 40:
            popup_y = below_y
        return popup_x, popup_y

    def _reposition(self, event=None):
        if self._popup is None:
            return
        if (
            event is not None
            and event.widget is not self.owner.winfo_toplevel()
            and event.widget is not self.owner
        ):
            return
        try:
            width, height = self._popup_size
            if width and height:
                popup_x, popup_y = self._popup_position(width, height)
                self._popup.place_configure(
                    x=popup_x, y=popup_y, width=width, height=height
                )
                self._popup.lift()
        except Exception:
            pass

    def open(self, x, y, width):
        self.close()
        row_h = 30
        row_px = self._px(row_h)
        row_gap = 2
        chrome_px = self._px(14) + 8
        visible = max(1, min(self.visible_rows, len(self.values) or 1))
        height = visible * (row_px + row_gap) + chrome_px
        width = max(width, 160)
        top = self.owner.winfo_toplevel()
        popup = tk.Frame(
            top, width=width, height=height, bg=IVORY, bd=0, highlightthickness=0
        )
        self._popup = popup
        self._popup_size = (width, height)
        popup.place(x=0, y=0, width=width, height=height)
        popup.place_forget()
        popup.pack_propagate(False)
        needs_scroll = len(self.values) > self.visible_rows
        shell = ctk.CTkFrame(
            popup, width=width, height=height, fg_color=WHITE,
            corner_radius=8, border_width=1, border_color=LINE,
        )
        shell.pack(fill="both", expand=True)
        shell.pack_propagate(False)
        body = ctk.CTkFrame(shell, fg_color=WHITE, corner_radius=8)
        body.pack(fill="both", expand=True, padx=4, pady=4)
        if needs_scroll:
            canvas = tk.Canvas(
                body, bg=WHITE, highlightthickness=0, bd=0,
                yscrollincrement=row_px + row_gap,
            )
            canvas.pack(side="left", fill="both", expand=True)
            scrollbar = ctk.CTkScrollbar(
                body, width=12, command=canvas.yview,
                button_color="#D8D3C6", button_hover_color="#C6BFAF",
            )
            scrollbar.pack(side="right", fill="y", padx=(4, 0))
            canvas.configure(yscrollcommand=scrollbar.set)
            frame = ctk.CTkFrame(canvas, fg_color=WHITE, corner_radius=0)
            window_id = canvas.create_window((0, 0), window=frame, anchor="nw")
            self._scroll = canvas
        else:
            canvas = None
            frame = body
            window_id = None
            self._scroll = None

        values = self.values or [""]
        for value in values:
            btn = ctk.CTkButton(
                frame, text=value, anchor="w", height=row_h, font=self.font,
                fg_color="transparent", hover_color=IVORY, text_color=INK,
                corner_radius=6,
                command=lambda v=value: self._select(v),
            )
            btn.pack(fill="x", padx=4, pady=1)
            btn.bind("<MouseWheel>", self._on_wheel, add="+")

        widgets = [popup, shell, body, frame]
        if canvas is not None:
            def sync_scroll_region(_e=None):
                canvas.configure(scrollregion=canvas.bbox("all"))
                canvas.itemconfigure(window_id, width=canvas.winfo_width())

            frame.bind("<Configure>", sync_scroll_region, add="+")
            canvas.bind("<Configure>", sync_scroll_region, add="+")
            widgets.append(canvas)

        for widget in widgets:
            widget.bind("<MouseWheel>", self._on_wheel, add="+")
            widget.bind("<Escape>", lambda _e: self.close(), add="+")
        self._reposition()
        self._outside_bind = top.bind("<Button-1>", self._maybe_close_from_click, add="+")
        self._top_config_bind = top.bind("<Configure>", self._reposition, add="+")
        self._owner_config_bind = self.owner.bind("<Configure>", self._reposition, add="+")

    def _select(self, value):
        self.close()
        if value:
            self.command(value)

    def _on_wheel(self, event):
        if event_has_control(event):
            return
        if self._scroll is not None:
            try:
                self._scroll.yview_scroll(-int(event.delta / 120), "units")
            except Exception:
                pass
        return "break"

    def _maybe_close_from_click(self, event):
        popup = self._popup
        if popup is None:
            return
        widget = event.widget
        popup_path = str(popup)
        owner_path = str(self.owner)
        widget_path = str(widget)
        if (
            widget_path == popup_path
            or widget_path.startswith(popup_path + ".")
            or widget_path == owner_path
            or widget_path.startswith(owner_path + ".")
        ):
            return
        self.close()
        try:
            widget.after_idle(lambda w=widget: w.focus_set())
        except Exception:
            pass


class PrettyOptionMenu(ctk.CTkFrame):
    """Select-style dropdown: left-aligned value, inline chevron, whole-row click."""

    def __init__(
        self, master, variable, values, width, height=30, font=None,
        command=None, state="normal",
    ):
        super().__init__(
            master, width=width, height=height, fg_color=WHITE,
            corner_radius=8, border_width=1, border_color=LINE,
            cursor="hand2",
        )
        self.pack_propagate(False)
        self.grid_propagate(False)
        self._variable = variable
        self._values = list(values)
        self._command = command
        self._state = state
        self._font = font
        self._trace_blocked = False
        self._trace_name = None
        self._current_value = variable.get() if variable is not None else (
            self._values[0] if self._values else ""
        )

        self._dropdown = ScrollDropdown(
            owner=self, values=self._values, command=self._select, font=font,
        )
        # chevron packs first (side=right) so it stays visible at any width;
        # CTkLabel (unlike CTkButton) has no 140px default that breaks narrow menus
        self._arrow = ctk.CTkLabel(
            self, text="▾", width=16, fg_color=WHITE, text_color=FAINT,
            font=ctk.CTkFont(size=13),
        )
        self._arrow.pack(side="right", padx=(2, 9), pady=1)
        self._text = ctk.CTkLabel(
            self, text=self._current_value, anchor="w", fg_color=WHITE,
            text_color=INK, font=font,
        )
        self._text.pack(side="left", fill="both", expand=True, padx=(10, 0), pady=1)

        for w in (self, self._text, self._arrow):
            w.bind("<Button-1>", self._on_click)
            w.bind("<Enter>", self._on_enter)
            w.bind("<Leave>", self._on_leave)
        if variable is not None:
            self._trace_name = variable.trace_add("write", self._sync_from_var)
        self._apply_state_colors()

    def destroy(self):
        if hasattr(self, "_dropdown"):
            try:
                self._dropdown.close()
            except Exception:
                pass
        if self._variable is not None and self._trace_name:
            self._variable.trace_remove("write", self._trace_name)
        super().destroy()

    def _apply_state_colors(self):
        if self._state == "disabled":
            self._set_bg(IVORY)
            self._text.configure(text_color=FAINT)
            self._arrow.configure(text_color="#C9C4B6")
            super().configure(cursor="arrow")
        else:
            self._set_bg(WHITE)
            self._text.configure(text_color=INK)
            self._arrow.configure(text_color=FAINT)
            super().configure(cursor="hand2")

    def configure(self, **kwargs):
        if not hasattr(self, "_dropdown"):
            return super().configure(**kwargs)
        values = kwargs.pop("values", None)
        state = kwargs.pop("state", None)
        if values is not None:
            self._values = list(values)
            self._dropdown.configure(values=self._values)
        if kwargs:
            super().configure(**kwargs)
        if state is not None:
            self._state = state
            self._apply_state_colors()

    def set(self, value):
        self._current_value = value
        self._text.configure(text=value)
        if self._variable is not None and self._variable.get() != value:
            self._trace_blocked = True
            self._variable.set(value)
            self._trace_blocked = False

    def get(self):
        return self._current_value

    def _sync_from_var(self, *_):
        if not self._trace_blocked and self._variable is not None:
            self.set(self._variable.get())

    def _set_bg(self, color):
        super().configure(fg_color=color)
        self._text.configure(fg_color=color)
        self._arrow.configure(fg_color=color)

    def _on_enter(self, _e=None):
        if self._state != "disabled":
            self._set_bg(GHOST_H)
            self._arrow.configure(text_color=SLATE)

    def _on_leave(self, _e=None):
        try:
            px, py = self.winfo_pointerxy()
            inside = self.winfo_containing(px, py)
        except Exception:
            inside = None
        path = str(self)
        if inside is None or not (
            str(inside) == path or str(inside).startswith(path + ".")
        ):
            self._apply_state_colors()

    def _on_click(self, _e=None):
        if self._state == "disabled":
            return
        self._dropdown.toggle(
            self.winfo_rootx(), self.winfo_rooty() + self.winfo_height(),
            self.winfo_width(),
        )

    def _select(self, value):
        self.set(value)
        if self._command:
            self._command(value)


class ServiceDialog(ctk.CTkToplevel):
    """Add / edit an AI translation service profile."""

    def iconbitmap(self, bitmap=None, default=None):
        if bitmap and "CustomTkinter_icon_Windows.ico" in str(bitmap):
            self._ignored_default_icon = True
            return None
        return super().iconbitmap(bitmap, default=default)

    def __init__(self, master, on_save, profile=None):
        super().__init__(master, fg_color=IVORY)
        self._ignored_default_icon = False
        self.master_ref = master
        self._geometry_job = None
        self.on_save = on_save
        self.profile = profile or {}
        self.title("编辑服务" if profile else "添加服务")
        self._set_icon_safe()
        self.after(240, self._set_icon_safe)
        saved_geometry = getattr(master, "_prefs", {}).get("service_dialog_geometry")
        if isinstance(saved_geometry, str) and re.match(r"^\d+x\d+[+-]\d+[+-]\d+$", saved_geometry):
            self.geometry(saved_geometry)
        else:
            self.geometry("520x600")
        self.minsize(520, 480)
        self.transient(master)
        self.grab_set()
        self.bind("<Configure>", self._schedule_geometry_save, add="+")
        self.protocol("WM_DELETE_WINDOW", self._on_close)

        f = master.f_body
        self._rows: list[tuple] = []
        self._model_menus: dict[str, PrettyOptionMenu] = {}

        ctk.CTkLabel(self, text="服务类型", font=master.f_section, text_color=INK).pack(
            anchor="w", padx=20, pady=(18, 4)
        )
        cur_type = self.profile.get("type", "openailiked")
        self.type_var = ctk.StringVar(value=SERVICE_TYPE_LABELS[cur_type])
        self.type_menu = PrettyOptionMenu(
            self, variable=self.type_var, values=list(TYPE_LABEL_TO_KEY),
            width=240, height=30, font=f,
            command=lambda _: self._render_fields(),
        )
        self.type_menu.pack(anchor="w", padx=20)
        if profile:
            self.type_menu.configure(state="disabled")

        ctk.CTkLabel(self, text="显示名称", font=master.f_section, text_color=INK).pack(
            anchor="w", padx=20, pady=(14, 4)
        )
        self.name_entry = ctk.CTkEntry(
            self, width=240, height=30, fg_color=WHITE, border_color=LINE,
            border_width=1, text_color=INK, corner_radius=8, font=f,
            placeholder_text="例如：我的 Claude",
        )
        self.name_entry.pack(anchor="w", padx=20)
        if self.profile.get("display"):
            self.name_entry.insert(0, self.profile["display"])

        self.fields_frame = ctk.CTkFrame(self, fg_color=PAPER, corner_radius=12,
                                         border_width=1, border_color=LINE)
        self.fields_frame.pack(fill="both", expand=True, padx=20, pady=10)

        self._bar = ctk.CTkFrame(self, fg_color="transparent")
        self._bar.pack(fill="x", padx=20, pady=(0, 4))
        self._build_bar()

        # multi-line message box (3 lines visible)
        self.err_box = ctk.CTkTextbox(
            self, height=50, wrap="word", state="disabled",
            fg_color=IVORY, text_color=COLOR_FAIL, font=master.f_small,
            border_width=0, activate_scrollbars=False,
        )
        self.err_box.pack(fill="x", padx=20, pady=(0, 8))

        self._render_fields()

    def _set_icon_safe(self):
        set_window_icon(self)

    def _schedule_geometry_save(self, event=None):
        if event is not None and event.widget is not self:
            return
        if self._geometry_job is not None:
            self.after_cancel(self._geometry_job)
        self._geometry_job = self.after(450, self._save_geometry_pref)

    def _save_geometry_pref(self):
        self._geometry_job = None
        try:
            geometry = self.geometry()
            if re.match(r"^\d+x\d+[+-]\d+[+-]\d+$", geometry):
                self.master_ref._prefs["service_dialog_geometry"] = geometry
                save_prefs(self.master_ref._prefs)
        except Exception:
            pass

    def _on_close(self):
        self.destroy()

    def destroy(self):
        if getattr(self, "_geometry_job", None) is not None:
            try:
                self.after_cancel(self._geometry_job)
            except Exception:
                pass
            self._geometry_job = None
        self._save_geometry_pref()
        super().destroy()

    def _build_bar(self):
        bar = self._bar
        for w in bar.winfo_children():
            w.destroy()
        ctk.CTkButton(
            bar, text="保存", width=90, height=34, font=self.master_ref.f_btn,
            fg_color=CLAY, hover_color=CLAY_DK, text_color=WHITE, corner_radius=8,
            command=self._save,
        ).pack(side="left")
        ctk.CTkButton(
            bar, text="取消", width=70, height=34,
            font=self.master_ref.f_body,
            fg_color="transparent", border_width=1, border_color=LINE,
            text_color=SLATE, hover_color=GHOST_H, corner_radius=8,
            command=self.destroy,
        ).pack(side="left", padx=6)
        ctk.CTkButton(
            bar, text="🔌 测试连通性", width=120, height=34,
            font=self.master_ref.f_small,
            fg_color="transparent", border_width=1, border_color="#6E8F5E",
            text_color="#6E8F5E", hover_color="#E4EDE0", corner_radius=8,
            command=self._test_connection,
        ).pack(side="right", padx=(0, 0))
        ctk.CTkButton(
            bar, text="📋 获取可用模型", width=120, height=34,
            font=self.master_ref.f_small,
            fg_color="transparent", border_width=1, border_color="#6E8F5E",
            text_color="#6E8F5E", hover_color="#E4EDE0", corner_radius=8,
            command=self._fetch_models,
        ).pack(side="right", padx=(0, 8))

    def _current_type_and_envs(self):
        key = TYPE_LABEL_TO_KEY[self.type_var.get()]
        envs = {}
        for env_key, entry, _required in self._rows:
            val = entry.get().strip()
            if val:
                # live-normalize base-URL entries so test/fetch use the right path
                if env_key.endswith("BASE_URL") or env_key.endswith("HOST"):
                    norm = _normalize_base_url(val, key)
                    if norm != val:
                        entry.delete(0, "end")
                        entry.insert(0, norm)
                    val = norm
                envs[env_key] = val
        return key, envs

    def _gather_model(self):
        _, envs = self._current_type_and_envs()
        schema = SERVICE_SCHEMAS[TYPE_LABEL_TO_KEY[self.type_var.get()]]
        model_keys = [k for k, *_ in schema if k.endswith("_MODEL")]
        model = next((envs.get(k, "") for k in model_keys if envs.get(k)), "")
        return model, envs

    def _set_err(self, text, color=COLOR_FAIL):
        self.err_box.configure(state="normal")
        self.err_box.delete("0.0", "end")
        self.err_box.insert("0.0", text)
        self.err_box.configure(text_color=color, state="disabled")

    def _test_connection(self):
        stype, envs = self._current_type_and_envs()
        model, _ = self._gather_model()
        self._set_err("⏳ 测试中...", SLATE)
        self._toggle_buttons(False)
        import threading as _th
        _th.Thread(target=self._do_test, args=(stype, envs, model), daemon=True).start()

    def _do_test(self, stype, envs, model):
        try:
            msg = test_service(stype, envs, model)
            self._after_safe(lambda: self._set_err(f"✅ {msg}", COLOR_OK))
        except Exception as e:
            self._after_safe(lambda: self._set_err(f"❌ {e}", COLOR_FAIL))
        finally:
            self._after_safe(lambda: self._toggle_buttons(True))

    def _fetch_models(self):
        stype, envs = self._current_type_and_envs()
        self._set_err("⏳ 获取模型列表...", SLATE)
        self._toggle_buttons(False)
        import threading as _th
        _th.Thread(target=self._do_fetch, args=(stype, envs), daemon=True).start()

    def _do_fetch(self, stype, envs):
        try:
            models = fetch_models(stype, envs)
            self._after_safe(lambda: self._set_err(
                f"✅ 找到 {len(models)} 个模型，已更新模型下拉菜单", COLOR_OK
            ))
            self._after_safe(lambda: self._fill_model(models))
        except Exception as e:
            self._after_safe(lambda: self._set_err(f"❌ {e}", COLOR_FAIL))
        finally:
            self._after_safe(lambda: self._toggle_buttons(True))

    def _fill_model(self, models: list[str]):
        """Refresh model dropdowns without silently overwriting the entry."""
        schema = SERVICE_SCHEMAS[TYPE_LABEL_TO_KEY[self.type_var.get()]]
        model_keys = {k for k, *_ in schema if k.endswith("_MODEL")}
        if not models:
            return
        for env_key, entry, _required in self._rows:
            if env_key in model_keys:
                menu = self._model_menus.get(env_key)
                cur = entry.get().strip()
                if menu:
                    menu.configure(values=models, state="normal")
                    menu.set(cur if cur in models else "选择模型")

    def _toggle_buttons(self, enabled):
        state = "normal" if enabled else "disabled"
        for w in self._bar.winfo_children():
            try:
                w.configure(state=state)
            except Exception:
                pass

    def _after_safe(self, fn):
        try:
            self.after(0, fn)
        except Exception:
            pass

    def _render_fields(self):
        for w in self.fields_frame.winfo_children():
            w.destroy()
        self._rows.clear()
        self._model_menus.clear()
        key = TYPE_LABEL_TO_KEY[self.type_var.get()]
        saved = self.profile.get("envs", {})
        for env_key, label, required, secret, default in SERVICE_SCHEMAS[key]:
            row = ctk.CTkFrame(self.fields_frame, fg_color="transparent")
            row.pack(fill="x", padx=12, pady=5)
            text = label + ("  *" if required else "")
            ctk.CTkLabel(row, text=text, width=110, anchor="w",
                         font=self.master_ref.f_body, text_color=SLATE).pack(side="left")
            entry = ctk.CTkEntry(
                row, height=30, fg_color=WHITE, border_color=LINE, border_width=1,
                text_color=INK, corner_radius=8, font=self.master_ref.f_body,
                show="•" if secret else "",
            )
            entry.pack(side="left", fill="x", expand=True, padx=(6, 0))
            entry.insert(0, saved.get(env_key, default))
            if secret:
                btn = ctk.CTkButton(
                    row, text="显示", width=44, height=28, font=self.master_ref.f_small,
                    fg_color="transparent", border_width=1, border_color=LINE,
                    text_color=FAINT, hover_color=GHOST_H, corner_radius=6,
                )
                btn.configure(command=lambda e=entry, b=btn: self._toggle(e, b))
                btn.pack(side="left", padx=(6, 0))
            elif env_key.endswith("_MODEL"):
                menu = PrettyOptionMenu(
                    row, variable=ctk.StringVar(value="选择模型"),
                    values=["选择模型"], width=104, height=30,
                    font=self.master_ref.f_small,
                    command=lambda value, e=entry: self._set_model_entry(e, value),
                )
                menu.set("选择模型")
                menu.configure(state="disabled")
                menu.pack(side="left", padx=(6, 0))
                self._model_menus[env_key] = menu
            self._rows.append((env_key, entry, required))

    @staticmethod
    def _set_model_entry(entry, value):
        if value == "选择模型":
            return
        entry.delete(0, "end")
        entry.insert(0, value)

    @staticmethod
    def _toggle(entry, btn):
        hidden = entry.cget("show")
        entry.configure(show="" if hidden else "•")
        btn.configure(text="隐藏" if hidden else "显示")

    def _save(self):
        display = self.name_entry.get().strip()
        if not display:
            self._set_err("请填写显示名称")
            return
        stype = TYPE_LABEL_TO_KEY[self.type_var.get()]
        envs = {}
        for env_key, entry, required in self._rows:
            val = entry.get().strip()
            if required and not val:
                self._set_err(f"必填项缺失：{env_key}")
                return
            if val:
                # smart-complete base-URL fields
                if env_key.endswith("BASE_URL") or env_key.endswith("HOST"):
                    if not val.startswith(("http://", "https://")):
                        self._set_err(f"{env_key} 应以 http:// 或 https:// 开头")
                        return
                    norm = _normalize_base_url(val, stype)
                    if norm != val:
                        entry.delete(0, "end")
                        entry.insert(0, norm)
                    val = norm
                envs[env_key] = val
        self.on_save({
            "display": display,
            "type": stype,
            "envs": envs,
        }, self.profile.get("display"))
        self.destroy()


class App(ctk.CTk, TkinterDnD.DnDWrapper):
    _model = None  # layout model, loaded once

    def __init__(self):
        prefs = load_prefs()
        saved_scale = float(prefs.get("ui_scale", 1.0) or 1.0)
        saved_scale = min(1.6, max(0.7, round(saved_scale, 2)))
        ctk.set_widget_scaling(saved_scale)
        super().__init__(fg_color=IVORY)
        self.TkdndVersion = TkinterDnD._require(self)

        self.title("PDF Translator")
        set_window_icon(self, set_default=True)
        self.withdraw()
        self._prefs = prefs
        sh = self.winfo_screenheight()
        sw = self.winfo_screenwidth()
        default_w = max(620, min(840, sw - 80))
        default_h = max(430, min(780, sh - 120))
        x = max(0, (sw - default_w) // 2)
        saved_geometry = self._prefs.get("window_geometry")
        clamped_geometry = (
            clamp_window_geometry(saved_geometry, sw, sh)
            if (
                isinstance(saved_geometry, str)
                and not is_legacy_startup_min_geometry(saved_geometry, self._prefs)
            ) else None
        )
        if clamped_geometry:
            startup_geometry = clamped_geometry
        else:
            startup_geometry = f"{default_w}x{default_h}+{x}+24"
        self._startup_geometry = startup_geometry
        self.geometry(startup_geometry)
        self.minsize(620, 430)

        self._q = queue.Queue()
        self._files: dict[str, dict] = {}  # path -> {row,status}
        self._cancel = threading.Event()
        self._running = False
        self._out_dirs: set[str] = set()
        self._file_scroll_job = None
        self._ui_scale = saved_scale
        self._pending_scale = saved_scale
        self._scale_job = None
        self._geometry_job = None
        self._geometry_save_ready = False
        self._is_scaling = False

        fams = set(tkfont.families(self))

        def pick(*prefs, fallback="Microsoft YaHei UI"):
            return next((f for f in prefs if f in fams), fallback)

        # CJK-aware UI fonts: prefer modern faces if installed, else YaHei
        ui = pick("MiSans", "HarmonyOS Sans SC", "OPPO Sans", "Sarasa UI SC",
                  "LXGW WenKai", "霞鹜文楷", "Noto Sans SC")
        serif = pick("LXGW WenKai", "霞鹜文楷", "Source Han Serif SC",
                     "思源宋体", "Noto Serif SC", "华文中宋", fallback="华文楷体")
        mono = pick("Sarasa Mono SC", "Cascadia Code", "JetBrains Mono",
                    "Fira Code", fallback="Consolas")

        self.f_body = ctk.CTkFont(family=ui, size=13)
        self.f_small = ctk.CTkFont(family=ui, size=11)
        self.f_section = ctk.CTkFont(family=ui, size=12, weight="bold")
        self.f_btn = ctk.CTkFont(family=ui, size=14, weight="bold")
        self.f_title = ctk.CTkFont(family=serif, size=26, weight="bold")
        self.f_sub = ctk.CTkFont(family=serif, size=12)
        self.f_mono = ctk.CTkFont(family=mono, size=11)

        cfg = load_config()
        self._cfg_services = [
            t.get("name") for t in cfg.get("translators", []) if t.get("name")
        ]
        self._profiles = load_profiles()
        logging.basicConfig(level=logging.INFO)
        logging.getLogger("pdf2zh").addHandler(QueueLogHandler(self._q))

        self._build_ui(self._service_options())
        self._select_default_service()
        self.drop_target_register(DND_FILES)
        self.dnd_bind("<<Drop>>", self._on_drop)

        # Ctrl+wheel zoom (debounced: a full re-scale costs ~0.4s)
        self.bind_all("<Control-MouseWheel>", self._on_ctrl_wheel)
        self.bind_all("<Control-Key-0>", self._reset_scale)
        self.bind("<Configure>", self._schedule_geometry_save, add="+")
        self.protocol("WM_DELETE_WINDOW", self._on_close)

        self.after(1200, self._enable_geometry_save)
        self.after(150, self._style_titlebar)
        self.after(100, self._poll)
        self.after_idle(self._show_startup_window)

    # ---------- zoom (Ctrl+wheel) ----------
    def _on_ctrl_wheel(self, event):
        step = 0.1 if event.delta > 0 else -0.1
        self._pending_scale = min(1.6, max(0.7, round(self._pending_scale + step, 2)))
        self._schedule_scale()
        return "break"

    def _show_startup_window(self):
        try:
            self.geometry(self._startup_geometry)
            self.update_idletasks()
            self.deiconify()
            self.geometry(self._startup_geometry)
            self.after(60, lambda: self.geometry(self._startup_geometry))
        except Exception:
            self.deiconify()

    def _reset_scale(self, _e=None):
        self._pending_scale = 1.0
        self._schedule_scale()

    def _schedule_scale(self):
        if self._scale_job is not None:
            self.after_cancel(self._scale_job)
        self._scale_job = self.after(220, self._apply_scale)

    def _apply_scale(self):
        self._scale_job = None
        if self._pending_scale == self._ui_scale:
            return
        self._ui_scale = self._pending_scale
        self._close_dropdowns()
        self._sync_ctk_window_size()
        if self._geometry_job is not None:
            self.after_cancel(self._geometry_job)
            self._geometry_job = None
        # Cover the window with a flat ivory curtain while the whole tree
        # re-lays out (~0.4s): one clean swap instead of piecemeal ghosting.
        # (WM_SETREDRAW freezing breaks Tk's internal buffer — don't.)
        curtain = ctk.CTkFrame(self, fg_color=IVORY, corner_radius=0)
        ctk.CTkLabel(
            curtain, text=f"{int(self._ui_scale * 100)}%",
            font=self.f_title, text_color=FAINT,
        ).place(relx=0.5, rely=0.45, anchor="center")
        curtain.place(relx=0, rely=0, relwidth=1, relheight=1)
        curtain.lift()
        self.update_idletasks()      # paint the curtain first
        self._is_scaling = True
        try:
            ctk.set_widget_scaling(self._ui_scale)
            if hasattr(self, "qa_box"):
                self._configure_qa_markdown_tags(
                    getattr(self, "_qa_answer_color", INK)
                )
            self.update_idletasks()  # settle the new layout beneath it
        finally:
            self._is_scaling = False
            curtain.destroy()
        self._q.put((
            "log",
            f"界面缩放 {int(self._ui_scale * 100)}%（Ctrl+0 复原）",
        ))
        self._save_ui_prefs()

    def _schedule_geometry_save(self, event=None):
        if event is not None and event.widget is not self:
            return
        if not getattr(self, "_geometry_save_ready", False):
            return
        if getattr(self, "_is_scaling", False):
            return
        if getattr(self, "_geometry_job", None) is not None:
            self.after_cancel(self._geometry_job)
        self._geometry_job = self.after(450, self._save_ui_prefs)

    def _enable_geometry_save(self):
        self._geometry_save_ready = True

    def _sync_ctk_window_size(self):
        try:
            self.update_idletasks()
            width = self.winfo_width()
            height = self.winfo_height()
            if width > 1 and height > 1:
                self._current_width = self._reverse_window_scaling(width)
                self._current_height = self._reverse_window_scaling(height)
        except Exception:
            pass

    def _close_dropdowns(self, widget=None):
        widget = widget or self
        for child in widget.winfo_children():
            if isinstance(child, PrettyOptionMenu):
                child._dropdown.close()
            self._close_dropdowns(child)

    def _save_ui_prefs(self):
        self._geometry_job = None
        try:
            geometry = self.geometry()
            if re.match(r"^\d+x\d+[+-]\d+[+-]\d+$", geometry):
                self._prefs["window_geometry"] = geometry
            self._prefs["ui_scale"] = self._ui_scale
            self._prefs["geometry_pref_version"] = GEOMETRY_PREF_VERSION
            save_prefs(self._prefs)
        except Exception:
            pass

    def _on_close(self):
        if getattr(self, "_geometry_job", None) is not None:
            try:
                self.after_cancel(self._geometry_job)
            except Exception:
                pass
            self._geometry_job = None
        self._save_ui_prefs()
        self.destroy()

    def _style_titlebar(self):
        """Cream Win11 title bar to blend with the ivory theme (no-op elsewhere)."""
        try:
            import ctypes

            hwnd = ctypes.windll.user32.GetParent(self.winfo_id())
            # caption #FAF9F5 (PAPER cream), text #1F1E1D (INK) — values are BGR
            for attr, val in ((35, 0x00F5F9FA), (36, 0x001D1E1F)):
                v = ctypes.c_int(val)
                ctypes.windll.dwmapi.DwmSetWindowAttribute(
                    hwnd, attr, ctypes.byref(v), 4
                )
        except Exception:
            pass

    # ---------- service profiles ----------
    def _service_options(self) -> list[str]:
        """Dropdown values: custom profiles, config translators, keyless."""
        names = [f"★ {p['display']}" for p in self._profiles]
        names += [s for s in self._cfg_services if s not in KEYLESS_SERVICES]
        names += list(KEYLESS_SERVICES)
        return list(dict.fromkeys(names)) or ["google"]

    def _select_default_service(self):
        opts = self._service_options()
        saved = self._prefs.get("service")
        self.service_var.set(saved if saved in opts else opts[0])

    def _on_service_selected(self, value):
        self._prefs["service"] = value
        self._save_settings_prefs()

    def _profile_by_display(self, display: str):
        return next((p for p in self._profiles if p["display"] == display), None)

    def _selected_service(self):
        """-> (service_str_for_pdf2zh, envs_dict_or_None)."""
        sel = self.service_var.get()
        if sel.startswith("★ "):
            p = self._profile_by_display(sel[2:])
            if p:
                schema = SERVICE_SCHEMAS[p["type"]]
                model_keys = [k for k, *_ in schema if k.endswith("_MODEL")]
                model = next(
                    (p["envs"][k] for k in model_keys if p["envs"].get(k)), ""
                )
                backend = SERVICE_BACKEND.get(p["type"], p["type"])
                service = f"{backend}:{model}" if model else backend
                return service, dict(p["envs"])
        return sel, None

    def _selected_ai_profile(self):
        """-> (stype, envs, model) of the selected ★ profile, else None."""
        sel = self.service_var.get()
        if sel.startswith("★ "):
            p = self._profile_by_display(sel[2:])
            if p:
                schema = SERVICE_SCHEMAS[p["type"]]
                model_keys = [k for k, *_ in schema if k.endswith("_MODEL")]
                model = next(
                    (p["envs"][k] for k in model_keys if p["envs"].get(k)), ""
                )
                return p["type"], dict(p["envs"]), model
        return None

    def _refresh_service_menu(self):
        self.service_menu.configure(values=self._service_options())

    def _manage_services(self):
        ServiceDialog(self, self._save_profile)

    def _edit_profile(self):
        sel = self.service_var.get()
        profile = self._profile_by_display(sel[2:]) if sel.startswith("★ ") else None
        if not profile:
            self._log("仅可编辑自定义服务（带 ★ 前缀），请先从下拉框选中")
            return
        ServiceDialog(self, self._save_profile, profile=profile)

    def _save_profile(self, profile: dict, old_display):
        self._profiles = [
            p for p in self._profiles
            if p["display"] not in (profile["display"], old_display)
        ]
        self._profiles.insert(0, profile)
        save_profiles(self._profiles)
        self._refresh_service_menu()
        self.service_var.set(f"★ {profile['display']}")
        self._on_service_selected(self.service_var.get())
        self._log(f"已保存服务：{profile['display']}（{SERVICE_TYPE_LABELS[profile['type']]}）")

    def _delete_profile(self):
        sel = self.service_var.get()
        if not sel.startswith("★ "):
            self._log("仅可删除自定义服务（带 ★ 前缀）")
            return
        name = sel[2:]
        self._profiles = [p for p in self._profiles if p["display"] != name]
        save_profiles(self._profiles)
        self._refresh_service_menu()
        self._select_default_service()
        self._on_service_selected(self.service_var.get())
        self._log(f"已删除服务：{name}")

    # ---------- styled widget helpers ----------
    def _card(self, parent=None, **kw):
        return ctk.CTkFrame(
            parent or self.page, fg_color=PAPER, corner_radius=14,
            border_width=1, border_color=LINE, **kw,
        )

    def _ghost_btn(self, parent, text, command, accent=False, **kw):
        return ctk.CTkButton(
            parent, text=text, command=command, font=self.f_body,
            fg_color="transparent", border_width=1,
            border_color=CLAY if accent else LINE,
            text_color=CLAY if accent else SLATE,
            hover_color=TINT if accent else GHOST_H,
            corner_radius=8, height=32, **kw,
        )

    def _menu(self, parent, var, values, width):
        return PrettyOptionMenu(parent, var, values, width, font=self.f_body)

    def _label(self, parent, text):
        return ctk.CTkLabel(parent, text=text, font=self.f_body, text_color=SLATE)

    # ---------- UI ----------
    def _build_ui(self, services):
        PADX = 16

        ctk.CTkFrame(self, height=1, fg_color=LINE, corner_radius=0).pack(fill="x")
        shell = ctk.CTkFrame(self, fg_color=IVORY, corner_radius=0)
        shell.pack(fill="both", expand=True)

        header = ctk.CTkFrame(shell, fg_color="transparent")
        header.pack(fill="x", padx=PADX, pady=(14, 6))
        tbox = ctk.CTkFrame(header, fg_color="transparent")
        tbox.pack(side="left", padx=(4, 0))
        ctk.CTkLabel(tbox, text="PDF Translator", font=self.f_title, text_color=INK).pack(
            anchor="w"
        )
        tabs = ctk.CTkFrame(
            header, fg_color=BAR_BG, corner_radius=10, border_width=1,
            border_color=LINE,
        )
        tabs.pack(side="right", padx=(12, 4), pady=2)
        self._tab_btns = {}
        for key, text in (
            ("translate", "PDF 翻译"),
            ("qa", "快问快答"),
            ("dict", "单词速查"),
            ("settings", "翻译设置"),
        ):
            btn = ctk.CTkButton(
                tabs, text=text, width=82, height=30, font=self.f_body,
                fg_color="transparent", hover_color=GHOST_H, text_color=SLATE,
                corner_radius=8, command=lambda k=key: self._show_tab(k),
            )
            btn.pack(side="left", padx=3, pady=3)
            self._tab_btns[key] = btn

        holder = ctk.CTkFrame(shell, fg_color="transparent")
        holder.pack(fill="both", expand=True)
        self._tab_pages = {
            "translate": ctk.CTkFrame(holder, fg_color="transparent"),
            "qa": ctk.CTkFrame(holder, fg_color="transparent"),
            "dict": ctk.CTkFrame(holder, fg_color="transparent"),
            "settings": ctk.CTkFrame(holder, fg_color="transparent"),
        }
        self.page = self._tab_pages["translate"]
        self._active_tab = None

        # file card (packed last so it absorbs shrink; bottom cards keep space)
        card = self._card()
        bar = ctk.CTkFrame(card, fg_color="transparent")
        bar.pack(fill="x", padx=12, pady=(10, 6))
        self._ghost_btn(bar, "＋ 添加 PDF", self._pick_files, accent=True, width=112).pack(
            side="left", padx=(0, 8)
        )
        self._ghost_btn(bar, "清空列表", self._clear_files, width=88).pack(side="left")
        ctk.CTkLabel(
            bar, text="或将 PDF 拖入窗口任意位置", font=self.f_small, text_color=FAINT
        ).pack(side="right", padx=4)

        file_box = ctk.CTkFrame(
            card, fg_color="transparent", height=92, corner_radius=0,
        )
        file_box.pack(fill="x", padx=12, pady=(0, 12))
        file_box.pack_propagate(False)
        self.file_frame = FastScrollFrame(
            file_box, fg_color=WHITE, corner_radius=10, height=75,
            border_width=1, border_color=LINE,
            scrollbar_button_color="#D8D3C6", scrollbar_button_hover_color="#C6BFAF",
        )
        self.file_frame.pack(fill="both", expand=True)
        self._empty_hint = ctk.CTkLabel(
            self.file_frame, text="将 PDF 拖到这里，或点击上方「添加 PDF」",
            font=self.f_body, text_color=FAINT, justify="center",
        )
        self._empty_hint.pack(pady=18)
        self.file_frame.set_scrollbar_visible(False)
        self.file_frame._parent_canvas.bind(
            "<Configure>", lambda _e: self._schedule_file_scrollbar_refresh(), add="+"
        )
        self.file_frame._parent_frame.bind(
            "<Configure>", lambda _e: self._schedule_file_scrollbar_refresh(), add="+"
        )
        self.after_idle(self._refresh_file_scrollbar)

        # settings card
        opt = self._card(parent=self._tab_pages["settings"])
        opt.grid_columnconfigure((1, 3, 5), weight=1)
        ctk.CTkLabel(opt, text="翻译设置", font=self.f_section, text_color=INK).grid(
            row=0, column=0, columnspan=6, padx=14, pady=(10, 0), sticky="w"
        )

        self._label(opt, "翻译服务").grid(row=1, column=0, padx=(14, 4), pady=8, sticky="e")
        svc_box = ctk.CTkFrame(opt, fg_color="transparent")
        svc_box.grid(row=1, column=1, columnspan=5, padx=4, pady=8, sticky="w")
        self.service_var = ctk.StringVar(value="google")
        self.service_menu = PrettyOptionMenu(
            svc_box, self.service_var, services, 240, font=self.f_body,
            command=self._on_service_selected,
        )
        self.service_menu.pack(side="left")
        ctk.CTkButton(
            svc_box, text="＋ 添加模型", width=92, height=30, font=self.f_body,
            fg_color="transparent", border_width=1, border_color=CLAY,
            text_color=CLAY, hover_color=TINT, corner_radius=8,
            command=self._manage_services,
        ).pack(side="left", padx=(8, 0))
        ctk.CTkButton(
            svc_box, text="编辑", width=56, height=30, font=self.f_body,
            fg_color="transparent", border_width=1, border_color=LINE,
            text_color=SLATE, hover_color=GHOST_H, corner_radius=8,
            command=self._edit_profile,
        ).pack(side="left", padx=(6, 0))
        ctk.CTkButton(
            svc_box, text="删除", width=56, height=30, font=self.f_body,
            fg_color="transparent", border_width=1, border_color=LINE,
            text_color=FAINT, hover_color=GHOST_H, corner_radius=8,
            command=self._delete_profile,
        ).pack(side="left", padx=(6, 0))

        # row 2 — what to produce: languages + output flavor
        self._label(opt, "源语言").grid(row=2, column=0, padx=(14, 4), pady=8, sticky="e")
        self.lang_in_var = ctk.StringVar(
            value=self._prefs.get("lang_in", "English")
            if self._prefs.get("lang_in") in LANGS else "English"
        )
        self._menu(opt, self.lang_in_var, list(LANGS), 152).grid(
            row=2, column=1, padx=4, pady=8, sticky="w"
        )
        self._label(opt, "目标语言").grid(row=2, column=2, padx=(12, 4), pady=8, sticky="e")
        self.lang_out_var = ctk.StringVar(
            value=self._prefs.get("lang_out", "简体中文")
            if self._prefs.get("lang_out") in LANGS else "简体中文"
        )
        self._menu(opt, self.lang_out_var, list(LANGS), 152).grid(
            row=2, column=3, padx=4, pady=8, sticky="w"
        )
        self._label(opt, "输出文件").grid(row=2, column=4, padx=(12, 4), pady=8, sticky="e")
        self.output_mode_var = ctk.StringVar(
            value=self._prefs.get("output_mode", "双语 + 中文")
            if self._prefs.get("output_mode") in OUTPUT_MODES else "双语 + 中文"
        )
        self._menu(opt, self.output_mode_var, list(OUTPUT_MODES), 152).grid(
            row=2, column=5, padx=(4, 14), pady=8, sticky="w"
        )

        # row 3 — how to run: scope, threads, cache toggle
        self._label(opt, "页码范围").grid(row=3, column=0, padx=(14, 4), pady=(2, 10), sticky="e")
        self.pages_entry = ctk.CTkEntry(
            opt, placeholder_text="全部，或如 1-5,8", width=152, height=30,
            fg_color=WHITE, border_color=LINE, border_width=1,
            text_color=INK, placeholder_text_color=FAINT,
            corner_radius=8, font=self.f_body,
        )
        self.pages_entry.grid(row=3, column=1, padx=4, pady=(2, 10), sticky="w")
        if self._prefs.get("pages"):
            self.pages_entry.insert(0, self._prefs["pages"])
        self._label(opt, "并发线程").grid(row=3, column=2, padx=(12, 4), pady=(2, 10), sticky="e")
        self.thread_var = ctk.StringVar(
            value=self._prefs.get("thread", "4")
            if self._prefs.get("thread") in {"1", "2", "4", "8"} else "4"
        )
        self._menu(opt, self.thread_var, ["1", "2", "4", "8"], 152).grid(
            row=3, column=3, padx=4, pady=(2, 10), sticky="w"
        )
        self.cache_var = ctk.BooleanVar(value=bool(self._prefs.get("ignore_cache", False)))
        ctk.CTkCheckBox(
            opt, text="忽略翻译缓存", variable=self.cache_var,
            font=self.f_body, text_color=SLATE,
            fg_color=CLAY, hover_color=CLAY_DK, checkmark_color=WHITE,
            border_color="#B9B3A5", border_width=2, corner_radius=6,
        ).grid(row=3, column=5, padx=(4, 14), pady=(2, 10), sticky="w")

        # row 4 — translator guidance baked into the LLM prompt, persisted
        self._label(opt, "注意事项").grid(
            row=4, column=0, padx=(14, 4), pady=(2, 12), sticky="e"
        )
        self.notes_entry = ctk.CTkTextbox(
            opt, height=164, wrap="word", activate_scrollbars=False,
            fg_color=WHITE, border_color=LINE, border_width=1,
            text_color=INK, corner_radius=8, font=self.f_body,
        )
        self.notes_entry.grid(
            row=4, column=1, columnspan=5, padx=(4, 14), pady=(2, 12), sticky="ew"
        )
        saved_notes = self._prefs.get("notes", DEFAULT_NOTES)
        if saved_notes:
            self.notes_entry.insert("1.0", saved_notes)

        # output card
        out = self._card()
        ctk.CTkLabel(out, text="输出位置", font=self.f_section, text_color=INK).pack(
            side="left", padx=(14, 10), pady=12
        )
        self.out_mode = ctk.StringVar(
            value=self._prefs.get("out_mode", "same")
            if self._prefs.get("out_mode") in {"same", "custom"} else "same"
        )
        rb_kw = dict(
            variable=self.out_mode, font=self.f_body, text_color=SLATE,
            fg_color=CLAY, hover_color=CLAY_DK, border_color="#B9B3A5",
        )
        ctk.CTkRadioButton(out, text="原 PDF 所在目录", value="same", **rb_kw).pack(
            side="left", padx=6
        )
        ctk.CTkRadioButton(out, text="指定：", value="custom", **rb_kw).pack(
            side="left", padx=(12, 2)
        )
        self.out_entry = ctk.CTkEntry(
            out, width=220, height=30, fg_color=WHITE, border_color=LINE,
            border_width=1, text_color=INK, corner_radius=8, font=self.f_body,
        )
        self.out_entry.pack(side="left", padx=2, fill="x", expand=True)
        if self._prefs.get("out_dir"):
            self.out_entry.insert(0, self._prefs["out_dir"])
        self._ghost_btn(out, "▾", self._browse_out, width=34).pack(
            side="left", padx=(8, 14)
        )

        # quick Q&A card (short context, current session only)
        self.qa_busy = False
        self.qa_history = []
        qa = self._card(parent=self._tab_pages["qa"])
        qa_top = ctk.CTkFrame(qa, fg_color="transparent")
        qa_top.pack(fill="x", padx=14, pady=(10, 4))
        ctk.CTkLabel(qa_top, text="快问快答", font=self.f_section, text_color=INK).pack(
            side="left"
        )
        ctk.CTkLabel(
            qa_top, text="使用翻译设置中的AI翻译服务，Enter快速提问",
            font=self.f_small, text_color=FAINT,
        ).pack(side="right")
        qa_bar = ctk.CTkFrame(qa, fg_color="transparent")
        qa_bar.pack(fill="x", padx=14, pady=(0, 8))
        self.qa_entry = ctk.CTkEntry(
            qa_bar, height=32, fg_color=WHITE, border_color=LINE, border_width=1,
            text_color=INK, placeholder_text_color=FAINT,
            corner_radius=8, font=self.f_body,
            placeholder_text="输入阅读文献时遇到的问题…",
        )
        self.qa_entry.pack(side="left", fill="x", expand=True)
        self.qa_entry.bind("<Return>", lambda _e: self._qa_ask())
        self.qa_btn = self._ghost_btn(
            qa_bar, "提问", self._qa_ask, accent=True, width=84
        )
        self.qa_btn.configure(height=32)
        self.qa_btn.pack(side="left", padx=(8, 0))
        self.qa_box = ctk.CTkTextbox(
            qa, height=300, state="disabled", wrap="word",
            fg_color=WHITE, text_color=FAINT, font=self.f_body,
            corner_radius=10, border_width=1, border_color=LINE,
            scrollbar_button_color="#D8D3C6", scrollbar_button_hover_color="#C6BFAF",
        )
        self.qa_box.pack(fill="x", padx=14, pady=(0, 12))
        self._qa_answer_color = INK
        self._configure_qa_markdown_tags()
        self._set_qa_text("问答结果会显示在这里", FAINT)

        # quick word lookup card (reuses the selected ★ AI service)
        dic = self._card(parent=self._tab_pages["dict"])
        dic_top = ctk.CTkFrame(dic, fg_color="transparent")
        dic_top.pack(fill="x", padx=14, pady=(10, 4))
        ctk.CTkLabel(dic_top, text="单词速查", font=self.f_section, text_color=INK).pack(
            side="left"
        )
        ctk.CTkLabel(
            dic_top, text="使用翻译设置中的AI翻译服务，Enter快速查询",
            font=self.f_small, text_color=FAINT,
        ).pack(side="right")
        dic_bar = ctk.CTkFrame(dic, fg_color="transparent")
        dic_bar.pack(fill="x", padx=14, pady=(0, 8))
        self.dict_entry = ctk.CTkEntry(
            dic_bar, height=32, fg_color=WHITE, border_color=LINE, border_width=1,
            text_color=INK, placeholder_text_color=FAINT,
            corner_radius=8, font=self.f_body,
            placeholder_text="输入英文单词 / 短语 / 中文词…",
        )
        self.dict_entry.pack(side="left", fill="x", expand=True)
        self.dict_entry.bind("<Return>", lambda _e: self._dict_lookup())
        self.dict_btn = self._ghost_btn(
            dic_bar, "查询", self._dict_lookup, accent=True, width=84
        )
        self.dict_btn.configure(height=32)
        self.dict_btn.pack(side="left", padx=(8, 0))
        self.dict_box = ctk.CTkTextbox(
            dic, height=300, state="disabled", wrap="word",
            fg_color=WHITE, text_color=FAINT, font=self.f_body,
            corner_radius=10, border_width=1, border_color=LINE,
            scrollbar_button_color="#D8D3C6", scrollbar_button_hover_color="#C6BFAF",
        )
        self.dict_box.pack(fill="x", padx=14, pady=(0, 12))
        self._set_dict_text("查询结果会显示在这里", FAINT)

        # run card
        run = self._card(height=110)
        run.pack_propagate(False)
        self.progress = ctk.CTkProgressBar(
            run, height=8, corner_radius=4, fg_color=BAR_BG, progress_color=CLAY
        )
        self.progress.set(0)
        self.progress.pack(fill="x", padx=14, pady=(14, 6))
        self.status_label = ctk.CTkLabel(
            run, text="就绪", font=self.f_small, text_color=SLATE
        )
        self.status_label.pack(anchor="w", padx=14, pady=(0, 4))

        btns = ctk.CTkFrame(run, fg_color="transparent", height=42)
        btns.pack(fill="x", padx=10, pady=(0, 12))
        btns.pack_propagate(False)
        self.start_btn = ctk.CTkButton(
            btns, text="开始翻译", width=150, height=38, command=self._start,
            font=self.f_btn, corner_radius=8,
            fg_color=CLAY, hover_color=CLAY_DK, text_color=WHITE,
            text_color_disabled="#EFD9CE",
        )
        self.start_btn.pack(side="left", padx=6)
        self.cancel_btn = self._ghost_btn(btns, "取消", self._cancel_run, width=90)
        self.cancel_btn.configure(state="disabled", height=38)
        self.cancel_btn.pack(side="left", padx=6)
        self.open_btn = self._ghost_btn(btns, "打开输出位置", self._open_out, width=120)
        self.open_btn.configure(state="disabled", height=38)
        self.open_btn.pack(side="left", padx=6)

        self.log_box = ctk.CTkTextbox(
            self.page, height=106, state="disabled", wrap="word",
            fg_color=WHITE, text_color=SLATE, font=self.f_mono,
            corner_radius=10, border_width=1, border_color=LINE,
            scrollbar_button_color="#D8D3C6", scrollbar_button_hover_color="#C6BFAF",
        )
        self._log_is_empty = True
        self._set_log_hint()
        # natural top-down flow inside the scrollable page
        card.pack(fill="x", padx=PADX, pady=6)
        out.pack(fill="x", padx=PADX, pady=6)
        run.pack(fill="x", padx=PADX, pady=6)
        self.log_box.pack(fill="x", padx=PADX, pady=(2, 14))
        opt.pack(fill="x", padx=PADX, pady=12)
        qa.pack(fill="x", padx=PADX, pady=12)
        dic.pack(fill="x", padx=PADX, pady=12)
        self._bind_setting_persistence()
        self._show_tab("translate")

    def _bind_setting_persistence(self):
        for var in (
            self.lang_in_var, self.lang_out_var, self.output_mode_var,
            self.thread_var, self.cache_var, self.out_mode,
        ):
            var.trace_add("write", lambda *_: self._save_settings_prefs())
        self.pages_entry.bind("<KeyRelease>", lambda _e: self._save_settings_prefs())
        self.notes_entry.bind("<KeyRelease>", lambda _e: self._save_settings_prefs())
        self.notes_entry.bind("<FocusOut>", lambda _e: self._save_settings_prefs())
        self.out_entry.bind("<KeyRelease>", lambda _e: self._save_settings_prefs())

    def _get_notes_text(self):
        return self.notes_entry.get("1.0", "end").strip()

    def _save_settings_prefs(self):
        try:
            self._prefs.update({
                "service": self.service_var.get(),
                "lang_in": self.lang_in_var.get(),
                "lang_out": self.lang_out_var.get(),
                "output_mode": self.output_mode_var.get(),
                "pages": self.pages_entry.get().strip(),
                "thread": self.thread_var.get(),
                "ignore_cache": bool(self.cache_var.get()),
                "notes": self._get_notes_text(),
                "out_mode": self.out_mode.get(),
                "out_dir": self.out_entry.get().strip(),
            })
            save_prefs(self._prefs)
        except Exception:
            pass

    def _show_tab(self, key):
        if key not in getattr(self, "_tab_pages", {}):
            return
        for tab_key, page in self._tab_pages.items():
            if tab_key == key:
                page.pack(fill="both", expand=True)
            else:
                page.pack_forget()
        for tab_key, btn in self._tab_btns.items():
            selected = tab_key == key
            btn.configure(
                fg_color=PAPER if selected else "transparent",
                text_color=INK if selected else SLATE,
                hover_color=PAPER if selected else GHOST_H,
            )
        self._active_tab = key

    # ---------- quick Q&A ----------
    def _qa_font(self, size=13, weight="normal", family=None):
        scale = getattr(self, "_ui_scale", 1.0) or 1.0
        base_family = family or self.f_body.cget("family")
        return (
            base_family,
            max(1, int(round(size * scale))),
            weight,
        )

    def _qa_rule(self):
        try:
            self.qa_box.update_idletasks()
            text_widget = self.qa_box._textbox
            width = max(text_widget.winfo_width(), self.qa_box.winfo_width(), 520) - 28
            font = tkfont.Font(font=self._qa_font(13))
            char_width = max(1, font.measure(QA_RULE_CHAR))
            return QA_RULE_CHAR * max(48, min(160, width // char_width))
        except Exception:
            return QA_RULE_CHAR * 72

    def _configure_qa_markdown_tags(self, answer_color=INK):
        try:
            tb = self.qa_box._textbox
            tb.tag_config("qa_question", foreground=INK, font=self._qa_font(15, "bold"))
            tb.tag_config("qa_answer", foreground=answer_color, font=self._qa_font(15))
            tb.tag_config("qa_rule", foreground=LINE, font=self._qa_font(13))
            tb.tag_config("md_h", foreground=answer_color, font=self._qa_font(17, "bold"))
            tb.tag_config("md_bold", foreground=answer_color, font=self._qa_font(15, "bold"))
            tb.tag_config(
                "md_code",
                foreground=answer_color,
                background=IVORY,
                font=self._qa_font(15, family=self.f_mono.cget("family")),
            )
            tb.tag_config(
                "md_code_block",
                foreground=answer_color,
                background=IVORY,
                lmargin1=8,
                lmargin2=8,
                spacing1=3,
                spacing3=3,
                font=self._qa_font(15, family=self.f_mono.cget("family")),
            )
            tb.tag_config("md_quote", foreground=SLATE, lmargin1=12, lmargin2=12)
            tb.tag_config("md_list", lmargin1=14, lmargin2=28)
        except Exception:
            pass

    def _set_qa_text(self, text, color=INK, focus_latest_answer=False):
        self.qa_box.configure(state="normal")
        self.qa_box.delete("0.0", "end")
        self.qa_box.insert("0.0", text)
        self.qa_box.configure(text_color=color, state="disabled")
        if focus_latest_answer:
            answer_pos = text.rfind("\n\nA：")
            if answer_pos < 0 and text.startswith("A："):
                answer_pos = 0
            elif answer_pos >= 0:
                answer_pos += 2
            if answer_pos >= 0:
                target = f"1.0 + {answer_pos} chars"
                try:
                    self.qa_box.yview(target)
                    self.qa_box.after_idle(lambda: self.qa_box.yview(target))
                except Exception:
                    try:
                        self.qa_box.see(target)
                        self.qa_box.after_idle(lambda: self.qa_box.see(target))
                    except Exception:
                        pass

    def _insert_markdown_line(self, line, default_tags=("qa_answer",)):
        raw = line.rstrip()
        stripped = raw.strip()
        if not stripped:
            self.qa_box.insert("end", "\n", default_tags)
            return
        heading = re.match(r"^(#{1,4})\s+(.+)$", stripped)
        if heading:
            self.qa_box.insert("end", heading.group(2).strip() + "\n", ("qa_answer", "md_h"))
            return
        quote = re.match(r"^>\s?(.*)$", raw)
        if quote:
            self.qa_box.insert("end", quote.group(1) + "\n", ("qa_answer", "md_quote"))
            return
        item = re.match(r"^(\s*)([-*+]|\d+[.)])\s+(.+)$", raw)
        if item:
            bullet = "• " if not re.match(r"\d", item.group(2)) else f"{item.group(2)} "
            self.qa_box.insert("end", bullet, ("qa_answer", "md_list"))
            self._insert_markdown_inline(item.group(3), ("qa_answer", "md_list"))
            self.qa_box.insert("end", "\n", ("qa_answer", "md_list"))
            return
        self._insert_markdown_inline(raw, default_tags)
        self.qa_box.insert("end", "\n", default_tags)

    def _insert_markdown_inline(self, text, default_tags=("qa_answer",)):
        pattern = re.compile(r"(`[^`]+`|\*\*[^*]+\*\*)")
        pos = 0
        for match in pattern.finditer(text):
            if match.start() > pos:
                self.qa_box.insert("end", text[pos:match.start()], default_tags)
            token = match.group(0)
            if token.startswith("`"):
                self.qa_box.insert("end", token[1:-1], default_tags + ("md_code",))
            else:
                self.qa_box.insert("end", token[2:-2], default_tags + ("md_bold",))
            pos = match.end()
        if pos < len(text):
            self.qa_box.insert("end", text[pos:], default_tags)

    def _insert_markdown(self, text):
        in_code = False
        code_lines = []
        for line in text.splitlines():
            if line.strip().startswith("```"):
                if in_code:
                    self.qa_box.insert(
                        "end", "\n".join(code_lines).rstrip() + "\n", ("qa_answer", "md_code_block")
                    )
                    code_lines = []
                    in_code = False
                else:
                    in_code = True
                    code_lines = []
                continue
            if in_code:
                code_lines.append(line)
            else:
                self._insert_markdown_line(line)
        if in_code or code_lines:
            self.qa_box.insert(
                "end", "\n".join(code_lines).rstrip() + "\n", ("qa_answer", "md_code_block")
            )

    def _render_qa_history(self, color=INK, focus_latest_answer=False):
        self._qa_answer_color = color
        self._configure_qa_markdown_tags(color)
        self.qa_box.configure(state="normal", text_color=color)
        self.qa_box.delete("0.0", "end")
        visible_items = [
            item for item in self.qa_history
            if item.get("question", "").strip() and item.get("answer", "").strip()
        ]
        if not visible_items:
            self.qa_box.insert("0.0", "问答结果会显示在这里")
        inner_separator = self._qa_rule()
        for index, item in enumerate(visible_items):
            if index:
                self.qa_box.insert("end", "\n\n", ("qa_answer",))
            self.qa_box.insert("end", f"Q：{item.get('question', '').strip()}\n", ("qa_question",))
            self.qa_box.insert("end", f"{inner_separator}\n", ("qa_rule",))
            if index == len(visible_items) - 1:
                self.qa_box.mark_set("latest_answer", "end")
            self.qa_box.insert("end", "A：", ("qa_answer", "md_bold"))
            answer = item.get("answer", "").strip()
            if answer:
                self.qa_box.insert("end", "\n", ("qa_answer",))
                self._insert_markdown(answer)
            else:
                self.qa_box.insert("end", "\n", ("qa_answer",))
        self.qa_box.configure(state="disabled")
        if focus_latest_answer and visible_items:
            try:
                self.qa_box.yview("latest_answer")
                self.qa_box.after_idle(lambda: self.qa_box.yview("latest_answer"))
            except Exception:
                try:
                    self.qa_box.see("latest_answer")
                    self.qa_box.after_idle(lambda: self.qa_box.see("latest_answer"))
                except Exception:
                    pass

    def _format_qa_history(self):
        if not self.qa_history:
            return "问答结果会显示在这里"
        inner_separator = self._qa_rule()
        chunks = []
        for item in self.qa_history:
            question = item.get("question", "").strip()
            answer = item.get("answer", "").strip()
            if question and answer:
                chunks.append(f"Q：{question}\n{inner_separator}\nA：{answer}")
        return "\n\n".join(chunks) if chunks else "问答结果会显示在这里"

    def _qa_ask(self):
        question = self.qa_entry.get().strip()
        if not question:
            self._set_qa_text("请输入要提问的内容", FAINT)
            return
        if self.qa_busy:
            return
        prof = self._selected_ai_profile()
        if prof is None:
            self._set_qa_text(
                "快问快答需要在「翻译设置」选择带 ★ 的 AI 服务"
                "（google/bing 不支持）", COLOR_FAIL,
            )
            return
        stype, envs, model = prof
        self.qa_busy = True
        self.qa_btn.configure(state="disabled")
        history = [
            item for item in self.qa_history
            if not item.get("pending")
            and not str(item.get("answer", "")).startswith("提问失败：")
        ][-QA_HISTORY_LIMIT:]
        self.qa_history.append({
            "question": question,
            "answer": "正在思考...",
            "pending": True,
        })
        self.qa_history = self.qa_history[-QA_HISTORY_LIMIT:]
        self._render_qa_history(SLATE, focus_latest_answer=True)
        threading.Thread(
            target=self._do_qa_ask, args=(stype, envs, model, question, history),
            daemon=True,
        ).start()

    def _do_qa_ask(self, stype, envs, model, question, history):
        try:
            result = quick_ask(stype, envs, model, question, history)
            if not result:
                raise ValueError("接口返回为空")
            self._q.put(("qa_result", question, result, INK))
        except Exception as e:
            self._q.put(("qa_error", question, f"提问失败：{e}", COLOR_FAIL))

    # ---------- quick dictionary ----------
    def _set_dict_text(self, text, color=INK):
        self.dict_box.configure(state="normal")
        self.dict_box.delete("0.0", "end")
        self.dict_box.insert("0.0", text)
        self.dict_box.configure(text_color=color, state="disabled")

    def _dict_lookup(self):
        query = self.dict_entry.get().strip()
        if not query:
            self._set_dict_text("请输入要查询的单词或短语", FAINT)
            return
        prof = self._selected_ai_profile()
        if prof is None:
            self._set_dict_text(
                "单词速查需要 AI 服务：请在「翻译服务」选择带 ★ 的自定义服务"
                "（google/bing 不支持）", COLOR_FAIL,
            )
            return
        stype, envs, model = prof
        self.dict_btn.configure(state="disabled")
        self._set_dict_text(f"⏳ 正在查询 {query} ...", SLATE)
        threading.Thread(
            target=self._do_dict_lookup, args=(stype, envs, model, query),
            daemon=True,
        ).start()

    def _do_dict_lookup(self, stype, envs, model, query):
        try:
            result = quick_translate(stype, envs, model, query)
            if not result:
                raise ValueError("接口返回为空")
            self._q.put(("dict_result", f"{query}\n\n{result}", INK))
        except Exception as e:
            self._q.put(("dict_result", f"查询失败：{e}", COLOR_FAIL))

    # ---------- file list ----------
    def _on_drop(self, event):
        paths = self.tk.splitlist(event.data)
        pdfs = [p for p in paths if p.lower().endswith(".pdf")]
        skipped = len(paths) - len(pdfs)
        if skipped:
            self._log(f"已忽略 {skipped} 个非 PDF 文件")
        self._add_files(pdfs)

    def _pick_files(self):
        paths = filedialog.askopenfilenames(
            title="选择 PDF 文件", filetypes=[("PDF 文件", "*.pdf")]
        )
        self._add_files(paths)

    def _add_files(self, paths):
        if self._running:
            return
        for p in paths:
            p = str(Path(p))
            if p in self._files or not Path(p).is_file():
                continue
            self._empty_hint.pack_forget()
            row = ctk.CTkFrame(self.file_frame, fg_color="transparent")
            row.pack(fill="x", pady=2)
            ctk.CTkButton(
                row, text="✕", width=26, height=24, font=self.f_body,
                fg_color="transparent", hover_color=TINT, text_color=FAINT,
                command=lambda _p=p: self._remove_file(_p),
            ).pack(side="left", padx=(2, 6))
            ctk.CTkLabel(
                row, text=Path(p).name, anchor="w", font=self.f_body, text_color=INK
            ).pack(side="left", fill="x", expand=True)
            status = ctk.CTkLabel(
                row, text="等待", width=110, anchor="e",
                font=self.f_small, text_color=COLOR_WAIT,
            )
            status.pack(side="right", padx=8)
            self._files[p] = {"row": row, "status": status}
        if self._files:
            self._log(f"列表共 {len(self._files)} 个文件")
        self._schedule_file_scrollbar_refresh()

    def _remove_file(self, path):
        if self._running:
            return
        info = self._files.pop(path, None)
        if info:
            info["row"].destroy()
        if not self._files:
            self._empty_hint.pack(pady=18)
        self._schedule_file_scrollbar_refresh()

    def _clear_files(self):
        if self._running:
            return
        for info in self._files.values():
            info["row"].destroy()
        self._files.clear()
        self._empty_hint.pack(pady=18)
        self._schedule_file_scrollbar_refresh()

    def _schedule_file_scrollbar_refresh(self):
        if self._file_scroll_job is not None:
            try:
                self.after_cancel(self._file_scroll_job)
            except Exception:
                pass
        self._file_scroll_job = self.after(80, self._refresh_file_scrollbar)

    def _refresh_file_scrollbar(self):
        self._file_scroll_job = None
        try:
            self.file_frame.update_idletasks()
            canvas = self.file_frame._parent_canvas
            content = canvas.bbox("all")
            content_h = (content[3] - content[1]) if content else 0
            visible_h = canvas.winfo_height()
            if self._files and content_h > visible_h + 2:
                self.file_frame.set_scrollbar_visible(True)
            else:
                self.file_frame.set_scrollbar_visible(False)
        except Exception:
            pass

    def _set_status(self, path, text, color):
        info = self._files.get(path)
        if info:
            info["status"].configure(text=text, text_color=color)

    # ---------- run ----------
    def _browse_out(self):
        d = filedialog.askdirectory(title="选择输出目录")
        if d:
            self.out_mode.set("custom")
            self.out_entry.delete(0, "end")
            self.out_entry.insert(0, d)
            self._save_settings_prefs()

    def _start(self):
        if self._running or not self._files:
            if not self._files:
                self._log("请先添加 PDF 文件")
            return
        try:
            pages = parse_pages(self.pages_entry.get())
        except ValueError as e:
            self._log(f"页码格式有误：{e}，示例：1-5,8")
            return
        output = ""
        if self.out_mode.get() == "custom":
            output = self.out_entry.get().strip()
            if not output:
                self._log("请填写输出目录，或选择「原 PDF 所在目录」")
                return
        service, envs = self._selected_service()

        notes = self._get_notes_text()
        self._save_settings_prefs()

        params = {
            "pages": pages,
            "lang_in": LANGS[self.lang_in_var.get()],
            "lang_out": LANGS[self.lang_out_var.get()],
            "service": service,
            "envs": envs,
            "thread": int(self.thread_var.get()),
            "output": output,
            "output_mode": OUTPUT_MODES[self.output_mode_var.get()],
            "ignore_cache": self.cache_var.get(),
            "prompt": build_prompt_template(notes),
        }
        files = list(self._files)
        for p in files:
            self._set_status(p, "等待", COLOR_WAIT)
        self._out_dirs.clear()
        self._cancel.clear()
        self._set_running(True)
        self.progress.set(0)
        threading.Thread(
            target=self._worker, args=(files, params, self._cancel), daemon=True
        ).start()

    def _cancel_run(self):
        if self._running:
            self._cancel.set()
            self._log("正在取消（当前页处理完后停止）...")

    def _set_running(self, running):
        self._running = running
        state = "disabled" if running else "normal"
        self.start_btn.configure(state=state)
        self.cancel_btn.configure(state="normal" if running else "disabled")

    def _open_out(self):
        for d in self._out_dirs or {str(Path.cwd())}:
            try:
                os.startfile(d)
            except OSError:
                self._log(f"无法打开目录：{d}")

    @staticmethod
    def _write_selected_outputs(out_dir, filename, mono_bytes, dual_bytes, output_mode):
        outputs = {
            "mono": (f"{filename}-mono.pdf", mono_bytes, "中文"),
            "dual": (f"{filename}-dual.pdf", dual_bytes, "双语"),
        }
        keep_keys = {
            "both": ("dual", "mono"),
            "dual": ("dual",),
            "mono": ("mono",),
        }.get(output_mode, ("dual", "mono"))

        kept = []
        for key in keep_keys:
            name, data, label = outputs[key]
            path = out_dir / name
            with open(path, "wb") as out_pdf:
                out_pdf.write(data)
            kept.append((path, label))
        return kept

    # ---------- worker (background thread) ----------
    def _worker(self, files, params, cancel):
        q = self._q
        try:
            q.put(("status", "正在加载 pdf2zh 核心与布局模型（首次需十几秒）..."))
            import asyncio
            patch_pdf2zh_runtime()
            from pdf2zh.doclayout import OnnxModel
            from pdf2zh.high_level import translate_stream

            if App._model is None:
                App._model = OnnxModel.load_available()
            q.put(("log", "布局模型就绪"))

            n = len(files)
            for i, f in enumerate(files, 1):
                if cancel.is_set():
                    q.put(("file_status", f, "已取消", COLOR_FAIL))
                    continue
                out_dir = Path(params["output"]) if params["output"] else Path(f).parent
                out_dir.mkdir(parents=True, exist_ok=True)
                q.put(("file_status", f, "翻译中", COLOR_RUN))
                q.put(("status", f"文件 {i}/{n}：{Path(f).name}"))

                def cb(t, _f=f, _i=i, _n=n):
                    total = getattr(t, "total", None) or 0
                    done = getattr(t, "n", 0)
                    q.put(("page", done, total, _f, _i, _n))

                try:
                    s_raw = Path(f).read_bytes()
                    s_mono, s_dual = translate_stream(
                        s_raw,
                        pages=params["pages"],
                        lang_in=params["lang_in"],
                        lang_out=params["lang_out"],
                        service=params["service"],
                        envs=params["envs"],
                        thread=params["thread"],
                        callback=cb,
                        cancellation_event=cancel,
                        model=App._model,
                        ignore_cache=params["ignore_cache"],
                        prompt=params["prompt"],
                    )
                    kept = self._write_selected_outputs(
                        out_dir, Path(f).stem, s_mono, s_dual, params["output_mode"]
                    )
                    kept_text = " / ".join(
                        f"{path.name}（{label}）" for path, label in kept
                    )
                    q.put(("file_status", f, "完成", COLOR_OK))
                    q.put(("log", f"完成：{kept_text}"))
                    q.put(("done_dir", str(out_dir)))
                except asyncio.CancelledError:
                    q.put(("file_status", f, "已取消", COLOR_FAIL))
                    q.put(("log", f"已取消：{Path(f).name}"))
                except Exception:
                    q.put(("file_status", f, "失败", COLOR_FAIL))
                    q.put(("log", f"失败:{Path(f).name}\n{traceback.format_exc(limit=3)}"))
        except Exception:
            q.put(("log", "初始化失败：\n" + traceback.format_exc(limit=5)))
        finally:
            q.put(("all_done",))

    # ---------- event pump ----------
    def _poll(self):
        try:
            while True:
                self._handle(self._q.get_nowait())
        except queue.Empty:
            pass
        self.after(100, self._poll)

    def _handle(self, ev):
        kind = ev[0]
        if kind == "log":
            self._log(ev[1])
        elif kind == "status":
            self.status_label.configure(text=ev[1])
        elif kind == "file_status":
            self._set_status(ev[1], ev[2], ev[3])
        elif kind == "page":
            done, total, f, i, n = ev[1:]
            if total:
                self.progress.set(done / total)
                self.status_label.configure(
                    text=f"文件 {i}/{n}：{Path(f).name} · 第 {done}/{total} 页"
                )
                self._set_status(f, f"翻译中 {done}/{total}", COLOR_RUN)
        elif kind == "done_dir":
            self._out_dirs.add(ev[1])
            self.open_btn.configure(state="normal")
        elif kind == "qa_result":
            _, question, answer, color = ev
            if (
                self.qa_history
                and self.qa_history[-1].get("pending")
                and self.qa_history[-1].get("question") == question
            ):
                self.qa_history[-1]["answer"] = answer
                self.qa_history[-1]["pending"] = False
            else:
                self.qa_history.append({"question": question, "answer": answer})
            self.qa_history = self.qa_history[-QA_HISTORY_LIMIT:]
            self._render_qa_history(color, focus_latest_answer=True)
            self.qa_entry.delete(0, "end")
            self.qa_btn.configure(state="normal")
            self.qa_busy = False
        elif kind == "qa_error":
            _, question, answer, color = ev
            if (
                self.qa_history
                and self.qa_history[-1].get("pending")
                and self.qa_history[-1].get("question") == question
            ):
                self.qa_history[-1]["answer"] = answer
                self.qa_history[-1]["pending"] = False
            else:
                self.qa_history.append({"question": question, "answer": answer})
            self.qa_history = self.qa_history[-QA_HISTORY_LIMIT:]
            self._render_qa_history(color, focus_latest_answer=True)
            self.qa_btn.configure(state="normal")
            self.qa_busy = False
        elif kind == "dict_result":
            self._set_dict_text(ev[1], ev[2])
            self.dict_btn.configure(state="normal")
        elif kind == "all_done":
            self._set_running(False)
            self.progress.set(1 if self._out_dirs else 0)
            self.status_label.configure(
                text="已完成" if not self._cancel.is_set() else "已取消"
            )

    def _log(self, msg):
        msg = str(msg).rstrip()
        if not msg:
            return
        self.log_box.configure(state="normal")
        if getattr(self, "_log_is_empty", False):
            self.log_box.delete("0.0", "end")
            self.log_box.configure(text_color=SLATE)
            self._log_is_empty = False
        self.log_box.insert("end", msg + "\n")
        self.log_box.see("end")
        self.log_box.configure(state="disabled")

    def _set_log_hint(self):
        self.log_box.configure(state="normal", text_color=FAINT)
        self.log_box.delete("0.0", "end")
        self.log_box.insert(
            "0.0",
            "暂无输出\n开始翻译后，这里会显示运行状态、输出文件和错误信息\nCtrl+滚轮以缩放界面",
        )
        self.log_box.configure(state="disabled")


def main():
    ctk.set_appearance_mode("light")
    ctk.set_default_color_theme("blue")
    app = App()
    app.mainloop()


if __name__ == "__main__":
    main()
