"""
OpsLens - local model client (LM Studio).

The local model runs in LM Studio (or LM Studio Bionic) on this computer and is
reached through its Local Model API, which speaks the OpenAI chat format
(default http://127.0.0.1:1234/v1). Nothing leaves the computer.

1. local_health(): is the server up, is the model there and loaded.
2. warm_up_local(): load the model before the first question.
3. call_local_edge_slm(): one short completion (guidance text).
4. call_inprocess_slm(): optional Transformers model inside Python.
"""

import os
import re
import json
import logging
from typing import Optional, Dict, Any, List
import requests

logger = logging.getLogger("opslens.edge_slm")
if not logger.handlers:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

# 127.0.0.1, not localhost: on Windows "localhost" tries ::1 first, which can
# cost ~2 s per request before falling back to IPv4.
DEFAULT_EDGE_URL = "http://127.0.0.1:1234"
DEFAULT_EDGE_MODEL = "qwen2.5-7b-instruct"
DEFAULT_TIMEOUT = float(os.environ.get("EDGE_LLM_TIMEOUT", "60"))
# Our diagnosis prompt is 5,000-7,000 tokens; a smaller loaded context cuts it off.
MIN_CONTEXT = 8192

# Global handle for lazy-loaded in-process SLM pipeline
_inprocess_pipeline = None


def check_online_availability(timeout: float = 0.5) -> bool:
    """
    Fast check to determine if the system is operating online with cloud LLM connectivity.
    Returns False if OPSLENS_OFFLINE is set, no API keys are present, or internet is unreachable.
    """
    if os.environ.get("OPSLENS_OFFLINE", "false").lower() in ("true", "1", "yes"):
        return False

    has_cloud_key = bool(
        os.environ.get("GEMINI_API_KEY") or
        os.environ.get("OPENAI_API_KEY") or
        os.environ.get("ANTHROPIC_API_KEY")
    )
    if not has_cloud_key:
        return False

    try:
        resp = requests.head("https://generativelanguage.googleapis.com", timeout=timeout)
        return resp.status_code < 500
    except Exception:
        try:
            resp = requests.head("https://www.google.com", timeout=timeout)
            return resp.status_code < 500
        except Exception:
            return False


def normalize_local_url(url: str) -> str:
    """IPv4 loopback, no trailing slash and no trailing /v1 (added per request)."""
    url = re.sub(r"^(https?://)localhost(?=[:/]|$)", r"\g<1>127.0.0.1", url.strip().rstrip("/"), flags=re.I)
    return re.sub(r"/v1$", "", url)


_warned_old_port = False


def local_base() -> str:
    """Base URL of the LM Studio server, without /v1. An older .env may still
    point at Ollama's port 11434; LM Studio's default 1234 is used instead."""
    global _warned_old_port
    url = normalize_local_url(os.environ.get("EDGE_LLM_URL") or DEFAULT_EDGE_URL)
    if re.search(r":11434$", url):
        if not _warned_old_port:
            _warned_old_port = True
            logger.warning("EDGE_LLM_URL points at Ollama's port 11434; using LM Studio at %s. Update edge_intelligence\\.env.", DEFAULT_EDGE_URL)
        return DEFAULT_EDGE_URL
    return url


def _edge_url(url: Optional[str] = None) -> str:
    return normalize_local_url(url) if url else local_base()


def local_session() -> requests.Session:
    """Session for the local model server. Ignores system proxy settings:
    a corporate or Windows proxy must never sit between OpsLens and 127.0.0.1."""
    session = requests.Session()
    session.trust_env = False
    return session


def local_model() -> str:
    return (os.environ.get("EDGE_LLM_MODEL") or DEFAULT_EDGE_MODEL).strip()


def _edge_model(model: Optional[str] = None) -> str:
    return model or local_model()


def _env_float(names: List[str], default: float, minimum: float) -> float:
    for name in names:
        value = os.environ.get(name)
        if value:
            try:
                return max(minimum, float(value))
            except ValueError:
                break
    return default


def local_llm_timeout() -> float:
    """Read timeout for one local answer. A 7B model on a laptop, including a
    cold model load, routinely takes 10-60 s."""
    return _env_float(["OPSLENS_LOCAL_TIMEOUT", "EDGE_LLM_TIMEOUT", "OPSLENS_LLM_TIMEOUT"], 60.0, 1.0)


def local_connect_timeout() -> float:
    return _env_float(["OPSLENS_LOCAL_CONNECT_TIMEOUT"], 2.0, 0.2)


def first_token_timeout() -> float:
    """Longest wait for the first word of a diagnosis. Includes loading the
    model and reading the prompt, which on a CPU can take minutes."""
    return _env_float(["OPSLENS_DIAG_FIRST_TOKEN_TIMEOUT"], 240.0, 5.0)


def total_generation_timeout() -> float:
    """Hard cap on one diagnosis, so a stalled model cannot hold the queue."""
    return _env_float(["OPSLENS_DIAG_TOTAL_TIMEOUT"], 480.0, 10.0)


def _norm(name: str) -> str:
    return re.sub(r"[^a-z0-9.]+", "", name.lower().replace("-gguf", "").split("/")[-1])


def model_matches(wanted: str, available: str) -> bool:
    """LM Studio names the same model 'qwen2.5-7b-instruct' or
    'lmstudio-community/Qwen2.5-7B-Instruct-GGUF'; treat those as one."""
    a, b = _norm(wanted), _norm(available)
    return bool(a) and (a == b or a in b or b in a)


def local_health(url: Optional[str] = None, timeout: float = 1.5) -> Dict[str, Any]:
    """Whether LM Studio's Local Model API is up, the model is there and loaded,
    and its context is large enough. Never raises. 'error_kind' lets callers give
    a precise fix."""
    base_url = _edge_url(url)
    model_name = _edge_model()
    result: Dict[str, Any] = {"url": f"{base_url}/v1", "model": model_name, "server": "LM Studio",
                              "reachable": False, "model_installed": False, "model_loaded": False,
                              "context_length": None, "models": [], "warning": None,
                              "error": None, "error_kind": None}
    try:
        with local_session() as session:
            # LM Studio's own listing says which models are loaded and with what context.
            detail = session.get(f"{base_url}/api/v0/models", timeout=timeout)
            if detail.status_code == 200:
                result["reachable"] = True
                rows = [m for m in (detail.json().get("data") or []) if m.get("type", "llm") in ("llm", "vlm")]
                result["models"] = [m.get("id") for m in rows if m.get("id")]
                match = next((m for m in rows if model_matches(model_name, m.get("id") or "")), None)
                if match:
                    result["model_installed"] = True
                    result["model_loaded"] = match.get("state") == "loaded"
                    result["context_length"] = match.get("loaded_context_length") or None
            else:
                plain = session.get(f"{base_url}/v1/models", timeout=timeout)
                if plain.status_code != 200:
                    result["error"] = f"LM Studio returned HTTP {plain.status_code} for /v1/models"
                    result["error_kind"] = "unavailable"
                    return result
                result["reachable"] = True
                result["models"] = [m.get("id") for m in (plain.json().get("data") or []) if m.get("id")]
                result["model_installed"] = any(model_matches(model_name, m) for m in result["models"])
                result["model_loaded"] = result["model_installed"]
    except requests.exceptions.ConnectionError:
        result["error_kind"] = "unreachable"
        result["error"] = (f"LM Studio's Local Model API is not running at {base_url}. "
                           "Start it in LM Studio (Developer / Local Model API) or run: lms server start")
        return result
    except requests.exceptions.Timeout:
        result["error_kind"] = "timeout"
        result["error"] = "LM Studio did not answer the health check in time."
        return result
    except (requests.exceptions.RequestException, ValueError) as exc:
        logger.warning("[EdgeSLM] LM Studio health check failed: %s", exc)
        result["error_kind"] = "unavailable"
        result["error"] = f"LM Studio health check failed: {type(exc).__name__}"
        return result
    if not result["model_installed"]:
        result["error_kind"] = "model_missing"
        result["error"] = (f"Model '{model_name}' is not in LM Studio (found: {', '.join(result['models']) or 'none'}). "
                           f"Download it in LM Studio, or set EDGE_LLM_MODEL to one of the names listed.")
    elif result["context_length"] and result["context_length"] < MIN_CONTEXT:
        result["warning"] = (f"The model is loaded with a {result['context_length']}-token context; OpsLens needs at least "
                             f"{MIN_CONTEXT}. Reload it in LM Studio with Context Length {MIN_CONTEXT} or more.")
    return result


def warm_up_local(url: Optional[str] = None) -> Dict[str, Any]:
    """Ask for one word so LM Studio loads the model before the first question
    (LM Studio loads a model on first use). Safe to call when LM Studio is off."""
    base_url = _edge_url(url)
    model_name = _edge_model()
    try:
        with local_session() as session:
            resp = session.post(
                f"{base_url}/v1/chat/completions",
                json={"model": model_name, "messages": [{"role": "user", "content": "Reply OK."}], "max_tokens": 1, "stream": False},
                timeout=(local_connect_timeout(), max(local_llm_timeout(), first_token_timeout())),
            )
        return {"model": model_name, "loaded": resp.status_code == 200, "http_status": resp.status_code}
    except requests.exceptions.RequestException as exc:
        logger.warning("[EdgeSLM] Warm-up of '%s' failed: %s", model_name, exc)
        return {"model": model_name, "loaded": False, "error": type(exc).__name__}


def check_edge_slm_availability(url: Optional[str] = None, timeout: float = 0.5) -> bool:
    """Quick check that the local model server answers."""
    try:
        with local_session() as session:
            return session.get(f"{_edge_url(url)}/v1/models", timeout=timeout).status_code == 200
    except Exception:
        return False


def call_local_edge_slm(
    prompt: str,
    url: Optional[str] = None,
    model: Optional[str] = None,
    timeout: Optional[float] = None
) -> Optional[str]:
    """One completion from LM Studio's Local Model API (OpenAI chat format).

    Returns the text, or None if LM Studio is off or answers with an error.
    Timeouts are re-raised so the caller can report them precisely."""
    base_url = _edge_url(url)
    model_name = _edge_model(model)
    req_timeout = (local_connect_timeout(), timeout or local_llm_timeout())
    payload = {"model": model_name, "messages": [{"role": "user", "content": prompt}],
               "temperature": 0.1, "max_tokens": int(os.environ.get("OPSLENS_LOCAL_MAX_TOKENS", "320")), "stream": False}
    try:
        with local_session() as session:
            resp = session.post(f"{base_url}/v1/chat/completions", json=payload, timeout=req_timeout)
        if resp.status_code != 200:
            logger.warning("[EdgeSLM] LM Studio returned HTTP %s for model '%s': %s", resp.status_code, model_name, resp.text[:200])
            return None
        choices = resp.json().get("choices") or []
        content = ((choices[0].get("message") or {}).get("content") if choices else None) or None
        if content:
            logger.info("[EdgeSLM] Received response from LM Studio model '%s'", model_name)
        else:
            logger.warning("[EdgeSLM] LM Studio returned an empty response for model '%s'", model_name)
        return content
    except requests.exceptions.Timeout:
        logger.warning("[EdgeSLM] LM Studio model '%s' exceeded the %.0f s read timeout (OPSLENS_LOCAL_TIMEOUT)", model_name, req_timeout[1])
        raise
    except requests.exceptions.ConnectionError:
        logger.warning("[EdgeSLM] LM Studio not reachable at %s; using deterministic output", base_url)
        return None
    except Exception as e:
        logger.warning("[EdgeSLM] LM Studio call failed: %s: %s", type(e).__name__, e)
        return None


def call_inprocess_slm(
    prompt: str,
    model_name: str = "Qwen/Qwen2.5-0.5B-Instruct",
    max_new_tokens: int = 256
) -> Optional[str]:
    """
    Run lightweight SLM in-process using Hugging Face transformers.
    Optimized for low-power edge devices with pure CPU inference.
    """
    global _inprocess_pipeline
    try:
        if _inprocess_pipeline is None:
            import torch
            from transformers import pipeline

            logger.info(f"[EdgeSLM] Initializing in-process lightweight SLM '{model_name}' on CPU...")
            _inprocess_pipeline = pipeline(
                "text-generation",
                model=model_name,
                torch_dtype=torch.float32,
                device="cpu",
                model_kwargs={"low_cpu_mem_usage": True}
            )

        messages = [
            {"role": "system", "content": "You are an industrial copilot. Output raw JSON only."},
            {"role": "user", "content": prompt}
        ]

        outputs = _inprocess_pipeline(
            messages,
            max_new_tokens=max_new_tokens,
            temperature=0.1,
            do_sample=False
        )

        if outputs and len(outputs) > 0:
            generated = outputs[0].get("generated_text", [])
            if isinstance(generated, list) and len(generated) > 0:
                last_msg = generated[-1]
                return last_msg.get("content", "")
            elif isinstance(generated, str):
                return generated
    except Exception as e:
        logger.warning(f"[EdgeSLM] In-process SLM inference failed: {e}")

    return None
