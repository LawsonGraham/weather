---
name: minimal-docs
description: >
  Enforce minimal-documentation / clean-code-first style for this repo. Do NOT write
  README.md files in subdirectories. Do NOT write "how to use this module" markdown.
  Do NOT duplicate content across multiple docs. Code should be self-documenting
  via good names; use comments only when the WHY is non-obvious; docstrings on public
  entry points only. Invoke when writing new code, reviewing code, considering
  adding a README, or considering adding a docstring / comment / architecture doc.
---

# Minimal docs — clean code first, targeted comments where informative

Documentation sprawl is explicitly undesired in this repo. Every new subdirectory does **not** get a README. Every new script does **not** get a multi-paragraph docstring. Every new function does **not** get a comment restating what its body does.

## The rule

**No README.md files in subdirectories.** The only markdown docs that should exist:

- `README.md` at repo root — GitHub entry point, minimal
- `CLAUDE.md` at repo root — project memory and operational rules for Claude Code
- `.claude/skills/<name>/SKILL.md` — skill definitions (operational instructions)
- `.claude/agents/<name>.md` — subagent definitions
- `vault/Weather Vault/…` — Obsidian project knowledge base (wiki pages, raw sources, syntheses)

Everything else is pressure toward deletion.

## Code as documentation

- **Module docstring** — single-paragraph top-of-file *only when* the module's purpose isn't obvious from its name. Skip when the filename is self-evident.
- **Function / class docstrings** — only on public entry points (CLI `main`, public API functions, classes imported by other modules). Skip private helpers unless the intent would surprise a reader.
- **Inline comments** — only when the **WHY** is non-obvious. Never explain **WHAT** the code does — identifiers do that.

Good vs bad:

```python
# NO — explains what, not why
counter += 1  # increment counter
if path.exists():  # check if file exists

# YES — explains non-obvious why
counter += 1  # HRRR file naming is 1-indexed for f01..f48, not 0
if path.exists():  # legacy bash run may have left this behind; guard rerun
```

- **CLI scripts** — the script file itself is the doc. Top docstring covers: what it does, upstream source, output path, key flags. Anything beyond that is scope creep.

## For data-source scripts specifically

See the `data-script` skill for the full contract. A new data source is:

1. Copy `.claude/skills/data-script/template.py` to `scripts/<download|transform>/<source>/script.py`
2. Write a 4–6 line top docstring: what it does, upstream URL, output path, key flags
3. Fill in source-specific logic
4. Done. No `<source>/README.md`. No separate architecture doc. Nothing.

Usage question? → `uv run python scripts/download/<source>/script.py --help` is the answer.

## Anti-patterns

- A README that enumerates files in its directory → `ls` exists
- A README that duplicates content from CLAUDE.md → link to CLAUDE.md instead
- A README as "notes to self" → those go in the vault, not the code tree
- A docstring that restates the function name → delete
- A comment that explains trivial control flow → delete
- A `CHANGELOG.md` → `git log` exists
- A `CONTRIBUTING.md` → solo project
- "Architecture overview" / "How to use X" markdown files

## When new docs are genuinely warranted

Rare. Real cases:

- Subtle invariant that would cause bugs if forgotten → module-level docstring with a specific example
- Workaround for a known upstream bug → inline comment with the issue URL
- Non-obvious performance constraint → inline comment
- Project rules that apply across many files → `CLAUDE.md`
- Reusable workflow pattern → `.claude/skills/<name>/SKILL.md`
- Durable project knowledge / research findings → `vault/Weather Vault/wiki/…`

If the answer isn't one of those, don't write the doc.

## Cleanup posture (Rule 6 alignment)

When this skill lands, existing subdirectory READMEs get deleted. Their content either migrates to CLAUDE.md, a skill, or a vault page — or it's already redundant and gets dropped. Per `CLAUDE.md` Rule 6, cleanup happens in the same commit that lands the rule.
