# ADR__deploy-single-container-sqlite-and-managed-accounts

## 元数据

- 状态：Accepted
- 提出日期：2026-11-10
- 接受日期：2026-11-10
- 取代：无

## 背景

项目此前是单用户本地工具：模拟账户写单个 `backend/data/paper_account.json`，自定义指数写全局目录，日线缓存写在本地 parquet。现在要部署到托管平台分享给朋友使用，需要：

- 容器化部署，降低平台差异。
- 账号系统：不开放注册，由管理员手动开账号。
- 多用户数据隔离：模拟账户、自定义指数各自独立。
- 日线缓存在部署环境仍可用，但不引入 Redis 等额外服务。
- 用户数据持久化。

## 决策

- **单容器**：多阶段构建，Node 构建前端，Python 运行时由 FastAPI 同时提供 `/api`、`/admin` 和前端静态文件。不引入 nginx 或独立前端容器。
- **SQLite + 持久卷**：`users`、`sessions`、`paper_accounts`、`custom_indices` 存于一个 SQLite 文件，数据库路径由 `DATABASE_PATH` 指定，默认位于 `DATA_DIR`。容器挂载 `/data` 卷。
- **认证**：用户名 + 密码；密码用标准库 `hashlib.scrypt` 加盐哈希；登录后发放随机会话令牌，数据库只存 SHA-256 摘要，支持停用账号后立即撤销会话。Cookie 为 HttpOnly、SameSite=Lax，`SECURE_COOKIES=1` 时加 Secure。状态变更接口依赖 SameSite=Lax 阻止跨站表单携带会话。
- **不开放注册**：`/admin` 提供独立服务端渲染的简易管理页，支持创建账号、启用/停用、重置密码、删除账号；表单单次 CSRF 令牌随会话保存。
- **管理员初始化**：数据库没有管理员时，用 `ADMIN_USERNAME` / `ADMIN_PASSWORD` 创建；未提供密码则生成随机密码并打印到启动日志。
- **数据隔离**：模拟账户以 `user_id` 为主键；自定义指数按 `user_id` 查询。自定义指数默认每个用户私有。
- **日线缓存**：继续用 parquet 磁盘缓存，不引入 Redis。镜像内置 `backend/data/seed_cache/`，首次启动复制到可写缓存目录；缺失代码在运行时从 Baostock 拉取。刷新失败时若已有缓存则继续使用旧数据，并记录警告。
- **并发约束**：单进程 uvicorn；SQLite 使用 WAL；Baostock 调用由进程内锁串行化。账户写入按用户加锁。

## 理由

- 单人维护，单容器和 SQLite 的运维成本最低，也最容易迁移到 VPS、Render、Fly、Railway 等平台。
- 用户量预期为朋友级小规模，单进程足以支撑；不引入 Postgres/Redis 可减少依赖和费用。
- 预置缓存避免海外托管首次访问等待 Baostock；运行时补拉保证任意股票仍可用。
- 自实现 scrypt 会话认证避免额外原生依赖，行为可控且可测试。

## 后果

- 收益：一条 `docker run` 即可部署；无外部服务；账号与数据都在一个卷里，备份只需复制 SQLite 文件和缓存目录。
- 代价：只能单实例运行，不能水平扩容；SQLite 和内存缓存不适合高并发。
- HTTPS：生产环境需由平台或反向代理终止 TLS，并设置 `SECURE_COOKIES=1`。
- Baostock 在海外可能慢或不可用；预置缓存只覆盖仓库中已有的股票，首次访问未缓存代码仍可能失败。
- 旧版本地数据会在首次启动时导入管理员账号（一次性标记文件 `DATA_DIR/.legacy_imported`）。

## 重新评估条件

- 需要多实例或明显更高的并发。
- 用户量或数据量超过单文件 SQLite 的舒适范围。
- Baostock 不可用，需要更换数据源或离线数据包。
