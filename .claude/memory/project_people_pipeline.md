---
name: People pipeline (no proactive queue)
description: People pipeline has no "Needs profile" queue; two paths only — silent KG-driven auto-promotion and chat-driven on-demand creation
type: project
originSessionId: 944f5190-cb83-4a69-8f2c-3c20098b1e26
---
Decision (2026-05): proactive people-bookkeeping surfaces are retired.
The People page is browse-only; nightly sleep does silent
earned-promotion (`run_earned_promotions` in `src/yeti/sleep/gaps.py`);
chat-driven creation uses `save_person_profile` in
`src/yeti/agents/chat.py`. Inbox no longer carries PERSON_UPDATE
items from sleep OR from triage — triage's `_resolve_people`
silently skips unknown names. DISAMBIGUATION items (multiple-match
"Which X is this?") stay; those are actionable choices, not
bookkeeping. Names that look email-local-style ("warren.hamilton")
are humanized to "Warren Hamilton" via
`name_resolver.humanize_email_local` before KG writes / drawer
lookups.

**Why:** Task queues work because tasks have intrinsic urgency.
Person profiles are bookkeeping — no deadline, no consumer. The old
"Who is X?" inbox prompts piled up because nothing forced
resolution, and threshold tuning never fixed the underlying mismatch.
Curated-memory philosophy (see `project_memory_philosophy.md`) says
YETI behaves like a person, not a CRM — people don't maintain daily
"unknown contacts" backlogs.

**How to apply:** Do not propose re-introducing a "Needs profile"
queue, nag surface, or proactive person prompt as a way to fix gaps.
If KG-known contacts aren't getting drawers, fix the silent
auto-promotion path (or the MemPalace persistence bug that swallows
chromadb upsert failures), not the surfacing logic. New autonomy
patterns belong inside `run_earned_promotions` (e.g. projects,
topics), not new inbox surfaces. Cold senders accumulate KG facts
via normal triage and become eligible later.
