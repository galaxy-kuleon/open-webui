import os


LOCAL_MODEL_SAMPLING = {
    "temperature": float(os.environ.get("SKIP_RAG_EXTRACTOR_TEMPERATURE", "1.0")),
    "top_p": float(os.environ.get("SKIP_RAG_EXTRACTOR_TOP_P", "0.95")),
    "top_k": int(os.environ.get("SKIP_RAG_EXTRACTOR_TOP_K", "20")),
    "min_p": float(os.environ.get("SKIP_RAG_EXTRACTOR_MIN_P", "0.0")),
    "presence_penalty": float(os.environ.get("SKIP_RAG_EXTRACTOR_PRESENCE_PENALTY", "0.0")),
    "repetition_penalty": float(os.environ.get("SKIP_RAG_EXTRACTOR_REPETITION_PENALTY", "1.0")),
}
