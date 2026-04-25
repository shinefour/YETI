---
name: MemPalace philosophy — curated memory, not dump
description: YETI's MemPalace must behave like a person remembering important bits, not a dump for all email content. Sleep-style consolidation is part of the design.
type: project
originSessionId: 5738d75f-5996-4fe2-8860-93d229badcb9
---
MemPalace is intended to be a curated long-term memory. Source-of-truth for raw correspondence is the email inbox itself; MemPalace stores only what's worth remembering. Pollution of MemPalace (storing noise drawers under the hope that "sleep will clean it later") degrades retrieval quality on every subsequent query — embedding space is precious.

**Why:** Daniel stated: "I don't want the mempalace to become a dump for ALL the information. It's supposed to be like a 'person' that remembers the important bits." He wants the system to play to AI strengths (consolidation, summarisation, gap-detection) and proposed "sleep" — a periodic process inspired by human memory consolidation. The intent is clear quality bias.

**How to apply:**
- Pre-classifier with hard drop on noise (no drawer for `discard` items) — the mailbox is backup; MemPalace stays clean.
- Sleep operations should be concrete and named (dedupe, reconcile, prune, gap-fill), not a vague "magic LLM cleanup".
- Every drawer added is a cost on every retrieval; ask "is this drawer worth retrieving on a query I might run later?"
- Daily / periodic consolidation is OK, but operate on the curated set — not on raw email dumps.
- Daniel's framing of me (Claude) as the consumer means I should optimise for *retrieval quality* over *ingestion completeness*.
