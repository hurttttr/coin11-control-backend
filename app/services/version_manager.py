"""
版本管理服务
负责 Git fetch/pull、更新检测、changelog
使用 asyncio.to_thread + subprocess.run 避免事件循环阻塞
（Windows Python 3.14+ 兼容方案）
"""

import asyncio
import os
import subprocess
from datetime import datetime
from typing import Optional

from app.core.config import get_settings


class UpdateCheckResult:
    """版本检测结果 (数据类)"""

    def __init__(
        self,
        has_update: bool = False,
        current_commit: str = "",
        latest_commit: str = "",
        commits_behind: int = 0,
        commit_messages: list[str] | None = None,
        checked_at: str | None = None,
    ):
        self.has_update = has_update
        self.current_commit = current_commit
        self.latest_commit = latest_commit
        self.commits_behind = commits_behind
        self.commit_messages = commit_messages or []
        self.checked_at = checked_at or datetime.now().isoformat()


class VersionManager:
    """版本管理服务 — Git 更新检测与拉取"""

    def __init__(self):
        self.settings = get_settings()
        self._repo_path = self.settings.COIN11_TB_PATH
        self._last_check: Optional[UpdateCheckResult] = None

    async def _run_git(self, *args, timeout: int = 30) -> tuple[str, str, int]:
        """执行 git 命令，返回 (stdout, stderr, returncode)"""
        cmd = ["git"] + list(args)

        def _run():
            try:
                result = subprocess.run(
                    cmd,
                    cwd=self._repo_path,
                    capture_output=True,
                    timeout=timeout,
                )
                return (
                    result.stdout.decode("utf-8", errors="replace").strip(),
                    result.stderr.decode("utf-8", errors="replace").strip(),
                    result.returncode,
                )
            except subprocess.TimeoutExpired:
                return "", "timeout", -1

        return await asyncio.to_thread(_run)

    async def check_update(self) -> "UpdateCheckResult":
        """
        检查原项目是否有远程更新
        1. git fetch origin main (后台)
        2. git rev-parse HEAD (本地 commit)
        3. git rev-parse origin/main (远程 commit)
        4. git rev-list --count HEAD..origin/main (落后 commit 数)
        5. git log --oneline HEAD..origin/main (commit 消息)
        """
        # 检查是否为 git 仓库
        git_dir = os.path.join(self._repo_path, ".git")
        if not os.path.isdir(git_dir):
            return UpdateCheckResult(
                has_update=False,
                checked_at=datetime.now().isoformat(),
            )

        # 1. git fetch (不阻塞，允许失败)
        _, stderr, rc = await self._run_git("fetch", "origin", "main", timeout=30)
        if rc != 0:
            pass  # fetch 可能失败（无网络等），继续获取本地信息

        # 2. 获取本地 HEAD
        stdout_local, _, rc_local = await self._run_git("rev-parse", "HEAD")
        current_commit = stdout_local if rc_local == 0 else ""

        # 3. 获取远程 HEAD
        stdout_remote, _, rc_remote = await self._run_git("rev-parse", "origin/main")
        latest_commit = stdout_remote if rc_remote == 0 else current_commit

        # 4. 计算落后 commit 数 + commit 消息
        commits_behind = 0
        commit_messages: list[str] = []
        if rc_remote == 0 and current_commit and latest_commit:
            stdout_count, _, rc_count = await self._run_git(
                "rev-list", "--count", f"{current_commit}..origin/main"
            )
            if rc_count == 0 and stdout_count:
                commits_behind = int(stdout_count)

            if commits_behind > 0:
                stdout_log, _, rc_log = await self._run_git(
                    "log", "--oneline", f"{current_commit}..origin/main"
                )
                if rc_log == 0 and stdout_log:
                    commit_messages = stdout_log.splitlines()

        result = UpdateCheckResult(
            has_update=commits_behind > 0,
            current_commit=current_commit,
            latest_commit=latest_commit or current_commit,
            commits_behind=commits_behind,
            commit_messages=commit_messages,
            checked_at=datetime.now().isoformat(),
        )
        self._last_check = result
        return result

    async def pull_update(self) -> dict:
        """
        拉取原项目更新
        执行 git pull origin main
        """
        git_dir = os.path.join(self._repo_path, ".git")
        if not os.path.isdir(git_dir):
            return {
                "success": False,
                "message": "不是 Git 仓库，无法拉取更新",
                "pulled_commits": [],
            }

        stdout, stderr, rc = await self._run_git("pull", "origin", "main", timeout=60)
        if rc != 0:
            return {
                "success": False,
                "message": stderr or stdout or "git pull 失败",
                "pulled_commits": [],
            }

        pulled_commits = []
        for line in stdout.splitlines():
            line = line.strip()
            if line.startswith("commit ") or line.startswith("Updating "):
                pulled_commits.append(line)

        return {
            "success": True,
            "message": stdout.strip() or "已更新到最新版本",
            "pulled_commits": pulled_commits,
        }


# 全局单例
version_manager = VersionManager()
