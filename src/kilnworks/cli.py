from pathlib import Path
from typing import NoReturn

import psycopg
import typer

from kilnworks.adapters.sources.localfolder import LocalFolderSource
from kilnworks.core.errors import ProviderError
from kilnworks.db.connection import connect, init_db
from kilnworks.evals.dataset import load_cases
from kilnworks.evals.runner import EvalRunner
from kilnworks.settings import Settings
from kilnworks.wiring import (
    Services,
    build_judge,
    build_services,
    embedding_dimensions_message,
    embedding_dimensions_out_of_range,
)

app = typer.Typer(help="Kilnworks: production-grade RAG knowledge assistant.")


def _die(message: str) -> NoReturn:
    typer.echo(message, err=True)
    raise typer.Exit(code=1)


def _db_help(exc: Exception) -> str:
    return (
        f"Could not connect to the database: {exc}\n"
        "Is it running? Try: docker compose up -d db"
    )


def _services_or_exit() -> Services:
    try:
        settings = Settings()
        return build_services(settings)
    except ValueError as exc:
        _die(str(exc))
    except psycopg.OperationalError as exc:
        _die(_db_help(exc))


@app.command("init-db")
def init_db_command() -> None:
    """Create the database schema (idempotent)."""
    try:
        settings = Settings()
    except ValueError as exc:
        _die(str(exc))
    if embedding_dimensions_out_of_range(settings.embedding_dimensions):
        _die(embedding_dimensions_message(settings.embedding_dimensions))
    try:
        conn = connect(settings.database_url)
    except ValueError as exc:
        _die(str(exc))
    except psycopg.OperationalError as exc:
        _die(_db_help(exc))
    init_db(conn, dimensions=settings.embedding_dimensions)
    typer.echo("Database initialized.")


@app.command("create-user")
def create_user_command(
    email: str,
    password: str = typer.Option(
        ..., "--password", prompt=True, hide_input=True, help="Password for the new user"
    ),
    display_name: str = typer.Option("", "--display-name"),
    principal: list[str] = typer.Option(None, "--principal", help="Repeatable ACL principal"),
) -> None:
    """Create a user account for API access."""
    from kilnworks.auth.users import PgUserStore

    try:
        conn = connect(Settings().database_url)
    except ValueError as exc:
        _die(str(exc))
    except psycopg.OperationalError as exc:
        _die(_db_help(exc))
    try:
        principals = principal or ["public"]
        try:
            user = PgUserStore(conn).create_user(
                email, password, display_name=display_name, principals=principals
            )
        except ValueError as exc:
            _die(str(exc))
    finally:
        conn.close()
    typer.echo(f"Created user {user.email} with principals {user.principals}")


@app.command()
def ingest(path: Path = typer.Argument(..., exists=True, file_okay=False, readable=True)) -> None:
    """Ingest all supported files under PATH — text/Markdown/PDF/DOCX/HTML/CSV/TSV/XLSX
    plus images and audio/video if a vision/transcription provider is configured (see
    README's "Multimodal ingestion" section)."""
    services = _services_or_exit()
    # provider errors surface per-document in report.failed; systemic outage -> exit 1 below
    report = services.ingestion.ingest(LocalFolderSource(path, media=services.media))
    typer.echo(f"Ingested {report.succeeded} document(s); {len(report.failed)} failed.")
    for uri, error in report.failed:
        typer.echo(f"  FAILED {uri}: {error}", err=True)
    if report.succeeded == 0 and report.failed:
        raise typer.Exit(code=1)


@app.command()
def ask(question: str) -> None:
    """Ask a question of the knowledge base."""
    services = _services_or_exit()
    try:
        answer = services.query.ask(question)
    except ProviderError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc
    typer.echo(answer.text)
    if answer.citations:
        typer.echo("\nSources:")
        for citation in answer.citations:
            typer.echo(f"  [{citation.index}] {citation.title} — {citation.source_uri}")


@app.command("eval")
def eval_command(
    dataset: Path = typer.Argument(..., exists=True, dir_okay=False),
    limit: int = typer.Option(8, "--limit"),
    min_hit_rate: float = typer.Option(0.0, "--min-hit-rate"),
    min_citation_rate: float = typer.Option(0.0, "--min-citation-rate"),
    min_faithfulness: float = typer.Option(0.0, "--min-faithfulness"),
    principal: list[str] = typer.Option(None, "--principal", help="Repeatable ACL principal"),
) -> None:
    """Run a JSONL eval dataset and report retrieval hit rate, citation rate, faithfulness."""
    services = _services_or_exit()
    principals = principal or ["public"]
    try:
        judge = build_judge(Settings())
        cases = load_cases(dataset)
    except ValueError as exc:
        _die(str(exc))

    try:
        summary = EvalRunner(services.query, judge).run(cases, principals, limit)
    except ProviderError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc

    for result in summary.results:
        hit_mark = "✓" if result.hit else "✗"
        cite_mark = "✓" if result.cited else "✗"
        faith_mark = "✓" if result.faithful else "✗"
        typer.echo(
            f"[hit {hit_mark}] [cite {cite_mark}] [faith {faith_mark}] {result.question}"
        )

    typer.echo(
        f"\n{summary.cases} case(s) — "
        f"hit_rate {summary.hit_rate:.0%}, "
        f"citation_rate {summary.citation_rate:.0%}, "
        f"faithfulness {summary.faithfulness_rate:.0%}"
    )

    failures = []
    if summary.hit_rate < min_hit_rate:
        failures.append(f"hit_rate {summary.hit_rate:.0%} < required {min_hit_rate:.0%}")
    if summary.citation_rate < min_citation_rate:
        failures.append(
            f"citation_rate {summary.citation_rate:.0%} < required {min_citation_rate:.0%}"
        )
    if summary.faithfulness_rate < min_faithfulness:
        failures.append(
            f"faithfulness {summary.faithfulness_rate:.0%} < required {min_faithfulness:.0%}"
        )
    if failures:
        _die("\n".join(failures))


@app.command()
def serve() -> None:
    """Run the Kilnworks API server."""
    import uvicorn

    from kilnworks.api.app import create_app

    try:
        settings = Settings()
        api = create_app(settings)
    except ValueError as exc:
        _die(str(exc))
    uvicorn.run(api, host=settings.api_host, port=settings.api_port)


@app.command()
def worker(once: bool = typer.Option(False, "--once", help="Drain the queue and exit.")) -> None:
    """Run the background ingestion worker."""
    from kilnworks.worker.loop import run_worker

    try:
        settings = Settings()
        processed = run_worker(settings, once=once)
    except ValueError as exc:
        _die(str(exc))
    except psycopg.OperationalError as exc:
        _die(_db_help(exc))
    if once:
        typer.echo(f"Processed {processed} job(s).")


if __name__ == "__main__":
    app()
