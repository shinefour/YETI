"""Sleep — periodic memory consolidation.

Deterministic operations that keep MemPalace clean without LLM cost:
dedupe drawers, reconcile contradictory KG facts, surface gaps. Each
operation is independently invocable and writes an audit log so we
can inspect what sleep did.
"""
