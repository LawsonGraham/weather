# Entities

Named things that matter to the project. Airports, markets, data providers, competitors, specific models.

## Conventions

- **Filename**: the entity's canonical name (e.g. `KLAX.md`, `Kalshi.md`, `Polymarket.md`, `HRRR Model.md`, `Tomorrow.io.md`).
- **Frontmatter**:
  ```yaml
  ---
  tags: [entity, <type>]
  type: airport | market | provider | competitor | model | person | org
  related: [[...]]
  ---
  ```
- Each page should have:
  - **One-line definition** at the top
  - **Key facts** (attributed to specific sources where possible)
  - **Sources that reference it** (inbound backlinks live in the graph, but call out the important ones explicitly)
  - **Related entities** with wikilinks

- Use `[[wikilinks]]` aggressively — the graph is the value, not the individual pages.
- `vault-scribe` maintains these automatically on ingest. Don't edit by hand unless fixing errors.

_(entity pages will appear here as sources are ingested)_
