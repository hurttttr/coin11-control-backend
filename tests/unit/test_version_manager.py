"""
version_manager 单元测试 — monkeypatch git_ops.run_git / is_git_repo，
绝不发起真实 git 或网络操作。
覆盖: check_update 有更新 / 无更新 / 非 git 仓库，分支名自动探测与回退，pull_update。
"""

from app.services import git_ops
from app.services.version_manager import VersionManager

CURRENT = "a1b2c3d4"  # 本地 HEAD
LATEST = "e5f6a7b8"  # 远端 HEAD


def _make_fake_run_git(
    calls,
    *,
    head=CURRENT,
    remote=LATEST,
    behind=3,
    messages=("feat: x", "fix: y", "init: z"),
    default_branch="main",
):
    """构造按 git 参数分发的假 run_git，记录全部调用到 calls"""

    async def fake_run_git(repo_path, *args, timeout=30):
        calls.append(args)
        if args[:1] == ("symbolic-ref",):
            return (f"refs/remotes/origin/{default_branch}", "", 0)
        if args[:1] == ("fetch",):
            return ("", "", 0)
        if args == ("rev-parse", "HEAD"):
            return (head, "", 0)
        if args == ("rev-parse", f"origin/{default_branch}"):
            return (remote, "", 0)
        if args[:2] == ("rev-list", "--count"):
            return (str(behind), "", 0)
        if args[:2] == ("log", "--oneline"):
            return ("\n".join(messages), "", 0)
        return ("", "", 1)

    return fake_run_git


# ---------- check_update: 三种路径 ----------


async def test_check_update_has_update(monkeypatch):
    calls: list = []
    monkeypatch.setattr(git_ops, "run_git", _make_fake_run_git(calls))
    monkeypatch.setattr(git_ops, "is_git_repo", lambda path: True)
    result = await VersionManager().check_update()
    assert result.has_update is True
    assert result.commits_behind == 3
    assert result.current_commit == CURRENT
    assert result.latest_commit == LATEST
    assert result.commit_messages == ["feat: x", "fix: y", "init: z"]
    assert result.checked_at
    # 完整调用序列: 分支探测 → fetch → HEAD → 远端 → rev-list → log
    assert ("fetch", "origin", "main") in calls


async def test_check_update_no_update(monkeypatch):
    calls: list = []
    monkeypatch.setattr(git_ops, "run_git", _make_fake_run_git(calls, behind=0, messages=()))
    monkeypatch.setattr(git_ops, "is_git_repo", lambda path: True)
    result = await VersionManager().check_update()
    assert result.has_update is False
    assert result.commits_behind == 0
    assert result.commit_messages == []
    # 无更新时不调用 log 查询
    assert not any(a[:2] == ("log", "--oneline") for a in calls)


async def test_check_update_not_git_repo(monkeypatch):
    calls: list = []
    async def fake_run_git(*args, **kwargs):
        calls.append(args)
        return ("", "", 1)
    monkeypatch.setattr(git_ops, "run_git", fake_run_git)
    monkeypatch.setattr(git_ops, "is_git_repo", lambda path: False)
    result = await VersionManager().check_update()
    assert result.has_update is False
    assert calls == []  # 非 git 仓库不发起任何 git 调用


# ---------- 分支名自动探测与回退 ----------


async def test_detect_default_branch_master(monkeypatch):
    """上游默认分支为 master 时，symbolic-ref 能正确探测到"""
    async def fake_run_git(repo_path, *args, timeout=30):
        return ("refs/remotes/origin/master", "", 0)
    monkeypatch.setattr(git_ops, "run_git", fake_run_git)
    assert await git_ops.detect_default_branch("whatever") == "master"


async def test_detect_default_branch_fallback(monkeypatch):
    """两种探测方式都失败时回退到默认分支 main"""
    calls: list = []
    async def fake_run_git(repo_path, *args, timeout=30):
        calls.append(args)
        return ("", "", 1)
    monkeypatch.setattr(git_ops, "run_git", fake_run_git)
    assert await git_ops.detect_default_branch("whatever") == git_ops.DEFAULT_BRANCH
    assert ("symbolic-ref", "refs/remotes/origin/HEAD") in calls
    assert ("rev-parse", "--abbrev-ref", "origin/HEAD") in calls


async def test_check_update_uses_detected_master_branch(monkeypatch):
    """check_update 对 master 分支仓库的 fetch/rev-parse 均使用探测到的分支名"""
    calls: list = []
    monkeypatch.setattr(git_ops, "run_git", _make_fake_run_git(calls, default_branch="master", behind=2, messages=("m1",)))
    monkeypatch.setattr(git_ops, "is_git_repo", lambda path: True)
    result = await VersionManager().check_update()
    assert ("fetch", "origin", "master") in calls
    assert ("rev-parse", "origin/master") in calls
    assert result.has_update is True


# ---------- pull_update ----------


async def test_pull_update_success(monkeypatch):
    calls: list = []
    async def fake_run_git(repo_path, *args, timeout=30):
        calls.append(args)
        if args[:1] == ("symbolic-ref",):
            return ("refs/remotes/origin/main", "", 0)
        if args[:1] == ("pull",):
            return ("Updating a1b2c3d4..e5f6a7b8\nFast-forward\n", "", 0)
        return ("", "", 1)
    monkeypatch.setattr(git_ops, "run_git", fake_run_git)
    monkeypatch.setattr(git_ops, "is_git_repo", lambda path: True)
    result = await VersionManager().pull_update()
    assert result["success"] is True
    assert result["pulled_commits"] == ["Updating a1b2c3d4..e5f6a7b8"]
    assert ("pull", "origin", "main") in calls


async def test_pull_update_failure(monkeypatch):
    async def fake_run_git(repo_path, *args, timeout=30):
        if args[:1] == ("symbolic-ref",):
            return ("refs/remotes/origin/main", "", 0)
        if args[:1] == ("pull",):
            return ("", "error: cannot pull", 1)
        return ("", "", 1)
    monkeypatch.setattr(git_ops, "run_git", fake_run_git)
    monkeypatch.setattr(git_ops, "is_git_repo", lambda path: True)
    result = await VersionManager().pull_update()
    assert result["success"] is False
    assert "cannot pull" in result["message"]
