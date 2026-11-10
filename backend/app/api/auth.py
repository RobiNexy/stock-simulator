from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field

from app.auth import User, create_session, delete_session
from app.bootstrap import (
    authenticate,
    check_login_allowed,
    clear_login_failures,
    login_attempt_key,
    record_login_failure,
)
from app.config import get_settings
from app.db import Database, get_database
from app.deps import get_current_session

router = APIRouter(prefix="/api/auth", tags=["auth"])


class LoginIn(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=256)


class UserOut(BaseModel):
    id: str
    username: str
    is_admin: bool


@router.post("/login", response_model=UserOut)
def login(
    payload: LoginIn,
    request: Request,
    response: Response,
    database: Annotated[Database, Depends(get_database)],
) -> UserOut:
    key = login_attempt_key(request.client.host if request.client else "unknown", payload.username)
    if not check_login_allowed(key):
        raise HTTPException(status_code=429, detail="尝试次数过多，请稍后再试")
    user = authenticate(database, payload.username, payload.password)
    if user is None:
        record_login_failure(key)
        raise HTTPException(status_code=401, detail="用户名或密码错误")
    clear_login_failures(key)
    settings = get_settings()
    token, _ = create_session(database, user.id, settings.session_days)
    response.set_cookie(
        settings.session_cookie,
        token,
        max_age=settings.session_days * 24 * 3600,
        httponly=True,
        samesite="lax",
        secure=settings.secure_cookies,
        path="/",
    )
    return UserOut(id=user.id, username=user.username, is_admin=user.is_admin)


@router.post("/logout", status_code=204)
def logout(
    request: Request,
    response: Response,
    database: Annotated[Database, Depends(get_database)],
) -> Response:
    settings = get_settings()
    delete_session(database, request.cookies.get(settings.session_cookie))
    response.delete_cookie(settings.session_cookie, path="/")
    response.status_code = 204
    return response


@router.get("/me", response_model=UserOut)
def me(
    session: Annotated[tuple[User, str], Depends(get_current_session)],
) -> UserOut:
    user = session[0]
    return UserOut(id=user.id, username=user.username, is_admin=user.is_admin)
