#!/usr/bin/env python3
"""SessionStart hook — report vault-repo alignment gap.

Prints a compact summary of the vault state vs the scripts/ tree, injected
into the Claude Code session context at start. Purpose: create structural
pressure to invoke the vault-capture skill when the vault is behind the
repo. Stdlib-only so it runs without the project venv.
"""

from __future__ import annotations

import sys
from pathlib import Path

# .claude/hooks/vault_health.py  →  parents[2] = repo root
REPO = Path(__file__).resolve().parents[2]
WIKI = REPO / "vault" / "Weather Vault" / "wiki"
SCRIPTS = REPO / "scripts"


def count_md(d: Path) -> int:
    if not d.exists():
        return 0
    return sum(
        1
        for p in d.iterdir()
        if p.is_file() and p.suffix == ".md" and not p.name.startswith("README")
    )


def script_sources() -> list[str]:
    if not SCRIPTS.exists():
        return []
    return sorted(
        d.name
        for d in SCRIPTS.iterdir()
        if d.is_dir() and not d.name.startswith(".") and not d.name.startswith("_")
    )


def last_log_entry() -> str:
    log = WIKI / "log.md"
    if not log.exists():
        return ""
    for line in reversed(log.read_text().splitlines()):
        if line.startswith("## ["):
            return line.lstrip("# ").strip()
    return ""


def main() -> int:
    try:
        entities = count_md(WIKI / "entities")
        concepts = count_md(WIKI / "concepts")
        syntheses = count_md(WIKI / "syntheses")
        sources = script_sources()
        last = last_log_entry()

        print("━━━━ VAULT HEALTH (SessionStart hook) ━━━━")
        print(f"  wiki:     {entities} entities · {concepts} concepts · {syntheses} syntheses")
        src_list = ", ".join(sources) if sources else "none"
        print(f"  scripts/: {len(sources)} source{'s' if len(sources) != 1 else ''} ({src_list})")
        if last:
            print(f"  last log: {last}")

        gap = max(0, len(sources) - entities)
        if gap > 0:
            suffix = "s" if gap != 1 else ""
            print(f"  ⚠ {gap} source{suffix} without a wiki/entities/ page")
            print("  → invoke the vault-capture skill to close the gap as you touch code")
            print("    (CLAUDE.md Rule 3 + .claude/skills/vault-capture/SKILL.md)")
        else:
            print("  ✓ vault source count matches scripts/")
        print("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
        return 0
    except Exception as e:
        # Never block the session on a hook error
        print(f"[vault-health hook error] {e}", file=sys.stderr)
        return 0


if __name__ == "__main__":
    sys.exit(main())
