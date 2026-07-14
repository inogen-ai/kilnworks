import concurrent.futures
import logging
import re
from collections.abc import Iterator, Sequence
from uuid import UUID, uuid4

from kilnworks.core.errors import ProviderError
from kilnworks.core.models import (
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

# Matches a transcript timestamp line as rendered by `adapters/media/transcript.py`
# ("[MM:SS] text" or "[HH:MM:SS] text"). Anchored to the start of a line (re.MULTILINE)
# so a stray "[12:34]"-shaped bracket mid-sentence in an ordinary document doesn't
# false-match -- transcription always places the timestamp at the start of its line.
_TIMESTAMP_RE = re.compile(r"(?m)^\[(\d{1,2}:\d{2}(?::\d{2})?)\]")

# Hard cap on how many federated (connector) context blocks a single query can append,
# regardless of how many connectors are selected or how many results each returns.
# Without this, N selected connectors each returning connector_result_limit results
# could inflate the LLM context (and cost) unbounded as more connectors are added.
DEFAULT_CONNECTOR_CONTEXT_CAP = 20


def _normalize_heading(text: str) -> str:
    return re.sub(r"[\s\-_]+", "", text).lower()


def _label(result: RetrievedChunk) -> str:
    if not result.heading_path:
        return result.title
    heading_path = result.heading_path
    if _normalize_heading(heading_path[0]) == _normalize_heading(result.title):
        # The first heading (typically an H1) is just a restatement of the document
        # title -- e.g. title "kiln-basics" + H1 "Kiln Basics". Rendering both is
        # redundant, so drop the leading element from the *label* only; the raw
        # `Citation.heading_path` (built separately in `_parse_citations`) is untouched.
        heading_path = heading_path[1:]
    if not heading_path:
        return result.title
    return f"{result.title} › {' › '.join(heading_path)}"


def format_context(results: Sequence[RetrievedChunk]) -> str:
    blocks = [f"[{i + 1}] ({_label(r)}) {r.text}" for i, r in enumerate(results)]
    return "\n\n".join(blocks)


def build_user_prompt(question: str, results: Sequence[RetrievedChunk]) -> str:
    return f"Context:\n\n{format_context(results)}\n\nQuestion: {question}"


def _locator_for(result: RetrievedChunk) -> str | None:
    """The citation's locator: a PDF page number (`p. 3`) when the chunk carries one,
    otherwise a leading transcript timestamp (`02:15`), otherwise None. Page takes
    precedence — a paginated chunk is never a transcript line."""
    if result.page is not None:
        return f"p. {result.page}"
    timestamp = _TIMESTAMP_RE.search(result.text)
    return timestamp.group(1) if timestamp else None


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
            Citation(
                index=n,
                chunk_id=result.id,
                source_uri=result.source_uri,
                title=result.title,
                heading_path=result.heading_path,
                locator=_locator_for(result),
            )
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
        connector_context_cap: int = DEFAULT_CONNECTOR_CONTEXT_CAP,
        system_prompt: str | None = None,
        no_answer_text: str | None = None,
        answer_language: str | None = None,
    ):
        self._embedder = embedder
        self._index = index
        self._llm = llm
        self._cost = cost
        self._connector_registry = connector_registry
        self._connector_timeout = connector_timeout
        self._connector_result_limit = connector_result_limit
        self._connector_context_cap = connector_context_cap
        self._system_prompt = system_prompt or SYSTEM_PROMPT
        self._no_answer_text = no_answer_text or NO_ANSWER_TEXT
        self._answer_language = answer_language or ""

    def _effective_system_prompt(self) -> str:
        if not self._answer_language:
            return self._system_prompt
        return (
            f"{self._system_prompt} Always write your answer in {self._answer_language}, "
            "regardless of the language of the question or the sources."
        )

    def _federated_results(
        self,
        question: str,
        principals: Sequence[str],
        connectors: Sequence[str],
    ) -> list[RetrievedChunk]:
        # Select purely by "selected name is allowed for these principals" -- no
        # status() pre-check here. A status() probe would spawn each connector's
        # server a second time (once to check status, once to search) and, being
        # sequential, isn't bounded by the parallel as_completed deadline below. A
        # down/stalled connector's search() now raises or times out and is caught
        # by the graceful-degradation handling further down instead.
        allowed = {c.name: c for c in self._connector_registry.allowed_for(principals)}
        chosen = [allowed[name] for name in connectors if name in allowed]
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
        # Cap total federated blocks regardless of how many connectors were selected
        # or how many results each returned -- bounds LLM context size/cost.
        return federated[: self._connector_context_cap]

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
            return Answer(text=self._no_answer_text, citations=[])
        completion = self._llm.complete(
            self._effective_system_prompt(), build_user_prompt(question, results)
        )
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
            yield Answer(text=self._no_answer_text, citations=[])
            return
        completion: Completion | None = None
        for event in self._llm.stream(
            self._effective_system_prompt(), build_user_prompt(question, results)
        ):
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
