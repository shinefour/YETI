# YETI — Design Document

## 1. Vision & Goals

YETI (Your Everyday Task Intelligence) is a single-user, AI-centric productivity system that consolidates fragmented screens, portals, and communications into one intelligent hub.

### Problem

Daily work is scattered across too many tools — Teams, Slack, Jira, Notion, Calendar, email — each with its own notifications, context, and mental overhead. Information falls through cracks. Meeting notes go unactioned. Relationships and context about people are stored only in memory. Tracking whether activities align with project or company goals requires manual effort that rarely happens.

### Goals

- **Consolidate** — One system to interact with all work tools, accessible via Telegram (mobile), web dashboard (desktop), and CLI (terminal)
- **Automate** — Background agents continuously gather, process, and organize information from all integrated sources
- **Remember** — A persistent knowledge base and person network that grows over time, making context available when needed
- **Track** — Longitudinal topic tracking: delegation decisions, role fit, goal alignment — queryable over time
- **Control** — Human-in-the-loop: YETI proposes, Daniel decides. Automation without loss of control

### Non-Goals

- Multi-user / multi-tenant system
- Real-time collaboration features
- Replacing the integrated tools (Jira, Notion, etc.) — YETI is a layer on top

---

## 2. Architecture

### Style: Modular Monolith

A single Python application with clearly separated modules, deployed via Kamal to a Hetzner VPS. No microservices — for a single-user system on a single server, microservices add operational complexity (networking, service discovery, distributed tracing) with no scaling benefit.

### System Overview

```
          +----------+  +-----------------+  +------------------+
          | CLI      |  | Telegram Bot    |  | Web Dashboard    |
          | (terminal)|  | (mobile access) |  | (browser access) |
          +-----+----+  +--------+--------+  +--------+---------+
                |                 |                    |
                +--------v--------v--------------------v-------+
                    |          kamal-proxy (Reverse Proxy)      |
                    |          TLS (Let's Encrypt), routing,    |
                    |          zero-downtime traffic switching   |
                    +---------------------+--------------------+
                                          |
                    +---------------------v--------------------+
                    |            YETI Core API (FastAPI)        |
                    |                                           |
                    |  +-------------+  +-------------------+  |
                    |  | Chat Agent  |  | Integration       |  |
                    |  | Triage Agent|  | Adapters          |  |
                    |  | Research Ag.|  | (Teams, Slack,    |  |
                    |  | Action Agent|  |  Jira, Notion,    |  |
                    |  +------+------+  |  Calendar)        |  |
                    |         |         +-------------------+  |
                    +---------+--------------------------------+
                              |
              +---------------+---------------+
              |               |               |
     +--------v--------+ +---v---+ +---------v---------+
     | AI Orchestration | | Redis | | Celery Workers    |
     | (LiteLLM Router) | |       | | + Celery Beat     |
     +--------+---------+ +-------+ | (Background Jobs) |
              |                      +-------------------+
     +--------v--------+
     | Model Providers  |
     | - Claude (API)   |
     | - ChatGPT (API)  |
     | - Ollama (local)  |
     +------------------+

     +------------------+  +------------------+
     | MemPalace        |  | ChromaDB         |
     | (MCP Server)     |  | (Vector Store)   |
     | SQLite KG        |  |                  |
     +------------------+  +------------------+
```

### Core Services

Deployed via Kamal — the application services are managed by Kamal's zero-downtime deploy pipeline, while supporting services (databases, inference) run as Kamal accessories.

**Application services** (managed by Kamal, zero-downtime deploys):

| Service | Role |
|---------|------|
| `yeti-api` | FastAPI core — API endpoints, agent orchestration, webhook receivers |
| `yeti-worker` | Celery worker(s) — background task execution |
| `yeti-scheduler` | Celery Beat — scheduled job triggers |
| `yeti-telegram` | Telegram bot — long-polling or webhook mode |

**Accessories** (managed by Kamal as long-running infrastructure):

| Service | Role |
|---------|------|
| `mempalace` | MemPalace MCP server — memory management |
| `chromadb` | ChromaDB — vector storage and semantic search |
| `redis` | Message broker (Celery) + cache |
| `ollama` | Local model inference (Llama 3, nomic-embed-text) |

**Proxy:** `kamal-proxy` handles reverse proxying, automatic TLS via Let's Encrypt, and zero-downtime traffic switching — replacing the need for a separate Caddy/Nginx/Traefik container.

### Technology Stack

| Layer | Technology | Rationale |
|-------|-----------|-----------|
| Language | Python 3.12+ | AI/ML ecosystem, mature SDKs for all integrations |
| Web framework | FastAPI | Async, OpenAPI docs, Pydantic-native |
| Task queue | Celery + Redis | Proven for background jobs; Redis doubles as cache |
| Database | SQLite (structured data, action items) | Simple, no separate server needed for single-user |
| Deployment | Kamal 2 | Zero-downtime deploys, built-in rollback, no orchestrator needed |
| Proxy | kamal-proxy | Automatic HTTPS, zero-downtime traffic switching, built into Kamal |
| Monitoring | Upright | Synthetic monitoring from external locations, owned infrastructure |

---

## 3. AI Orchestration

### Model Router: LiteLLM

LiteLLM provides a unified OpenAI-compatible API that routes to all model providers. It handles retries, fallbacks, and cost tracking.

**Routing Rules:**

| Task Type | Primary Model | Fallback | Rationale |
|-----------|--------------|----------|-----------|
| Complex reasoning, planning, code | Claude Opus / Sonnet | GPT-4o | Best at structured analysis |
| Quick summarization, chat | Claude Haiku / GPT-4o-mini | — | Cost-efficient for lightweight tasks |
| Image analysis | Claude Sonnet (vision) | GPT-4o (vision) | Both support multimodal input |
| Sensitive / private data | Local model (Ollama — Llama 3) | — | Data never leaves the server |
| Embeddings | Local (nomic-embed-text via Ollama) | OpenAI text-embedding-3-small | Saves API costs, good quality |

### Agent Framework: PydanticAI

PydanticAI is lightweight, type-safe, and Pydantic-native — a natural fit with FastAPI. It supports tool definitions, structured output, and dependency injection without the abstraction weight of LangChain/LangGraph.

If complex multi-agent orchestration (cyclic graphs, human-in-the-loop checkpointing) proves necessary later, LangGraph can be introduced for specific workflows.

### Agent Types

| Agent | Trigger | Purpose |
|-------|---------|---------|
| **Chat Agent** | User message (Telegram or web) | Interactive conversation, routes to tools, answers questions |
| **Triage Agent** | Incoming notification / webhook | Classifies incoming information, decides action: store, notify, create action item, or ignore |
| **Research Agent** | User request or scheduled | Given a topic, searches across integrations, web, and knowledge base to compile a briefing |
| **Action Agent** | Approved action item | Executes write operations (create Jira ticket, send Teams message, update Notion page) — always with confirmation for destructive actions |

---

## 4. Integration Layer

### Adapter Protocol

Every external system is accessed through an adapter that implements a common interface:

```python
class IntegrationAdapter(Protocol):
    async def pull(self, since: datetime) -> list[Event]:
        """Pull new events/changes since the given timestamp."""
        ...

    async def push(self, action: Action) -> Result:
        """Execute a write action on the external system."""
        ...

    async def search(self, query: str) -> list[Item]:
        """Search the external system."""
        ...
```

This ensures all integrations are interchangeable from the agents' perspective and simplifies adding new integrations.

### Integrations

| Integration | SDK / API | Inbound Strategy | Outbound Strategy |
|-------------|-----------|------------------|-------------------|
| **Microsoft Teams** | Microsoft Graph API (`msgraph-sdk-python`) | Webhooks for messages and mentions | Send messages, create channels |
| **Slack** | Slack SDK (`slack-bolt`) | Events API or Socket Mode | Send messages, update channels |
| **Jira** | `atlassian-python-api` | Webhooks (JQL-filtered) + periodic full sync | Create/update issues, transitions, comments |
| **Notion** | `notion-client` (official SDK) | Polling every 5 min (Notion has no webhooks) | Create/update pages and databases |
| **Calendar (Outlook)** | Microsoft Graph API | Webhooks (push notifications) for event changes | Create/update events |
| **Email (Outlook)** | Microsoft Graph API | Webhooks for new mail, polling for search | Send replies, flag/categorize |

### Webhook Gateway

Teams, Slack, and Jira webhooks require a public HTTPS endpoint. kamal-proxy on Hetzner with Daniel's domain provides this. A simple `/webhooks/{integration}` FastAPI route handles incoming payloads and dispatches them to the Triage Agent.

### Notion Polling

Notion does not support webhooks. A scheduled Celery job polls for changes every 5 minutes using the `last_edited_time` filter. This is well within API rate limits.

---

## 5. Memory & Context System

### Why MemPalace (Not Just ChromaDB)

ChromaDB alone is a flat vector database — semantic search over documents with metadata filtering. It handles "find similar content" well, but lacks:

- **Hierarchy** — no way to scope "find Alice's comments" to a specific project without manual filtering
- **Temporal logic** — no concept of "what was true in Q1?" vs "what is true now?"
- **Cross-domain linking** — no connections between related topics in different projects

MemPalace uses ChromaDB internally but adds three critical layers:

| Layer | What It Adds | Impact |
|-------|-------------|--------|
| Hierarchical structure (Wings/Rooms) | Scopes memory by person, project, topic | +34% retrieval accuracy |
| Temporal knowledge graph (SQLite) | Entity relationships with time validity windows | Enables "how has this changed?" queries |
| Cross-linking (Halls/Tunnels) | Connects related topics across domains | Decisions in Project A that affect Project B are discoverable from both |

### Architecture

MemPalace runs as an MCP (Model Context Protocol) server within the Docker environment. All AI agents connect to it via MCP, sharing the same memory state.

```
Agent (PydanticAI)
  --> MCP Client
    --> MemPalace MCP Server
      --> ChromaDB (semantic search, verbatim storage)
      --> SQLite (temporal knowledge graph)
```

### Memory Organization

**Wings** map to Daniel's work domains:

| Wing Type | Examples |
|-----------|---------|
| Project wings | Project Alpha, Product Beta |
| People wings | Key colleagues, stakeholders, external contacts |
| Domain wings | Engineering, Management, Company Strategy |
| System wing | YETI development (see below) |

### YETI Development Context

YETI's own development is managed as a regular project Wing (`yeti-development`) within the same MemPalace instance. There is no separate system or second context — YETI manages its own evolution alongside business work. This means Daniel can use YETI to track YETI features, bugs, architecture decisions, and iteration plans just like any other project.

Benefits of this approach:
- **Single system** — no context-switching between "the tool" and "the work"
- **Dogfooding** — YETI's own workflows validate and stress-test the system
- **Cross-linking** — a business need ("I wish YETI could do X") naturally links to a YETI development action item

**Security consideration:** The `yeti-development` Wing contains sensitive system internals (API keys, infrastructure decisions, security architecture). This Wing is tagged as `sensitive`, which means its contents are routed to the local Ollama model only and are excluded from external API calls to Claude/OpenAI.

### Development Resilience

If a bad deploy breaks YETI, Daniel can't use YETI to fix YETI. The system must remain debuggable and recoverable independently.

**Safeguards:**

| Risk | Mitigation |
|------|-----------|
| Bad deploy breaks the API | **Instant rollback:** `kamal rollback` reverts to the previous container in one command. Kamal keeps the previous version available on the server. |
| MemPalace data corrupted | **Separate data volumes:** Accessories (MemPalace, ChromaDB, Redis) run independently from app deploys. A bad application deploy cannot corrupt stored data. |
| System fully down | **SSH is always available.** SSH access, Docker, and system tools are independent of YETI. Daniel can SSH in, inspect logs (`kamal app logs`), roll back, or rebuild from scratch. |
| Need AI assistance while YETI is down | **Claude Code works independently.** Claude Code on the server only needs SSH and the codebase — it does not depend on YETI services being up. MemPalace data files are still on disk and readable even if the MCP server is down. |
| Undetected outage | **Upright synthetic monitoring** checks YETI from external locations and alerts Daniel via Telegram/email if the system is down. |

**Development workflow to minimize risk:**

1. **Kamal's built-in zero-downtime deploy:** `kamal deploy` builds the new image, starts it alongside the running one, health-checks it, then switches traffic via kamal-proxy. If the new container fails its health check, the old one keeps serving — the deploy simply fails without affecting uptime.
2. **One-command rollback:** `kamal rollback` instantly reverts to the previous version. No manual image tag tracking needed.
3. **Accessories are stable:** MemPalace, ChromaDB, Redis, and Ollama run as Kamal accessories — they are not redeployed when the application code changes. Only `kamal accessory reboot <name>` touches them.
4. **Health checks:** Every service defines a health check endpoint. kamal-proxy only routes traffic to healthy containers.

```bash
# Normal deploy
kamal deploy                           # build, push, deploy, health-check, switch

# Something went wrong
kamal app logs                         # diagnose
kamal rollback                         # revert to previous version

# Worst case — SSH in directly
ssh yeti-server
docker ps                              # see what's running
docker logs <container>                # inspect
kamal app logs                         # or use kamal directly
```

The key principle: **SSH + Kamal + Git are the recovery baseline.** These three never depend on YETI itself being functional.

**Rooms** within each Wing organize by functional area:
- `decisions/` — recorded decisions with context and rationale
- `meetings/` — meeting notes linked to attendees and action items
- `architecture/` — technical design and evolution
- `action-items/` — tracked items with status and deadlines

### Person Network

Each person in Daniel's professional network gets a dedicated Wing containing:

- **Contact info** — name, role, organization, communication channels
- **Interaction history** — meetings attended together, messages exchanged, decisions made jointly
- **Context** — working style, expertise areas, current responsibilities
- **Cross-links** — which projects they're involved in, what topics they own

This enables queries like "What did I discuss with Alice about the auth migration?" or "Who was responsible for the billing decision in Q1?"

### Topic Tracking

The temporal knowledge graph tracks how topics evolve over time. Each entity-relationship triple has a validity window:

```
(Daniel, "considers delegating", "CI pipeline ownership")
  valid_from: 2026-01-15
  valid_to: null  (still active)
  context: "Pipeline is stable, team member expressed interest"

(Project Alpha, "aligned with", "Company Goal: Platform Reliability")
  valid_from: 2025-10-01
  valid_to: null
  context: "Q4 OKR alignment"
```

This lets Daniel query: "What tasks have I been considering delegating?" or "Are current activities still aligned with company goals?"

### Embedding Model

Local `nomic-embed-text` via Ollama for all embedding operations. Benefits:
- Zero API cost for high-volume memory operations
- Data never leaves the server
- Good quality (comparable to OpenAI text-embedding-3-small)

---

## 6. Interfaces

YETI is accessible through three channels, all hitting the same FastAPI backend and sharing the same memory/context:

### 6.1 Terminal (CLI)

A command-line client for power-user interaction directly from the terminal.

```bash
# Interactive chat
yeti chat "What's the status of Project Alpha?"

# Quick queries
yeti calendar today
yeti actions --pending
yeti person "Alice Mueller"

# Project context
yeti project alpha --summary
yeti project alpha --link-ticket PROJ-1234

# Pipe input
cat meeting-notes.md | yeti ingest --project alpha --type meeting-notes

# Interactive session (REPL)
yeti
> What did we decide about the API gateway?
> Create an action item: review Bob's PR by Friday
> Show me all open items for Project Alpha
```

**Implementation:** A Python CLI (`click` or `typer`) that calls the YETI API. Installed via `pip install yeti-cli` or just available on the server via `docker exec`. Supports both one-shot commands and an interactive REPL mode.

The CLI is especially useful for:
- Working alongside code in the terminal
- Bulk operations (ingesting documents, linking tickets)
- Scripting and automation (cron jobs, shell aliases)
- SSH access to the server when on the go

### Full AI Sessions via SSH

Daniel can SSH into the Hetzner VPS and run a full Claude Code session (or any AI-powered CLI harness) directly on the server. Since MemPalace runs as an MCP server on `localhost:3100`, it can be configured as a local MCP server in Claude Code's settings — giving the session direct access to YETI's entire memory, knowledge base, and context.

This means Daniel can:
- Work on the YETI codebase itself with full context
- Query and update the knowledge base conversationally
- Run complex multi-step workflows (research, plan, execute) with YETI's memory available throughout
- Use any MCP-compatible AI harness (Claude Code, Cursor, etc.) with the same shared memory

```bash
ssh yeti-server
# Claude Code picks up MemPalace as a configured MCP server
claude
> @mempalace What did we decide about the auth migration?
> Update the Jira adapter to handle pagination
```

This is complementary to the YETI CLI — the CLI is purpose-built for YETI's workflows, while a full AI session provides open-ended development and exploration power.

### 6.2 Mobile (Telegram)

### Why Telegram

| Feature | Telegram | Signal | WhatsApp |
|---------|----------|--------|----------|
| Bot API quality | Excellent | Unofficial, fragile | Business API, costs per conversation |
| Rich formatting | Markdown, inline keyboards | Limited | Limited |
| File/image support | Full | Full | Full |
| Business verification | Not required | Not required | Required |
| Voice messages | Supported | Supported | Supported |

### Capabilities

| Input Type | Processing |
|------------|-----------|
| Text message | Routed to Chat Agent for response |
| Image/photo | Forwarded to vision model (Claude Sonnet) for analysis |
| Voice message | Transcribed via Whisper (local or API), then processed as text |
| File upload | Stored in knowledge base, indexed |
| Inline keyboard tap | Approve/reject/modify action items and pending decisions |

### Interaction Patterns

**On-demand queries:**
- "What's on my calendar today?"
- "Summarize the last Jira updates on Project Alpha"
- "What did Bob say about the API migration?"

**Proactive notifications:**
- Morning briefing (daily digest of calendar, action items, notable updates)
- High-priority mentions in Teams/Slack
- Meeting prep briefing (15 min before a meeting, context about attendees and topics)
- Action item reminders

**Action confirmation:**
- Background agent proposes: "Create Jira ticket: Update auth middleware — Priority: High"
- Daniel sees inline keyboard: `[Approve] [Modify] [Reject]`

### Security

- Bot restricted to Daniel's Telegram chat ID (hardcoded allowlist)
- No sensitive data in notification previews
- Commands for sensitive operations require additional confirmation

### Implementation

`python-telegram-bot` library (async, well-maintained). Runs as a long-polling service or webhook-based within Docker Compose.

### 6.3 Web Dashboard

See [Section 9: Web Dashboard](#9-web-dashboard) for full details.

---

## 7. Background Agents & Scheduling

### Scheduler: Celery Beat

Celery Beat triggers scheduled jobs. Celery workers execute them. Redis serves as the message broker.

### Scheduled Jobs

| Job | Schedule | What It Does |
|-----|----------|-------------|
| Morning Briefing | Daily 07:00 | Compiles calendar, action items, Jira updates, notable messages. Pushes summary to Telegram. |
| Jira Sync | Every 15 min | Pulls new/updated issues, stores in knowledge base, updates MemPalace |
| Notion Sync | Every 5 min | Polls for page changes, indexes content into memory |
| Teams Digest | Every 30 min | Summarizes unread messages/mentions, stores in memory, notifies if high priority |
| Slack Digest | Every 30 min | Same as Teams — summarize, store, notify |
| Calendar Watch | Webhook + 5 min fallback | Detects upcoming meetings, triggers meeting prep briefings |
| Knowledge Compaction | Weekly (Sunday) | Summarizes old memories, updates knowledge graph, prunes stale action items |
| Security Audit | Daily 02:00 | Reviews logs, checks token expiry, detects anomalies |

### Event-Driven Agents

Triggered by webhooks, not schedules:

| Trigger | Agent | Action |
|---------|-------|--------|
| New Jira comment on watched issue | Triage Agent | Summarize, store in memory, notify if relevant |
| Calendar event in 15 min | Research Agent | Prepare meeting brief with attendee context from person network |
| Teams/Slack @mention | Triage Agent | Classify priority, notify via Telegram if high |
| Webhook from any integration | Triage Agent | Classify, route to appropriate handler |

---

## 8. Knowledge Base

### Storage Architecture

Hybrid approach combining three storage layers:

| Layer | Technology | What It Stores | Why |
|-------|-----------|---------------|-----|
| Documents | Markdown files in Git | Specs, architecture decisions, meeting notes, runbooks, status reports | Human-readable, versioned, diffable |
| Structured data | SQLite | Action items, person records, metadata, job state | Queryable, relational |
| Semantic index | ChromaDB (via MemPalace) | Embeddings of all content for similarity search | Enables natural language queries |

### Content Types

**Project/Product Documentation:**
- Architecture decisions and rationale
- Specifications and requirements
- Meeting notes (auto-linked to attendees and projects)
- Status reports and progress updates
- Runbooks and procedures
- Design documents

**Person Network:**

```
Person Record:
  - name: "Alice Mueller"
  - role: "Backend Lead"
  - org: "Platform Team"
  - contact: {teams: "...", slack: "...", email: "..."}
  - context: "Owns auth service, prefers async communication"
  - projects: ["Project Alpha", "Auth Migration"]
  - last_interaction: "2026-04-07 — discussed API gateway options"
```

Stored as structured SQLite records with contextual memory in MemPalace. Cross-linked to all projects, meetings, and decisions they're involved in.

**Project Tracking:**

Each project/initiative gets a unified context that ties together all related artifacts:

```
Project Context: "Auth Migration"
  - Jira tickets: PROJ-1234, PROJ-1267, PROJ-1301
  - Action items: 3 active, 2 completed
  - Related emails/threads: "Re: Auth timeline discussion"
  - Meeting notes: 2026-03-15 kickoff, 2026-04-02 status review
  - Decisions: "Use OAuth2 PKCE flow" (decided 2026-03-15)
  - People involved: Alice (lead), Bob (reviewer), Carol (stakeholder)
  - Status: In progress — blocked on PROJ-1267
```

This enables:
- "Show me everything related to the auth migration"
- "What Jira tickets are linked to this project?"
- "What's blocking progress on Project Alpha?"
- Linking a new email or action item to an existing project context

When Daniel works on a specific project, YETI loads the full context — all linked Jira tickets, past meeting notes, related decisions, and involved people — so nothing falls through the cracks.

**Topic Tracking:**

Longitudinal views on recurring themes:
- Delegation patterns: "Which tasks have I considered delegating? To whom? What happened?"
- Role fit: "Am I spending time on activities that fit my role, or drifting?"
- Goal alignment: "Are current project activities aligned with stated company/team goals?"

These are powered by the temporal knowledge graph in MemPalace, queryable with time ranges.

### Action Items

| Field | Type | Description |
|-------|------|-------------|
| `id` | UUID | Unique identifier |
| `title` | string | Short description |
| `source` | string | Where it came from (meeting, Jira, Telegram, agent-generated) |
| `status` | enum | `pending_review` / `active` / `blocked` / `completed` / `cancelled` |
| `assignee` | string | Who should do it |
| `due_date` | date | Deadline |
| `project` | string | Linked project |
| `context` | text | Why this action item exists, relevant background |
| `created_at` | datetime | When created |
| `decided_at` | datetime | When Daniel approved/rejected (null if pending) |

**Lifecycle:**

```
Agent proposes action item
  --> status: pending_review
    --> Daniel approves (Telegram/Dashboard): status: active
    --> Daniel modifies: updated, status: active
    --> Daniel rejects: status: cancelled
  --> active
    --> completed (manually or auto-detected via Jira status)
    --> blocked (dependency identified)
```

---

## 9. Web Dashboard

Hosted on the same domain as the API (e.g., `yeti.daniels-domain.com`), served by kamal-proxy alongside the API.

### Pages

**System Status:**
- Health of all Docker services (up/down, resource usage)
- Sync status per integration (last successful sync, error count)
- Last run time for each scheduled agent
- API cost tracking (LiteLLM usage by model)

**Knowledge Base Browser:**
- Browse project/product documentation by category
- Full-text and semantic search across all stored content
- View meeting notes with linked attendees and action items

**Person Network:**
- Directory of all tracked contacts with context
- Interaction timeline per person
- Cross-references to projects and decisions

**Topic Tracking:**
- Timeline views on tracked topics (delegation, role fit, goal alignment)
- Visual indicators for trends and changes over time

**Action Items & Decisions:**
- Queue of pending action items proposed by background agents
- Approve / modify / reject controls
- History of past decisions with context
- Filter by project, source, status, date

**Activity Feed:**
- Recent events across all integrations
- What YETI has done in the background (syncs, notifications sent, items created)
- Audit log of all AI-initiated external actions

### Technology

**Option A: HTMX + Jinja2 (Recommended for v1)**
- Server-rendered HTML with HTMX for interactivity
- No build step, no JS framework, no separate frontend deployment
- FastAPI serves HTML templates directly
- Fast to build, easy to iterate

**Option B: React SPA (If richer interactivity is needed later)**
- Vite + React, built as static files served by kamal-proxy
- Communicates with FastAPI via REST/WebSocket
- Better for complex interactive visualizations (e.g., person network graph, topic timelines)

**Recommendation:** Start with HTMX + Jinja2. Migrate specific pages to React components if interactivity demands it.

---

## 10. Security Model

### Threat Model

Single-user, self-hosted system. Primary threats:

| Threat | Risk Level | Description |
|--------|-----------|-------------|
| AI over-authority | High | AI agents performing unintended write operations on external systems |
| Data exfiltration | Medium | Knowledge base or memory contents leaking via API calls to model providers |
| Credential exposure | Medium | API tokens for Teams/Jira/Notion/Calendar being compromised |
| Unauthorized access | Medium | External party reaching the YETI API, dashboard, or Telegram bot |

### Controls

**AI Over-Authority:**

All agent actions are classified by risk level:

| Level | Examples | Requires Confirmation |
|-------|---------|----------------------|
| Read | Search Jira, read Notion page, check calendar | No |
| Write | Create Jira ticket, send Teams message, update Notion | Yes (unless pre-approved by rule) |
| Destructive | Delete issue, close ticket, reassign ownership | Always |

Write and destructive actions are queued as pending decisions in the dashboard / Telegram inline keyboard. Daniel approves before execution.

**Pre-approval rules** can be configured for routine write actions (e.g., "always create meeting notes in Notion after a calendar event").

**Data Isolation:**

- Sensitive/private data is routed to the local Ollama model — data never leaves the server
- API calls to Claude/OpenAI are logged with a summary of what was sent (for auditability)
- MemPalace data is stored on an encrypted volume (LUKS on Hetzner)
- No knowledge base content is included in external API calls unless explicitly needed for the query

**Credential Management:**

- All API tokens stored in Docker secrets or `.env` with strict file permissions (0600)
- Tokens are never passed to AI models as context
- Integration adapters use tokens directly — the AI layer never sees raw credentials
- Token rotation reminders via Security Audit scheduled job

**Access Control:**

| Surface | Control |
|---------|---------|
| Telegram bot | Restricted to Daniel's chat ID (hardcoded allowlist) |
| Web dashboard | Authentication required (session-based or API key) |
| API endpoints | Behind kamal-proxy, API key or mTLS |
| SSH to server | Key-only authentication, no password |
| Hetzner firewall | Allow only ports 443, 80, 22 |

**AI Guardrails:**

- Token budget per request (prevent runaway API costs)
- Rate limiting on write operations (max N operations per hour per integration)
- Comprehensive audit log of all AI-initiated external actions
- System prompts include explicit boundaries on what agents may modify

---

## 11. Deployment (Hetzner Cloud)

### Server Specification

| Spec | Value | Notes |
|------|-------|-------|
| Plan | CPX31 | Upgrade to CPX41 if heavy local model inference |
| vCPU | 4 | Sufficient for all services + occasional Ollama inference |
| RAM | 8 GB | Llama 3 8B fits in ~5 GB; ChromaDB and Redis share the rest |
| Storage | 160 GB SSD | Ample for knowledge base, vector store, and model weights |
| Cost | ~EUR 12/month | CPX41 at ~EUR 22/month if needed |
| Location | EU (Falkenstein or Helsinki) | Data sovereignty |

### Deployment with Kamal 2

Kamal deploys Docker containers directly to the VPS via SSH — no orchestrator, no cluster, no complexity. It builds images, pushes them to a registry, SSHes into the server, pulls the new image, starts it, health-checks it, and switches traffic — all in a single `kamal deploy` command.

**Philosophy (37signals):** You don't need Kubernetes. You need SSH, Docker, and a good deploy tool. Kamal is that tool.

**Key capabilities for YETI:**
- **Zero-downtime deploys:** kamal-proxy holds requests briefly during container switchover — no dropped connections
- **Built-in rollback:** `kamal rollback` reverts to the previous version instantly
- **Accessories:** Long-running infrastructure (MemPalace, ChromaDB, Redis, Ollama) managed separately from app deploys — they are not restarted when application code changes
- **Automatic HTTPS:** kamal-proxy handles Let's Encrypt certificate provisioning
- **Multi-service on one server:** Designed for exactly this scenario

```yaml
# config/deploy.yml (Kamal configuration)
service: yeti
image: yeti-api

servers:
  web:
    hosts:
      - <hetzner-vps-ip>
    labels:
      kamal-proxy-ssl: true

  worker:
    hosts:
      - <hetzner-vps-ip>
    cmd: celery -A yeti.worker worker --loglevel=info

  scheduler:
    hosts:
      - <hetzner-vps-ip>
    cmd: celery -A yeti.worker beat --loglevel=info

  telegram:
    hosts:
      - <hetzner-vps-ip>
    cmd: python -m yeti.bot.telegram

proxy:
  host: yeti.daniels-domain.com
  ssl: true
  app_port: 8000
  healthcheck:
    path: /health
    interval: 5

registry:
  server: ghcr.io
  username: daniel
  password:
    - KAMAL_REGISTRY_PASSWORD

accessories:
  mempalace:
    image: mempalace:latest
    host: <hetzner-vps-ip>
    port: 3100
    volumes:
      - mempalace-data:/data

  chromadb:
    image: chromadb/chroma:latest
    host: <hetzner-vps-ip>
    port: "8001:8000"
    volumes:
      - chroma-data:/chroma/chroma

  redis:
    image: redis:7-alpine
    host: <hetzner-vps-ip>
    port: 6379
    volumes:
      - redis-data:/data

  ollama:
    image: ollama/ollama:latest
    host: <hetzner-vps-ip>
    port: 11434
    volumes:
      - ollama-data:/root/.ollama

env:
  secret:
    - ANTHROPIC_API_KEY
    - OPENAI_API_KEY
    - TELEGRAM_BOT_TOKEN
    - JIRA_API_TOKEN
    - MICROSOFT_CLIENT_SECRET
    - SLACK_BOT_TOKEN
```

### Domain & TLS

Daniel's existing domain pointed at the Hetzner VPS via A record. kamal-proxy handles automatic TLS certificate provisioning via Let's Encrypt — no separate reverse proxy configuration needed.

### Deployment Workflow

```bash
# First-time setup
kamal setup                     # provisions server, installs Docker, starts accessories

# Normal deploy (from local machine or CI)
kamal deploy                    # build → push → pull → health-check → switch traffic

# Rollback if something breaks
kamal rollback                  # revert to previous version

# View logs
kamal app logs                  # application logs
kamal accessory logs mempalace  # accessory logs

# Restart a single accessory
kamal accessory reboot redis    # restart Redis without touching the app

# SSH into the server
kamal app exec -i bash          # interactive shell in app container
```

**CI integration (optional):**
1. Push to `main` triggers GitHub Actions
2. CI runs lints and tests
3. On success, CI runs `kamal deploy`

### Monitoring with Upright

Upright (37signals' open-source synthetic monitoring) runs on a separate cheap VPS and monitors YETI from the outside — so Daniel gets alerted even if YETI is completely down.

**Setup:** A small Hetzner CX22 (~EUR 4/month) or DigitalOcean droplet running Upright, deployed via Kamal.

**Probes configured for YETI:**

| Probe Type | What It Checks | Interval |
|------------|---------------|----------|
| HTTP | `/health` endpoint returns 200 | Every 30 seconds |
| HTTP | `/api/status` returns healthy integration states | Every 5 minutes |
| Playwright | Dashboard loads, login works, action items page renders | Every 15 minutes |
| HTTP | Telegram webhook endpoint is reachable | Every 1 minute |

**Alerting:** Upright sends alerts via Telegram (ironic but practical — if the YETI bot is down, the alert goes to Daniel's Telegram directly from Upright, not through YETI) and email.

**Why Upright over commercial monitoring:**
- Owned infrastructure — no vendor dependency for observability
- Playwright probes catch real user-facing issues, not just "port is open"
- ~EUR 4/month for a dedicated monitoring node
- Deployed and managed with the same Kamal workflow as YETI itself

### Backup Strategy

Data falls into two categories with different backup strategies:

**Git-backed (versioned, diffable):**

| Data | Backup Method |
|------|--------------|
| Application code | GitHub (private repo) — the source of truth, Kamal deploys from here |
| Knowledge base Markdown files | Separate private GitHub repo — versioned documentation, specs, meeting notes, runbooks |
| Kamal config (`deploy.yml`) | Part of the application repo |

**Volume-backed (binary/database — not suited for Git):**

| Data | Backup Method |
|------|--------------|
| MemPalace (ChromaDB + SQLite KG) | restic → Hetzner Storage Box |
| SQLite (action items, structured data) | restic → Hetzner Storage Box |
| Redis (task queue state) | restic → Hetzner Storage Box |
| Ollama model weights | Re-downloadable, no backup needed |

**restic backup schedule:**
- **When:** Daily at 03:00 via cron
- **Where:** Hetzner Storage Box (separate infrastructure from the VPS)
- **How:** Encrypted, deduplicated, incremental
- **Retention:** 7 daily, 4 weekly, 3 monthly
- **Restore test:** Monthly automated restore to a temp directory to verify backups are valid

---

## Appendix: Key Technology Decisions

| Decision | Chosen | Alternatives Considered | Rationale |
|----------|--------|------------------------|-----------|
| Architecture style | Modular monolith | Microservices | Single-user, single-server — no scaling benefit from microservices |
| Language | Python | TypeScript, Go | AI/ML ecosystem is Python-first; mature SDKs for all integrations |
| Web framework | FastAPI | Flask, Django | Async, OpenAPI docs, Pydantic-native |
| Agent framework | PydanticAI | LangGraph, CrewAI, Autogen | Lightweight, type-safe, natural fit with FastAPI; LangGraph available as upgrade path |
| Model routing | LiteLLM | Manual API switching | Unified interface, fallback support, cost tracking |
| Mobile interface | Telegram | Signal, WhatsApp | Best bot API, rich features, no business verification, free |
| Memory system | MemPalace (MCP) | Raw ChromaDB, Zep, custom | Hierarchical scoping (+34% retrieval), temporal knowledge graph, MCP protocol |
| Embeddings | Ollama (nomic-embed-text) | OpenAI API | Free, fast, data stays local |
| Deployment | Kamal 2 | Docker Compose, K3s, Kubernetes | Zero-downtime deploys, built-in rollback, accessories for infra — no orchestrator needed (37signals philosophy) |
| Proxy | kamal-proxy | Caddy, Nginx, Traefik | Built into Kamal, automatic HTTPS, zero-downtime traffic switching |
| Monitoring | Upright | Pingdom, UptimeRobot, Datadog | Owned infrastructure, Playwright probes for real user-facing checks, ~EUR 4/month |
| Task queue | Celery + Redis | APScheduler | Production-grade, supports scheduled and event-driven jobs |
| Knowledge base storage | Markdown/Git + SQLite + ChromaDB | Full database (Postgres) | Human-readable, versionable, searchable; Postgres is overkill for single-user |
| Dashboard (v1) | HTMX + Jinja2 | React SPA | No build step, fast to iterate, upgrade path to React exists |
