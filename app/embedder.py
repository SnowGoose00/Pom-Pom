"""Local embedding via fastembed (BAAI/bge-small-zh-v1.5, ONNX)."""
from __future__ import annotations

from pathlib import Path


class Embedder:
    model_name = "BAAI/bge-small-zh-v1.5"
    dim = 512

    def __init__(self, cache_dir: str | Path | None = None):
        self.cache_dir = Path(cache_dir) if cache_dir else None
        self._model = None

    def _ensure_model(self):
        if self._model is None:
            from fastembed import TextEmbedding

            kwargs = {"model_name": self.model_name}
            if self.cache_dir:
                kwargs["cache_dir"] = str(self.cache_dir)
            self._model = TextEmbedding(**kwargs)
        return self._model

    def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        model = self._ensure_model()
        return [[float(x) for x in vec] for vec in model.embed(texts)]
