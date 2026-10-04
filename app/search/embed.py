"""Text embedding providers. All return L2-normalised float32 numpy arrays."""
import logging
import os
import threading

import numpy as np
import requests

log = logging.getLogger(__name__)

EMBED_PROVIDERS = {
    "local": {
        "label": "Local (fastembed, offline)",
        "models": [
            "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2",
            "sentence-transformers/paraphrase-multilingual-mpnet-base-v2",
            "intfloat/multilingual-e5-large",
            "BAAI/bge-small-en-v1.5",
        ],
    },
    "openai": {"label": "OpenAI", "models": ["text-embedding-3-small", "text-embedding-3-large"]},
    "google": {"label": "Google Gemini", "models": ["gemini-embedding-001", "text-embedding-004"]},
    "ollama": {"label": "Ollama", "models": ["nomic-embed-text", "bge-m3", "mxbai-embed-large"]},
    "openai_compatible": {"label": "OpenAI-compatible", "models": []},
    "off": {"label": "Off (fuzzy text search only)", "models": []},
}


class EmbedError(RuntimeError):
    pass


def _normalise(vecs) -> np.ndarray:
    arr = np.asarray(vecs, dtype=np.float32)
    if arr.ndim == 1:
        arr = arr[None, :]
    norms = np.linalg.norm(arr, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    return arr / norms


class Embedder:
    provider = ""

    def __init__(self, model: str):
        self.model = model

    @property
    def model_id(self) -> str:
        return f"{self.provider}:{self.model}"

    def embed(self, texts: list[str], kind: str = "passage") -> np.ndarray:
        raise NotImplementedError


class LocalEmbedder(Embedder):
    provider = "local"
    _models: dict = {}
    _lock = threading.Lock()

    def _get(self):
        with self._lock:
            m = self._models.get(self.model)
            if m is None:
                from fastembed import TextEmbedding
                cache = os.environ.get("FASTEMBED_CACHE_PATH")
                log.info("Loading local embedding model %s", self.model)
                m = TextEmbedding(model_name=self.model, cache_dir=cache)
                self._models[self.model] = m
            return m

    def embed(self, texts, kind="passage"):
        if "e5" in self.model.lower():
            texts = [f"{'query' if kind == 'query' else 'passage'}: {t}" for t in texts]
        model = self._get()
        with self._lock:  # onnxruntime sessions are thread-safe, but keep RAM spikes bounded
            vecs = list(model.embed(texts, batch_size=32))
        return _normalise(vecs)


class OpenAIEmbedder(Embedder):
    provider = "openai"

    def __init__(self, model, api_key, base_url=None, timeout=60, provider="openai"):
        super().__init__(model)
        from openai import OpenAI
        self.provider = provider
        self.client = OpenAI(api_key=api_key or "none", base_url=base_url or None, timeout=timeout)

    def embed(self, texts, kind="passage"):
        res = self.client.embeddings.create(model=self.model, input=texts)
        return _normalise([d.embedding for d in sorted(res.data, key=lambda d: d.index)])


class GoogleEmbedder(Embedder):
    provider = "google"

    def __init__(self, model, api_key, timeout=60):
        super().__init__(model)
        from google import genai
        self.client = genai.Client(api_key=api_key)

    def embed(self, texts, kind="passage"):
        from google.genai import types
        task = "RETRIEVAL_QUERY" if kind == "query" else "RETRIEVAL_DOCUMENT"
        res = self.client.models.embed_content(
            model=self.model, contents=texts, config=types.EmbedContentConfig(task_type=task))
        return _normalise([e.values for e in res.embeddings])


class OllamaEmbedder(Embedder):
    provider = "ollama"

    def __init__(self, model, base_url, timeout=60):
        super().__init__(model)
        self.base_url = (base_url or "http://localhost:11434").rstrip("/")
        self.timeout = timeout

    def embed(self, texts, kind="passage"):
        r = requests.post(f"{self.base_url}/api/embed", json={"model": self.model, "input": texts},
                          timeout=self.timeout)
        if r.status_code != 200:
            raise EmbedError(f"Ollama embed failed ({r.status_code}): {r.text[:200]}")
        return _normalise(r.json()["embeddings"])


_cache: dict = {}
_cache_lock = threading.Lock()


def get_embedder() -> Embedder | None:
    """Embedder for current settings (None when semantic search is off). Requires app context."""
    from ..services import settings

    provider = settings.get("search.provider") or "off"
    model = settings.get("search.model") or ""
    if provider == "off" or not model:
        return None
    ai = settings.ai_config(reveal=True)["providers"]
    pconf = ai.get(provider, {})
    key = (provider, model, pconf.get("api_key"), pconf.get("base_url"))
    with _cache_lock:
        emb = _cache.get(key)
        if emb is not None:
            return emb
        timeout = int(pconf.get("timeout") or 60)
        if provider == "local":
            emb = LocalEmbedder(model)
        elif provider in ("openai", "openai_compatible"):
            emb = OpenAIEmbedder(model, pconf.get("api_key"), pconf.get("base_url"), timeout, provider)
        elif provider == "google":
            emb = GoogleEmbedder(model, pconf.get("api_key"), timeout)
        elif provider == "ollama":
            emb = OllamaEmbedder(model, pconf.get("base_url"), timeout)
        else:
            raise EmbedError(f"Unknown embedding provider: {provider}")
        _cache.clear()
        _cache[key] = emb
        return emb


def current_model_id() -> str | None:
    from ..services import settings
    provider = settings.get("search.provider") or "off"
    model = settings.get("search.model") or ""
    return None if provider == "off" or not model else f"{provider}:{model}"
