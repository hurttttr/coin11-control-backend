"""
版本管理端点
"""
from fastapi import APIRouter

from app.schemas.device import UpdateCheckResult, UpdatePullResult
from app.services.version_manager import version_manager

router = APIRouter(prefix="/update", tags=["update"])


@router.get("/check", response_model=UpdateCheckResult)
async def check_update():
    """检查原项目是否有远程更新"""
    result = await version_manager.check_update()
    return {
        "has_update": result.has_update,
        "current_commit": result.current_commit,
        "latest_commit": result.latest_commit,
        "commits_behind": result.commits_behind,
        "commit_messages": result.commit_messages,
        "checked_at": result.checked_at,
    }


@router.post("/pull", response_model=UpdatePullResult)
async def pull_update():
    """拉取原项目更新"""
    return await version_manager.pull_update()


@router.get("/repo-status")
async def get_repo_status():
    """获取 coin11-tb 仓库状态"""
    from app.services.repo_manager import repo_manager
    if repo_manager is None:
        return {"status": "unknown", "error": "RepoManager 未初始化"}
    return {
        "status": repo_manager.status,
        "error": repo_manager.error_msg,
        "path": repo_manager.repo_path,
    }
