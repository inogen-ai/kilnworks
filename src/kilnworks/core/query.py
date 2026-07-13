import concurrent.futures
import logging
import re
from collections.abc import Iterator, Sequence
from uuid import UUID, uuid4

from kilnworks.core.errors import ProviderError
from kilnworks.core.models import (
    CONNECTOR_STATUS_READY,
    Answer,
    Citation,
    Completion,
    RetrievedChunk,
)
from kilnworks.core.ports import ConnectorRegistry, CostRecorder, Embedder, LLMProvider, VectorIndex

logger = logging.getLogger(__name__)

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
        connector_registry: ConnectorRegistry | None = None,
        connector_timeout: float = 8.0,
        connector_result_limit: int = 5,
    ):
        self._embedder = embedder
        self._index = index
        self._llm = llm
        self._cost = cost
        self._connector_registry = connector_registry
        self._connector_timeout = connector_timeout
        self._connector_result_limit = connector_result_limit

    def _federated_results(
        self,
        question: str,
        principals: Sequence[str],
        connectors: Sequence[str],
    ) -> list[RetrievedChunk]:
        allowed = {c.name: c for c in self._connector_registry.allowed_for(principals)}
        chosen = [
            allowed[name]
            for name in connectors
            if name in allowed and allowed[name].status() == CONNECTOR_STATUS_READY
        ]
        if not chosen:
            return []

        federated: list[RetrievedChunk] = []
        skipped: list[str] = []
        executor = concurrent.futures.ThreadPoolExecutor(max_workers=max(1, len(chosen)))
        try:
            futures = {
                executor.submit(c.search, question, self._connector_result_limit): c
                for c in chosen
            }
            try:
                deadline = self._connector_timeout
                for future in concurrent.futures.as_completed(futures, timeout=deadline):
                    connector = futures[future]
                    try:
                        connector_results = future.result()
                    except Exception:
                        skipped.append(connector.name)
                        logger.warning(
                            "connector %r failed; skipping", connector.name, exc_info=True
                        )
                        continue
                    for r in connector_results:
                        federated.append(
                            RetrievedChunk(
                                id=uuid4(),
                                document_id=uuid4(),
                                ordinal=0,
                                text=r.text,
                                heading_path=[],
                                acl_tags=[],
                                source_uri=(r.link or r.connector),
                                title=f"{r.connector}: {r.title}",
                                score=0.0,
                            )
                        )
            except concurrent.futures.TimeoutError:
                # Any futures not yet done have blown the shared deadline; skip them
                # without waiting for their threads to finish.
                for future, connector in futures.items():
                    if not future.done():
                        skipped.append(connector.name)
                        logger.warning("connector %r timed out; skipping", connector.name)
        finally:
            # Don't block retrieve() on connector threads that are still running past
            # the shared deadline (or forever, for a hung connector); let them run to
            # completion in the background and get reaped/GC'd once they finish.
            executor.shutdown(wait=False, cancel_futures=True)
        return federated

    def retrieve(
        self,
        question: str,
        principals: Sequence[str] = ("public",),
        limit: int = 8,
        user_id: str | None = None,
        source_ids: Sequence[UUID] | None = None,
        connectors: Sequence[str] | None = None,
    ) -> list[RetrievedChunk]:
        batch = self._embedder.embed([question])
        if self._cost:
            self._cost.record_cost(
                "embedding", self._embedder.model_name, batch.total_tokens, 0,
                "query", user_id=user_id,
            )
        results = self._index.search(batch.vectors[0], principals, limit, source_ids=source_ids)
        if self._connector_registry is not None and connectors:
            results = list(results) + self._federated_results(question, principals, connectors)
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
