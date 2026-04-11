---
name: vault-ingest
description: "Ingest a source into the Obsidian wiki. Use /ingest <path> or 'ingest this article/chat/paper' to add research to the knowledge base. Delegates to the vault-scribe subagent which handles summary writing, entity/concept page updates, cross-linking, index and log updates, and contradiction flagging."
allowed-tools: Read, Glob, Grep, Bash, Write, WebFetch, Agent
---

# Vault ingest — add a source to the knowledge base

## Workflow

1. **Identify the source** based on user input:
   - If the user provided a path inside `vault/Weather Vault/raw-sources/`, use it directly.
   - If the user pasted content, first write it verbatim to `vault/Weather Vault/raw-sources/<subfolder>/<YYYY-MM-DD> <title>.md` — preserve original content unchanged.
   - If the source is a URL, use WebFetch to retrieve content, then save as above.

2. **Pick the right `raw-sources/` subfolder**:
   - `articles/` — web articles, blog posts, news
   - `papers/` — academic papers, arXiv, research reports
   - `chats/` — Claude/ChatGPT/Cursor research conversations
   - `notes/` — hand-written notes, voice memo transcripts, scratch thoughts

3. **Delegate to the `vault-scribe` subagent** via the Agent tool. The prompt must be self-contained and must include:
   - Absolute path to the raw source file
   - Explicit instruction to: read the source in full, write a summary to `wiki/syntheses/`, update/create entity pages in `wiki/entities/`, update/create concept pages in `wiki/concepts/`, update `wiki/index.md`, append to `wiki/log.md`, flag any contradictions with existing wiki content

4. **Report back to the user**: list of files created/updated (from the scribe's output), any contradictions flagged, any entities/concepts that look like they need the user's review.

## Notes

- The raw source is immutable after it's saved. The scribe only writes to `wiki/`.
- If the source is already in `raw-sources/`, skip step 1.
- For multiple sources in one session: call `/ingest` once per source. The scribe does the per-source work; you coordinate sequencing.
- If the user drops a source directly into `raw-sources/` without calling `/ingest`, you can still run this skill — just point the scribe at the new file.
