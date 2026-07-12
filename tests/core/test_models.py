from kilnworks.core.models import Answer, Chunk, Document, IngestReport, RetrievedChunk


def test_document_defaults_to_public_acl_and_generated_id():
    doc = Document(source_uri="file:///a.md", title="a", text="hello")
    assert doc.acl_tags == ["public"]
    assert doc.id is not None


def test_two_documents_get_distinct_ids():
    kwargs = dict(source_uri="file:///a.md", title="a", text="hello")
    assert Document(**kwargs).id != Document(**kwargs).id


def test_retrieved_chunk_extends_chunk():
    doc = Document(source_uri="file:///a.md", title="a", text="hello")
    chunk = Chunk(document_id=doc.id, ordinal=0, text="hello", heading_path=[], acl_tags=["public"])
    hit = RetrievedChunk(
        **chunk.model_dump(), score=0.9, source_uri=doc.source_uri, title=doc.title
    )
    assert hit.score == 0.9
    assert hit.text == "hello"


def test_ingest_report_defaults():
    report = IngestReport()
    assert report.succeeded == 0
    assert report.failed == []


def test_answer_defaults():
    answer = Answer(text="hi", citations=[])
    assert answer.model == ""
