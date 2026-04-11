# Wiki Log

> Chronological append-only log of wiki activity. Ingests, queries, lint runs. Used by Claude to understand what's been done recently.
>
> Format: every entry starts with `## [YYYY-MM-DD] <op> | <title>` so it's grep-able:
>
> ```
> grep "^## \[" log.md | tail -5
> ```

---

## [2026-04-10] bootstrap | wiki scaffolding created

- Scaffolded `wiki/` and `raw-sources/` directories per Karpathy LLM Wiki pattern
- `Project Scope.md` already in place at vault root
- `Research Chats/` moved under `raw-sources/chats/`
- `.claude/` skills and agents configured (pipeline, vault-*, weather-data, model-training)
- Awaiting first ingest
