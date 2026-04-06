"""
LM Studio memory-aware model loading check.

Pure functions that estimate whether a target model can be loaded given
current LM Studio memory usage.  Designed for macOS + LM Studio (unified
memory, no NVIDIA), but works for any `lms` CLI environment.

Data flow:
  1. `lms ps --json`  → currently loaded models + sizeBytes
  2. `lms ls --json`  → all downloaded models + sizeBytes + maxContextLength
  3. Compare (loaded_total + target_model_estimate) against a configurable budget

The budget defaults to LMSTUDIO_MEMORY_BUDGET_GB env var (default 120 GB,
safe for a 128 GB Mac Studio leaving ~8 GB for OS/apps).
"""

import json
import logging
import os
import subprocess
import time
from typing import Optional

log = logging.getLogger(__name__)

# ── Configuration ──────────────────────────────────────────────────

MEMORY_BUDGET_GB = float(os.environ.get("LMSTUDIO_MEMORY_BUDGET_GB", "120"))

# Overhead multiplier: model weights × this = estimated runtime memory
# Covers KV cache, activations, framework overhead.
# Conservative default 1.25 (25% overhead).
OVERHEAD_MULTIPLIER = float(os.environ.get("LMSTUDIO_OVERHEAD_MULTIPLIER", "1.25"))

# Cache TTL for lms CLI results (seconds)
_CACHE_TTL = 30

# ── Simple TTL cache ───────────────────────────────────────────────

_cache: dict[str, tuple[float, object]] = {}


def _cached_call(key: str, fn):
    """Return cached result if fresh, else call fn and cache."""
    now = time.monotonic()
    if key in _cache:
        ts, val = _cache[key]
        if now - ts < _CACHE_TTL:
            return val
    val = fn()
    _cache[key] = (now, val)
    return val


def invalidate_cache():
    """Clear the lms CLI cache (useful after load/unload)."""
    _cache.clear()


# ── lms CLI wrappers ──────────────────────────────────────────────

def _run_lms(args: list[str], timeout: int = 10) -> Optional[list[dict]]:
    """Run `lms <args> --json` and parse JSON output."""
    try:
        result = subprocess.run(
            ["lms", *args, "--json"],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        if result.returncode == 0 and result.stdout.strip():
            return json.loads(result.stdout)
    except FileNotFoundError:
        log.debug("lms CLI not found — memory check unavailable")
    except subprocess.TimeoutExpired:
        log.warning("lms %s timed out after %ds", " ".join(args), timeout)
    except (json.JSONDecodeError, Exception) as e:
        log.warning("lms %s failed: %s", " ".join(args), e)
    return None


def get_loaded_models() -> list[dict]:
    """Currently loaded models from `lms ps --json`."""
    return _cached_call("ps", lambda: _run_lms(["ps"]) or [])


def get_all_models() -> list[dict]:
    """All downloaded models from `lms ls --json`."""
    return _cached_call("ls", lambda: _run_lms(["ls"]) or [])


# ── Pure functions ─────────────────────────────────────────────────

def strip_provider_prefix(openwebui_model_id: str) -> str:
    """
    'lmstudio.qwen3.5-122b-a10b' → 'qwen3.5-122b-a10b'

    Open WebUI prefixes with 'lmstudio.' — LM Studio uses the bare key.
    """
    if openwebui_model_id.startswith("lmstudio."):
        return openwebui_model_id[len("lmstudio."):]
    return openwebui_model_id


def find_model(models: list[dict], model_key: str) -> Optional[dict]:
    """Find a model in `lms ls` or `lms ps` output by modelKey."""
    for m in models:
        if m.get("modelKey") == model_key:
            return m
        # Also try identifier field (lms ps uses this)
        if m.get("identifier") == model_key:
            return m
    return None


def is_model_loaded(model_key: str) -> bool:
    """Check if a model is already loaded in LM Studio."""
    loaded = get_loaded_models()
    return find_model(loaded, model_key) is not None


def loaded_memory_bytes() -> int:
    """Sum of sizeBytes for all currently loaded models."""
    return sum(m.get("sizeBytes", 0) for m in get_loaded_models())


def estimate_runtime_bytes(model_size_bytes: int) -> int:
    """Estimate runtime memory: weights + KV cache + activations overhead."""
    return int(model_size_bytes * OVERHEAD_MULTIPLIER)


def format_gb(byte_count: int) -> str:
    """Format bytes as human-readable GB string."""
    return f"{byte_count / (1024**3):.1f} GB"


# ── Main check ─────────────────────────────────────────────────────

def check_can_load_model(openwebui_model_id: str) -> Optional[dict]:
    """
    Check if there's enough memory to load a model in LM Studio.

    Returns None if the model can be loaded (or check is inconclusive).
    Returns a warning dict if memory is likely insufficient:
        {
            "warning": str,        # Human-readable warning message
            "model_key": str,      # LM Studio model key
            "model_size_gb": float,
            "estimated_need_gb": float,
            "loaded_total_gb": float,
            "budget_gb": float,
            "headroom_gb": float,  # Can be negative
        }
    """
    model_key = strip_provider_prefix(openwebui_model_id)

    # Only check lmstudio.* models
    if openwebui_model_id == model_key:
        # No lmstudio. prefix — not an LM Studio model
        return None

    # Already loaded? No check needed.
    if is_model_loaded(model_key):
        return None

    # Find model info from lms ls
    all_models = get_all_models()
    model_info = find_model(all_models, model_key)
    if not model_info:
        log.debug(
            "Model %s not found in lms ls — skipping memory check", model_key
        )
        return None

    model_size = model_info.get("sizeBytes", 0)
    if model_size == 0:
        return None

    estimated_need = estimate_runtime_bytes(model_size)
    currently_loaded = loaded_memory_bytes()
    budget_bytes = int(MEMORY_BUDGET_GB * (1024**3))
    headroom = budget_bytes - currently_loaded - estimated_need

    if headroom >= 0:
        return None

    # Not enough memory — build warning
    model_size_gb = model_size / (1024**3)
    estimated_need_gb = estimated_need / (1024**3)
    loaded_total_gb = currently_loaded / (1024**3)
    headroom_gb = headroom / (1024**3)

    loaded_models = get_loaded_models()
    loaded_names = [m.get("modelKey", "?") for m in loaded_models]

    warning = (
        f"Memory warning: loading {model_key} needs ~{estimated_need_gb:.1f} GB "
        f"but only ~{MEMORY_BUDGET_GB - loaded_total_gb:.1f} GB available "
        f"(budget {MEMORY_BUDGET_GB:.0f} GB, "
        f"currently loaded: {', '.join(loaded_names)} = {loaded_total_gb:.1f} GB). "
        f"LM Studio may return errors or need to unload models."
    )

    return {
        "warning": warning,
        "model_key": model_key,
        "model_size_gb": round(model_size_gb, 1),
        "estimated_need_gb": round(estimated_need_gb, 1),
        "loaded_total_gb": round(loaded_total_gb, 1),
        "budget_gb": MEMORY_BUDGET_GB,
        "headroom_gb": round(headroom_gb, 1),
    }
