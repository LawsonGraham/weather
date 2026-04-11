#!/usr/bin/env python3
"""PostToolUse hook for Bash(git commit) — nudge toward vault-capture.

Fires after a successful git commit. If the commit touched `scripts/` (new
or modified data-source code) but touched nothing under `vault/`, print a
reminder to capture the knowledge into the wiki. Stdlib-only.

Non-blocking: always exits 0. The purpose is to surface a reminder in the
Claude session context, not to fail the commit.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]


def last_commit_files() -> list[str]:
    try:
        result = subprocess.run(
            ["git", "-C", str(REPO), "diff-tree", "--no-commit-id", "--name-only", "-r", "HEAD"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode != 0:
            return []
        return [line.strip() for line in result.stdout.splitlines() if line.strip()]
    except (subprocess.SubprocessError, OSError):
        return []


def main() -> int:
    try:
        files = last_commit_files()
        if not files:
            return 0

        touched_scripts = any(f.startswith("scripts/") for f in files)
        touched_vault = any(f.startswith("vault/") for f in files)
        touched_skills = any(f.startswith(".claude/skills/") for f in files)
        touched_claude_md = "CLAUDE.md" in files

        # Heuristic: if the commit was about scripts/ but nothing under vault/
        # and nothing under .claude/ or CLAUDE.md (which would indicate
        # meta-work rather than new data-source work), nudge toward capture.
        if touched_scripts and not touched_vault and not touched_skills and not touched_claude_md:
            script_files = [f for f in files if f.startswith("scripts/")]
            print("━━━━ VAULT CAPTURE REMINDER (post-commit hook) ━━━━")
            print("  This commit touched scripts/ but nothing under vault/.")
            print("  If the commit produced durable knowledge (new data source,")
            print("  schema gotcha, architectural decision, failure + lesson,")
            print("  phase milestone), invoke the vault-capture skill to record it.")
            print()
            print("  Files in this commit that touched scripts/:")
            for f in script_files[:8]:
                print(f"    {f}")
            if len(script_files) > 8:
                print(f"    ... and {len(script_files) - 8} more")
            print()
            print("  See: CLAUDE.md Rule 3 · .claude/skills/vault-capture/SKILL.md")
            print("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
        return 0
    except Exception as e:
        print(f"[vault-capture-reminder hook error] {e}", file=sys.stderr)
        return 0


if __name__ == "__main__":
    sys.exit(main())
