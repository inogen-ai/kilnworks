import { useEffect, useRef, useState, type FormEvent } from "react";

import { ApiError, askStream, type Answer, type Citation } from "./api";
import type { Selection } from "./Sources";

type Message = {
  role: "user" | "assistant";
  text: string;
  citations?: Citation[];
  error?: boolean;
};

export default function Chat({
  token,
  onAuthError,
  selection,
}: {
  token: string;
  onAuthError: () => void;
  selection: Selection;
}) {
  const [messages, setMessages] = useState<Message[]>([]);
  const [question, setQuestion] = useState("");
  const [busy, setBusy] = useState(false);
  const scrollRef = useRef<HTMLDivElement>(null);
  const abortRef = useRef<AbortController | null>(null);

  useEffect(() => {
    return () => abortRef.current?.abort();
  }, []);

  function patchLast(patch: (last: Message) => Message) {
    setMessages((current) => [
      ...current.slice(0, -1),
      patch(current[current.length - 1]),
    ]);
    const el = scrollRef.current;
    const nearBottom = !el || el.scrollHeight - el.scrollTop - el.clientHeight < 60;
    if (nearBottom) {
      queueMicrotask(() =>
        scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight }),
      );
    }
  }

  async function handleSubmit(event: FormEvent) {
    event.preventDefault();
    const asked = question.trim();
    if (!asked || busy) return;
    setQuestion("");
    setBusy(true);
    setMessages((current) => [
      ...current,
      { role: "user", text: asked },
      { role: "assistant", text: "" },
    ]);
    abortRef.current?.abort();
    const controller = new AbortController();
    abortRef.current = controller;
    try {
      const sourceIds = [...selection.documentIds];
      const connectors = [...selection.connectorNames];
      for await (const sse of askStream(
        token,
        asked,
        controller.signal,
        sourceIds,
        connectors,
      )) {
        if (sse.event === "delta") {
          const delta = (sse.data as { text: string }).text;
          patchLast((last) => ({ ...last, text: last.text + delta }));
        } else if (sse.event === "answer") {
          const answer = sse.data as Answer;
          patchLast((last) => ({
            ...last,
            text: answer.text,
            citations: answer.citations,
          }));
        } else if (sse.event === "error") {
          const detail = (sse.data as { detail: string }).detail;
          patchLast((last) => ({ ...last, text: detail, error: true }));
        }
      }
    } catch (err) {
      if (err instanceof DOMException && err.name === "AbortError") return;
      const message = err instanceof Error ? err.message : "request failed";
      if (err instanceof ApiError && err.status === 401) onAuthError();
      patchLast((last) => ({ ...last, text: message, error: true }));
    } finally {
      if (abortRef.current === controller) {
        setBusy(false);
        abortRef.current = null;
      }
    }
  }

  return (
    <main className="chat">
      <div className="messages" ref={scrollRef}>
        {messages.length === 0 && (
          <p className="empty">Upload a document, then ask it a question.</p>
        )}
        {messages.map((message, i) => (
          <div key={i} className={`message ${message.role} ${message.error ? "error" : ""}`}>
            <p>{message.text || "…"}</p>
            {message.citations && message.citations.length > 0 && (
              <ul className="citations">
                {message.citations.map((citation) => (
                  <li key={citation.index}>
                    [{citation.index}] {citation.title}
                  </li>
                ))}
              </ul>
            )}
          </div>
        ))}
      </div>
      <form className="ask" onSubmit={handleSubmit}>
        <input
          value={question}
          onChange={(e) => setQuestion(e.target.value)}
          placeholder="Ask your documents…"
          disabled={busy}
        />
        <button type="submit" disabled={busy || !question.trim()}>
          {busy ? "…" : "Ask"}
        </button>
      </form>
    </main>
  );
}
