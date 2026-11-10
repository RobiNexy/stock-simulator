# 项目现状

> 仅记录不在代码或 Git 中显而易见、且下次工作需要知道的当前事实。无此类事实时可保持为空。

## 当前状态

- 已建立 Git 仓库并推送到 GitHub：`RobiNexy/stock-simulator`（public，`main`）。`backend/data/research/` 的研究结果纳入版本控制；缓存、自定义指数和模拟账户状态不纳入。
- 模拟器、日线缓存、自定义指数、日/周/月图表、MACD 实证与理论研究均已完成；剩余任务见 [[task_board]]。
- 已支持容器化部署与多用户：单容器由 FastAPI 同时提供 `/api`、`/admin` 和前端静态文件；SQLite 存用户、会话、模拟账户和自定义指数；数据目录挂载 `/data`。见 [[ADR__deploy-single-container-sqlite-and-managed-accounts]]。
- 账号不开放注册。管理员在 `/admin` 创建/停用/重置/删除账号；首次启动用 `ADMIN_PASSWORD` 初始化管理员，未设置时随机生成并打印到日志。
- 每个用户的模拟账户和自定义指数按 `user_id` 隔离；旧版单用户 JSON 数据在首次启动时导入管理员账号。
- 日线缓存继续使用 parquet，不引入 Redis。镜像内置沪深300成分股的种子缓存（约 300 只，约 46 MB），启动时复制缺失文件到数据目录；未缓存代码运行时补拉，Baostock 刷新失败时回退到已有缓存。`backend/scripts/build_seed_cache.py` 可重新生成或扩大范围。
- 周/月图表已按 W-FRI / 自然月分组、标签取最后交易日、MA 按周期根数、周/月 MACD 使用 `close` + SMA 种子 + ×2 柱体实现；周线 MACD 采用时间语义 `(12,26,9)`。日线“一月”MA 取 21；停牌日按前后真实 K 线线性插值生成虚拟 K，并参与指标计算。未完成周期标记 provisional。

## 已知限制

- 数据源为 Baostock（见 [[ADR__use-baostock-as-daily-data-source]]）。其复权为涨跌幅复权法，与同花顺、AKShare 数值不同，不得混用。
- Baostock 服务器在中国，海外托管可能慢或不可用；种子缓存覆盖沪深300约 300 只，其余代码首次访问仍需运行时拉取。
- 部署形态为单实例单进程：SQLite + WAL + 进程内缓存，不能水平扩容；Baostock 调用由进程内锁串行化。
- 生产环境需由平台/反向代理终止 HTTPS，并设置 `SECURE_COOKIES=1`；否则会话 cookie 不加密传输。
- 备份需要覆盖整个 `DATA_DIR`（SQLite、缓存、旧版导入标记）。
- Baostock 5 分钟线仅 2020 年起有数据。
- Baostock 无分钟线的历史数据范围限制会影响“按历史重现日内行情”，本项目只做日线模拟。
- Baostock 无科创 50（`sh.000688`）指数数据，已验证为空。
- 模拟器状态接口最多返回截至模拟日的最近 2000 根日线，图表无法查看更早历史的指标起点；不足部分由该窗口计算。
- baostock 0.9.x 的 `get_data()` 使用已删除的 `DataFrame.append`，与 pandas 3 不兼容。项目不调用 `get_data()`，改为循环 `rs.next()` 读取行数据，因此不需要固定 pandas 版本。
- 后端以 `uvicorn --reload` 运行；在 `backend/` 内执行 `compileall` 等会产生 `__pycache__` 变更并触发 reload。若 reload 卡在关闭阶段，用 `./start.sh` 重启。

## 重要偏离

仅记录会影响后续工作的设计与实现偏离；无内容时删除本节。
