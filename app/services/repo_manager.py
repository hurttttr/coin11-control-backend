"""Coin11-TB 仓库管理服务 — 自动 clone / 更新检测 / pull

git 子进程操作统一走 app.services.git_ops，避免与 version_manager 重复实现。
"""

import asyncio
import logging
import os
import subprocess
from datetime import datetime

from app.services import git_ops

logger = logging.getLogger(__name__)


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
        确保仓库目录存在（启动路径保持快速，不在此处做网络请求）：

        - 目录不存在 → git clone
        - 目录已存在且是 git 仓库 → 直接标记 ready 返回
        - 目录已存在但不是 git 仓库 → 报错

        注意: 这里**不做** git fetch / 更新检查（旧 docstring 声称会 fetch，
        实际从未执行）。更新检测与拉取由 check_update() / pull_update()
        显式完成（前端调用 /api/update/* 时触发），避免在 lifespan 启动时
        因网络阻塞拖慢整个后端。
        """
        if os.path.isdir(self.repo_path):
            if git_ops.is_git_repo(self.repo_path):
                # 仓库已存在
                self._status = "ready"
                logger.info("[RepoManager] 仓库已存在: %s", self.repo_path)
                return True
            else:
                # 目录存在但不是 git 仓库
                self._status = "error"
                self._error_msg = f"路径 {self.repo_path} 已存在但不是 Git 仓库"
                logger.warning("[RepoManager] %s", self._error_msg)
                return False

        # 目录不存在，clone
        self._status = "cloning"
        logger.info("[RepoManager] 正在克隆 coin11-tb 仓库 (%s) ...", self.repo_url)

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
            logger.info("[RepoManager] coin11-tb 仓库克隆成功: %s", self.repo_path)
        else:
            self._status = "error"
            self._error_msg = f"克隆失败: {err}"
            logger.warning("[RepoManager] %s", self._error_msg)
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

        if not git_ops.is_git_repo(self.repo_path):
            return result

        # 探测默认分支（main / master 自适应），fetch 允许失败
        branch = await git_ops.detect_default_branch(self.repo_path)
        await git_ops.fetch(self.repo_path, branch, timeout=30)

        current_commit = await git_ops.get_head_commit(self.repo_path)
        latest_commit = await git_ops.get_remote_commit(self.repo_path, branch) or current_commit

        result["current_commit"] = current_commit
        result["latest_commit"] = latest_commit

        if current_commit and latest_commit:
            behind = await git_ops.count_commits_behind(self.repo_path, current_commit, branch)
            result["commits_behind"] = behind
            result["has_update"] = behind > 0
            if behind > 0:
                result["commit_messages"] = await git_ops.list_commit_messages(
                    self.repo_path, current_commit, branch
                )

        self._last_check = datetime.now()
        return result

    async def pull_update(self) -> dict:
        """拉取远程更新（分支名自动探测，不再硬编码 main）"""
        if not git_ops.is_git_repo(self.repo_path):
            return {"success": False, "message": "不是 Git 仓库，无法拉取更新"}

        branch = await git_ops.detect_default_branch(self.repo_path)
        stdout, stderr, rc = await git_ops.pull(self.repo_path, branch, timeout=60)
        if rc != 0:
            return {
                "success": False,
                "message": stderr or stdout or "git pull 失败",
            }

        return {
            "success": True,
            "message": stdout or "已更新到最新版本",
            "pulled_commits": git_ops.parse_pulled_commits(stdout),
        }

    @property
    def status(self) -> str:
        return self._status

    @property
    def error_msg(self) -> str:
        return self._error_msg


# 全局单例（在 main.py 中初始化）
repo_manager: RepoManager | None = None
