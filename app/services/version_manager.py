"""
版本管理服务
负责 Git fetch/pull、更新检测、changelog
git 子进程逻辑统一走 app.services.git_ops（与 repo_manager 共享），
避免两份几乎逐行相同的实现。
"""

import logging
from datetime import datetime
from typing import Optional

from app.core.config import get_settings
from app.services import git_ops

logger = logging.getLogger(__name__)


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
        self._last_check: Optional[UpdateCheckResult] = None

    @property
    def settings(self):
        """每次访问都取当前配置单例（理由同 DeviceManager.settings）"""
        return get_settings()

    @property
    def _repo_path(self) -> str:
        """coin11-tb 仓库路径（延迟读取，随配置变化生效）"""
        return self.settings.coin11_tb_path_resolved

    async def check_update(self) -> "UpdateCheckResult":
        """
        检查原项目是否有远程更新
        1. 探测远端默认分支（origin/HEAD，失败回退 main）
        2. git fetch origin <branch>（允许失败，无网络时继续用本地信息）
        3. git rev-parse HEAD / origin/<branch>
        4. git rev-list --count（落后 commit 数）
        5. git log --oneline（commit 消息）
        """
        if not git_ops.is_git_repo(self._repo_path):
            return UpdateCheckResult(
                has_update=False,
                checked_at=datetime.now().isoformat(),
            )

        branch = await git_ops.detect_default_branch(self._repo_path)

        # fetch 可能失败（无网络等），不阻塞后续本地查询
        await git_ops.fetch(self._repo_path, branch, timeout=30)

        current_commit = await git_ops.get_head_commit(self._repo_path)
        latest_commit = await git_ops.get_remote_commit(self._repo_path, branch) or current_commit

        commits_behind = 0
        commit_messages: list[str] = []
        if current_commit and latest_commit:
            commits_behind = await git_ops.count_commits_behind(
                self._repo_path, current_commit, branch
            )
            if commits_behind > 0:
                commit_messages = await git_ops.list_commit_messages(
                    self._repo_path, current_commit, branch
                )

        result = UpdateCheckResult(
            has_update=commits_behind > 0,
            current_commit=current_commit,
            latest_commit=latest_commit,
            commits_behind=commits_behind,
            commit_messages=commit_messages,
            checked_at=datetime.now().isoformat(),
        )
        self._last_check = result
        return result

    async def pull_update(self) -> dict:
        """
        拉取原项目更新
        执行 git pull origin <branch>（分支名自动探测，不再硬编码 main）
        """
        if not git_ops.is_git_repo(self._repo_path):
            return {
                "success": False,
                "message": "不是 Git 仓库，无法拉取更新",
                "pulled_commits": [],
            }

        branch = await git_ops.detect_default_branch(self._repo_path)
        stdout, stderr, rc = await git_ops.pull(self._repo_path, branch, timeout=60)
        if rc != 0:
            return {
                "success": False,
                "message": stderr or stdout or "git pull 失败",
                "pulled_commits": [],
            }

        return {
            "success": True,
            "message": stdout.strip() or "已更新到最新版本",
            "pulled_commits": git_ops.parse_pulled_commits(stdout),
        }


# 全局单例
version_manager = VersionManager()
