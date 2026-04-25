---
name: YETI project overview
description: Personal AI productivity system — current state, tech stack, key decisions, deployed surface
type: project
originSessionId: 5738d75f-5996-4fe2-8860-93d229badcb9
---
YETI (Your Everyday Task Intelligence) is Daniel's personal AI-centric productivity system. Consolidates Outlook + Gmail + Teams + Slack + Jira + Notion + Calendar into one hub. Single-user.

**Current state (2026-04-25):** Live on Hetzner via Kamal at `https://yeti.diconve.com`. Three ingestion paths working: Gmail (Global Studio), Outlook (Conetic mailbox; Above mailbox waiting on Azure multi-tenant flip), Telegram + dashboard + CLI for manual notes. Image OCR with manual-fallback. Memory in MemPalace (ChromaDB + KG facts via MCP).

**Architecture pipeline:** Ingest → pre-classifier (discard / informational / full) → triage (people / facts / proposed actions / clarifications) → MemPalace drawers + KG + Inbox. Nightly sleep at 04:00: dedupe drawers, reconcile KG facts, surface gap-people. Daily-summary drawer at 23:30 in `wing=meta`. See `project_maturation_pipeline.md`.

**Tech stack:** Python 3.12+, FastAPI, PydanticAI, LiteLLM (default Sonnet 4, fast Haiku 4.5), Celery+Redis, HTMX+Jinja2 dashboard, Kamal 2 deploy, MemPalace MCP for memory, MSAL for Outlook OAuth, google-auth + msal for the two mail integrations.

**Key decisions:**
- Wing isolation is hard, not advisory — globalstudio / conetic / above never mix; ingestion sources pin a wing.
- Memory is curated, not a dump — pre-classifier hard-drops noise.
- Inbox = clarification queue, not action queue — Daniel always confirms before YETI executes.
- Sleep ops are deterministic first; LLM passes opt-in only.
- Pattern learning prefills, doesn't auto-apply, until Daniel toggles per-pattern autonomy.
- Hetzner cloud + Docker containers via Kamal; uv installer in Dockerfile (pip resolver was hanging).

**Repo:** git@github.com:shinefour/YETI.git on `main`. Deploy: `bin/deploy` runs lint + tests + `kamal deploy`. fd ulimit raised + stale pytest tmpdirs purged in the script (macOS quirk).

**Why:** Daniel has too many screens/portals/communications. Wants knowledge base for project docs, person network, topic tracking over time, and human-in-the-loop approval. Future-Claude (in fresh sessions) is the primary consumer of MemPalace; retrieval quality is the optimisation target.
