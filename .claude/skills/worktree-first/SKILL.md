---
name: worktree-first
description: >
  Standard workflow for working in a git worktree instead of the main checkout.
  Use BEFORE starting any non-trivial code change, experiment, pipeline run, or
  multi-file edit that might conflict with a parallel Claude session. Covers
  worktree creation, the .main-repo-lock file that coordinates main-checkout
  access between parallel sessions, and cleanup. Invoke whenever the user says
  "let's work on", "start building", "let me kick off", or at the start of any
  session that will make non-trivial changes.
allowed-tools: Bash, Read, Write
---

# Worktree-first workflow

Every Claude session that makes changes in this repo defaults to working in a **git worktree**, not the main checkout. This prevents thrashing when multiple parallel Claude sessions are active. The governing rule is [CLAUDE.md Rule 8](../../../CLAUDE.md).

## Quick decision tree

```
Starting work?
├── Is this a trivial edit (typo, single-line, doc tweak)?
│   ├── YES → Is .main-repo-lock present?
│   │        ├── YES → Use a worktree anyway (respect the lock)
│   │        └── NO  → OK to edit main checkout directly
│   └── NO  → Use a worktree (Option A or B below)
└── Need to edit main checkout specifically (merge coordination, etc.)?
    └── Acquire .main-repo-lock first. Release when done.
```

## Before any work — check the lock

```sh
if [ -f .main-repo-lock ]; then
  cat .main-repo-lock
  echo "main checkout is LOCKED → use a worktree instead"
else
  echo "unlocked"
fi
```

If the lock is present, do NOT touch the main checkout. Create a worktree instead.

## Option A — `Agent` tool with `isolation: "worktree"` (ephemeral)

For bounded subagent tasks, spawn an Agent with `isolation: "worktree"`. Claude Code creates a temporary worktree, the subagent works in it, and Claude Code cleans up automatically if no changes were made. If changes *were* made, the worktree path + branch are returned for review and merge.

**Use for:** focused research, experiments that may not produce code, parallel exploration that might fan out.

## Option B — Manual `git worktree add` (longer-lived)

For multi-step work that spans several commits, or when you want to keep the worktree around for iteration:

```sh
# from the main checkout:
BRANCH=wt/iem-asos-download
WT_PATH=../weather-wt/iem-asos-download

git worktree add "$WT_PATH" -b "$BRANCH"
cd "$WT_PATH"

# ... work happens here: edit, commit, test ...

# when done:
git push origin "$BRANCH"            # or merge back to main directly
cd -                                  # back to main checkout
git worktree remove "$WT_PATH"
git branch -d "$BRANCH"               # after merge
```

**Conventions:**
- Branch names: `wt/<purpose>` (e.g. `wt/iem-asos-download`, `wt/calib-weather-markets`)
- Worktree paths: `../weather-wt/<name>` — sibling to the main repo, outside its tree (no cross-contamination of ignored files, easier to nuke)
- One worktree per task. Don't reuse.

## When you MUST use the main checkout — the lock

Some operations genuinely need the main checkout: coordinating a merge, cleaning up global state, running something that depends on uncommitted main-repo state. In that case, acquire the lock first, do the work, release it.

### Acquire

```sh
cat > .main-repo-lock <<EOF
{"session": "<short-identifier>", "acquired_at": "$(date -u +%Y-%m-%dT%H:%M:%SZ)", "reason": "<one-line why>"}
EOF
```

Session identifier is a short human-readable string (e.g. `cc-weather-download-202604110530` or `cc-clean-merge`). The JSON is one line in practice — the heredoc is just for readability.

### Release

**ALWAYS release when done, even on error:**

```sh
rm -f .main-repo-lock
```

Wrap main-checkout work in a clear try/finally pattern (or bash `trap EXIT`) so the lock is released even if the work fails.

### Check the lock from another session

```sh
# with jq:
[ -f .main-repo-lock ] && jq . .main-repo-lock || echo "unlocked"

# without jq:
cat .main-repo-lock 2>/dev/null || echo "unlocked"
```

## Rules

- **Default is worktree.** Main checkout is the exception.
- **Never hold the lock for long operations** (downloads, training runs, anything over ~1 minute). The lock is for the *edit* of the main checkout, not the long op. Release before you sleep; reacquire after if needed.
- **Never leave the lock behind.** If you crash or error, the lock must still be released. Use `trap 'rm -f .main-repo-lock' EXIT` in shell scripts.
- **Lock file is gitignored.** It's runtime state, not a repo artifact. Never commit it.
- **Cleanup worktrees when done.** Don't accumulate stale worktrees — `git worktree list` shows them, `git worktree remove` cleans.
- **Trivial carveout:** single-file edits under ~20 lines, typo fixes, and documentation corrections can skip the worktree *if* the lock is not held. Anything bigger → worktree.

## Anti-patterns

- **Holding the lock for 30+ minutes** while a download or training job runs. The lock is for edit coordination, not long-running execution.
- **Forgetting to remove the worktree** after merging. `git worktree list` regularly; remove anything stale.
- **Treating the lock as a real mutex.** It's advisory coordination between cooperating Claude sessions, not a security primitive. A buggy or malicious agent can ignore it. Assume cooperation, not enforcement.
- **Making a worktree for every single edit.** Use the trivial carveout — not every typo fix needs a new branch.
- **Leaving stale locks** from crashed sessions. If you see a lock with an old `acquired_at` (> 1 hour) and no obvious owner, check with the user before removing. Don't unilaterally steal the lock.
