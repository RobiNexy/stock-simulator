# FEATURE__daily-bars-from-baostock

> 状态：已确认并已实现（2026-11-06）。验证：单元与接口测试通过；集成测试（`uv run pytest -m network`）与 Baostock 前复权逐日一致，含成交量；真实接口首次请求约 14 秒，缓存命中约 0.04 秒。实现位于 `backend/app/data/`、`backend/app/api/bars.py`。

## 目标与行为

研究和模拟器都需要 A 股日线。本功能让后端能按股票代码和日期区间取得日线，并在本地缓存，避免重复请求 Baostock。

- 给定 股票代码（如 `600000`）和日期区间，当 请求日线，则 返回该区间内每个交易日的 开高低收、成交量、成交额。
- 给定 同一股票和区间已缓存，当 再次请求，则 不访问 Baostock，直接读取缓存。
- 给定 股票代码不存在或区间无交易日，当 请求日线，则 返回 404，不返回伪造数据。
- 给定 Baostock 不可用或登录失败，当 请求日线，则 返回 503 并说明原因，不静默返回部分数据。
- 给定 `start` 晚于 `end`，当 请求日线，则 返回 422。
- 返回的日期为 `YYYY-MM-DD` 字符串，价格、成交量、成交额为数字类型（不是字符串）。
- 复权参数 `adjust` 取值 `none`（不复权）或 `forward`（前复权），默认 `forward`。

## 不包含或不变量

- 不做实时行情、盘口、分钟线。
- 不做财务数据、指数成分等其他数据。
- 不同复权口径的数据不得混存在同一结果中；缓存只存不复权原始数据和除权因子，复权在读取时计算。
- Baostock 的数据不与其他数据源混用（见 [[ADR__use-baostock-as-daily-data-source]]）。
- 缓存只追加和覆盖本功能自己的文件，不删除用户手动放入的文件。
- 不依赖 Baostock 的 `get_data()`，由本项目自行读取行数据，避免 pandas 版本兼容问题。

## 已确定的实现选择

- **复权**：缓存不复权原始日线与 Baostock 除权因子（`query_adjust_factor`）；前复权在读取时计算。计算结果须与 Baostock 直接返回的前复权（`adjustflag=2`）在测试区间内一致，否则不得上线。
- **缓存格式**：Parquet，每只股票两个文件（日线、除权因子），另有一个 JSON 记录获取日期（`fetched_through`）。缓存目录默认 `backend/data/cache/`，已加入 `.gitignore`。
- **缓存范围**：每次获取从上市日 `1990-12-19` 至获取当日的完整历史。除权因子值与查询结束日无关（已验证），因此完整历史缓存可复用。
- **刷新条件**：请求的 `end` 晚于 `fetched_through` 时重新获取全量。已知限制：当日盘后数据若在当日盘前已缓存，需次日请求才会刷新。
- **前复权算法**（已与 Baostock `adjustflag=2` 逐日对比，600000 2010–2024 共 3643 个交易日，最大误差 1.8e-15）：某日的前复权因子取该日及之前最近一次除权日的 `foreAdjustFactor`；首个除权日之前取 1。开高低收乘以该因子；成交量与成交额不变（由集成测试核对 Baostock 前复权返回的成交量）。
- **API**：`GET /api/bars/daily?code=600000&start=2024-01-01&end=2024-12-31&adjust=forward`。模拟器通过 `/api/sim/state` 使用同一份缓存日线，前端图表负责展示。

## 相关设计

- [[ADR__use-baostock-as-daily-data-source]]
- [[ADR__frontend-backend-split-fastapi-react]]
