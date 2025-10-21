from pydantic import BaseModel, Field, ConfigDict
from typing import List, Optional
import uuid

# --- 权限定义 ---
class PermissionBase(BaseModel):
    name: str = Field(..., description="权限名称，例如：'users.create'")
    description: Optional[str] = Field(None, description="权限的详细描述")

class PermissionCreate(PermissionBase):
    pass

class PermissionSchema(PermissionBase):
    id: uuid.UUID
    model_config = ConfigDict(from_attributes=True)

# --- 角色定义 ---
class RoleBase(BaseModel):
    name: str = Field(..., description="角色名称，例如：'Administrator'")
    description: Optional[str] = Field(None, description="角色的详细描述")

class RoleCreate(RoleBase):
    pass

class RoleSchema(RoleBase):
    id: uuid.UUID
    permissions: List[PermissionSchema] = []
    model_config = ConfigDict(from_attributes=True)

# --- 关联操作 ---
class RolePermissionRequest(BaseModel):
    permission_name: str = Field(..., description="要关联的权限名称")

class UserRoleRequest(BaseModel):
    role_name: str = Field(..., description="要分配给用户的角色名称")

# --- 响应模型 ---
class UserRolesResponse(BaseModel):
    user_id: uuid.UUID
    username: str
    roles: List[RoleSchema]