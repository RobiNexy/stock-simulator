# stock-simulator

A 股日线模拟交易与指标研究工具。只做本地模拟和研究，不连接实盘，不构成投资建议。

## 快速开始（本地开发）

需要 `uv` 和 Node.js 20+。

```bash
./start.sh
```

- 前端：http://127.0.0.1:5173
- 后端：http://127.0.0.1:8000
- 管理后台：http://127.0.0.1:8000/admin

首次启动时，如果数据库里还没有管理员，会用 `ADMIN_USERNAME`（默认 `admin`）和 `ADMIN_PASSWORD` 创建管理员。未设置 `ADMIN_PASSWORD` 时会生成随机密码并打印在启动日志里，登录后请到 `/admin` 重置。

账号不开放注册，管理员在 `/admin` 手动创建、停用、重置密码或删除账号。每个用户的模拟账户和自定义指数彼此隔离。

## Docker 部署

```bash
cp .env.example .env
# 修改 .env 中的 ADMIN_PASSWORD
docker compose up -d --build
```

打开 http://127.0.0.1:8000 ，用 `.env` 中的管理员账号登录。数据保存在 `stock-data` 卷的 `/data` 目录。宿主机端口可通过 `.env` 的 `HOST_PORT` 修改。

部署到托管平台时，把同一镜像发布出去，并满足：

- 暴露 8000 端口（平台若注入 `PORT`，容器会自动使用该端口），健康检查用 `GET /api/health`。
- 为 `/data` 挂载持久卷。
- 设置 `ADMIN_USERNAME`、`ADMIN_PASSWORD`；站点使用 HTTPS 时设置 `SECURE_COOKIES=1`。
- 只运行一个实例（SQLite + 进程内缓存的约束，见 [[ADR__deploy-single-container-sqlite-and-managed-accounts]]）。

常用环境变量：

| 变量 | 默认值 | 说明 |
|---|---|---|
| `DATA_DIR` | `backend/data`（容器内 `/data`） | 可写数据目录 |
| `DATABASE_PATH` | `DATA_DIR/stock-simulator.db` | SQLite 文件 |
| `CACHE_DIR` | `DATA_DIR/cache` | 日线 parquet 缓存 |
| `SEED_CACHE_DIR` | `backend/data/seed_cache` | 镜像内置种子缓存 |
| `STATIC_DIR` | `frontend/dist` | 前端构建产物 |
| `ADMIN_USERNAME` / `ADMIN_PASSWORD` | `admin` / 无 | 首次启动创建管理员 |
| `SECURE_COOKIES` | `0` | 生产环境设为 `1` |
| `SESSION_DAYS` | `30` | 登录有效期 |

## Railway 部署

1. 把仓库推到 GitHub。
2. 登录 [Railway](https://railway.com)，选择 **New Project → Deploy from GitHub repo**，选中本仓库。Railway 会自动使用根目录的 `Dockerfile`。
3. 在服务的 **Settings → Region** 选择 **Southeast Asia (Singapore)**，距离 Baostock 更近。
4. 在 **Variables** 添加：

   ```text
   ADMIN_USERNAME=admin
   ADMIN_PASSWORD=<至少 8 位的密码>
   SECURE_COOKIES=1
   TZ=Asia/Shanghai
   SESSION_DAYS=30
   ```

   不要手动设置 `PORT`；Railway 会注入，入口脚本会自动监听该端口。Railway 域名是 HTTPS，所以 `SECURE_COOKIES=1`。

5. 在 **Volumes** 添加卷，**Mount Path** 填 `/data`。SQLite、账户、自定义指数和运行期日线缓存都会写入这里；卷被删除时数据会丢失。入口脚本会在启动时修正卷属主再降权运行，不需要手工 chown。
6. 在 **Settings → Networking** 点击 **Generate Domain**。如果要求填写端口，填 Railway 在 Variables 中注入的 `PORT` 值。健康检查路径填 `/api/health`。
7. 部署完成后打开域名，用管理员账号登录；进入 `/admin` 创建朋友账号。
8. 重新部署或重启不会清空 `/data`。备份时复制该卷中的 SQLite 文件和缓存目录。

Railway 按当前套餐与用量计费。首次构建会把约 46 MB 的种子缓存打进镜像，因此比后续部署慢一些。服务实例数保持 1，不要开多副本：SQLite 和进程内锁不支持多实例。

## 功能

- 日/周/月 K 线，MA、成交量、MACD
- 逐交易日推进的本地模拟交易
- 自定义指数（每个用户私有）
- MACD 参数研究与回测脚本
- 管理员手动开通账号，用户数据隔离

## 目录

- `backend/`：FastAPI、SQLite、Baostock 数据缓存、模拟交易、研究脚本
- `frontend/`：React + TypeScript + lightweight-charts
- `design/`：设计、ADR 与研究结论
- `human-source/`：用户提供的原始资料

数据源为 Baostock，日线缓存位于 `backend/data/cache/`。镜像内置沪深300成分股的种子缓存（约 300 只），首次启动复制到数据卷；未缓存的股票在运行时补拉，Baostock 不可用时会退回已有旧缓存。

重新生成种子缓存：

```bash
cd backend
uv run python scripts/build_seed_cache.py --pool hs300 --refresh
```

也支持 `--pool sz50|zz500|all`、`--limit N` 和 `--output <目录>`。

## 数据与备份

SQLite 数据库、日线缓存、会话都在 `DATA_DIR`。备份时复制整个目录即可；恢复时停止容器后覆盖回卷。

## 检查

```bash
cd backend && uv run pytest -q
cd frontend && npm run build && npm run check:chart
```
