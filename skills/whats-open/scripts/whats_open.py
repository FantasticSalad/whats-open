#!/usr/bin/env python3
"""What's open: unfinished git work across every repo under the configured folders.

Read-only apart from `git fetch`, which updates each repo's view of its remote. Prints one
plain-text report: edits not in any commit, work on this machine only (commits no remote has,
stashes, repos with no remote), branches on origin not merged into the default branch,
paused-work notes, tidy-up, and branches the config keeps on purpose. The config format is in
config-format.md beside the skill.

    whats_open.py [--no-fetch] [--offline] [--config FILE] [--root DIR]... [--paths]

Exit 0 with the report, 2 when there's no config or nothing to scan.
"""
from __future__ import annotations

import argparse
import fnmatch
import glob
import json
import os
import re
import shutil
import socket
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from urllib.parse import quote

LIST_SECTIONS = ("roots", "skip", "first", "worktrees", "review", "paused work", "sync folders", "machines")
MAP_SECTIONS = ("prefixes", "local only", "bundles", "kept")
SKIP_DIRS = {"node_modules", "vendor", "__pycache__"}
MAX_LINES = 40  # per repo and section, so one busy repo can't bury the rest
FAILING = {"failure", "timed_out", "cancelled", "action_required", "startup_failure"}
GITHUB = re.compile(r"github\.com[:/](?:\d+/)?([^/\s]+)/([^/\s]+?)(?:\.git)?/?$")
# No optional index refresh (git status then writes nothing), and never a credential prompt.
ENV = {**os.environ, "GIT_OPTIONAL_LOCKS": "0", "GIT_TERMINAL_PROMPT": "0"}


# ---------- config ----------

def config_home() -> Path:
    env = os.environ.get("WHATS_OPEN_HOME")
    return expand(env) if env else Path.home() / ".whats-open"


def expand(value: str) -> Path:
    return Path(os.path.expandvars(os.path.expanduser(value)))


def parse_config(text: str) -> dict:
    """Markdown: '## Heading' sections of '- value' items. A ' # ' starts a comment. Notes are kept whole."""
    cfg: dict = {k: [] for k in LIST_SECTIONS}
    cfg.update({k: {} for k in MAP_SECTIONS})
    cfg["notes"] = []
    section = None
    for raw in text.splitlines():
        line = raw.strip()
        heading = re.match(r"#{2,6}\s+(.+?)\s*#*$", line)
        if heading:
            section = heading.group(1).strip().lower()
            continue
        if section == "notes":
            if line:
                cfg["notes"].append(line)
            continue
        item = re.match(r"[-*]\s+(.+)$", line)
        if not item or section is None:
            continue
        value = re.sub(r"\s+#\s.*$", "", item.group(1)).strip().strip("`").strip()
        if not value:
            continue
        if section in LIST_SECTIONS:
            cfg[section].append(value)
        elif section in MAP_SECTIONS:
            key, sep, rest = value.partition(": ")
            cfg[section][key.strip().strip("`")] = rest.strip() if sep else ""
    return cfg


# ---------- git ----------

def run(cmd: list[str], timeout: int = 60) -> tuple[int, str]:
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace",
                           env=ENV, stdin=subprocess.DEVNULL, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as e:
        return 1, str(e)
    return p.returncode, p.stdout


def git(repo: Path | str, *args: str, timeout: int = 60) -> tuple[int, str]:
    return run(["git", "-C", str(repo), *args], timeout=timeout)


def count(repo: Path, *args: str) -> int:
    rc, out = git(repo, "rev-list", "--count", *args)
    return int(out.strip()) if rc == 0 and out.strip().isdigit() else 0


def is_ancestor(repo: Path, a: str, b: str) -> bool:
    return git(repo, "merge-base", "--is-ancestor", a, b)[0] == 0


def default_branch(repo: Path) -> str:
    rc, out = git(repo, "symbolic-ref", "-q", "--short", "refs/remotes/origin/HEAD")
    if rc == 0 and out.strip().startswith("origin/"):
        return out.strip()[len("origin/"):]
    for c in ("main", "master"):
        if git(repo, "rev-parse", "-q", "--verify", f"refs/remotes/origin/{c}")[0] == 0:
            return c
    return ""


def worktrees(repo: Path) -> list[dict]:
    """git worktree list --porcelain, the repo's own checkout first."""
    items, cur = [], None
    for line in git(repo, "worktree", "list", "--porcelain")[1].splitlines() + [""]:
        if line.startswith("worktree "):
            cur = {"path": line[9:], "head": "", "branch": "", "prunable": False, "bare": False, "state": ""}
        elif cur is None:
            continue
        elif line.startswith("HEAD "):
            cur["head"] = line[5:]
        elif line.startswith("branch "):
            cur["branch"] = line[7:].removeprefix("refs/heads/")
        elif line.startswith("prunable"):
            cur["prunable"] = True
        elif line == "bare":
            cur["bare"] = True
        elif line == "":
            items.append(cur)
            cur = None
    return [w for w in items if not w["bare"]]


def changes(path: str) -> str:
    rc, out = git(path, "status", "--porcelain")
    if rc != 0:
        return "can't read its status"
    lines = [line for line in out.splitlines() if line.strip()]
    new = sum(line.startswith("??") for line in lines)
    parts = [f"{len(lines) - new} changed"] if len(lines) - new else []
    if new:
        parts.append(f"{new} untracked")
    return ", ".join(parts)


def short(text: str, n: int = 90) -> str:
    text = " ".join(text.split())
    return text if len(text) <= n else text[: n - 3] + "..."


def same_or_under(path: str, root: Path) -> bool:
    p, r = os.path.normcase(os.path.abspath(path)), os.path.normcase(os.path.abspath(root))
    return p == r or p.startswith(r.rstrip("\\/") + os.sep)


# ---------- one repo ----------

@dataclass
class Repo:
    name: str
    path: Path
    fetch_failed: bool = False
    github: str = ""
    uncommitted: list[str] = field(default_factory=list)
    unpushed: list[str] = field(default_factory=list)
    branches: list[dict] = field(default_factory=list)  # on origin, not merged
    prs: list[str] = field(default_factory=list)        # open PRs whose branch isn't listed
    tidy: list[str] = field(default_factory=list)
    kept: list[str] = field(default_factory=list)       # kept on purpose (the config's Kept)
    kept_names: set[str] = field(default_factory=set)   # their branch names, so no other pass lists them


def kept_reason(cfg: dict | None, repo: str, branch: str) -> str | None:
    """The config's reason for keeping a branch, or None. A Kept pattern is a branch name or glob,
    for any repo, or `repo:pattern` for one repo (no space after that colon). Branch names can't
    contain a colon. The repo part ignores case; the branch part doesn't."""
    for pattern, reason in ((cfg or {}).get("kept") or {}).items():
        if ":" in pattern:
            only, _, pattern = pattern.partition(":")
            if only.strip().strip("`").lower() != repo.lower():
                continue
        if fnmatch.fnmatchcase(branch, pattern.strip().strip("`")):
            return reason or "kept on purpose"
    return None


def bundle_heads(repo: Path, pattern: str) -> tuple[str, dict[str, str]]:
    files = sorted(glob.glob(str(expand(pattern))), key=os.path.getmtime)
    if not files:
        return "", {}
    heads = {}
    for line in git(repo, "bundle", "list-heads", files[-1])[1].splitlines():
        sha, _, ref = line.partition(" ")
        if ref.startswith("refs/heads/"):
            heads[ref[len("refs/heads/"):]] = sha
    return Path(files[-1]).name, heads


def scan(name: str, path: Path, cfg: dict, fetch_failed: bool) -> Repo:
    r = Repo(name, path, fetch_failed=fetch_failed)
    wts = worktrees(path)
    own, others = (wts[0], wts[1:]) if wts else (None, [])
    if own:
        state = changes(own["path"])
        if state:
            r.uncommitted.append(f"checkout ({own['branch'] or 'detached'}): {state}")
    for w in others:
        label = f"worktree {w['path']} [{w['branch'] or 'detached'}]"
        if w["prunable"]:
            r.tidy.append(f"{label} is gone from disk: git -C {path.as_posix()} worktree prune")
            continue
        w["state"] = changes(w["path"])
        if w["state"]:
            r.uncommitted.append(f"{label}: {w['state']}")
    stashes = [line.split("\x00") for line in git(path, "stash", "list", "--format=%cr%x00%s")[1].splitlines() if line]
    if stashes:
        when, subject = (stashes[0] + [""])[:2]
        r.unpushed.append(f"{len(stashes)} stash(es), newest {when}: {short(subject)}")

    remotes = set(git(path, "remote")[1].split())
    if "origin" not in remotes:
        r.unpushed.insert(0, "no remote: nothing in this repo is backed up by git" if not remotes
                          else f"no remote called origin (has {', '.join(sorted(remotes))}): not checked against it")
        return r
    m = GITHUB.search(git(path, "remote", "get-url", "origin")[1].strip())
    if m:
        r.github = f"{m.group(1)}/{m.group(2)}"
    d = default_branch(path)
    if not d:
        r.tidy.append("can't tell origin's default branch, so its branches weren't checked")
        return r
    base = f"refs/remotes/origin/{d}"

    local, remote = {}, {}
    out = git(path, "for-each-ref", "--format=%(refname)%00%(objectname)%00%(committerdate:short)%00%(subject)",
              "refs/heads", "refs/remotes/origin")[1]
    for line in out.splitlines():
        ref, sha, date, subject = (line.split("\x00") + ["", "", ""])[:4]
        if ref.startswith("refs/heads/"):
            local[ref[len("refs/heads/"):]] = sha
        elif ref.startswith("refs/remotes/origin/") and ref != "refs/remotes/origin/HEAD":
            remote[ref[len("refs/remotes/origin/"):]] = (sha, date, subject)

    cur = own["branch"] if own else ""
    if own and cur != d:
        r.tidy.append(f"checkout is on {cur or 'a detached HEAD'}, not {d}")
    if d in local:
        ahead, behind = count(path, f"{base}..refs/heads/{d}"), count(path, f"refs/heads/{d}..{base}")
        if ahead:
            r.unpushed.append(f"{d}: {ahead} commit(s) not on origin/{d}" + (f", and {behind} behind it (diverged)" if behind else ""))
        elif behind and cur == d:
            r.tidy.append(f"checkout is {behind} commit(s) behind origin/{d}")

    design = ""
    if name in cfg["local only"]:
        design = f" (local by design: {cfg['local only'][name]})" if cfg["local only"][name] else " (local by design)"
    bundle = bundle_heads(path, cfg["bundles"][name]) if name in cfg["bundles"] else None

    def note(branch: str, sha: str) -> str:
        if bundle is None:
            return design
        bname, heads = bundle
        old = heads.get(branch)
        if not bname:
            return f"{design}; no bundle found"
        if old == sha:
            return f"{design}; in bundle {bname}"
        if old and is_ancestor(path, old, sha):
            return f"{design}; {count(path, f'{old}..{sha}')} commit(s) newer than bundle {bname}"
        return f"{design}; not in bundle {bname}"

    wt_root = expand(cfg["worktrees"][0]) if cfg["worktrees"] else None
    for w in others:
        if w["prunable"] or not w["head"]:
            continue
        label = f"worktree {w['path']} [{w['branch'] or 'detached'}]"
        n = count(path, w["head"], "--not", "--remotes=origin")
        if n:
            r.unpushed.append(f"{label}: {n} commit(s) on no remote{note(w['branch'], w['head'])}")
        if wt_root and not same_or_under(w["path"], wt_root):
            r.tidy.append(f"{label} is outside {wt_root.as_posix()}")
        if not n and not w["state"] and is_ancestor(path, w["head"], base):
            r.tidy.append(f"{label}: no edits and nothing unlanded (just started, or finished and not removed)")

    checked_out = {w["branch"] for w in wts if w["branch"]}
    merged_local = []
    for b, sha in sorted(local.items()):
        if b == d or b in checked_out:
            continue
        n = count(path, f"refs/heads/{b}", "--not", "--remotes=origin")
        if n:
            r.unpushed.append(f"branch {b}: {n} commit(s) on no remote{note(b, sha)}")
        elif b not in remote:
            if is_ancestor(path, sha, base):
                reason = kept_reason(cfg, name, b)
                if reason is None:
                    merged_local.append(b)
                else:
                    r.kept.append(f"local branch {b} (merged already): {reason}")
            else:
                r.tidy.append(f"branch {b}: local only, and its commits are on origin in other branches")
    if merged_local:
        r.tidy.append("local branches merged already, safe to delete: " + ", ".join(merged_local))

    def origin_refs(flag: str) -> set[str]:
        out = git(path, "for-each-ref", f"{flag}={base}", "--format=%(refname)", "refs/remotes/origin")[1]
        names = {line[len("refs/remotes/origin/"):] for line in out.splitlines() if line.startswith("refs/remotes/origin/")}
        return names - {"HEAD", d}

    for b in sorted(origin_refs("--no-merged")):
        sha, date, subject = remote.get(b, ("", "", ""))
        reason = kept_reason(cfg, name, b)
        if reason is not None:
            r.kept.append(f"{b} ({date}, not merged): {reason}")
            r.kept_names.add(b)
            continue
        label = max((p for p in cfg["prefixes"] if b.startswith(p)), key=len, default="")
        r.branches.append({"name": b, "sha": sha, "date": date, "subject": subject,
                           "label": cfg["prefixes"].get(label, "") if label else "",
                           "review": any(b.startswith(p) for p in cfg["review"]), "checks": "", "pr": ""})
    merged = []
    for b in sorted(origin_refs("--merged")):
        reason = kept_reason(cfg, name, b)
        if reason is None:
            merged.append(b)
        else:
            r.kept.append(f"{b} (merged already): {reason}")
            r.kept_names.add(b)
    if merged:
        r.tidy.append("merged already, still on origin (safe to delete): " + ", ".join(merged))
    return r


# ---------- GitHub (optional) ----------

def pull_requests(r: Repo) -> None:
    rc, out = run(["gh", "pr", "list", "-R", r.github, "--state", "open", "--limit", "100",
                   "--json", "number,title,headRefName,isDraft"])
    try:
        prs = json.loads(out) if rc == 0 else None
    except ValueError:
        prs = None
    if prs is None:
        r.prs.append("open pull requests: couldn't list them")
        return
    by_name = {b["name"]: b for b in r.branches}
    for pr in prs:
        if pr.get("headRefName", "") in r.kept_names:
            continue  # listed once, under KEPT
        tag = f"#{pr.get('number')}" + (" draft" if pr.get("isDraft") else "")
        b = by_name.get(pr.get("headRefName", ""))
        if b:
            b["pr"] = tag
        else:
            r.prs.append(f"PR {tag} from {pr.get('headRefName', '?')}: {short(pr.get('title', ''))}")


def check_runs(r: Repo, b: dict) -> None:
    rc, out = run(["gh", "api", f"repos/{r.github}/commits/{b['sha']}/check-runs?per_page=100"])
    try:
        found = json.loads(out).get("check_runs") if rc == 0 else None
    except (ValueError, AttributeError):
        found = None
    if found is None:
        b["checks"] = "unknown"
    elif not found:
        b["checks"] = "none"
    elif any(x.get("conclusion") in FAILING for x in found):
        b["checks"] = "failing"
    elif any(x.get("status") != "completed" for x in found):
        b["checks"] = "running"
    else:
        b["checks"] = "passing"


def gh_problem() -> str:
    """Why GitHub can't be asked, or "" when gh is installed and logged in."""
    if not shutil.which("gh"):
        return "GitHub not checked: the gh CLI isn't installed."
    if run(["gh", "auth", "status"], timeout=30)[0] != 0:
        return "GitHub not checked: gh isn't logged in (gh auth login)."
    return ""


def gh_json(cmd: list[str]):
    rc, out = run(cmd)
    try:
        return json.loads(out) if rc == 0 else None
    except ValueError:
        return None


def remote_open_work(repo: dict, cfg: dict | None = None) -> tuple[str, list[str]]:
    """Branches ahead of the default branch, and open PRs, for a repo with no clone here. Branches
    the config keeps aren't compared or listed, and neither are their PRs."""
    nwo = repo.get("nameWithOwner", "")
    short_name = nwo.split("/")[-1]
    default = (repo.get("defaultBranchRef") or {}).get("name") or ""
    if not default:
        return nwo, []  # empty repo
    branches = gh_json(["gh", "api", f"repos/{nwo}/branches?per_page=100"])
    if branches is None:
        return nwo, ["branches: couldn't list them"]
    prs = gh_json(["gh", "pr", "list", "-R", nwo, "--state", "open", "--limit", "100",
                   "--json", "number,title,headRefName,isDraft"]) or []
    pr_tag = {p.get("headRefName", ""): f"#{p.get('number')}" + (" draft" if p.get("isDraft") else "") for p in prs}
    items, names = [], set()
    for b in branches:
        name = b.get("name", "")
        names.add(name)
        if not name or name == default or kept_reason(cfg, short_name, name) is not None:
            continue
        cmp = gh_json(["gh", "api", f"repos/{nwo}/compare/{quote(default, safe='')}...{quote(name, safe='')}"])
        if cmp is None:
            items.append(f"{name}: couldn't compare it with {default}")
            continue
        if not cmp.get("ahead_by"):
            continue  # merged, or nothing of its own
        last = ((cmp.get("commits") or [{}])[-1]).get("commit") or {}
        date = ((last.get("committer") or {}).get("date") or "")[:10]
        subject = (last.get("message") or "").splitlines()[0] if last.get("message") else ""
        bits = [date, f"{cmp['ahead_by']} ahead of {default}"] + ([f"PR {pr_tag[name]}"] if name in pr_tag else [])
        items.append(f"{name} ({'; '.join(x for x in bits if x)}): {short(subject)}")
    for p in prs:
        if p.get("headRefName", "") not in names and kept_reason(cfg, short_name, p.get("headRefName", "")) is None:
            items.append(f"PR {pr_tag[p.get('headRefName', '')]} from {p.get('headRefName', '?')}: {short(p.get('title', ''))}")
    return nwo, items


def uncloned(repos: list[Repo], skip: set[str], cfg: dict | None = None) -> list[tuple[str, list[str]]]:
    """Repos on GitHub, under the owners of the repos cloned here, that have no clone under the
    roots: their unmerged branches and open PRs. A repo named in Skip is never asked about, and
    branches the config keeps are left out."""
    cloned = {r.github.lower() for r in repos if r.github}
    owners = sorted({c.split("/")[0] for c in cloned})
    skipped = {s.lower().split("/")[-1] for s in skip}
    rows, quiet = [], []
    for owner in owners:
        listed = gh_json(["gh", "repo", "list", owner, "--limit", "1000", "--no-archived",
                          "--json", "nameWithOwner,defaultBranchRef"])
        if listed is None:
            rows.append((owner, ["couldn't list its repos"]))
            continue
        targets = [x for x in listed if x.get("nameWithOwner", "").lower() not in cloned
                   and x.get("nameWithOwner", "").split("/")[-1].lower() not in skipped]
        with ThreadPoolExecutor(max_workers=8) as ex:
            for nwo, items in ex.map(lambda t: remote_open_work(t, cfg), targets):
                (rows.append((nwo, items)) if items else quiet.append(nwo))
    if quiet:
        rows.append(("also not cloned here, nothing open", [", ".join(sorted(quiet))]))
    return rows


def github_pass(repos: list[Repo]) -> str:
    targets = [r for r in repos if r.github]
    if not targets:
        return ""
    problem = gh_problem()
    if problem:
        return problem
    with ThreadPoolExecutor(max_workers=8) as ex:
        jobs = [ex.submit(pull_requests, r) for r in targets]
        jobs += [ex.submit(check_runs, r, b) for r in targets for b in r.branches if b["review"]]
        for j in jobs:
            j.result()
    return ""


# ---------- the rest of the report ----------

def find_repos(roots: list[Path], skip: set[str]) -> list[tuple[str, Path]]:
    """Repos directly under each root, or one level deeper (root/org/app)."""
    def dirs(p: Path) -> list[Path]:
        try:
            return sorted((c for c in p.iterdir() if c.is_dir()), key=lambda c: c.name.lower())
        except OSError:
            return []

    found, seen = [], set()
    for root in roots:
        for d in dirs(root):
            if d.name.startswith(".") or d.name in SKIP_DIRS or d.name in skip:
                continue
            if (d / ".git").is_dir():
                found.append((d.name, d))
            elif not (d / ".git").exists():  # a .git file is a worktree or submodule, reached through its repo
                found += [(f"{d.name}/{s.name}", s) for s in dirs(d)
                          if not s.name.startswith(".") and s.name not in skip
                          and f"{d.name}/{s.name}" not in skip and (s / ".git").is_dir()]
    out = []
    for name, p in found:
        out.append((name if name not in seen else p.as_posix(), p))
        seen.add(name)
    return out


def note_title(path: Path) -> str | None:
    """A note's first `# ` heading outside a fenced block. Reading stops there."""
    fenced = False
    try:
        with path.open(encoding="utf-8", errors="replace") as fh:
            for line in fh:
                if line.startswith(("```", "~~~")):
                    fenced = not fenced
                elif not fenced and line.startswith("# "):
                    return line[2:].strip()
    except OSError:
        pass
    return None


def paused_notes(folders: list[str]) -> list[tuple[str, list[str]]]:
    rows = []
    for f in folders:
        p = expand(f)
        if not p.is_dir():
            rows.append((p.as_posix(), ["folder not found"]))
            continue
        notes = []
        for n in sorted(p.glob("*.md"), key=lambda x: x.name, reverse=True):
            if n.name.lower() == "readme.md":
                continue
            notes.append(f"{n.name}: {short(note_title(n) or n.stem)}")
        rows.append((p.as_posix(), notes))
    return rows


def conflict_copies(folders: list[str], machines: list[str]) -> list[str]:
    """Sync clients' conflict copies: OneDrive's name-MACHINE(-2), Dropbox's and Syncthing's markers."""
    names = {m for m in machines if m} | {socket.gethostname(), os.environ.get("COMPUTERNAME", "")}
    names.discard("")
    by_machine = re.compile(r"-(?:%s)(?:-\d+)?$" % "|".join(re.escape(n) for n in sorted(names)), re.IGNORECASE)
    marked = re.compile(r"conflicted copy|\.sync-conflict-", re.IGNORECASE)
    hits = []
    for f in folders:
        base = expand(f)
        if not base.is_dir():
            continue
        for dirpath, dirnames, filenames in os.walk(base):
            dirnames[:] = [x for x in dirnames if not x.startswith(".")]
            for fn in filenames:
                if by_machine.search(os.path.splitext(fn)[0]) or marked.search(fn):
                    hits.append(Path(dirpath, fn).relative_to(base.parent).as_posix())
    return sorted(hits)


def fetch(path: Path) -> bool:
    if "origin" not in git(path, "remote")[1].split():
        return True
    # No automatic maintenance (fetch would otherwise run `git maintenance --auto`, which can
    # repack and prune) and no submodule remotes: only origin's refs change.
    return git(path, "fetch", "-q", "--prune", "--no-auto-maintenance", "--recurse-submodules=no",
               "origin", timeout=120)[0] == 0


def branch_line(b: dict) -> str:
    bits = [b["date"]] + ([b["label"]] if b["label"] else [])
    if b["review"]:
        bits.append(f"checks {b['checks'] or 'not looked up'}")
    if b["pr"]:
        bits.append(f"PR {b['pr']}")
    return f"{b['name']} ({'; '.join(x for x in bits if x)}): {short(b['subject'])}"


def section(title: str, rows: list[tuple[str, list[str]]]) -> list[str]:
    rows = [(n, items) for n, items in rows if items]
    if not rows:
        return []
    out = ["", title]
    for name, items in rows:
        out.append(f"  {name}")
        out += [f"    {i}" for i in items[:MAX_LINES]]
        if len(items) > MAX_LINES:
            out.append(f"    ... and {len(items) - MAX_LINES} more")
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Report unfinished git work across your repos. Read-only apart from git fetch.")
    ap.add_argument("--no-fetch", action="store_true", help="don't git fetch first")
    ap.add_argument("--offline", action="store_true", help="no git fetch and no GitHub calls")
    ap.add_argument("--config", help="config file (default: $WHATS_OPEN_HOME/config.md, else ~/.whats-open/config.md)")
    ap.add_argument("--root", action="append", default=[], help="a folder of repos, instead of the config's Roots")
    ap.add_argument("--paths", action="store_true", help="say where the config is looked for, then stop")
    args = ap.parse_args(argv)
    try:
        sys.stdout.reconfigure(errors="replace")
    except (AttributeError, ValueError):
        pass

    cfg_path = expand(args.config) if args.config else config_home() / "config.md"
    if args.paths:
        print(f"config: {cfg_path.as_posix()} ({'found' if cfg_path.is_file() else 'not there yet'})")
        return 0
    if cfg_path.is_file():
        cfg = parse_config(cfg_path.read_text(encoding="utf-8", errors="replace"))
    elif args.root:
        cfg = parse_config("")
    else:
        print(f"No config at {cfg_path.as_posix()}. Run the skill's setup, or pass --root <folder of repos>.", file=sys.stderr)
        return 2
    roots = [expand(x) for x in (args.root or cfg["roots"])]
    missing = [x.as_posix() for x in roots if not x.is_dir()]
    repos_found = find_repos([x for x in roots if x.is_dir()], set(cfg["skip"]))
    if not repos_found:
        print(f"No git repos under {', '.join(x.as_posix() for x in roots)}.", file=sys.stderr)
        return 2
    first = {n.lower(): i for i, n in enumerate(cfg["first"])}
    repos_found.sort(key=lambda nr: (first.get(nr[0].lower(), len(first)), nr[0].lower()))

    fetched = not (args.no_fetch or args.offline)
    failed: set[str] = set()
    if fetched:
        with ThreadPoolExecutor(max_workers=12) as ex:
            for (name, _), ok in zip(repos_found, ex.map(lambda nr: fetch(nr[1]), repos_found)):
                if not ok:
                    failed.add(name)
    with ThreadPoolExecutor(max_workers=8) as ex:
        repos = list(ex.map(lambda nr: scan(nr[0], nr[1], cfg, nr[0] in failed), repos_found))
    gh_note = "" if args.offline else github_pass(repos)
    not_cloned = [] if (args.offline or gh_note) else uncloned(repos, set(cfg["skip"]), cfg)
    paused = paused_notes(cfg["paused work"])
    conflicts = conflict_copies(cfg["sync folders"], cfg["machines"])

    unc = [(r.name, r.uncommitted) for r in repos]
    unp = [(r.name, r.unpushed) for r in repos]
    unm = [(r.name, [branch_line(b) for b in r.branches] + r.prs) for r in repos]
    tidy = [(r.name, r.tidy) for r in repos] + [("conflict copies in synced folders", conflicts)]
    kept = [(r.name, r.kept) for r in repos]

    def total(rows: list[tuple[str, list[str]]]) -> int:
        return sum(len(items) for _, items in rows)

    paused_count = sum(len([i for i in items if i != "folder not found"]) for _, items in paused)

    now = datetime.now().astimezone().strftime("%Y-%m-%d %H:%M %z")
    lines = [f"What's open: {len(repos)} repos under {', '.join(x.as_posix() for x in roots)}, at {now}"]
    if missing:
        lines.append(f"Not found: {', '.join(missing)}")
    if not fetched:
        lines.append("Not fetched: each repo's view of origin is as of its last fetch.")
    elif failed:
        lines.append(f"Couldn't fetch {', '.join(sorted(failed))}: their view of origin may be out of date.")
    else:
        lines.append("Fetched every repo first.")
    open_elsewhere = total([(n, items) for n, items in not_cloned if not n.startswith("also not cloned")])
    lines.append(f"Uncommitted {total(unc)} | Unpushed {total(unp)} | Unmerged {total(unm)} | "
                 f"Paused {paused_count} | Tidy-up {total(tidy)}"
                 + (f" | Not cloned here {open_elsewhere}"
                    if not (args.offline or gh_note) and any(r.github for r in repos) else ""))
    if not (total(unc) or total(unp) or total(unm) or paused_count or open_elsewhere):
        lines.append("Nothing open: everything is committed, pushed and merged.")
    lines += section("UNCOMMITTED: edits not in any commit", unc)
    lines += section("UNPUSHED: on this machine only (commits no remote has, stashes)", unp)
    lines += section("UNMERGED: on origin, not in the default branch", unm)
    lines += section("NOT CLONED HERE: on GitHub, with no clone under the roots", not_cloned)
    lines += section("PAUSED WORK: notes about work left part-way", paused)
    lines += section("TIDY-UP", tidy)
    lines += section("KEPT: on origin on purpose, per the config (not counted above)", kept)
    lines.append("")
    if gh_note:
        lines.append(gh_note)
    if cfg["notes"]:
        lines.append("Notes from the config:")
        lines += [f"  {n}" for n in cfg["notes"]]
    lines.append("This machine's view only. Nothing was changed" + (", apart from git fetch updating each repo's view of origin." if fetched else "."))
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    sys.exit(main())
