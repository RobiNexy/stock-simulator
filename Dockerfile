# syntax=docker/dockerfile:1

# [依情况] 前端需要 Node 构建、后端运行只需 Python，因此采用多阶段。
FROM node:22-alpine AS frontend
WORKDIR /app
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

FROM python:3.12-slim AS runtime

# 生产加固：两个基础镜像可改为 @sha256:<digest>，完全固定构建输入。
# 固定构建工具版本，避免构建环境随 latest 漂移。
COPY --from=ghcr.io/astral-sh/uv:0.10.11 /uv /uvx /bin/

ENV PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    TZ=Asia/Shanghai

# 交易日按 Asia/Shanghai 判断，运行镜像需要 tzdata。
RUN apt-get update \
    && apt-get install -y --no-install-recommends tzdata \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# 先装依赖再拷代码，代码变更可复用依赖层。
COPY backend/pyproject.toml backend/uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

COPY backend/app ./app
# 种子缓存是只读镜像内容；运行期新增缓存写入 /data。
COPY backend/data/seed_cache ./seed_cache
COPY docker-entrypoint.sh ./docker-entrypoint.sh
COPY --from=frontend /app/dist ./static

RUN chmod +x ./docker-entrypoint.sh

# 运行期状态全部落在 /data，镜像内不写入账户、数据库或缓存。
ENV DATA_DIR=/data \
    SEED_CACHE_DIR=/app/seed_cache \
    STATIC_DIR=/app/static

# 非 root 运行；入口脚本修正卷属主后降权到该用户。
RUN useradd --create-home --uid 10001 appuser \
    && mkdir -p /data \
    && chown -R appuser:appuser /data

VOLUME ["/data"]
EXPOSE 8000

# 平台注入 PORT 时使用该端口；健康检查与入口脚本读取同一个变量。
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import os, urllib.request; urllib.request.urlopen('http://127.0.0.1:' + os.environ.get('PORT', '8000') + '/api/health', timeout=3)"

CMD ["/app/docker-entrypoint.sh"]
