---
name: vault-lint
description: "Health check the Obsidian wiki for contradictions, orphan pages, missing cross-references, stale claims, and concepts that deserve their own page. Use /lint weekly or after large ingest batches. Parallel 4-way fan-out. Writes lint-report.md with specific actionable fixes. Does NOT auto-fix."
allowed-tools: Read, Glob, Grep, Bash, Write, Agent
---

# Vault lint — health check the wiki

## Workflow

1. **Enumerate wiki pages** with Glob: `vault/Weather Vault/wiki/**/*.md`

2. **Parallel 4-way fan-out** — spawn 4 general-purpose Agent subagents **in a single message**, each covering one dimension:

   a. **Contradictions** — find pages that make conflicting claims about the same entity or concept. Cross-reference claims across entities/concepts/syntheses. Return specific quotes and the conflicting pages.

   b. **Orphans** — find wiki pages that have no inbound `[[wikilinks]]` from any other wiki page. They either need links or should be considered for deletion.

   c. **Missing pages** — find concepts or entities mentioned repeatedly (3+ pages) across the wiki that don't have their own dedicated page. These are promotion candidates.

   d. **Stale claims** — find wiki pages that cite older raw sources where newer sources in `raw-sources/` might have superseded them.

3. **Merge outputs** into `vault/Weather Vault/wiki/lint-report.md` (overwrite if it exists). Format:

   ```markdown
   # Lint Report — <YYYY-MM-DD>

   ## Contradictions
   <bullet list with page links and the conflicting claims, quoted>

   ## Orphan pages
   <bullet list of pages with no inbound links>

   ## Missing pages (promotion candidates)
   <concepts/entities mentioned in N+ pages with no dedicated page>

   ## Possibly stale
   <pages whose sources are older than newer raw-sources that may supersede them>

   ## Suggested fixes
   <concrete, actionable items the user should decide on>
   ```

4. **Do NOT auto-fix.** This skill only reports. The user decides what to fix. Auto-fixing contradictions or deleting orphans without approval can destroy knowledge.

5. **Append to `log.md`** — one line summarizing the lint run:
   ```
   ## [YYYY-MM-DD] lint | <summary counts>
   ```

## Constraints

- Lint is read-only on wiki pages. It only writes `lint-report.md` and appends to `log.md`.
- Don't hallucinate contradictions — only flag real ones backed by specific quotes.
- Bound the report: ~20–30 items max per category. Prioritize the most important.
- If the wiki is very small (under ~10 pages), lint is probably premature — tell the user.
