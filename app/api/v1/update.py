"""
版本管理端点
"""
from fastapi import APIRouter

from app.services.version_manager import version_manager

router = APIRouter(prefix="/update", tags=["update"])


@router.get("/check")
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


@router.post("/pull")
async def pull_update():
    """拉取原项目更新"""
    result = await version_manager.pull_update()
    return result
