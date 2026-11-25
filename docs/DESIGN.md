# Design

## One script, standard library only

The report is one Python script with no dependencies beyond the standard library, so it runs
wherever Python 3.9 and git do, with nothing to install. It fetches repos in parallel, then scans
them in parallel, and prints plain text that reads the same in a terminal or in a session.

## Four questions

The sections answer four questions, in order of risk:
1. **What isn't committed?** Lost if the folder goes.
2. **What exists only on this machine?** Lost if the machine goes.
3. **What isn't merged?** Waiting on a decision.
4. **What needs tidying?** Clutter, not risk.

Everything that needs you is counted on one line at the top, so a clean machine says so in one
sentence. That includes open work on GitHub in repos with no clone here, when GitHub is
available. Kept branches aren't counted, by design: you've already decided about them.

## Read-only, apart from the fetch

A report that changes things is a different tool. The only change is `git fetch`, which updates
each repo's record of its remote; without it the report would be stale. The fetch runs no
automatic maintenance and doesn't reach submodules. Optional index refreshes and git's own
password prompts are switched off through its environment.

## Bounded output

Each repo and section is capped at 40 lines, so one busy repo can't bury the rest, and the agent
is asked to lead with at most three lines on what needs the user.

## GitHub is optional

With `gh` logged in, the report adds pull requests, check results for branches that wait for
review, and repos on GitHub that have no clone here. Without it, everything else still works, and
the report says why GitHub was skipped.

## Origin

This plugin is the public form of a skill its author built in October 2026, to the same public
standard from its first commit.

| Private skill | This plugin | Why |
|---|---|---|
| A skill folder copied into a skills directory | A Claude Code plugin with a marketplace entry | One install line, and updates through the plugin system |
| Branches kept on purpose showed as unmerged in every report | A Kept section in the config | They were noise beside work that needs a decision |
| Config format in the skill's README | Its own file, linked from the skill | The README is now for people installing the plugin |
| Tests beside the script | `tests/`, with a runner and CI on three systems | Same tests, run on every change |
