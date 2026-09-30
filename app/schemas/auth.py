"""Request/response contracts for authentication and user management."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.core.permissions import Role


class LoginRequest(BaseModel):
    email: str = Field(min_length=3, max_length=320)
    password: str = Field(min_length=1, max_length=512)


class PasswordChangeRequest(BaseModel):
    current_password: str = Field(min_length=1, max_length=512)
    new_password: str = Field(min_length=10, max_length=512)


class UserResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    email: str
    display_name: str
    role: Role
    team_id: str | None
    is_active: bool
    created_at: datetime
    last_login_at: datetime | None


class CurrentUserResponse(UserResponse):
    """The signed-in user plus what they are allowed to do, so the interface can hide
    what it must not offer without hard-coding the role table a second time."""

    permissions: list[str] = Field(default_factory=list)
    team_name: str = ""


class UserCreateRequest(BaseModel):
    email: str = Field(min_length=3, max_length=320)
    password: str = Field(min_length=10, max_length=512)
    display_name: str = Field(default="", max_length=128)
    role: Role = Role.VIEWER
    team_id: str | None = None


class UserUpdateRequest(BaseModel):
    display_name: str | None = Field(default=None, max_length=128)
    role: Role | None = None
    team_id: str | None = None
    is_active: bool | None = None
    new_password: str | None = Field(default=None, min_length=10, max_length=512)


class UserListResponse(BaseModel):
    total: int
    items: list[UserResponse]


class TeamResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    department: str
    created_at: datetime


class TeamCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    department: str = Field(default="", max_length=128)


class SessionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    created_at: datetime
    last_seen_at: datetime
    expires_at: datetime
    user_agent: str
    ip: str
