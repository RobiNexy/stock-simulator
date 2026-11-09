# 项目现状

> 仅记录不在代码或 Git 中显而易见、且下次工作需要知道的当前事实。无此类事实时可保持为空。

## 当前状态

- 已建立 Git 仓库并推送到 GitHub：`RobiNexy/stock-learn`（private，`main`）。`backend/data/research/` 的研究结果纳入版本控制；缓存、自定义指数和模拟账户状态不纳入。
- 模拟器、日线缓存、自定义指数、日/周/月图表、MACD 实证与理论研究均已完成；剩余任务见 [[task_board]]。

## 已知限制

- 数据源为 Baostock（见 [[ADR__use-baostock-as-daily-data-source]]）。其复权为涨跌幅复权法，与同花顺、AKShare 数值不同，不得混用。
- Baostock 5 分钟线仅 2020 年起有数据。
- Baostock 无分钟线的历史数据范围限制会影响“按历史重现日内行情”，本项目只做日线模拟。
- Baostock 无科创 50（`sh.000688`）指数数据，已验证为空。
- 模拟器状态接口最多返回截至模拟日的最近 2000 根日线，图表无法查看更早历史的指标起点；不足部分由该窗口计算。
- baostock 0.9.x 的 `get_data()` 使用已删除的 `DataFrame.append`，与 pandas 3 不兼容。项目不调用 `get_data()`，改为循环 `rs.next()` 读取行数据，因此不需要固定 pandas 版本。
- 后端以 `uvicorn --reload` 运行；在 `backend/` 内执行 `compileall` 等会产生 `__pycache__` 变更并触发 reload。若 reload 卡在关闭阶段，用 `./start.sh` 重启。

## 重要偏离

仅记录会影响后续工作的设计与实现偏离；无内容时删除本节。
