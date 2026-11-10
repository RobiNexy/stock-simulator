from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, Request
from fastapi.staticfiles import StaticFiles

from app.admin import router as admin_router
from app.api.auth import router as auth_router
from app.api.bars import router as bars_router
from app.api.indices import router as indices_router
from app.api.simulator import router as simulator_router
from app.api.stocks import router as stocks_router
from app.bootstrap import ensure_admin, import_legacy_data
from app.config import get_settings
from app.data.seed_cache import seed_cache
from app.db import get_database
from app.deps import get_current_user


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    database = get_database()
    database.initialize()
    admin, _ = ensure_admin(database, settings)
    import_legacy_data(database, settings, admin.id)
    copied = seed_cache(settings.seed_cache_dir, settings.cache_dir)
    if copied:
        print(f"已复制 {copied} 个种子日线缓存文件到 {settings.cache_dir}", flush=True)
    yield


app = FastAPI(title="stock-simulator", lifespan=lifespan)


@app.middleware("http")
async def security_headers(request: Request, call_next):  # type: ignore[no-untyped-def]
    response = await call_next(request)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "same-origin")
    if request.url.path.startswith(("/admin", "/api/auth")):
        response.headers.setdefault("Cache-Control", "no-store")
    return response


app.include_router(auth_router)
app.include_router(admin_router)
protected = [Depends(get_current_user)]
app.include_router(bars_router, dependencies=protected)
app.include_router(indices_router, dependencies=protected)
app.include_router(simulator_router, dependencies=protected)
app.include_router(stocks_router, dependencies=protected)


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


settings = get_settings()
if settings.static_dir.is_dir():
    app.mount("/", StaticFiles(directory=settings.static_dir, html=True), name="static")
