import random
from datetime import date, timedelta

import baostock as bs

from app.data.baostock_source import DataSourceError, _rows, _session


class StockUniverse:
    def random_stock(self, pool: str = "all") -> tuple[str, str]:
        today = date.today()
        trading_day = self._latest_trading_day(today)
        with _session():
            if pool == "all":
                result = bs.query_all_stock(trading_day)
            elif pool == "hs300":
                result = bs.query_hs300_stocks(trading_day)
            elif pool == "sz50":
                result = bs.query_sz50_stocks(trading_day)
            elif pool == "zz500":
                result = bs.query_zz500_stocks(trading_day)
            else:
                raise ValueError("未知随机抽股范围")
            fields = result.fields
            code_index = fields.index("code") if "code" in fields else 0
            name_field = "code_name" if "code_name" in fields else "name"
            name_index = fields.index(name_field) if name_field in fields else min(2, len(fields) - 1)
            status_index = fields.index("tradeStatus") if "tradeStatus" in fields else None
            rows = _rows(result)
            candidates: list[tuple[str, str]] = []
            for row in rows:
                code, name = row[code_index], row[name_index]
                if status_index is not None and row[status_index] != "1":
                    continue
                if not (code.startswith(("sh.60", "sz.00", "sz.30"))):
                    continue
                if "ST" in name.upper() or "退" in name:
                    continue
                candidates.append((code, name))
            random.SystemRandom().shuffle(candidates)
            cutoff = today - timedelta(days=365)
            for code, name in candidates[:80]:
                basic = bs.query_stock_basic(code=code)
                basic_rows = _rows(basic)
                if not basic_rows:
                    continue
                ipo_date = basic_rows[0][2]
                if ipo_date and date.fromisoformat(ipo_date) <= cutoff:
                    return code.split(".", maxsplit=1)[1], name
        raise DataSourceError("当前抽股范围内没有符合条件的股票")

    @staticmethod
    def _latest_trading_day(today: date) -> str:
        completed_through = today - timedelta(days=1)
        with _session():
            result = bs.query_trade_dates(
                start_date=(completed_through - timedelta(days=30)).isoformat(),
                end_date=completed_through.isoformat(),
            )
            rows = _rows(result)
        trading_dates = [
            row[0]
            for row in rows
            if len(row) > 1 and row[1] == "1" and row[0] <= completed_through.isoformat()
        ]
        return max(trading_dates) if trading_dates else completed_through.isoformat()
