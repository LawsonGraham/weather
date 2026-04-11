# Syntheses

Cross-source analyses — summaries of individual sources plus multi-source writeups promoted from `/ask` queries.

## Two kinds of synthesis pages

### 1. Per-source summaries

Automatic output of `/ingest`. One per source. Filename format:
`<YYYY-MM-DD> <source title>.md`

Frontmatter:
```yaml
---
tags: [synthesis, <topic tags>]
date: YYYY-MM-DD
source: [[raw-sources/<subfolder>/<filename>]]
related: [[...]]
---
```

Content: key claims from the source, entities touched, concepts touched, contradictions with existing wiki content.

### 2. Cross-source syntheses

Promoted from `/ask` queries when the user approves. These combine facts across multiple wiki pages and raw sources into a novel connection or insight worth keeping.

Filename: pick a descriptive title (e.g. `Why HRRR ensemble spread beats deterministic for binaries.md`).

Both kinds live in this same folder.

## Conventions

- Cite every claim. Link to specific wiki pages or raw sources.
- Keep pages under ~200 lines. Split if growing bigger.
- `vault-scribe` maintains per-source summaries automatically on ingest. Cross-source syntheses need user approval before writing.
