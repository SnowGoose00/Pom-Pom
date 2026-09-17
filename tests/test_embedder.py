import json

import numpy as np

from app.embedder import Embedder


class FakeModel:
    def embed(self, texts):
        return [np.array([0.1, 0.2, 0.3], dtype=np.float32) for _ in texts]


def test_embed_returns_json_serializable_python_floats():
    embedder = Embedder()
    embedder._model = FakeModel()
    out = embedder.embed(["你好"])
    assert len(out) == 1
    assert len(out[0]) == 3
    assert isinstance(out[0][0], float)
    json.dumps(out)  # must not raise
