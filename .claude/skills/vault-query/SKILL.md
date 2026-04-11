---
name: vault-query
description: "Query the Obsidian wiki for what we know about a topic. Use /ask <question> or 'what does the vault say about X'. Returns a synthesis with citations to wiki pages and raw sources. Optionally promotes the synthesis back into the wiki as a new page with user approval."
allowed-tools: Read, Glob, Grep, Write
---

# Vault query — ask the knowledge base

## Workflow

1. **Read `vault/Weather Vault/wiki/index.md`** to see what pages exist.

2. **Grep across the wiki** for the query terms using the Grep tool against `vault/Weather Vault/wiki/`. Try multiple keyword variations if the first doesn't hit.

3. **Read the matching pages** in full. Prefer wiki pages over raw sources — the wiki is the synthesis layer. Only drop to `raw-sources/` if the wiki doesn't have the answer directly.

4. **Synthesize an answer with citations**:
   ```
   Answer: <your synthesis>

   Sources:
   - [[wiki/entities/KLAX]] — <specific fact used>
   - [[wiki/concepts/HRRR]] — <specific fact used>
   - [[raw-sources/chats/2026-04-08 Scoping...]] — <specific fact used>
   ```

5. **Offer to promote.** If the synthesis is genuinely new (cross-links or insights that don't already exist as a wiki page), ask the user whether to save it as a new page in `wiki/syntheses/`. Do not auto-promote — synthesis quality needs user judgment.

## If the wiki has no answer

Say so clearly: "vault has nothing on this." Optionally suggest a research direction (WebSearch, a specific source to ingest) and ask whether to proceed.

## Constraints

- Never fabricate. If you didn't find a fact in the vault, don't claim it came from there.
- Prefer specific citations over hand-waving summaries.
- If the wiki disagrees with itself, surface both sides and the contradiction — don't silently pick one. Suggest running `/lint` to audit the broader wiki.
- Stay short. A synthesized answer of 3–5 paragraphs with crisp citations beats a 20-paragraph essay.
