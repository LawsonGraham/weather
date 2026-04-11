---
name: vault-seed
description: "Seed context from the Obsidian vault before any project-related work. Use PROACTIVELY before planning, designing, researching, coding, answering questions about data sources, model design, trading strategy, architecture, scoping, or competitors. Reads Project Scope.md, wiki/index.md, and relevant entity/concept pages. The vault is the project memory — this skill is how Claude remembers across sessions."
allowed-tools: Read, Glob, Grep
---

# Vault seed — load context from the vault

Before answering any non-trivial project question, pull relevant context from the Obsidian vault at `vault/Weather Vault/`. The vault is the project's persistent memory; this skill is how you stay coherent across sessions.

## Workflow

1. **Read `vault/Weather Vault/Project Scope.md`** if not already in context. This is the canonical scoping doc.

2. **Read `vault/Weather Vault/wiki/index.md`**. This is the catalog of what the wiki knows.

3. **Identify relevant wiki pages** by cross-referencing the user's task against the index. Look for:
   - Entity pages in `wiki/entities/` — airports, markets, providers, competitors, models
   - Concept pages in `wiki/concepts/` — HRRR, MOS, calibration, Kelly sizing, ensemble spread, etc.
   - Syntheses in `wiki/syntheses/` — per-source summaries and cross-source analyses

4. **Grep aggressively** if the index doesn't surface something obvious. Use the Grep tool against `vault/Weather Vault/wiki/` for keywords from the user's task.

5. **Read the top 3–5 most relevant pages** directly. Be selective — don't read the entire wiki.

6. **Summarize what you learned** as a short context dump before answering the user's actual question:
   ```
   (from vault)
   - <1-line point from a wiki page, with source>
   - <another 1-line point>
   ```

7. **Answer the user's question** using the loaded context. Cite specific pages with `[[wikilinks]]` where relevant.

## When to invoke proactively

- Planning anything ("let's plan X", "how should we design Y")
- Data source questions ("where do we get X data", "what format is X")
- Model or training questions ("what's our eval strategy", "which split")
- Architecture questions ("how should module X look")
- Competitive questions ("who else is doing this")
- Anything starting with "what do we know about..."
- Before invoking `/pipeline` on a non-trivial task (the architect will do it anyway, but seeding the main context helps)

## When NOT to invoke

- Simple mechanical tasks (format a file, rename a var)
- Questions about the tools or environment itself, not the project
- When the user is explicitly telling you to IGNORE the vault

## If the vault has nothing

Say so explicitly: "vault has no prior context on this." Don't fabricate entries. Offer to run `/ingest` if the user wants to file what they're about to discuss.
