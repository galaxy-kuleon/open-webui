import os


LOCAL_MODEL_REASONING_TOKEN_RESERVE = int(os.environ.get("QWEN_REASONING_TOKEN_RESERVE", "8196"))
if LOCAL_MODEL_REASONING_TOKEN_RESERVE < 0:
    raise ValueError("QWEN_REASONING_TOKEN_RESERVE must not be negative")

_thinking_budget = os.environ.get("QWEN_THINKING_TOKEN_BUDGET", "").strip()
LOCAL_MODEL_THINKING_TOKEN_BUDGET = int(_thinking_budget) if _thinking_budget else None
if LOCAL_MODEL_THINKING_TOKEN_BUDGET is not None and not 0 < LOCAL_MODEL_THINKING_TOKEN_BUDGET <= LOCAL_MODEL_REASONING_TOKEN_RESERVE:
    raise ValueError("QWEN_THINKING_TOKEN_BUDGET must be positive and no greater than QWEN_REASONING_TOKEN_RESERVE")


LOCAL_MODEL_SAMPLING = {
    "temperature": float(os.environ.get("SKIP_RAG_EXTRACTOR_TEMPERATURE", "1.0")),
    "top_p": float(os.environ.get("SKIP_RAG_EXTRACTOR_TOP_P", "0.95")),
    "top_k": int(os.environ.get("SKIP_RAG_EXTRACTOR_TOP_K", "20")),
    "min_p": float(os.environ.get("SKIP_RAG_EXTRACTOR_MIN_P", "0.0")),
    "presence_penalty": float(os.environ.get("SKIP_RAG_EXTRACTOR_PRESENCE_PENALTY", "0.0")),
    "repetition_penalty": float(os.environ.get("SKIP_RAG_EXTRACTOR_REPETITION_PENALTY", "1.0")),
}

LOCAL_MODEL_REASONING = {
    "reasoning_effort": os.environ.get("QWEN_REASONING_EFFORT", "xhigh"),
    "enable_thinking": True,
}
