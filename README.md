# stock-simulator

A 股日线模拟交易与指标研究工具。只做本地模拟和研究，不连接实盘，不构成投资建议。

## 快速开始

需要 `uv` 和 Node.js 20+。

```bash
./start.sh
```

- 前端：http://127.0.0.1:5173
- 后端：http://127.0.0.1:8000

## 功能

- 日/周/月 K 线，MA、成交量、MACD
- 逐交易日推进的本地模拟交易
- 自定义指数
- MACD 参数研究与回测脚本

## 目录

- `backend/`：FastAPI、Baostock 数据缓存、模拟交易、研究脚本
- `frontend/`：React + TypeScript + lightweight-charts
- `design/`：设计、ADR 与研究结论
- `human-source/`：用户提供的原始资料

数据源为 Baostock，日线缓存位于 `backend/data/cache/`。

## 检查

```bash
cd backend && uv run pytest -q
cd frontend && npm run build && npm run check:chart
```
