---
name: Maturation pipeline — classify, triage, sleep, prefill
description: How notes flow through YETI today, where each stage lives, and the extension points for future curation work
type: project
originSessionId: 5738d75f-5996-4fe2-8860-93d229badcb9
---
End-to-end note pipeline as of session 2026-04-25. Each stage is a Celery task with a clear failure mode (fail-open or fail-soft). All stages exist on `main`; deploy via `bin/deploy`.

## Flow

```
ingest (sync_gmail / sync_outlook / api/notes) ─┐
                                                ▼
                                       classify_note  ── src/yeti/agents/prefilter.py
                                       │
                          ┌────────────┼────────────┐
                          ▼            ▼            ▼
                       discard   informational    full
                          │            │            │
                       (drop)     drawer in       triage_note
                                  room=context-   (existing pipeline)
                                  only            │
                                                  ▼
                                           drawers + KG facts
                                           + inbox items
                                           (PROPOSED_ACTION,
                                            DECISION,
                                            PERSON_UPDATE,
                                            DISAMBIGUATION)
```

Pre-classifier rules first (Auto-Submitted, noreply, vendor domains, security/calendar subjects, mailing-list headers); LLM (Haiku) only for ambiguous remainder. Verdict + reason persisted on `notes.triage_level` / `notes.triage_reason`. Fail-open to `full` on any error.

## Triage hooks added in this session

- **Name canonicalisation** (`src/yeti/agents/name_resolver.py`): `people_mentioned` folded through canonical-name index built from contact drawers. NFC + diacritic strip + lowercase. Single match → substitute. Multi or zero → pass through. 5-min TTL cache.
- **KG-known fast path** (`triage._person_known_in_kg`): bare-name kg_query returning any fact short-circuits PERSON_UPDATE creation. Fold-compares for diacritic-tolerant equality.
- **Auto-rendered contact drawer** (`identity.ensure_contact_drawer`): after KG facts about a person are added, re-render their drawer in `people/contacts` deterministically (Name / Role / Company / Emails / Phone / Other).
- **Pattern prefill** (`models/resolution_patterns.py` + `triage._prefill_with_pattern`): PROPOSED_ACTION + DECISION items get `suggested_disposition` if pattern count ≥ 2; auto-apply when explicitly toggled per pattern.

## Sleep — nightly at 04:00 (`worker.sleep_deterministic`)

1. **Dedupe** (`src/yeti/sleep/dedupe.py`): exact-text match per (wing, room). Older drawer marked superseded via `SupersededStore`. Search filters superseded ids out. Soft delete; reversible.
2. **Reconcile** (`src/yeti/sleep/reconcile.py`): per-entity walk over contact drawers. Within predicate-equivalence groups (role/has_role/title; works_at/company/employer; phone/has_mobile_number) keep newest fact, `kg_invalidate` older. Emails not reconciled — multiple current addresses are normal.
3. **Gap-fill** (`src/yeti/sleep/gaps.py`): senders with ≥3 emails in last 14 days who lack any people/contacts drawer get a PERSON_UPDATE inbox item prefilled from the From: header. Skip-if-pending so re-runs don't pile duplicates.

## Daily summary — 23:30 (`worker.daily_summary`)

Writes one drawer to `wing=meta, room=daily-summary, source=sleep-summary:YYYY-MM-DD`. Aggregates last 24h: notes by triage level, inbox created/resolved/auto-resolved, drawers superseded. No LLM. Daniel + future-Claude can query the timeline via `memory_search wing=meta`.

## Retrieval log

`retrieval_log` table (`src/yeti/memory/usage.py`) records every drawer/fact retrieval with `source` tag (chat / triage / api / skill / etc). Powers future pruning ("never-retrieved drawers older than N days") and visibility ("entity X queried ≥ N times this week"). Logging is fire-and-forget; never blocks retrievals.

## Wing/room conventions in use

| Wing | Rooms (notable) | Notes |
|------|-----------------|-------|
| `globalstudio`, `conetic`, `above` | per-org content | Hard isolation; ingestion sources pin via `forced_wing` |
| `globalstudio`, `conetic`, `above` | `hr-profiles` | HR profiles live under each org's wing — never a flat `hr` wing |
| `people` | `contacts` | Person drawers (incl. self with `source=self`); cross-wing read for resolver |
| `meta` | `daily-summary` | One drawer per day from sleep summary |
| `<wing>` | `context-only` | Informational drawer, no extraction |
| `<wing>` | `noise` | (currently unused — discard drops, doesn't write) |

## Drawer source tags

- `task:<id>` — written by the yeti-task skill at session close
- `note:<id>` — raw note drawer
- `note:<id>:informational` — informational classifier output
- `contact-auto:<name-lower>` — auto-rendered contact
- `self` — canonical owner profile
- `sleep-summary:<date>` — daily summary
- `sleep-gaps` — gap-fill inbox prompts (on payload, not drawer source)
- `triage:<note-id>` — older bootstrap drawers from triage path

## API additions

- `GET  /api/memory/entity/<name>` — merged drawer + KG facts + retrieval stats in one call
- `GET  /api/inbox/patterns` — list learned patterns
- `POST /api/inbox/patterns/auto-apply` — flip per-pattern autonomy

## Extension points (deferred)

- **Cosine-similarity dedupe**: only exact-text shipped; near-duplicates remain. Risk-managed threshold tuning required.
- **LLM-composed gap-fill drafts**: gap-fill only prefills name from headers; could compose a profile body via Haiku from incidental mentions.
- **Telemetry sparkline dashboard**: dropped intentionally — saturation visible via the daily-summary drawers themselves.
- **Pattern-key normalisation**: today raw title; tighten (strip dates / IDs / names) only if false positives appear.
- **Negative knowledge**: "asked about X, no answer" not yet remembered; would prevent re-asking the same hole.

## Operational gotchas captured this session

- Kamal 2 has no `kamal env push` — use `kamal redeploy` or `kamal deploy`.
- Dockerfile uses `uv` (was pip backtracking on `litellm` + `pydantic-ai` and hanging >10 min).
- `bin/deploy` raises `ulimit -n 4096` and purges stale pytest tmpdirs (macOS default fd cap is 256; pytest's symlink cleanup walks all past sessions).
- Outlook above.aero failed with `MailboxNotEnabledForRESTAPI` because Daniel's identity is a B2B guest in `touchinflight.onmicrosoft.com` rather than a provisioned mailbox there. Real fix: flip YETI app to multi-tenant in Azure, clear `YETI_MICROSOFT_TENANT_ID`, re-auth above mailbox.
- Worker now advances `sync_watermark` on 4xx so a permanently-broken mailbox doesn't re-scan 24h every 5 minutes.
