---
name: Bug report
about: Something in Kilnworks isn't working as documented
title: ""
labels: bug
assignees: ""
---

**Version / commit**
The git commit or Docker image tag you're running.

**Deployment**
`docker compose up` or manual (`uv run kilnworks serve` + `worker`)?

**Provider config**
Chat/embedding provider and model (`KILNWORKS_CHAT_PROVIDER`, `KILNWORKS_EMBEDDING_PROVIDER`,
model names, etc.) — **omit API keys and secrets**.

**Steps to reproduce**
1.
2.
3.

**Expected behavior**


**Actual behavior**


**Logs**
Relevant output from `docker compose logs api` / `worker`, or CLI stderr. Redact
any secrets first.
