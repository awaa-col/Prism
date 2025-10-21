from fastapi import APIRouter, Depends
from app.api.auth_deps import require_permission
from app.db.models import User
from app.utils.responses import APIResponse

router = APIRouter()

# 这个端点需要 "read:demo" 权限
@router.get("/protected-resource", summary="受保护的资源示例")
async def get_protected_resource(
    current_user: User = Depends(require_permission("read:demo"))
):
    """
    这是一个受保护的端点。
    只有拥有 `read:demo` 权限的用户才能访问。
    管理员默认拥有所有权限。
    """
    return APIResponse.success(
        message=f"Hello, {current_user.username}! You have access to this protected resource.",
        data={"user_id": str(current_user.id), "required_permission": "read:demo"}
    )