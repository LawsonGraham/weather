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

## Option B — Manual worktree with `data/` symlinked to main (longer-lived)

For multi-step work that spans several commits, or when you want to keep the worktree around for iteration. **Every manual worktree's `data/` MUST be a symlink into main's `data/`** so downloaded files land in the canonical place and are never lost when the worktree is removed.

### Canonical creation

```sh
# from the main checkout:
BRANCH=wt/iem-asos-download
WT_PATH=../weather-wt/iem-asos-download

git worktree add "$WT_PATH" -b "$BRANCH"
mkdir -p data                                     # ensure main's data/ exists
ln -sfn "$(pwd)/data" "$WT_PATH/data"             # wt/data → main/data

# now the worktree is ready — every download script that computes
# REPO_ROOT / "data" will write to main's data/ via the symlink
```

**Why the symlink:** scripts compute `REPO_ROOT = Path(__file__).resolve().parents[3]` and then `RAW_DIR = REPO_ROOT / "data" / "raw" / SOURCE_NAME`. In a worktree, `REPO_ROOT` resolves to the worktree path. Without a symlink, downloads would land in `../weather-wt/<name>/data/raw/<source>/` — duplicated per worktree and lost when the worktree is removed. With the symlink, the `data/` segment of the path resolves transparently to main's `data/`, so every download lands in the canonical location regardless of which worktree ran the script.

**No script changes needed.** Python's file I/O follows symlinks by default, and the data-script template doesn't need to know whether it's running in a worktree or the main checkout.

### Working in the worktree

```sh
cd "$WT_PATH"
# make edits, run scripts, commit as usual
uv run python scripts/download/iem_asos_1min/script.py --stations NYC LGA
# ^ this writes to main's data/raw/iem_asos_1min/ via the symlink

git add scripts/ CLAUDE.md
git commit -m "..."
# commits stay on wt/iem-asos-download; master is untouched
```

### Closing out — merge + cleanup

```sh
# from the main checkout (cd back if you were in the worktree):
cd /path/to/main/checkout

git merge --ff-only wt/iem-asos-download          # bring branch commits to master
git worktree remove ../weather-wt/iem-asos-download   # removes the symlink; main/data untouched
git branch -d wt/iem-asos-download                # delete the merged branch
```

**No data porting step.** Data was always in main's `data/` the whole time. `git worktree remove` unlinks the symlink (it's a symlink, not the target), so main's data is safe.

### Verification

```sh
git worktree list                             # confirm worktree gone
git log --oneline -3                          # confirm branch commits landed on master
ls data/raw/<source>/MANIFEST.json            # confirm data is in main
```

**Conventions:**
- Branch names: `wt/<purpose>` (e.g. `wt/iem-asos-download`, `wt/calib-weather-markets`)
- Worktree paths: `../weather-wt/<name>` — sibling to main repo, outside its tree
- One worktree per task. Don't reuse. If you need to return to a closed task, create a new worktree from master.
- `data/` in every worktree is a symlink to main's `data/`. No exceptions.

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
- **`data/` in every manual worktree is a symlink to main's `data/`.** Set up at worktree creation (`ln -sfn "$(pwd)/data" "$WT_PATH/data"`). Scripts write through the symlink, so downloads always land in main. No duplication, no porting, no data loss on worktree removal.
- **Commit on the worktree's branch, never to master directly from inside the worktree.** Let the merge do that.
- **Merge back only when the worktree's work is fully done.** Prefer fast-forward (`git merge --ff-only wt/<name>`). If master has moved, rebase the branch onto master in the worktree, resolve conflicts there, then retry the fast-forward.
- **Never hold the main-repo lock for long operations** (downloads, training runs, anything over ~1 minute). The lock is for editing main-checkout files, not long-running execution.
- **Never leave the lock behind.** If you crash or error, the lock must still be released. Use `trap 'rm -f .main-repo-lock' EXIT` in shell scripts.
- **Lock file is gitignored.** Runtime state. Never commit it.
- **Cleanup worktrees when done.** Don't accumulate stale worktrees — `git worktree list` to inspect, `git worktree remove` to clean.
- **Trivial carveout:** single-file edits under ~20 lines, typo fixes, and documentation corrections can skip the worktree *if* `.main-repo-lock` is not held.

## Anti-patterns

- **`rm -rf <wt>/data/*` or `rm -rf <wt>/data/raw/`** — glob-delete inside a symlinked directory follows the symlink and destroys files in main's `data/`. **Only `git worktree remove <wt>` is the safe cleanup**; it removes the symlink as a link-file without following it.
- **Creating a manual worktree without the data symlink.** The worktree will write downloads to its own `data/`, duplicating everything on disk and losing it on cleanup. The 3-line creation recipe is mandatory for all manual worktrees.
- **Holding the main-repo lock for 30+ minutes** while a download or training job runs. The lock is for edit coordination, not long-running execution.
- **Forgetting to remove the worktree after merging.** `git worktree list` regularly; remove anything stale.
- **Treating the lock as a real mutex.** It's advisory coordination between cooperating Claude sessions, not a security primitive. Assume cooperation, not enforcement.
- **Making a worktree for every single edit.** Use the trivial carveout — not every typo fix needs a new branch.
- **Leaving stale locks** from crashed sessions. If you see a lock with an old `acquired_at` (> 1 hour) and no obvious owner, check with the user before removing.
- **Committing to master from inside a worktree** via `git checkout master` + cherry-pick. Let the merge step do that; don't interleave master commits with worktree work.

## Migrating a pre-existing worktree (one without the data symlink)

If a worktree was created before the symlink rule and already has data under its own `data/` tree:

```sh
# from the main checkout:
WT_PATH=../weather-wt/<name>
MAIN_DATA="$(pwd)/data"

# 1. Port any downloaded files from the worktree's data/ into main's data/
#    (no overwrites — if a filename collision exists, resolve it manually first)
rsync -av --ignore-existing "$WT_PATH/data/" "$MAIN_DATA/"

# 2. Replace the worktree's data/ directory with a symlink to main
rm -rf "$WT_PATH/data"
ln -sfn "$MAIN_DATA" "$WT_PATH/data"

# 3. Confirm no tracked files were affected (data/ is gitignored so there shouldn't be)
git -C "$WT_PATH" status
```

One-time migration per legacy worktree. Once migrated, the standard commit / merge / cleanup flow applies.
