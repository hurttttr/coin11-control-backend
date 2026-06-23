"""Coin11-TB 仓库管理服务 — 自动 clone / 更新检测 / pull"""

import asyncio
import os
import subprocess
from datetime import datetime


class RepoManager:
    """管理 coin11-tb 仓库的克隆和更新"""

    def __init__(self, repo_path: str, repo_url: str):
        self.repo_path = repo_path
        self.repo_url = repo_url
        self._status = "unknown"  # unknown | cloning | ready | error
        self._error_msg = ""
        self._last_check: datetime | None = None

    async def ensure_repo(self) -> bool:
        """
        确保仓库存在且是最新代码。
        - 如果目录不存在 → git clone
        - 如果目录存在且是 git 仓库 → git fetch + 检查更新
        - 如果目录存在但不是 git 仓库 → 报错
        """
        if os.path.isdir(self.repo_path):
            git_dir = os.path.join(self.repo_path, ".git")
            if os.path.isdir(git_dir):
                # 仓库已存在，git fetch 检查更新
                self._status = "ready"
                print(f"[RepoManager] 仓库已存在: {self.repo_path}")
                return True
            else:
                # 目录存在但不是 git 仓库
                self._status = "error"
                self._error_msg = f"路径 {self.repo_path} 已存在但不是 Git 仓库"
                print(f"[RepoManager] {self._error_msg}")
                return False

        # 目录不存在，clone
        self._status = "cloning"
        print(f"[RepoManager] 正在克隆 coin11-tb 仓库 ({self.repo_url}) ...")

        def _clone():
            result = subprocess.run(
                ["git", "clone", self.repo_url, self.repo_path],
                capture_output=True,
                text=True,
                timeout=120,
            )
            return result.returncode == 0, result.stderr

        success, err = await asyncio.to_thread(_clone)
        if success:
            self._status = "ready"
            print(f"[RepoManager] coin11-tb 仓库克隆成功: {self.repo_path}")
        else:
            self._status = "error"
            self._error_msg = f"克隆失败: {err}"
            print(f"[RepoManager] {self._error_msg}")
        return success

    async def check_update(self) -> dict:
        """
        检查远程是否有更新。
        返回: {"has_update": bool, "current_commit": str, "latest_commit": str, "commits_behind": int, "commit_messages": list[str]}
        """
        result = {
            "has_update": False,
            "current_commit": "",
            "latest_commit": "",
            "commits_behind": 0,
            "commit_messages": [],
        }

        git_dir = os.path.join(self.repo_path, ".git")
        if not os.path.isdir(git_dir):
            return result

        def _fetch():
            subprocess.run(
                ["git", "fetch", "origin", "main"],
                cwd=self.repo_path,
                capture_output=True,
                text=True,
                timeout=30,
            )

        await asyncio.to_thread(_fetch)

        def _run_git(*args):
            cmd = ["git"] + list(args)
            r = subprocess.run(cmd, cwd=self.repo_path, capture_output=True, text=True, timeout=30)
            return r.stdout.strip(), r.stderr.strip(), r.returncode

        # 获取本地 HEAD
        stdout_local, _, rc_local = await asyncio.to_thread(_run_git, "rev-parse", "HEAD")
        current_commit = stdout_local if rc_local == 0 else ""

        # 获取远程 HEAD
        stdout_remote, _, rc_remote = await asyncio.to_thread(_run_git, "rev-parse", "origin/main")
        latest_commit = stdout_remote if rc_remote == 0 else current_commit

        result["current_commit"] = current_commit
        result["latest_commit"] = latest_commit

        # 计算落后 commit 数
        if rc_remote == 0 and current_commit and latest_commit:
            stdout_count, _, rc_count = await asyncio.to_thread(
                _run_git, "rev-list", "--count", f"{current_commit}..origin/main"
            )
            if rc_count == 0 and stdout_count:
                result["commits_behind"] = int(stdout_count)
                result["has_update"] = int(stdout_count) > 0

            if result["has_update"]:
                stdout_log, _, rc_log = await asyncio.to_thread(
                    _run_git, "log", "--oneline", f"{current_commit}..origin/main"
                )
                if rc_log == 0 and stdout_log:
                    result["commit_messages"] = stdout_log.splitlines()

        self._last_check = datetime.now()
        return result

    async def pull_update(self) -> dict:
        """拉取远程更新"""
        git_dir = os.path.join(self.repo_path, ".git")
        if not os.path.isdir(git_dir):
            return {"success": False, "message": "不是 Git 仓库，无法拉取更新"}

        def _pull():
            result = subprocess.run(
                ["git", "pull", "origin", "main"],
                cwd=self.repo_path,
                capture_output=True,
                text=True,
                timeout=60,
            )
            return result.stdout.strip(), result.stderr.strip(), result.returncode

        stdout, stderr, rc = await asyncio.to_thread(_pull)
        if rc != 0:
            return {
                "success": False,
                "message": stderr or stdout or "git pull 失败",
            }

        pulled_commits = []
        for line in stdout.splitlines():
            line = line.strip()
            if line.startswith("commit ") or line.startswith("Updating "):
                pulled_commits.append(line)

        return {
            "success": True,
            "message": stdout or "已更新到最新版本",
            "pulled_commits": pulled_commits,
        }

    @property
    def status(self) -> str:
        return self._status

    @property
    def error_msg(self) -> str:
        return self._error_msg


# 全局单例（在 main.py 中初始化）
repo_manager: RepoManager | None = None
