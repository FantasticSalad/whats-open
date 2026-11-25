# The config file

`config.md` in `$WHATS_OPEN_HOME`, or in `~/.whats-open/` when that isn't set. Under each
heading, a line starting `- ` is a value, and ` # ` starts a comment. Only Roots is required.

```markdown
## Roots
- ~/code

## Skip
- scratch  # a folder name under a root

## First
- infra  # listed before the rest, which go A to Z

## Worktrees
- ~/code/.worktrees  # flags any worktree outside this folder

## Prefixes
- feature/: feature work
- bot/: automated change, waits for review

## Review
- bot/  # gets check results, and is never called ready to merge

## Local only
- lab-tools: branches stay on this machine on purpose

## Bundles
- lab-tools: ~/backups/lab-tools-*.bundle  # the newest match is checked

## Kept
- archive/*: old history, kept by decision  # any repo
- infra:release-1.x: maintained on its own  # only in the repo infra

## Paused work
- ~/notes/paused

## Sync folders
- ~/Dropbox/shared-notes

## Machines
- LAPTOP-1234  # names a sync client puts in conflict copies; this machine's is added

## Notes
- Approvals for bot/ branches are in the team tracker.
```

- **Kept** lines are a branch name or a glob (`*` matches anything, `/` included), then `: ` and
  the reason.
  - `repo:pattern` limits a line to one repo. Put no space after that colon: `infra: release`
    would mean a branch called `infra`, kept for the reason "release". Branch names can't contain
    a colon, so `repo:pattern` is never ambiguous.
  - The repo part ignores case; the branch part doesn't. A reason can't contain ` # `, which
    starts a comment.
  - A kept branch on origin, or local and merged already, is listed once, under KEPT, with its
    reason. It isn't counted as unmerged, its pull requests aren't listed, and it isn't offered
    for deletion.
  - In a repo with no clone here, which the GitHub pass finds, a kept branch and its pull
    requests are simply left out. They aren't compared either, which saves API calls. The repo
    part there is GitHub's name for the repo, which can differ from the folder name of a clone
    elsewhere, so a line for each name may be needed.
- **Notes** are printed at the end of each report, for you and for the agent.
