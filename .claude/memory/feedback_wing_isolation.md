---
name: Wing isolation is hard requirement
description: Mailbox/integration contexts (globalstudio, above, conetic) must not share memory or context in MemPalace — isolation enforced, not advisory
type: feedback
originSessionId: 5738d75f-5996-4fe2-8860-93d229badcb9
---
Each YETI integration source (mailbox, tenant, company) maps to its own MemPalace wing. Storage and retrieval must **never** mix content across these wings.

**Why:** Daniel has clear separation between Global Studio work and Conetic/Above Aero work — legally, contractually, and cognitively. Leaking a Conetic email into the globalstudio wing (or vice versa) is a correctness bug, not a preference. User stated: "functionally its wanted to interact in a central spot, but in terms of memory and context it can't be mixed."

**How to apply:**
- Any new ingestion source (email, Slack, calendar, etc.) must be pinned to a specific wing via config, not inferred by LLM.
- Triage agent must treat a forced wing as non-overridable when the source supplies one.
- Central surfaces (chat, dashboard search) may *query across* wings but writes stay wing-scoped.
- Never route "ambiguous" notes to `general` when source is known — use the source wing.
- When designing new integrations, add wing config upfront; don't defer.
