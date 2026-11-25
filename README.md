# What's Open

A Claude Code plugin that answers "what's open?" across all your repos in one read-only report:
what isn't committed, what exists only on this machine, what isn't merged, and what needs
tidying. Ask in plain words and Claude runs one script and shows you the list, with the few
things that need you first.

It's for anyone with more than a handful of repos, and especially for people whose AI sessions
leave branches and worktrees behind.

## What it reports

| Section | What's in it |
|---|---|
| Uncommitted | Edits in each repo's checkout and in every worktree |
| Unpushed | Commits no remote has: the default branch ahead of origin, local and worktree branches with commits on no origin branch, stashes, and repos with no remote. For repos whose branches stay local on purpose, whether the newest bundle backup holds them |
| Unmerged | Branches on origin not in the default branch, with the date, what the prefix means and the last commit's subject. With `gh`: open pull requests, and check results for branches that wait for review |
| Not cloned here | With `gh`: repos on GitHub, under the owners of repos you have cloned, with no clone under your roots, and their open work. Catches work only another machine has a clone of |
| Paused work | Notes in a folder you name, one file per piece of paused work, listed by title |
| Tidy-up | Branches merged but still on origin, local branches merged already, worktrees gone from disk or outside your worktree folder, worktrees with nothing left in them, a checkout behind origin or off its default branch, and sync-conflict copies (OneDrive, Dropbox, Syncthing) in folders you name |
| Kept | Branches you keep on origin on purpose, such as archives or release lines, each listed once with your reason, and not counted above |

## What it never does

It never commits, pushes, merges, deletes a branch, prunes a worktree, stashes or edits anything.

The one thing it changes is each repo's record of its remote, through
`git fetch --prune`. That also drops its record of branches already deleted on origin. Git's
automatic maintenance is switched off for that fetch, and so is fetching submodules.
`--no-fetch` skips the fetch.

It reads no file contents apart from the config, and each paused-work note as far as its first
heading. It sends nothing anywhere except `git fetch` to each repo's own remote and, if you use
`gh`, read-only GitHub API calls under your own login. If you ask it to tidy something, Claude
gives you the command and waits for your go-ahead.

## Requirements

- Claude Code with plugin support. Tested with 2.1.316.
- git 2.31 or newer.
- Python 3.9 or newer, through `uv` or `python3` (`py -3` on Windows). Standard library only.
- Optional: the GitHub CLI, logged in (`gh auth login`), for pull requests, checks and repos with
  no clone here.

## Install

    /plugin marketplace add FantasticSalad/whats-open
    /plugin install whats-open@whats-open

## Set up

Ask "what's open?". With no config, Claude asks which folders hold your repos and offers the
optional parts, then writes `~/.whats-open/config.md` (`$WHATS_OPEN_HOME` moves it). The format,
with every section, is in
[skills/whats-open/config-format.md](skills/whats-open/config-format.md). Only the list of folders
is required.

## Use

- "What's open?"
- "What haven't I pushed?"
- "Is everything backed up before I wipe this laptop?"
- "What's unmerged across my repos?"

The script can also run on its own:

    uv run --no-project python skills/whats-open/scripts/whats_open.py [--no-fetch] [--offline] [--config FILE] [--root DIR]... [--paths]

`--root` scans a folder without a config, `--paths` says where the config is looked for, and
`--offline` skips both the fetch and GitHub.

## Cost

One script run, usually 10 to 20 seconds with the fetch, and a short reply. The script makes no
model calls. With `gh`, it makes one API call per repo for pull requests, one per review branch
for its checks, and a few more for repos with no clone here.

## Limits

- It sees this machine only. Another machine's uncommitted or unpushed work isn't visible until
  it's pushed.
- It can't see reviews or approvals, so it never calls a branch ready to merge.

## Security

[SECURITY.md](SECURITY.md) covers what it defends against, its controls, what they can't stop,
and how to report a problem.

## Development

    bash tests/run-all.sh

The tests build throwaway repos and need git and pytest; the runner uses a `.venv`, `uv` or the
Python on your path. CI runs them on Linux, macOS and Windows. `tests/public-scan.sh` checks that
nothing outside `docs/internal` carries workspace markers or secret-shaped values.
[docs/DESIGN.md](docs/DESIGN.md) explains why it's built this way.

## Licence

MIT. See [LICENSE](LICENSE).
