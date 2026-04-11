# Raw sources

Immutable source material for the knowledge base. Claude **reads** from here but never writes. The synthesis layer lives in `../wiki/`.

## Subfolders

- `articles/` — web articles, blog posts, news clippings (use Obsidian Web Clipper or paste content manually)
- `papers/` — academic papers, arXiv PDFs, research reports
- `chats/` — Claude / ChatGPT / Cursor research conversations saved as markdown
- `notes/` — hand-written notes, voice memo transcripts, scratch thoughts

## Naming convention

`<YYYY-MM-DD> <descriptive title>.md`

For example:
- `2026-04-08 Scoping — Airport Weather Prediction Markets.md`
- `2025-11-14 HRRRv4 operational release announcement.md`

## Workflow

1. Drop a source into the appropriate subfolder (or have Claude save it there via `/ingest`).
2. Run `/ingest <path>` to have the `vault-scribe` subagent read it and integrate into the wiki.
3. Never edit sources in this directory after they're filed — they're the immutable ground truth of what was said where.

If you notice a transcription error or want to annotate a source, do it in a wiki page that links back, not by editing the raw file.
