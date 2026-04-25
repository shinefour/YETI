# YETI auto-memory (committed)

This directory holds Claude Code's auto-memory for the YETI project. Files
are committed so the same context follows you across machines and is shared
with collaborators. Generic / cross-project preferences live in
`~/.claude/CLAUDE.md` instead.

## How auto-memory works

Claude Code reads `MEMORY.md` (the index) plus the linked files at the
start of every session. Default storage is
`~/.claude/projects/<sanitized-cwd>/memory/`. We override that to point at
this in-repo directory so memory travels with the code.

## One-time setup per machine

Auto-memory location is intentionally NOT settable from a checked-in
`.claude/settings.json` (Claude Code ignores `autoMemoryDirectory` there
for security — a malicious commit could redirect memory writes). Each
contributor sets it once in their user-global settings.

Add this key to `~/.claude/settings.json` (replace the path with your
checkout location):

```json
{
  "autoMemoryDirectory": "/absolute/path/to/YETI/.claude/memory"
}
```

Restart Claude Code. Verify by asking it about a project memory item — it
should answer using content from this directory.

## File layout

- `MEMORY.md` — index. One line per memory file with a short hook. Always
  loaded; keep concise.
- `feedback_*.md` — durable rules ("don't add Co-Authored-By", "never mix
  wings", etc.). Follow the format defined in
  `~/.claude/CLAUDE.md` (Why / How to apply).
- `project_*.md` — project-specific context (architecture, conventions,
  current state). Update when the situation changes.
- `user_*.md` — Daniel's role, tools, working preferences.

## What NOT to put here

Anything Claude Code can derive from the codebase or git history (file
paths, symbol locations, commit messages, recent diffs). Memory is for
the durable context-around-the-code, not a mirror of it.

Secrets, credentials, and personal identifiers tied to private accounts
also do not belong here — this directory is committed.
