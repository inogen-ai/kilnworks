# Known limitations (tracked, by design at this stage)

- **HNSW + ACL recall:** pgvector's HNSW index gathers candidates before the ACL
  filter applies. A principal whose accessible chunks are a small minority of the
  corpus can receive fewer than `limit` results even when matches exist. Revisit
  with pgvector iterative scans / `hnsw.ef_search` tuning when corpora grow (M3).
- **Fence handling in the chunker is a naive toggle** (``` and ~~~ are
  interchangeable; an unterminated fence suppresses heading detection to EOF).
- **Embedding dimensions are configurable but not hot-swappable:** `KILNWORKS_EMBEDDING_DIMENSIONS`
  sets the vector column width (default `1536`, matching OpenAI's
  `text-embedding-3-small`), but changing it — or the embedding model or provider —
  requires re-running `kilnworks init-db` and re-ingesting all documents. The
  startup check enforces that the configured dimensions match the schema.
- **`POST /ask/stream` is POST-based SSE**; browser `EventSource` requires GET,
  so it can't be used against this endpoint as-is — a websocket or GET variant
  is an M3 UI consideration.
- **Parsers run in-process with no timeout:** a pathological file can hang or exhaust
  memory during parsing. Extracted text is capped at 10M characters, which also bounds
  embedding spend per document; the worker bounds wall-clock time via
  `KILNWORKS_JOB_TIMEOUT_SECONDS`, but memory isolation remains future work.
- **No upload dedupe:** re-uploading the same file creates a new document (and new
  chunks/embeddings) each time. Content-hash dedupe is future work.
- **A worker killed mid-job (OOM, SIGKILL) leaves that job `running`;** lease-based
  reclaim recovers it once `KILNWORKS_JOB_LEASE_SECONDS` (default 420s) has elapsed
  since it started, and is safe with multiple concurrent workers — any worker's
  reclaim pass can requeue (or terminally fail, if attempts are exhausted) a job
  whose lease has expired. Jobs that exhaust their attempts across crashes are marked
  failed rather than retried forever. `complete()`/`fail()` are fenced on `attempts`
  (incremented on every claim), so a worker that stalls past its lease without
  crashing — and later calls `complete()`/`fail()` on the stale claim — can't clobber
  the job a subsequent worker has since reclaimed and re-claimed.
- **No reverse proxy in the compose topology:** multipart bodies are fully received before
  the size cap applies. Put a body-size-limiting proxy in front for internet exposure.
- **Orphaned upload files:** cleaned up by the worker once a job reaches a terminal
  state (`done` or `failed`); a worker crash between finishing a job and this cleanup
  can still leave a file behind.
- **The built-in UI keeps its bearer token in `sessionStorage`**, which is readable by any
  XSS in the same origin; this is acceptable for the single-origin built-in UI, but
  hardened deployments should use httpOnly-cookie auth instead.
- **Faithfulness is LLM-as-judge**, subject to judge model error; a judge may hallucinate
  or make subjective calls on grounding. The judge's correctness depends on the model
  and prompt, not on ground-truth labels.
- **The judge is the same model/provider as the generator** (self-judging bias); there
  is no separate judge-model override yet.
- **With fake providers, all eval metrics exercise the pipeline mechanically, not
  semantically.** The fake embedder (SHA256-based exact-match ranking) and fake LLM
  (canned responses) do not reflect real retrieval or generation quality. Deterministic
  evals (smoke tests) catch pipeline regressions but are not suitable for measuring
  quality; use real providers and a golden eval set for that.
- **OIDC/SSO has no refresh tokens and doesn't track the IdP session:** Kilnworks
  issues its own bearer token at login, and that token's own TTL
  (`KILNWORKS_TOKEN_TTL_MINUTES`) governs when the user has to sign in again — signing
  out of, or being revoked at, the IdP has no effect on an already-issued Kilnworks
  token.
- **Principals re-sync only at SSO login, not continuously:** a group added or removed
  at the IdP is only picked up on the user's next login. A revoked group's access
  persists until the current Kilnworks token expires and the user logs in again.
- **Run OIDC/SSO behind TLS in production:** the state cookie and the final redirect
  (which carries the token in a URL fragment) are only as safe as the transport; both
  are sent over plain HTTP in a non-TLS deployment.
- **No single logout:** signing out of Kilnworks (or letting its token expire) doesn't
  end the session at the IdP, and there's no SLO endpoint wired up to do so.
- **The OIDC callback redirect URI is derived from the incoming request, with no
  public-URL override setting.** Deployments sitting behind a reverse proxy that
  rewrites scheme/host may need to register a different redirect URI at the IdP than
  what Kilnworks computes; a `KILNWORKS_PUBLIC_URL`-style override is future work.
- **SSO accounts are linked by email, with the IdP fully trusted for it:** a matching
  email signs in as the existing local account and each SSO login overwrites that
  account's principals with the IdP's groups; `email_verified` is not checked; there is
  no allowlist or domain restriction, so anyone the IdP authenticates gets an account
  with the `public` principal. Use a tenant-scoped IdP that verifies emails. Identity
  is keyed on email rather than `(iss, sub)`, so an email reused at the IdP (departed
  employee → new hire) inherits the old account's documents and history.
- **API-only deployments (no bundled web UI) leave the SSO token in the URL fragment**
  after the callback redirect — nothing scrubs it from the address bar/history. The
  standard compose topology always ships the UI, which consumes and scrubs it
  immediately.
