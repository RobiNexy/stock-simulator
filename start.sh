#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BACKEND_PORT="${BACKEND_PORT:-8000}"
FRONTEND_PORT="${FRONTEND_PORT:-5173}"

for cmd in uv npm; do
  if ! command -v "$cmd" >/dev/null 2>&1; then
    echo "未找到 $cmd，请先安装。" >&2
    exit 1
  fi
done

echo "==> 安装后端依赖"
(cd "$ROOT/backend" && uv sync)

if [ ! -d "$ROOT/frontend/node_modules" ]; then
  echo "==> 安装前端依赖"
  (cd "$ROOT/frontend" && npm install)
fi

pids=()
cleanup() {
  for pid in "${pids[@]}"; do
    kill "$pid" 2>/dev/null || true
  done
}
trap cleanup EXIT INT TERM

echo "==> 启动后端 http://127.0.0.1:$BACKEND_PORT"
(cd "$ROOT/backend" && exec .venv/bin/uvicorn app.main:app --reload --host 127.0.0.1 --port "$BACKEND_PORT") &
pids+=($!)

echo "==> 等待后端就绪"
backend_ready=0
for _ in {1..30}; do
  if "$ROOT/backend/.venv/bin/python" -c \
    "from urllib.request import urlopen; urlopen('http://127.0.0.1:$BACKEND_PORT/api/health', timeout=1)" \
    >/dev/null 2>&1; then
    backend_ready=1
    break
  fi
  sleep 1
done
if [ "$backend_ready" -ne 1 ]; then
  echo "后端未能在 30 秒内启动。" >&2
  exit 1
fi

echo "==> 管理后台 http://127.0.0.1:$BACKEND_PORT/admin（首次启动的管理员密码见后端日志）"

echo "==> 启动前端 http://127.0.0.1:$FRONTEND_PORT"
(cd "$ROOT/frontend" && exec node_modules/.bin/vite --host 127.0.0.1 --port "$FRONTEND_PORT" --strictPort) &
pids+=($!)

wait -n
