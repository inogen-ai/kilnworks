# Contributing to Kilnworks

## Dev setup

Requires Python 3.12+, [uv](https://docs.astral.sh/uv/), Docker (for integration
tests, which use testcontainers), and Node 22 for the web UI.

    uv sync
    uv run pytest -q
    uv run ruff check .

The test suite spins up real Postgres containers via testcontainers — Docker must
be running. `uv run kilnworks init-db` and `uv run kilnworks ingest examples/corpus`
are useful for exercising the CLI by hand; see the README for the full quickstart.

For the web UI:

    cd web
    npm ci
    npm test
    npm run build

## The eval gate

Pipeline-affecting changes (retrieval, chunking, prompts, ingestion) should stay
green against the deterministic smoke eval, which runs against fake providers so
it needs no API keys:

    docker compose up -d --wait db
    export KILNWORKS_FAKE_PROVIDERS=true
    uv run kilnworks init-db
    uv run kilnworks ingest evals/smoke-corpus
    uv run kilnworks eval evals/smoke.jsonl --limit 1 \
      --min-hit-rate 1.0 --min-citation-rate 1.0 --min-faithfulness 1.0

This is exactly what CI runs (see `.github/workflows/ci.yml`). It catches pipeline
regressions mechanically — it is not a quality benchmark. See the README's Evals
section for how to run evals against real providers with a golden dataset.

## Pull requests

- Add tests for any behavior change. Bug fixes should include a test that fails
  without the fix.
- `uv run ruff check .` must be clean.
- Keep PRs small and focused on one change — easier to review, easier to revert.
- CI (tests, ruff, web build, docker build, eval gate) must be green before merge.
- Update docs (README, `docs/limitations.md`) when behavior or a documented
  limitation changes.
- Describe *why*, not just *what*, in the PR description — see
  `.github/pull_request_template.md`.

## Discussion

Use GitHub issues for bugs, feature proposals, and questions. There's no separate
mailing list or chat — keep the discussion where the code is.
