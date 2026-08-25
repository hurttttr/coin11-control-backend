"""Git 操作共享层 — repo_manager 与 version_manager 复用的 git 子进程封装

统一负责：分支探测、fetch、rev-parse、rev-list --count、log --oneline、pull。
两个管理器不再各自维护一份几乎逐行相同的 git 调用代码。

所有函数均通过 asyncio.to_thread 执行子进程，避免阻塞事件循环
（Windows Python 3.14+ 兼容方案：create_subprocess_exec 可能触发 NotImplementedError）。
"""

import asyncio
import logging
import os
import subprocess
from typing import Optional

logger = logging.getLogger(__name__)

# 分支探测失败时的回退分支（历史代码硬编码 "main"，此处保留为默认值）
DEFAULT_BRANCH = "main"


async def run_git(
    repo_path: str, *args: str, timeout: int = 30
) -> tuple[str, str, int]:
    """执行 git 命令，返回 (stdout, stderr, returncode)。

    - 超时返回 ("", "timeout", -1)
    - git 不在 PATH 返回 ("", "git 不可用", -1)
    - 其余 OSError 返回 ("", str(e), -1) 并记录 warning
    """
    cmd = ["git", *args]

    def _run():
        try:
            result = subprocess.run(
                cmd,
                cwd=repo_path,
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
        except FileNotFoundError:
            return "", "git 不可用", -1
        except OSError as e:
            logger.warning("git 命令执行失败: %s (%s)", cmd, e)
            return "", str(e), -1

    return await asyncio.to_thread(_run)


def is_git_repo(repo_path: str) -> bool:
    """目录是否为 git 仓库（存在 .git 目录即可，语义与原实现保持一致）"""
    return os.path.isdir(os.path.join(repo_path, ".git"))


async def detect_default_branch(repo_path: str) -> str:
    """探测远端默认分支，失败时回退 DEFAULT_BRANCH。

    上游仓库（coin11-tb）默认分支可能是 main 或 master，
    不再像旧实现那样硬编码 "main"：
    1. git symbolic-ref refs/remotes/origin/HEAD（最可靠，clone 后即存在）
    2. git rev-parse --abbrev-ref origin/HEAD
    3. 都失败 → DEFAULT_BRANCH
    """
    # 1. 符号引用，输出形如 refs/remotes/origin/main
    stdout, _, rc = await run_git(
        repo_path, "symbolic-ref", "refs/remotes/origin/HEAD", timeout=10
    )
    if rc == 0 and stdout:
        branch = stdout.removeprefix("refs/remotes/origin/").strip("/")
        if branch:
            return branch

    # 2. 短格式探测，输出形如 origin/main
    stdout, _, rc = await run_git(
        repo_path, "rev-parse", "--abbrev-ref", "origin/HEAD", timeout=10
    )
    if rc == 0 and stdout and stdout != "origin/HEAD":
        branch = stdout.rsplit("/", 1)[-1]
        if branch:
            return branch

    # 3. 回退
    return DEFAULT_BRANCH


async def fetch(
    repo_path: str, branch: Optional[str] = None, timeout: int = 30
) -> tuple[str, str, int]:
    """git fetch origin [branch] —— 失败不抛异常，由调用方自行判断"""
    if branch:
        return await run_git(repo_path, "fetch", "origin", branch, timeout=timeout)
    return await run_git(repo_path, "fetch", "origin", timeout=timeout)


async def get_head_commit(repo_path: str) -> str:
    """获取本地 HEAD commit；失败返回空串"""
    stdout, _, rc = await run_git(repo_path, "rev-parse", "HEAD")
    return stdout if rc == 0 else ""


async def get_remote_commit(repo_path: str, branch: str) -> str:
    """获取远端分支最新 commit；失败返回空串"""
    stdout, _, rc = await run_git(repo_path, "rev-parse", f"origin/{branch}")
    return stdout if rc == 0 else ""


async def count_commits_behind(repo_path: str, current: str, branch: str) -> int:
    """计算本地 HEAD 落后远端分支的 commit 数；失败返回 0"""
    stdout, _, rc = await run_git(
        repo_path, "rev-list", "--count", f"{current}..origin/{branch}"
    )
    if rc == 0 and stdout.isdigit():
        return int(stdout)
    return 0


async def list_commit_messages(repo_path: str, current: str, branch: str) -> list[str]:
    """列出本地 HEAD 与远端分支之间的 oneline 提交信息；失败返回空列表"""
    stdout, _, rc = await run_git(
        repo_path, "log", "--oneline", f"{current}..origin/{branch}"
    )
    if rc == 0 and stdout:
        return stdout.splitlines()
    return []


async def pull(repo_path: str, branch: str, timeout: int = 60) -> tuple[str, str, int]:
    """git pull origin <branch>"""
    return await run_git(repo_path, "pull", "origin", branch, timeout=timeout)


def parse_pulled_commits(stdout: str) -> list[str]:
    """从 git pull 输出中提取提交描述行（commit xxx / Updating xxx）"""
    pulled = []
    for line in stdout.splitlines():
        line = line.strip()
        if line.startswith("commit ") or line.startswith("Updating "):
            pulled.append(line)
    return pulled
