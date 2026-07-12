import math

from kilnworks.adapters.embedders.fake import FakeEmbedder


def test_dimension_is_1536():
    assert FakeEmbedder().dimension == 1536
    assert len(FakeEmbedder().embed(["hello"]).vectors[0]) == 1536


def test_deterministic_for_same_text():
    embedder = FakeEmbedder()
    assert embedder.embed(["hello"]).vectors == embedder.embed(["hello"]).vectors


def test_different_texts_differ():
    embedder = FakeEmbedder()
    assert embedder.embed(["hello"]).vectors[0] != embedder.embed(["goodbye"]).vectors[0]


def test_vectors_are_unit_normalised():
    vec = FakeEmbedder().embed(["hello"]).vectors[0]
    assert math.isclose(math.sqrt(sum(v * v for v in vec)), 1.0, rel_tol=1e-6)


def test_reports_positive_token_usage():
    batch = FakeEmbedder().embed(["hello world", "goodbye"])
    assert batch.total_tokens == 3  # naive whitespace token count


def test_custom_dimension():
    embedder = FakeEmbedder(dimension=768)
    assert embedder.dimension == 768
    vec = embedder.embed(["hello"]).vectors[0]
    assert len(vec) == 768
    assert math.isclose(math.sqrt(sum(v * v for v in vec)), 1.0, rel_tol=1e-6)
