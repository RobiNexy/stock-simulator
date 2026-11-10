#!/bin/sh
set -eu

# 平台卷通常以 root 挂载；启动时修正数据目录属主，再降权运行。
if [ "$(id -u)" = "0" ]; then
  mkdir -p "${DATA_DIR:-/data}"
  chown -R appuser:appuser "${DATA_DIR:-/data}"
  exec setpriv --reuid=10001 --regid=10001 --init-groups "$0" "$@"
fi

# 容器内默认 8000；托管平台注入 PORT 时使用平台端口。
exec /app/.venv/bin/uvicorn app.main:app \
  --host 0.0.0.0 \
  --port "${PORT:-8000}" \
  --proxy-headers \
  --forwarded-allow-ips "${FORWARDED_ALLOW_IPS:-*}"
