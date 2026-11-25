"""Tests for whats_open.py, over throwaway repos. Needs git 2.31 or newer and pytest:

    bash tests/run-all.sh
"""
from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "skills" / "whats-open" / "scripts"))
import whats_open as wo  # noqa: E402

IDENTITY = ["-c", "user.name=Test", "-c", "user.email=test@example.com", "-c", "commit.gpgsign=false"]


def g(cwd: Path, *args: str) -> str:
    return subprocess.run(["git", *IDENTITY, *args], cwd=cwd, check=True, capture_output=True, text=True).stdout


def commit(repo: Path, name: str, text: str = "x\n") -> None:
    (repo / name).write_text(text, encoding="utf-8")
    g(repo, "add", name)
    g(repo, "commit", "-q", "-m", f"add {name}")


def clone_with_origin(tmp: Path, root: Path, name: str) -> Path:
    origin = tmp / f"{name}.git"
    g(tmp, "init", "-q", "--bare", "-b", "main", str(origin))
    repo = root / name
    g(tmp, "clone", "-q", str(origin), str(repo))
    g(repo, "symbolic-ref", "HEAD", "refs/heads/main")
    commit(repo, "README.md", "# app\n")
    g(repo, "push", "-q", "-u", "origin", "main")
    g(repo, "remote", "set-head", "origin", "main")
    return repo


def section_of(out: str, title: str) -> str:
    start = out.index(title)
    rest = out[start + len(title):]
    ends = [rest.index(t) for t in ("\nUNPUSHED", "\nUNMERGED", "\nPAUSED", "\nTIDY-UP", "\nKEPT", "\n\n") if t in rest]
    return rest[: min(ends)] if ends else rest


@pytest.fixture
def world(tmp_path: Path) -> SimpleNamespace:
    """One cloned repo in every state the report covers, plus repos with no remote."""
    root = tmp_path / "code"
    root.mkdir()
    app = clone_with_origin(tmp_path, root, "app")

    # On origin, not merged: a review branch and a session branch.
    for branch in ("auto/2026-01-02-tune", "b/2026-01-03-docs"):
        g(app, "checkout", "-q", "-b", branch)
        commit(app, branch.replace("/", "-") + ".txt")
        g(app, "push", "-q", "origin", branch)
        g(app, "checkout", "-q", "main")
    # Merged into main and still on origin.
    g(app, "checkout", "-q", "-b", "done-already")
    commit(app, "done.txt")
    g(app, "checkout", "-q", "main")
    g(app, "merge", "-q", "--ff-only", "done-already")
    g(app, "push", "-q", "origin", "main", "done-already")
    g(app, "branch", "-q", "-D", "done-already")
    # A local branch that's merged already, and one with work no remote has (and an older bundle).
    g(app, "branch", "old-merged", "main")
    g(app, "checkout", "-q", "-b", "local-work")
    commit(app, "local1.txt")
    bundles = tmp_path / "bundles"
    bundles.mkdir()
    g(app, "bundle", "create", "-q", str(bundles / "app-1.bundle"), "local-work")
    commit(app, "local2.txt")
    g(app, "checkout", "-q", "main")
    # A worktree with a commit no remote has and an uncommitted edit; one that's gone from disk.
    wts = tmp_path / "worktrees"
    g(app, "worktree", "add", "-q", "-b", "b/wt", str(wts / "wt"), "origin/main")
    commit(wts / "wt", "wt.txt")
    (wts / "wt" / "README.md").write_text("# changed\n", encoding="utf-8")
    g(app, "worktree", "add", "-q", "-b", "gone-wt", str(wts / "gone"), "origin/main")
    shutil.rmtree(wts / "gone")
    # main ahead of origin, a stash, and an untracked file in the checkout.
    commit(app, "ahead.txt")
    (app / "README.md").write_text("# half done\n", encoding="utf-8")
    g(app, "stash", "push", "-q", "-m", "half done")
    (app / "scratch.txt").write_text("scratch\n", encoding="utf-8")

    for name in ("loner", "skipme", "org/tool"):
        p = root / name
        p.mkdir(parents=True)
        g(p, "init", "-q", "-b", "main")
        commit(p, "a.txt")

    handoffs = tmp_path / "handoffs"
    handoffs.mkdir()
    (handoffs / "README.md").write_text("# About this folder\n", encoding="utf-8")
    (handoffs / "2026-01-04-0900-app-thing.md").write_text("# Thing to resume\n\nbody\n", encoding="utf-8")
    sync = tmp_path / "sync"
    sync.mkdir()
    (sync / "notes.md").write_text("ok\n", encoding="utf-8")
    (sync / "notes-TESTHOST.md").write_text("copy\n", encoding="utf-8")

    cfg = tmp_path / "config.md"
    cfg.write_text(f"""# whats-open config

## Roots
- {root.as_posix()}

## Skip
- skipme  # not this one

## First
- loner

## Worktrees
- {wts.as_posix()}

## Prefixes
- auto/: automated run
- `b/`: session

## Review
- auto/

## Local only
- app: pushed by hand

## Bundles
- app: {bundles.as_posix()}/app-*.bundle

## Paused work
- {handoffs.as_posix()}

## Sync folders
- {sync.as_posix()}

## Machines
- TESTHOST

## Notes
- Approvals live in the review queue.
""", encoding="utf-8")
    return SimpleNamespace(cfg=cfg, root=root, wts=wts)


def test_report_covers_every_state(world, capsys):
    # Fetch is on: origin is a local bare repo, and nothing here is on GitHub, so gh isn't called.
    assert wo.main(["--config", str(world.cfg)]) == 0
    out = capsys.readouterr().out
    assert "Fetched every repo first." in out
    assert "skipme" not in out

    unc = section_of(out, "UNCOMMITTED")
    assert "checkout (main): 1 untracked" in unc
    assert "[b/wt]: 1 changed" in unc

    unp = section_of(out, "UNPUSHED")
    assert unp.index("  loner") < unp.index("  app"), "First puts loner ahead of the rest"
    assert "no remote: nothing in this repo is backed up by git" in unp
    assert "org/tool" in unp
    assert "main: 1 commit(s) not on origin/main" in unp
    assert ("branch local-work: 2 commit(s) on no remote (local by design: pushed by hand); "
            "1 commit(s) newer than bundle app-1.bundle") in unp
    assert "[b/wt]: 1 commit(s) on no remote (local by design: pushed by hand); not in bundle app-1.bundle" in unp
    assert "1 stash(es), newest" in unp and "half done" in unp

    unm = section_of(out, "UNMERGED")
    assert "auto/2026-01-02-tune (" in unm and "automated run; checks not looked up): add auto-2026-01-02-tune.txt" in unm
    assert "b/2026-01-03-docs (" in unm and "session): add b-2026-01-03-docs.txt" in unm
    assert "done-already" not in unm

    tidy = section_of(out, "TIDY-UP")
    assert "merged already, still on origin (safe to delete): done-already" in tidy
    assert "local branches merged already, safe to delete: old-merged" in tidy
    assert "[gone-wt] is gone from disk" in tidy
    assert "sync/notes-TESTHOST.md" in tidy and "sync/notes.md" not in tidy

    paused = section_of(out, "PAUSED WORK")
    assert "2026-01-04-0900-app-thing.md: Thing to resume" in paused
    assert "README.md" not in paused

    assert "Approvals live in the review queue." in out
    assert "Nothing open" not in out


def test_the_report_changes_nothing(world, capsys):
    app = world.root / "app"

    def state() -> list[str]:
        return [g(app, "for-each-ref", "--format=%(refname) %(objectname)", "refs/heads", "refs/remotes"),
                g(app, "stash", "list"), g(app, "status", "--porcelain=v1", "--untracked-files=all"),
                g(app, "worktree", "list", "--porcelain")]

    before = state()
    assert wo.main(["--config", str(world.cfg), "--offline"]) == 0
    capsys.readouterr()
    assert state() == before, "branches, stashes, edits and worktrees are as they were"


def test_worktree_outside_the_root_and_a_finished_one(world, capsys, tmp_path):
    app = world.root / "app"
    g(app, "worktree", "add", "-q", "-b", "b/stray", str(tmp_path / "stray"), "origin/main")
    assert wo.main(["--config", str(world.cfg), "--offline"]) == 0
    tidy = section_of(capsys.readouterr().out, "TIDY-UP")
    assert f"[b/stray] is outside {world.wts.as_posix()}" in tidy
    assert "[b/stray]: no edits and nothing unlanded" in tidy


def test_nothing_open(tmp_path, capsys):
    root = tmp_path / "code"
    root.mkdir()
    clone_with_origin(tmp_path, root, "tidy")
    cfg = tmp_path / "config.md"
    cfg.write_text(f"## Roots\n- {root.as_posix()}\n", encoding="utf-8")
    assert wo.main(["--config", str(cfg), "--no-fetch"]) == 0
    out = capsys.readouterr().out
    assert "Not fetched" in out
    assert "Nothing open: everything is committed, pushed and merged." in out
    assert "UNCOMMITTED" not in out and "UNPUSHED" not in out and "UNMERGED" not in out


def test_kept_branches_are_listed_once_as_kept(tmp_path, capsys):
    root = tmp_path / "code"
    root.mkdir()
    lib = clone_with_origin(tmp_path, root, "lib")
    for branch in ("history-v1-archive", "history-v2-archive", "feature/live"):
        g(lib, "checkout", "-q", "-b", branch)
        commit(lib, branch.replace("/", "-") + ".txt")
        g(lib, "push", "-q", "origin", branch)
        g(lib, "checkout", "-q", "main")
    for branch in ("release-line", "stale-merged"):  # merged into main, still on origin
        g(lib, "push", "-q", "origin", f"main:refs/heads/{branch}")
    g(lib, "branch", "history-local-archive", "main")  # local only, merged already
    cfg = tmp_path / "config.md"
    cfg.write_text(f"""## Roots
- {root.as_posix()}

## Kept
- history-*-archive: old history, kept by decision  # any repo
- lib:`release-line`: maintained on its own
- other:feature/live: only in another repo, so not this one
""", encoding="utf-8")
    assert wo.main(["--config", str(cfg), "--offline"]) == 0
    out = capsys.readouterr().out

    unm = section_of(out, "UNMERGED")
    assert "feature/live" in unm, "a pattern for another repo doesn't apply here"
    assert "archive" not in unm, "kept branches aren't listed as unmerged"
    assert "Unmerged 1 |" in out, "kept branches don't count"

    tidy = section_of(out, "TIDY-UP")
    assert "stale-merged" in tidy and "release-line" not in tidy, "a kept merged branch isn't offered for deletion"
    assert "history-local-archive" not in tidy, "nor is a kept local branch"

    kept = section_of(out, "KEPT")
    assert "history-v1-archive (" in kept and "not merged): old history, kept by decision" in kept
    assert "history-v2-archive (" in kept
    assert "release-line (merged already): maintained on its own" in kept, "backticks around a pattern are fine"
    assert "local branch history-local-archive (merged already): old history" in kept
    assert "feature/live" not in kept


def test_a_pull_request_from_a_kept_branch_stays_under_kept(monkeypatch):
    import json as _json
    monkeypatch.setattr(wo, "run", lambda cmd, timeout=60: (0, _json.dumps([
        {"number": 3, "title": "Archive", "headRefName": "archive/old", "isDraft": False},
        {"number": 4, "title": "Live", "headRefName": "feature/x", "isDraft": True}])))
    r = wo.Repo("lib", Path("."), github="Acme/lib")
    r.kept_names = {"archive/old"}
    r.branches = [{"name": "feature/x", "pr": ""}]
    wo.pull_requests(r)
    assert r.prs == [], "a PR from a kept branch isn't listed as unmerged work"
    assert r.branches[0]["pr"] == "#4 draft"


def test_uncloned_leaves_out_kept_branches(monkeypatch):
    calls = fake_gh(monkeypatch)
    cfg = wo.parse_config("## Kept\n- tools:feature/*: parked on purpose\n")
    rows = dict(wo.uncloned([wo.Repo("app", Path("."), github="Acme/app")], {"secret"}, cfg))
    assert not any(i.startswith("feature/new") for i in rows["Acme/tools"]), "a kept branch isn't open work"
    assert not any("compare/main...feature%2Fnew" in " ".join(c) for c in calls), "and isn't even compared"
    assert any(i.startswith("PR #7 from patch-1") for i in rows["Acme/tools"])


def test_fetch_never_runs_maintenance_or_reaches_submodules(monkeypatch):
    seen = []
    monkeypatch.setattr(wo, "git", lambda repo, *args, timeout=60: (seen.append(args) or (0, "origin\n")))
    assert wo.fetch(Path("."))
    fetch_args = next(a for a in seen if a and a[0] == "fetch")
    assert "--no-auto-maintenance" in fetch_args and "--recurse-submodules=no" in fetch_args


def test_a_paused_note_title_skips_fenced_blocks(tmp_path):
    note = tmp_path / "2026-01-01-x.md"
    note.write_text("intro\n```sh\n# not a heading\n```\n# The real title\n\n# Another\n", encoding="utf-8")
    assert wo.note_title(note) == "The real title"
    assert wo.note_title(tmp_path / "missing.md") is None


def test_open_work_only_on_github_means_not_nothing_open(tmp_path, capsys, monkeypatch):
    root = tmp_path / "code"
    root.mkdir()
    repo = clone_with_origin(tmp_path, root, "tidy")
    g(repo, "remote", "set-url", "origin", "https://github.com/Acme/tidy.git")
    cfg = tmp_path / "config.md"
    cfg.write_text(f"## Roots\n- {root.as_posix()}\n", encoding="utf-8")
    monkeypatch.setattr(wo, "github_pass", lambda repos: "")
    monkeypatch.setattr(wo, "uncloned", lambda repos, skip, cfg=None: [("Acme/other", ["wip (2026-01-01; 1 ahead of main): x"])])
    assert wo.main(["--config", str(cfg), "--no-fetch"]) == 0
    out = capsys.readouterr().out
    assert "| Not cloned here 1" in out
    assert "Nothing open" not in out


def test_kept_reason_patterns():
    cfg = wo.parse_config("## Kept\n- `wip/*`\n- Lib:keep-me: by decision\n")
    assert wo.kept_reason(cfg, "anything", "wip/x") == "kept on purpose"
    assert wo.kept_reason(cfg, "lib", "keep-me") == "by decision", "the repo part ignores case"
    assert wo.kept_reason(cfg, "other", "keep-me") is None
    assert wo.kept_reason(cfg, "lib", "wip") is None


def test_root_flag_works_without_a_config(tmp_path, capsys):
    root = tmp_path / "code"
    root.mkdir()
    clone_with_origin(tmp_path, root, "solo")
    assert wo.main(["--config", str(tmp_path / "none.md"), "--root", root.as_posix(), "--offline"]) == 0
    assert "1 repos under" in capsys.readouterr().out


def test_no_config_says_so(tmp_path, capsys):
    assert wo.main(["--config", str(tmp_path / "missing.md"), "--offline"]) == 2
    assert "No config at" in capsys.readouterr().err


def test_paths_reports_the_config_location(tmp_path, capsys, monkeypatch):
    monkeypatch.setenv("WHATS_OPEN_HOME", str(tmp_path / "home"))
    assert wo.main(["--paths"]) == 0
    out = capsys.readouterr().out
    assert (tmp_path / "home" / "config.md").as_posix() in out and "not there yet" in out


def test_parse_config():
    cfg = wo.parse_config(
        "# title\n## Roots\n- `D:/work/repos`  # main one\n- ~/src\n"
        "## Prefixes\n- `auto/`: bot runs\n- wip/\n"
        "## Notes\nfree text\n- a note\n"
        "## Something else\n- ignored\n"
    )
    assert cfg["roots"] == ["D:/work/repos", "~/src"]
    assert cfg["prefixes"] == {"auto/": "bot runs", "wip/": ""}
    assert cfg["notes"] == ["free text", "- a note"]
    assert "something else" not in cfg


def test_github_url_forms():
    for url in ("git@github.com:Acme/widgets.git", "https://github.com/Acme/widgets",
                "ssh://git@ssh.github.com:443/Acme/widgets.git"):
        m = wo.GITHUB.search(url)
        assert m and (m.group(1), m.group(2)) == ("Acme", "widgets"), url
    assert wo.GITHUB.search("https://gitlab.com/Acme/widgets.git") is None


def fake_gh(monkeypatch):
    """Stands in for gh: Acme has app (cloned), tools (not cloned: one open branch, one merged
    branch, one PR from a fork), quiet (not cloned, nothing open) and secret (on the skip list)."""
    import json as _json
    calls = []
    compare = {
        "main...feature%2Fnew": {"ahead_by": 2, "commits": [
            {"commit": {"message": "first"}},
            {"commit": {"message": "Add the new thing\n\nbody", "committer": {"date": "2026-11-01T10:00:00Z"}}}]},
        "main...old": {"ahead_by": 0, "commits": []},
    }

    def run(cmd, timeout=60):
        calls.append(cmd)
        joined = " ".join(cmd)
        if cmd[:3] == ["gh", "repo", "list"]:
            return 0, _json.dumps([{"nameWithOwner": f"Acme/{n}", "defaultBranchRef": {"name": "main"}}
                                   for n in ("app", "tools", "quiet", "secret")])
        if "repos/Acme/tools/branches" in joined:
            return 0, _json.dumps([{"name": "main"}, {"name": "feature/new"}, {"name": "old"}])
        if "repos/Acme/quiet/branches" in joined:
            return 0, _json.dumps([{"name": "main"}])
        if "repos/Acme/tools/compare/" in joined:
            return 0, _json.dumps(compare[cmd[-1].split("/compare/")[1]])
        if cmd[:3] == ["gh", "pr", "list"] and "Acme/tools" in cmd:
            return 0, _json.dumps([{"number": 7, "title": "Fork fix", "headRefName": "patch-1", "isDraft": False},
                                   {"number": 8, "title": "Ours", "headRefName": "feature/new", "isDraft": True}])
        if cmd[:3] == ["gh", "pr", "list"]:
            return 0, "[]"
        return 1, ""

    monkeypatch.setattr(wo, "run", run)
    return calls


def test_uncloned_lists_open_work_on_repos_with_no_clone(monkeypatch):
    calls = fake_gh(monkeypatch)
    rows = dict(wo.uncloned([wo.Repo("app", Path("."), github="Acme/app")], {"secret"}))
    assert "Acme/app" not in rows, "a cloned repo is reported in its own sections"
    tools = rows["Acme/tools"]
    assert any(i.startswith("feature/new (2026-11-01; 2 ahead of main; PR #8 draft): Add the new thing") for i in tools)
    assert not any(i.startswith("old") for i in tools), "a merged branch isn't open work"
    assert any(i.startswith("PR #7 from patch-1: Fork fix") for i in tools)
    assert rows["also not cloned here, nothing open"] == ["Acme/quiet"]
    assert not any("secret" in " ".join(c) for c in calls[1:]), "a skipped repo is never asked about"


def test_uncloned_only_asks_the_owners_cloned_here(monkeypatch):
    calls = fake_gh(monkeypatch)
    assert wo.uncloned([wo.Repo("local", Path("."))], set()) == []
    assert calls == [], "no GitHub repos cloned means no owner to ask about"
