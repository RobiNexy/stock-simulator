import html
import sqlite3
from typing import Annotated
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response

from app.auth import (
    User,
    create_session,
    delete_session,
    delete_user,
    resolve_session,
    set_user_active,
    set_user_password,
)
from app.bootstrap import (
    authenticate,
    check_login_allowed,
    clear_login_failures,
    login_attempt_key,
    record_login_failure,
)
from app.config import get_settings
from app.db import Database, get_database
from app.deps import require_admin

router = APIRouter(prefix="/admin", tags=["admin"])


def _redirect(message: str | None = None, error: str | None = None) -> RedirectResponse:
    query = {}
    if message:
        query["msg"] = message
    if error:
        query["err"] = error
    location = "/admin" + (f"?{urlencode(query)}" if query else "")
    return RedirectResponse(location, status_code=303)


def _page(title: str, body: str) -> str:
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{html.escape(title)} · 股票模拟器管理</title>
<style>
:root {{ color-scheme: light; --ink:#243042; --muted:#6b778c; --line:#dfe4ee;
  --card:#ffffff; --bg:#f3f5fb; --accent:#6c4ee3; --danger:#b42338; --ok:#1f7a4d; }}
* {{ box-sizing: border-box; }}
body {{ margin:0; font-family: ui-sans-serif, system-ui, "PingFang SC", "Microsoft YaHei", sans-serif;
  background: var(--bg); color: var(--ink); }}
main {{ max-width: 980px; margin: 0 auto; padding: 32px 20px 64px; }}
h1 {{ font-size: 24px; margin: 0 0 6px; }}
h2 {{ font-size: 17px; margin: 0 0 14px; }}
p.muted {{ color: var(--muted); margin: 0 0 24px; }}
.card {{ background: var(--card); border: 1px solid var(--line); border-radius: 14px;
  padding: 22px; margin-bottom: 18px; box-shadow: 0 8px 24px rgba(36,48,66,.05); }}
label {{ display:block; font-size: 13px; color: var(--muted); margin: 0 0 4px; }}
input[type=text], input[type=password] {{ width:100%; padding:9px 11px; border:1px solid var(--line);
  border-radius: 8px; font: inherit; background:#fbfcff; }}
.row {{ display:flex; gap:12px; flex-wrap:wrap; align-items:end; }}
.row > div {{ flex:1 1 180px; }}
button {{ font: inherit; border:0; border-radius:8px; padding:9px 14px; cursor:pointer;
  background:var(--accent); color:#fff; }}
button.secondary {{ background:#eef0f8; color:var(--ink); }}
button.danger {{ background:#fbecef; color:var(--danger); }}
button:disabled {{ opacity:.5; cursor:not-allowed; }}
table {{ width:100%; border-collapse: collapse; font-size:14px; }}
th, td {{ text-align:left; padding:10px 8px; border-bottom:1px solid var(--line); vertical-align:middle; }}
th {{ color:var(--muted); font-weight:600; font-size:12px; text-transform:uppercase; letter-spacing:.04em; }}
.tag {{ display:inline-block; padding:1px 8px; border-radius:999px; font-size:12px; background:#eef0f8; }}
.tag.admin {{ background:#ede8ff; color:var(--accent); }}
.tag.off {{ background:#fbecef; color:var(--danger); }}
.tag.on {{ background:#e8f7ef; color:var(--ok); }}
.actions {{ display:flex; gap:6px; flex-wrap:wrap; }}
.actions form {{ display:flex; gap:6px; align-items:center; }}
.actions input[type=password] {{ width:130px; padding:6px 8px; }}
.msg {{ padding:10px 14px; border-radius:8px; margin-bottom:16px; background:#e8f7ef; color:var(--ok); }}
.err {{ padding:10px 14px; border-radius:8px; margin-bottom:16px; background:#fbecef; color:var(--danger); }}
.hint {{ color:var(--muted); font-size:12px; margin-top:8px; }}
a {{ color: var(--accent); }}
</style>
</head>
<body><main>{body}</main></body></html>"""


def _login_page(error: str | None = None) -> str:
    error_html = f'<div class="err">{html.escape(error)}</div>' if error else ""
    return _page(
        "登录",
        f"""
<h1>股票模拟器 · 管理后台</h1>
<p class="muted">仅管理员可登录。普通用户请回到主应用登录。</p>
<div class="card">
  {error_html}
  <form method="post" action="/admin/login">
    <div class="row">
      <div><label for="username">用户名</label>
        <input id="username" name="username" type="text" autocomplete="username" required autofocus></div>
      <div><label for="password">密码</label>
        <input id="password" name="password" type="password" autocomplete="current-password" required></div>
      <div style="flex:0 0 auto"><button type="submit">登录</button></div>
    </div>
  </form>
</div>
<p class="hint">忘记管理员密码时，可用 ADMIN_PASSWORD 环境变量重新初始化空数据库，或直接修改数据库。</p>
""",
    )


def _dashboard(
    user: User,
    csrf_token: str,
    users: list[sqlite3.Row],
    message: str | None,
    error: str | None,
) -> str:
    rows = []
    for row in users:
        tags = []
        if row["is_admin"]:
            tags.append('<span class="tag admin">管理员</span>')
        tags.append(
            '<span class="tag on">启用</span>'
            if row["is_active"]
            else '<span class="tag off">停用</span>'
        )
        usage = f'{row["index_count"]} 个指数 · {"有模拟账户" if row["has_account"] else "无模拟账户"}'
        is_self = row["id"] == user.id
        actions = []
        if not is_self:
            toggle_label = "停用" if row["is_active"] else "启用"
            actions.append(
                f"""<form method="post" action="/admin/users/{row['id']}/toggle">
                  <input type="hidden" name="csrf" value="{html.escape(csrf_token)}">
                  <button class="secondary" type="submit">{toggle_label}</button></form>"""
            )
        actions.append(
            f"""<form method="post" action="/admin/users/{row['id']}/password">
              <input type="hidden" name="csrf" value="{html.escape(csrf_token)}">
              <input type="password" name="password" minlength="8" placeholder="新密码" required>
              <button class="secondary" type="submit">重置</button></form>"""
        )
        if not is_self:
            actions.append(
                f"""<form method="post" action="/admin/users/{row['id']}/delete"
                  onsubmit="return confirm('删除 {html.escape(row['username'])} 及其全部账户数据？此操作不可撤销。')">
                  <input type="hidden" name="csrf" value="{html.escape(csrf_token)}">
                  <button class="danger" type="submit">删除</button></form>"""
            )
        rows.append(
            f"""<tr>
              <td><strong>{html.escape(row['username'])}</strong><div class="hint">{usage}</div></td>
              <td>{''.join(tags)}</td>
              <td>{html.escape(row['created_at'][:10])}</td>
              <td><div class="actions">{''.join(actions)}</div></td>
            </tr>"""
        )
    message_html = f'<div class="msg">{html.escape(message)}</div>' if message else ""
    error_html = f'<div class="err">{html.escape(error)}</div>' if error else ""
    return _page(
        "管理后台",
        f"""
<div class="row" style="align-items:center; margin-bottom:18px">
  <div style="flex:1"><h1>账号管理</h1>
    <p class="muted">当前登录：{html.escape(user.username)} · 不开放注册，请在此手动创建账号。</p></div>
  <form method="post" action="/admin/logout">
    <button class="secondary" type="submit">退出登录</button></form>
</div>
{message_html}{error_html}
<div class="card">
  <h2>新建账号</h2>
  <form method="post" action="/admin/users">
    <input type="hidden" name="csrf" value="{html.escape(csrf_token)}">
    <div class="row">
      <div><label for="new-username">用户名</label>
        <input id="new-username" name="username" type="text" pattern="[A-Za-z0-9_.-]{{3,32}}"
          title="3 至 32 位字母、数字、下划线、点或连字符" required></div>
      <div><label for="new-password">初始密码（至少 8 位）</label>
        <input id="new-password" name="password" type="password" minlength="8" required></div>
      <div style="flex:0 0 auto">
        <label><input type="checkbox" name="is_admin" value="1"> 管理员</label>
        <button type="submit">创建</button></div>
    </div>
    <p class="hint">新账号创建后即可在主应用登录；请通过安全渠道把密码告知对方。</p>
  </form>
</div>
<div class="card">
  <h2>已有账号（{len(users)}）</h2>
  <table>
    <thead><tr><th>用户</th><th>状态</th><th>创建日期</th><th>操作</th></tr></thead>
    <tbody>{''.join(rows)}</tbody>
  </table>
</div>
<p class="hint"><a href="/">返回模拟器</a></p>
""",
    )


def _load_users(database: Database) -> list[sqlite3.Row]:
    with database.connect() as connection:
        return list(
            connection.execute(
                "SELECT u.id, u.username, u.is_admin, u.is_active, u.created_at,"
                " (SELECT COUNT(*) FROM custom_indices c WHERE c.user_id = u.id) AS index_count,"
                " EXISTS(SELECT 1 FROM paper_accounts p WHERE p.user_id = u.id) AS has_account"
                " FROM users u ORDER BY u.created_at, u.username"
            )
        )


@router.get("", response_class=HTMLResponse)
def admin_page(
    request: Request,
    database: Annotated[Database, Depends(get_database)],
    message: str | None = None,
    error: str | None = None,
) -> HTMLResponse:
    token = request.cookies.get(get_settings().session_cookie)
    session = resolve_session(database, token)
    if session is None:
        return HTMLResponse(_login_page(error))
    user, csrf_token = session
    if not user.is_admin:
        return HTMLResponse(_login_page("该账号不是管理员"), status_code=403)
    return HTMLResponse(
        _dashboard(user, csrf_token, _load_users(database), message, error)
    )


@router.post("/login")
def admin_login(
    request: Request,
    username: Annotated[str, Form()],
    password: Annotated[str, Form()],
    database: Annotated[Database, Depends(get_database)],
) -> Response:
    key = login_attempt_key(
        request.client.host if request.client else "unknown", username
    )
    if not check_login_allowed(key):
        return _redirect(error="尝试次数过多，请稍后再试")
    user = authenticate(database, username, password)
    if user is None or not user.is_admin:
        record_login_failure(key)
        return _redirect(error="用户名或密码错误，或该账号不是管理员")
    clear_login_failures(key)
    settings = get_settings()
    token, _ = create_session(database, user.id, settings.session_days)
    response = _redirect(message="登录成功")
    response.set_cookie(
        settings.session_cookie,
        token,
        max_age=settings.session_days * 24 * 3600,
        httponly=True,
        samesite="lax",
        secure=settings.secure_cookies,
        path="/",
    )
    return response


@router.post("/logout")
def admin_logout(
    request: Request,
    database: Annotated[Database, Depends(get_database)],
) -> Response:
    settings = get_settings()
    delete_session(database, request.cookies.get(settings.session_cookie))
    response = _redirect(message="已退出登录")
    response.delete_cookie(settings.session_cookie, path="/")
    return response


def _verify_csrf(expected: str, provided: str) -> None:
    import hmac

    if not hmac.compare_digest(expected, provided):
        raise ValueError("表单已过期，请刷新页面后重试")


@router.post("/users")
def admin_create_user(
    admin: Annotated[tuple[User, str], Depends(require_admin)],
    username: Annotated[str, Form()],
    password: Annotated[str, Form()],
    database: Annotated[Database, Depends(get_database)],
    csrf: Annotated[str, Form()] = "",
    is_admin: Annotated[str | None, Form()] = None,
) -> Response:
    import re

    from app.auth import create_user

    _user, csrf_token = admin
    try:
        _verify_csrf(csrf_token, csrf)
        clean = username.strip()
        if not re.fullmatch(r"[A-Za-z0-9_.-]{3,32}", clean):
            raise ValueError("用户名须为 3 至 32 位字母、数字、下划线、点或连字符")
        if len(password) < 8:
            raise ValueError("密码至少 8 位")
        create_user(database, clean, password, is_admin=is_admin == "1")
    except sqlite3.IntegrityError:
        return _redirect(error="用户名已存在")
    except ValueError as exc:
        return _redirect(error=str(exc))
    return _redirect(message=f"已创建账号 {clean}")


@router.post("/users/{user_id}/toggle")
def admin_toggle_user(
    user_id: str,
    admin: Annotated[tuple[User, str], Depends(require_admin)],
    database: Annotated[Database, Depends(get_database)],
    csrf: Annotated[str, Form()] = "",
) -> Response:
    from app.auth import get_user_by_id

    current, csrf_token = admin
    try:
        _verify_csrf(csrf_token, csrf)
    except ValueError as exc:
        return _redirect(error=str(exc))
    target = get_user_by_id(database, user_id)
    if target is None:
        return _redirect(error="账号不存在")
    if target["id"] == current.id:
        return _redirect(error="不能停用当前登录的管理员账号")
    set_user_active(database, user_id, not bool(target["is_active"]))
    return _redirect(message=("已启用 " if not target["is_active"] else "已停用 ") + target["username"])


@router.post("/users/{user_id}/password")
def admin_reset_password(
    user_id: str,
    admin: Annotated[tuple[User, str], Depends(require_admin)],
    password: Annotated[str, Form()],
    database: Annotated[Database, Depends(get_database)],
    csrf: Annotated[str, Form()] = "",
) -> Response:
    from app.auth import get_user_by_id

    _current, csrf_token = admin
    try:
        _verify_csrf(csrf_token, csrf)
        if len(password) < 8:
            raise ValueError("密码至少 8 位")
    except ValueError as exc:
        return _redirect(error=str(exc))
    target = get_user_by_id(database, user_id)
    if target is None:
        return _redirect(error="账号不存在")
    set_user_password(database, user_id, password)
    return _redirect(message=f"已重置 {target['username']} 的密码，该账号需要重新登录")


@router.post("/users/{user_id}/delete")
def admin_delete_user(
    user_id: str,
    admin: Annotated[tuple[User, str], Depends(require_admin)],
    database: Annotated[Database, Depends(get_database)],
    csrf: Annotated[str, Form()] = "",
) -> Response:
    from app.auth import get_user_by_id

    current, csrf_token = admin
    try:
        _verify_csrf(csrf_token, csrf)
    except ValueError as exc:
        return _redirect(error=str(exc))
    target = get_user_by_id(database, user_id)
    if target is None:
        return _redirect(error="账号不存在")
    if target["id"] == current.id:
        return _redirect(error="不能删除当前登录的管理员账号")
    delete_user(database, user_id)
    return _redirect(message=f"已删除账号 {target['username']}")
