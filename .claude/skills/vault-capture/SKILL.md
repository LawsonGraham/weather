---
name: vault-capture
description: >
  Capture project-internal knowledge into the Obsidian wiki as it's produced.
  Use PROACTIVELY and IMMEDIATELY after: adding a new data source (new
  scripts/<source>/ folder), discovering a schema gotcha or API quirk, making
  an architectural decision, learning from a failure, or completing a phase
  milestone. Writes structured entity / concept / synthesis pages under
  wiki/, updates wiki/index.md, and appends to wiki/log.md. Complements
  vault-ingest (which handles raw-source files dropped into raw-sources/) —
  this skill handles project-internal knowledge that emerges from the work
  itself. Invoke whenever the session has produced durable knowledge that
  should survive into future sessions.
allowed-tools: Read, Glob, Grep, Write, Edit
---

# Vault capture — write project knowledge back to the wiki as you produce it

The vault is the project's persistent memory. Every session reads from it (via `vault-seed`). Every session should also **write** to it when it produces durable knowledge. Without this discipline, the vault atrophies while the repo grows — a knowledge gap that compounds and makes every future session start cold.

This skill is complementary to `vault-ingest` (which handles external raw-source files the user drops into `raw-sources/`). **`vault-capture` is for project-internal knowledge that emerges from doing the work** — knowledge that lives nowhere else until we write it down.

## When to invoke

Invoke this skill **immediately**, in the same session, after any of these:

- **A new data source landed.** A new `scripts/<source>/` folder means there's a new upstream, a new set of fields, a new set of access patterns. Create or update `wiki/entities/<Source>.md`, any referenced provider pages (`wiki/entities/<Provider>.md`), and any concept pages for novel methods used.
- **A schema gotcha or API quirk discovered.** Undocumented fields, always-null columns, rate-limit details, pagination surprises, auth flows that aren't in the upstream docs → add a synthesis page OR update an existing entity/concept page with a "gotchas" section.
- **An architectural decision made.** Choice of library, approach, repo to fork, contract boundary, threshold value → synthesis page `wiki/syntheses/<YYYY-MM-DD> <decision>.md` with the reasoning + alternatives considered + impact.
- **A failure or lesson learned.** Something broke in a non-obvious way → capture the root cause in a concept or entity page so future sessions don't repeat the mistake.
- **A phase / milestone completed.** Big refactor, data source fully wired, notebook producing useful analysis → append an entry to `wiki/log.md` and, if the scope justifies, a synthesis summarizing what shipped.
- **You noticed the vault is missing obvious content the repo already has.** Opportunistic backfill. If a data source has been in `scripts/` for weeks but has no entity page, add one when you're touching that code anyway.

## Where to write — vault layout reminder

```
vault/Weather Vault/
├── Project Scope.md                       # top-level, canonical scoping (do not edit casually)
├── Execution Stack — Source Review.md     # top-level, execution-stack decision (do not edit casually)
└── wiki/
    ├── index.md                           # catalog — UPDATE on every page add
    ├── log.md                             # chronological — APPEND on every write
    ├── entities/<Name>.md                 # named things: airports, providers, markets, models, people
    ├── concepts/<Name>.md                 # ideas and methods
    └── syntheses/<YYYY-MM-DD> <topic>.md  # cross-source analyses, decision records, lessons learned
```

## What goes where

| Knowledge type | Page type | Examples |
|---|---|---|
| A station / airport / data provider / API / repo / market venue / resolving body | **entity** | `entities/KNYC.md`, `entities/Polymarket.md`, `entities/IEM.md`, `entities/Kalshi.md`, `entities/Goldsky subgraph.md` |
| A method, concept, or technique | **concept** | `concepts/HRRRx ensemble.md`, `concepts/Kelly sizing.md`, `concepts/MOS bias correction.md` |
| A dated analysis, decision, or gotcha pulling from multiple entities/concepts | **synthesis** | `syntheses/2026-04-11 Polymarket schema corrections.md` |
| A specific script or module in `scripts/` | **nothing — don't document code in the vault.** The script's docstring + `--help` + `.claude/skills/data-script/SKILL.md` cover the code. The vault is for *domain knowledge*, not code documentation. |

## Page template — every vault page starts with this

```markdown
---
tags: [entity | concept | synthesis, <topic-tags>]
date: YYYY-MM-DD
related: "[[Other Page]], [[Another Page]]"
---

# <Page title>

<1-3 sentence definition or summary — what this is, briefly.>

## <Section>

Concrete facts, bullets where possible. Each claim attributed when the
source is non-obvious — cite commit SHAs (`7254c73`), file paths
(`scripts/polymarket_weather/download.py:45`), URLs (upstream docs), or
other wiki pages.

## <Another section>

...

## Related

- [[Entity or concept page]] — one-line why it's related
- [[Synthesis page]] — one-line why it's related
```

## Mandatory updates on every new page

Every time you add a page under `wiki/entities/`, `wiki/concepts/`, or `wiki/syntheses/`:

1. **Update `wiki/index.md`** — add a link under the right section with a one-line description. Alphabetical within each section.
2. **Append to `wiki/log.md`** — one entry in this format (grep-able):
   ```
   ## [YYYY-MM-DD] capture | <page title>
   - Page: wiki/<type>/<filename>.md
   - Trigger: <new data source | decision | gotcha | failure | milestone | backfill>
   - Related: [[link]], [[link]]
   ```
3. **Wikilink aggressively.** The value of the wiki is the graph. Every page should link to at least 2–3 other pages. If you're creating a page that links to nothing, you probably haven't found the related entities/concepts yet — search for them first.

## Workflow — a single capture

1. **Identify the knowledge.** What specific fact, decision, or discovery did the work produce? Be concrete — "we learned X" not "we did some stuff."
2. **Pick the page type.** Entity (a thing), concept (an idea), synthesis (a dated cross-source analysis or decision record).
3. **Search first.** `Grep` the wiki for the topic. If a page already exists, **update it** instead of creating a duplicate.
4. **Write the page** using the template. Keep it under ~200 lines.
5. **Update `wiki/index.md`.**
6. **Append to `wiki/log.md`.**
7. **Verify wikilinks resolve.** If you linked `[[Foo]]` but `wiki/entities/Foo.md` doesn't exist, either create it or remove the link.

## Content rules

- **Facts over prose.** A vault page is a fact sheet with attribution, not a README-style how-to. If you're tempted to explain *how to use* something, you're writing the wrong kind of doc — that belongs in a SKILL.md or code docstring.
- **Cite sources.** Commit SHAs, file paths, URLs, upstream docs, or other wiki pages. "I remember from earlier" is not a source.
- **Short pages.** Target < 200 lines. If a page grows bigger, split into focused sub-pages and cross-link.
- **No speculation.** If you don't have a source for a claim, don't write it. "As far as I know" has no place in the vault.
- **UTC dates** in frontmatter and log entries. `date: 2026-04-11`.
- **Wikilinks aggressively.** `[[Entity]]` format, matching the filename exactly (without the `.md` suffix).

## Anti-patterns

- **Forgetting to update `wiki/index.md`.** A page that isn't in the index is effectively orphaned. `vault-lint` will flag it.
- **Forgetting to append to `wiki/log.md`.** The log is how sessions reconstruct what happened recently.
- **Writing prose without wikilinks.** Flat pages waste the graph.
- **Duplicating an existing page** because you didn't grep first. Always check before creating.
- **Treating the vault as a code-documentation surface.** Code lives in `scripts/`; code docs live in docstrings and SKILL.md files. The vault is for *domain knowledge*.
- **Waiting for a "big enough" update.** Small captures compound. A 5-line entity page beats a 500-line synthesis that never gets written.
- **Writing a README in a new format.** Vault pages have a different shape from READMEs. If you find yourself listing "how to install" or "how to run", stop and put that in a SKILL.md instead. See the [`minimal-docs`](../minimal-docs/SKILL.md) skill.

## Current backlog (opportunistic backfill)

At skill creation (2026-04-11), the repo has multiple active data sources (`scripts/iem_asos_1min/`, `scripts/polymarket_weather/`, `scripts/polymarket_weather_slugs/`, plus at least one transform) but `wiki/entities/` and `wiki/concepts/` are empty. The only synthesis is `2026-04-11 Polymarket schema corrections.md`.

There is an obvious backlog of entity and concept pages to create, for example:

- **Entities to write eventually**: `IEM`, `Polymarket`, `Kalshi`, `KNYC`, `KLGA`, `KJFK`, `HRRR` (as a system), `NOAA`, `Gamma API`, `Goldsky subgraph`, `NegRisk CTF Exchange`, `Central Park NWS CLI product`
- **Concepts to write eventually**: `HRRRx ensemble`, `ASOS 1-minute`, `METAR`, `MOS bias correction`, `TAF`, `calibration`, `Brier score`, `Kelly sizing`, `ensemble spread`, `fill model`, `slippage`, `bid-ask spread`, `NegRisk vs base CTF`

**Don't backfill all at once — that's batch thinking.** Instead: when you touch a source or concept during normal work, create its vault page in the same session. Over time the backlog empties naturally, with pages that actually reflect what was learned while doing the work.

## Relationship to other skills

- `vault-seed` — reads the vault at the start of work. Complementary read-side skill.
- `vault-query` — asks specific questions of the vault. Read-side.
- `vault-ingest` — ingests raw-source files dropped into `raw-sources/`. Different trigger: external files, not project-internal knowledge.
- `vault-scribe` (subagent) — the heavy-lifting ingest worker behind `vault-ingest`. You can also delegate large capture work to the `vault-scribe` subagent if the scope is big, but for small captures (a single entity or concept page), write directly.
- `vault-lint` — health-checks the vault for orphans, contradictions, missing pages. Periodically catches what `vault-capture` missed.
- `minimal-docs` — the "no README, no documentation sprawl" rule. The vault is the one place where structured long-form knowledge is welcome — but only in the specific shapes above (entity / concept / synthesis), never as README-style how-to prose.
