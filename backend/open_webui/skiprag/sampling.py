import os


LOCAL_MODEL_REASONING_TOKEN_RESERVE = int(os.environ.get("QWEN_REASONING_TOKEN_RESERVE", "8196"))
if LOCAL_MODEL_REASONING_TOKEN_RESERVE < 0:
    raise ValueError("QWEN_REASONING_TOKEN_RESERVE must not be negative")

LOCAL_MODEL_SAMPLING = {
    "temperature": float(os.environ.get("SKIP_RAG_EXTRACTOR_TEMPERATURE", "0.8")),
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

TASK_MODEL_SAMPLING = {
    **LOCAL_MODEL_SAMPLING,
    "temperature": float(os.environ.get("SKIP_RAG_TASK_TEMPERATURE", "1.0")),
    "presence_penalty": float(os.environ.get("SKIP_RAG_TASK_PRESENCE_PENALTY", "0.0")),
}
