import secrets
import threading
import time

from app.auth import User, create_user, get_user_by_username, verify_password
from app.config import Settings
from app.db import Database

_LEGACY_IMPORT_MARKER = ".legacy_imported"


def ensure_admin(database: Database, settings: Settings) -> tuple[User, bool]:
    with database.connect() as connection:
        row = connection.execute(
            "SELECT id, username, is_admin FROM users WHERE is_admin = 1 LIMIT 1"
        ).fetchone()
    if row is not None:
        return User(row["id"], row["username"], True), False

    password = settings.admin_password
    generated = password is None
    if generated:
        password = secrets.token_urlsafe(12)
    elif len(password) < 8:
        raise RuntimeError("ADMIN_PASSWORD 至少需要 8 位")
    user = create_user(
        database, settings.admin_username, password, is_admin=True
    )
    if generated:
        print(
            "未设置 ADMIN_PASSWORD，已生成管理员账号：\n"
            f"  用户名：{settings.admin_username}\n"
            f"  密码：{password}\n"
            "请登录 /admin 后重置密码，或设置 ADMIN_PASSWORD 后重新部署。",
            flush=True,
        )
    return user, True


def import_legacy_data(
    database: Database, settings: Settings, admin_id: str
) -> None:
    """把旧版单账户 JSON 状态导入管理员账号，可重复执行。"""
    marker = settings.data_dir / _LEGACY_IMPORT_MARKER
    if marker.exists():
        return

    imported = False
    account_path = settings.data_dir / "paper_account.json"
    if account_path.exists():
        payload = account_path.read_text(encoding="utf-8")
        with database.connect() as connection:
            cursor = connection.execute(
                "INSERT OR IGNORE INTO paper_accounts (user_id, payload, updated_at)"
                " VALUES (?, ?, datetime('now'))",
                (admin_id, payload),
            )
            imported = imported or cursor.rowcount > 0

    indices_dir = settings.data_dir / "indices"
    if indices_dir.is_dir():
        import json

        for path in sorted(indices_dir.glob("*.json")):
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
                with database.connect() as connection:
                    cursor = connection.execute(
                        "INSERT OR IGNORE INTO custom_indices"
                        " (id, user_id, name, base_date, base_point, components, created_at)"
                        " VALUES (?, ?, ?, ?, ?, ?, datetime('now'))",
                        (
                            payload["id"],
                            admin_id,
                            payload["name"],
                            payload["base_date"],
                            payload["base_point"],
                            json.dumps(
                                payload["components"], ensure_ascii=False
                            ),
                        ),
                    )
                    imported = imported or cursor.rowcount > 0
            except (KeyError, ValueError, TypeError):
                continue

    if imported:
        print("已把旧版本地账户与自定义指数导入管理员账号。", flush=True)
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text("ok\n", encoding="utf-8")


_FAILURES: dict[str, list[float]] = {}
_FAILURES_LOCK = threading.Lock()
FAILURE_WINDOW_SECONDS = 15 * 60
MAX_FAILURES = 5


def login_attempt_key(client_host: str, username: str) -> str:
    return f"{client_host}:{username.strip().lower()}"


def check_login_allowed(key: str) -> bool:
    now = time.monotonic()
    with _FAILURES_LOCK:
        attempts = [item for item in _FAILURES.get(key, []) if now - item < FAILURE_WINDOW_SECONDS]
        _FAILURES[key] = attempts
        return len(attempts) < MAX_FAILURES


def record_login_failure(key: str) -> None:
    now = time.monotonic()
    with _FAILURES_LOCK:
        attempts = [item for item in _FAILURES.get(key, []) if now - item < FAILURE_WINDOW_SECONDS]
        attempts.append(now)
        _FAILURES[key] = attempts


def clear_login_failures(key: str) -> None:
    with _FAILURES_LOCK:
        _FAILURES.pop(key, None)


def authenticate(
    database: Database, username: str, password: str
) -> User | None:
    row = get_user_by_username(database, username)
    if row is None or not row["is_active"]:
        return None
    if not verify_password(password, row["password_hash"]):
        return None
    return User(row["id"], row["username"], bool(row["is_admin"]))
