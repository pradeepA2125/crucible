import math

from agentd.memory.embedder import Embedder


def test_embed_unit_normalizes_injected_vectors():
    emb = Embedder(encoder=lambda texts: [[3.0, 4.0] for _ in texts])  # |[3,4]| = 5
    out = emb.embed(["a", "b"])
    assert len(out) == 2
    assert math.isclose(out[0][0], 0.6) and math.isclose(out[0][1], 0.8)
    assert math.isclose(math.hypot(*out[0]), 1.0)


def test_embed_empty_input_returns_empty():
    emb = Embedder(encoder=lambda texts: [[1.0, 0.0] for _ in texts])
    assert emb.embed([]) == []


def test_unavailable_embedder_returns_empty_and_flags():
    def boom(texts):
        raise RuntimeError("model missing")

    emb = Embedder(encoder=boom)
    assert emb.available is False
    assert emb.embed(["x"]) == []
