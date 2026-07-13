import re
from collections.abc import Iterator, Sequence
from uuid import UUID

from kilnworks.core.errors import ProviderError
from kilnworks.core.models import Answer, Citation, Completion, RetrievedChunk
from kilnworks.core.ports import CostRecorder, Embedder, LLMProvider, VectorIndex

SYSTEM_PROMPT = (
    "You are a knowledge assistant. Answer using ONLY the provided context blocks. "
    "Cite sources inline as [n], where n is a context block number. Every factual claim "
    "must carry a citation. If the context does not contain the answer, say so plainly."
)

NO_ANSWER_TEXT = "I couldn't find anything relevant in the knowledge base for that question."


def format_context(results: Sequence[RetrievedChunk]) -> str:
    blocks = [f"[{i + 1}] ({r.title}) {r.text}" for i, r in enumerate(results)]
    return "\n\n".join(blocks)


def build_user_prompt(question: str, results: Sequence[RetrievedChunk]) -> str:
    return f"Context:\n\n{format_context(results)}\n\nQuestion: {question}"


def _parse_citations(text: str, results: Sequence[RetrievedChunk]) -> list[Citation]:
    citations: list[Citation] = []
    seen: set[int] = set()
    for match in re.finditer(r"\[(\d+)\]", text):
        n = int(match.group(1))
        if n in seen or not 1 <= n <= len(results):
            continue
        seen.add(n)
        result = results[n - 1]
        citations.append(
            Citation(index=n, chunk_id=result.id, source_uri=result.source_uri, title=result.title)
        )
    return citations


class QueryService:
    def __init__(
        self,
        embedder: Embedder,
        index: VectorIndex,
        llm: LLMProvider,
        cost: CostRecorder | None = None,
    ):
        self._embedder = embedder
        self._index = index
        self._llm = llm
        self._cost = cost

    def retrieve(
        self,
        question: str,
        principals: Sequence[str] = ("public",),
        limit: int = 8,
        user_id: str | None = None,
        source_ids: Sequence[UUID] | None = None,
        connectors: Sequence[str] | None = None,
    ) -> list[RetrievedChunk]:
        # `connectors` is reserved for federated retrieval (task C2) and is not yet used.
        batch = self._embedder.embed([question])
        if self._cost:
            self._cost.record_cost(
                "embedding", self._embedder.model_name, batch.total_tokens, 0,
                "query", user_id=user_id,
            )
        results = self._index.search(batch.vectors[0], principals, limit, source_ids=source_ids)
        return results

    def ask(
        self,
        question: str,
        principals: Sequence[str] = ("public",),
        limit: int = 8,
        user_id: str | None = None,
        source_ids: Sequence[UUID] | None = None,
        connectors: Sequence[str] | None = None,
    ) -> Answer:
        results = self.retrieve(question, principals, limit, user_id, source_ids, connectors)
        if not results:
            return Answer(text=NO_ANSWER_TEXT, citations=[])
        completion = self._llm.complete(SYSTEM_PROMPT, build_user_prompt(question, results))
        if self._cost:
            self._cost.record_cost(
                "chat", completion.model, completion.input_tokens,
                completion.output_tokens, "query", user_id=user_id,
            )
        return Answer(
            text=completion.text,
            citations=_parse_citations(completion.text, results),
            model=completion.model,
        )

    def ask_stream(
        self,
        question: str,
        principals: Sequence[str] = ("public",),
        limit: int = 8,
        user_id: str | None = None,
        source_ids: Sequence[UUID] | None = None,
        connectors: Sequence[str] | None = None,
    ) -> Iterator[str | Answer]:
        results = self.retrieve(question, principals, limit, user_id, source_ids, connectors)
        if not results:
            yield Answer(text=NO_ANSWER_TEXT, citations=[])
            return
        completion: Completion | None = None
        for event in self._llm.stream(SYSTEM_PROMPT, build_user_prompt(question, results)):
            if isinstance(event, Completion):
                completion = event
            else:
                yield event
        if completion is None:  # a misbehaving provider never sent the terminal event
            raise ProviderError("llm", "stream ended without a terminal completion")
        if self._cost:
            self._cost.record_cost(
                "chat", completion.model, completion.input_tokens,
                completion.output_tokens, "query", user_id=user_id,
            )
        yield Answer(
            text=completion.text,
            citations=_parse_citations(completion.text, results),
            model=completion.model,
        )
