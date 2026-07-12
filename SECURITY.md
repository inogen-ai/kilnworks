# Security Policy

## Supported versions

Kilnworks is pre-1.0. Only the latest commit on `main` and the most recent
tagged release are supported with security fixes. There is no long-term-support
branch.

## Reporting a vulnerability

Report vulnerabilities privately through GitHub Security Advisories: open the
repo's **Security** tab and use **"Report a vulnerability"**. Do not open a
public issue for anything that could be exploited before a fix ships.

Include what you'd include in a bug report — affected version/commit,
reproduction steps, and impact. We'll acknowledge new reports within a few
business days and follow up with a plan or fix timeline.

## Scope

Kilnworks is a self-hosted product: you run the API, worker, and database, and
you're responsible for the deployment environment (network exposure, TLS
termination, secrets management, OS patching). Configuration secrets
(`KILNWORKS_SECRET_KEY`, provider API keys, OIDC client secret) belong in
environment variables or a secrets manager — never commit them.

Some things are documented non-goals rather than vulnerabilities — see
[docs/limitations.md](docs/limitations.md) for the current list, including that
Kilnworks does not terminate TLS itself and expects a reverse proxy in front of
it for internet-facing deployments.
