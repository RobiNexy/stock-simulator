from typing import Annotated

from fastapi import Depends, HTTPException, Request

from app.auth import User, resolve_session
from app.config import get_settings
from app.db import Database, get_database


def get_current_session(
    request: Request,
    database: Annotated[Database, Depends(get_database)],
) -> tuple[User, str]:
    token = request.cookies.get(get_settings().session_cookie)
    resolved = resolve_session(database, token)
    if resolved is None:
        raise HTTPException(status_code=401, detail="请先登录")
    return resolved


def get_current_user(
    session: Annotated[tuple[User, str], Depends(get_current_session)],
) -> User:
    return session[0]


def require_admin(
    session: Annotated[tuple[User, str], Depends(get_current_session)],
) -> tuple[User, str]:
    user, csrf_token = session
    if not user.is_admin:
        raise HTTPException(status_code=403, detail="需要管理员权限")
    return user, csrf_token
